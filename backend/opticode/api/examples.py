"""The examples library: reading curated MiniC programs off disk, safely.

The example programs are the compiler's regression corpus *and* the frontend's menu, which
means this module has two obligations that pull in opposite directions. It has to be the one
place the corpus is described, so that a file on disk and its entry in the API cannot drift
apart. And it has to take a name from a URL and turn it into a file read, which is the shape
of thing that turns into a directory traversal if nobody is careful.

Three rules make that safe, and all three are enforced here rather than trusted:

* **The manifest is the only index.** A request names an example by its ``name``; the file it
  resolves to comes from :data:`MANIFEST`. There is no "append ``.c`` and look in the
  directory" path anywhere, so a name that is not in the manifest cannot reach a file.
* **Names are slugs.** :data:`SLUG` accepts lowercase alphanumerics and single hyphens and
  nothing else, which rules out ``.``, ``/``, ``\\`` and NUL before any path is built. A name
  that fails the pattern is a client error, not a miss: it is a malformed request rather than
  an absent example, and the two deserve different status codes.
* **The resolved path is checked against the root anyway.** The manifest is a file a person
  can edit, so ``file: "../../../secrets"`` is a possibility and not a hypothetical. Every
  read re-checks that the resolved path is inside :data:`EXAMPLES_ROOT` and refuses otherwise,
  which means a mistake in the manifest is contained rather than served.

The manifest is read on every request rather than cached at import. It is a few kilobytes
next to a compile that runs three data-flow analyses, and re-reading means an edited manifest
is picked up without restarting the server — which is what makes the corpus editable while
the app is running.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "EXAMPLES_ROOT",
    "MANIFEST_PATH",
    "ExampleDetail",
    "ExampleError",
    "ExampleNotFound",
    "InvalidExampleName",
    "UnsafeExamplePath",
    "example_detail",
    "example_summaries",
    "load_manifest",
    "resolve_example_path",
]

#: ``backend/opticode/api/examples.py`` -> repository root is three levels up.
EXAMPLES_ROOT = Path(__file__).resolve().parents[3] / "examples"
MANIFEST_PATH = EXAMPLES_ROOT / "manifest.json"

#: The only names a request may use. Deliberately narrower than a filename: no dots, no
#: separators, no uppercase, no empty segments. Anything outside this is a malformed request.
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Upper bound on a source file, so a manifest pointing at something enormous is refused
#: rather than read. The compiler's own limit is 100 KB; the examples are all far smaller.
MAX_EXAMPLE_BYTES = 64 * 1024


class ExampleError(Exception):
    """Base class, so a route can distinguish its failures from real bugs."""


class InvalidExampleName(ExampleError):
    """The name is not a slug, so it could not have come from the manifest."""


class ExampleNotFound(ExampleError):
    """The name is a well-formed slug but the manifest has no such example."""


class UnsafeExamplePath(ExampleError):
    """The manifest points outside the examples directory, or at something unreadable."""


@dataclass(frozen=True, slots=True)
class Example:
    """One manifest entry, with the defaults filled in."""

    name: str
    title: str
    file: str
    valid: bool
    summary: str
    tags: tuple[str, ...]
    features: tuple[str, ...]
    expected_diagnostics: tuple[str, ...]
    stdin: str
    suggested_mode: str

    @classmethod
    def from_dict(cls, entry: dict[str, Any]) -> Example:
        """Read one entry, tolerating the fields a hand-edited manifest might leave out.

        Defaults are only allowed for presentation fields. ``name`` and ``file`` have no
        default, because an entry without them describes nothing and should fail loudly when
        the manifest is read rather than serve an empty example.
        """
        name = entry.get("name")
        file = entry.get("file")
        if not isinstance(name, str) or not isinstance(file, str):
            raise UnsafeExamplePath(f"manifest entry is missing 'name' or 'file': {entry!r}")
        return cls(
            name=name,
            title=str(entry.get("title") or name),
            file=file,
            valid=bool(entry.get("valid", True)),
            summary=str(entry.get("summary") or ""),
            tags=tuple(str(item) for item in entry.get("tags") or ()),
            features=tuple(str(item) for item in entry.get("features") or ()),
            expected_diagnostics=tuple(
                str(item) for item in entry.get("expected_diagnostics") or ()
            ),
            stdin=str(entry.get("stdin") or ""),
            suggested_mode=str(entry.get("suggested_mode") or "analyze"),
        )

    def summary_dict(self) -> dict[str, Any]:
        """The listing shape: everything except the source text."""
        return {
            "name": self.name,
            "title": self.title,
            "file": self.file,
            "valid": self.valid,
            "summary": self.summary,
            "tags": list(self.tags),
            "features": list(self.features),
            "expected_diagnostics": list(self.expected_diagnostics),
            "suggested_mode": self.suggested_mode,
        }


@dataclass(frozen=True, slots=True)
class ExampleDetail:
    """A manifest entry plus the program text, which is the point of the request."""

    example: Example
    source: str
    lines: int

    def to_dict(self) -> dict[str, Any]:
        payload = self.example.summary_dict()
        payload["source"] = self.source
        payload["stdin"] = self.example.stdin
        payload["lines"] = self.lines
        return payload


def load_manifest() -> tuple[Example, ...]:
    """Every entry in the manifest, in file order.

    Raises :class:`UnsafeExamplePath` when the manifest is missing or unreadable. That is a
    deployment problem rather than a request problem, so it is allowed to propagate as a 500:
    a curated library that failed to load has nothing useful to say about any one example.
    """
    if not MANIFEST_PATH.is_file():
        raise UnsafeExamplePath(f"{MANIFEST_PATH} does not exist")
    try:
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UnsafeExamplePath(f"{MANIFEST_PATH} could not be read: {error}") from error

    entries = document.get("examples") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        raise UnsafeExamplePath(f"{MANIFEST_PATH} has no 'examples' list")

    examples: list[Example] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnsafeExamplePath(f"manifest entry is not an object: {entry!r}")
        example = Example.from_dict(entry)
        if not SLUG.match(example.name):
            raise UnsafeExamplePath(f"manifest name is not a slug: {example.name!r}")
        if example.name in seen:
            raise UnsafeExamplePath(f"manifest lists {example.name!r} twice")
        seen.add(example.name)
        examples.append(example)
    return tuple(examples)


def _find(name: str) -> Example:
    """The manifest entry for ``name``, or a failure that says which kind."""
    if not SLUG.match(name):
        raise InvalidExampleName(
            "an example name is lowercase letters, digits, and single hyphens"
        )
    for example in load_manifest():
        if example.name == name:
            return example
    raise ExampleNotFound(f"there is no example named {name!r}")


def resolve_example_path(example: Example) -> Path:
    """The file an entry points at, proved to be inside :data:`EXAMPLES_ROOT`.

    The containment check is on the *resolved* path, after symlinks and ``..`` have been
    collapsed, so neither a hand-edited manifest nor a symlink dropped into the directory can
    reach outside it.
    """
    root = EXAMPLES_ROOT.resolve()
    candidate = (EXAMPLES_ROOT / example.file).resolve()
    if candidate != root and root not in candidate.parents:
        raise UnsafeExamplePath(
            f"example {example.name!r} points at {example.file!r}, which is outside "
            f"{EXAMPLES_ROOT}"
        )
    if not candidate.is_file():
        raise UnsafeExamplePath(f"example {example.name!r} names a missing file: {example.file}")
    return candidate


@lru_cache(maxsize=32)
def _read(path: Path, stamp: float) -> str:
    """The file's text, cached against its mtime.

    The cache is keyed on the modification time rather than the path alone, so editing an
    example is visible on the next request instead of after a restart — which is the whole
    reason to re-read the manifest in the first place.
    """
    if path.stat().st_size > MAX_EXAMPLE_BYTES:
        raise UnsafeExamplePath(f"{path} is larger than {MAX_EXAMPLE_BYTES} bytes")
    return path.read_text(encoding="utf-8")


def example_summaries() -> tuple[dict[str, Any], ...]:
    """Every example, without its source. What the menu is built from."""
    return tuple(example.summary_dict() for example in load_manifest())


def example_detail(name: str) -> ExampleDetail:
    """One example's metadata and its source text."""
    example = _find(name)
    path = resolve_example_path(example)
    source = _read(path, path.stat().st_mtime)
    return ExampleDetail(example=example, source=source, lines=len(source.splitlines()))
