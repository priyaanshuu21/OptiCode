"""The examples library endpoints, and the path handling behind them.

Two things are being defended here, and they are tested separately on purpose.

The **contract**: ``GET /api/examples`` lists the corpus without its source, ``GET
/api/examples/{name}`` returns one program, and both are shaped so the frontend can build a
menu from the listing alone.

The **path handling**: a name from a URL becomes a file read. A slug pattern that rejects
``.`` and ``/`` before building a path is the primary defence, and a containment re-check on
the resolved path is the second one for when the *manifest* is wrong rather than the request.
Both are tested, and so is the third case: a well-formed name that simply is not in the
manifest, which has to be a 404 rather than a 400.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opticode.api import examples as library
from opticode.api.examples import (
    EXAMPLES_ROOT,
    MAX_EXAMPLE_BYTES,
    SLUG,
    Example,
    ExampleNotFound,
    InvalidExampleName,
    UnsafeExamplePath,
    example_detail,
    example_summaries,
    load_manifest,
    resolve_example_path,
)

EXAMPLES = load_manifest()
NAMES = [example.name for example in EXAMPLES]


def _entry(**overrides: object) -> Example:
    """A manifest entry built by hand, so a test can say only what it cares about."""
    fields: dict[str, object] = {
        "name": "handmade",
        "title": "handmade",
        "file": "valid/01_hello.c",
        "valid": True,
        "summary": "built by a test",
        "tags": ("test",),
        "features": ("declarations",),
        "expected_diagnostics": (),
        "stdin": "",
        "suggested_mode": "analyze",
    }
    fields.update(overrides)
    return Example(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------- the contract


def test_listing_returns_every_example_without_their_source(client: TestClient):
    response = client.get("/api/examples")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == len(EXAMPLES)
    assert body["count"] >= 10
    assert len(body["examples"]) == body["count"]
    assert body["invalid_count"] == sum(1 for example in EXAMPLES if not example.valid)
    for item in body["examples"]:
        assert set(item) == {
            "name",
            "title",
            "file",
            "valid",
            "summary",
            "tags",
            "features",
            "expected_diagnostics",
            "suggested_mode",
        }
        # The listing exists so a menu does not have to download every program.
        assert "source" not in item


def test_listing_order_is_stable(client: TestClient):
    first = client.get("/api/examples").json()["examples"]
    second = client.get("/api/examples").json()["examples"]
    assert first == second
    assert [item["name"] for item in first] == NAMES


def test_detail_returns_the_source_and_its_metadata(client: TestClient):
    for example in EXAMPLES:
        response = client.get(f"/api/examples/{example.name}")
        assert response.status_code == 200, example.name
        body = response.json()
        assert body["name"] == example.name
        assert body["valid"] == example.valid
        assert body["stdin"] == example.stdin
        assert body["expected_diagnostics"] == list(example.expected_diagnostics)
        # The text has to be the file, not a description of it: a program that renders in
        # the editor has to be the program the compiler was tested against.
        on_disk = resolve_example_path(example).read_text(encoding="utf-8")
        assert body["source"] == on_disk
        assert body["lines"] == len(on_disk.splitlines())


def test_detail_of_a_valid_example_compiles_through_the_api(client: TestClient):
    detail = client.get("/api/examples/loops").json()
    body = client.post(
        "/api/compile",
        json={"source": detail["source"], "mode": "optimize"},
    ).json()
    assert body["success"] is True, [d["message"] for d in body["diagnostics"]]


def test_detail_of_an_invalid_example_reports_the_expected_diagnostics(client: TestClient):
    """The corpus is half programs that must fail, so the failure has to be the *right* one.

    Checking codes and severities together is what makes this worth having: an example that
    fails for a different reason still "fails", and a corpus entry that quietly stopped
    testing the construct it was written for is worse than a missing example.
    """
    for example in EXAMPLES:
        if example.valid:
            continue
        detail = client.get(f"/api/examples/{example.name}").json()
        assert detail["valid"] is False
        body = client.post("/api/compile", json={"source": detail["source"]}).json()
        assert body["success"] is False, example.name
        reported = {d["code"]: d["severity"] for d in body["diagnostics"]}
        expected = {
            code: ("warning" if code.startswith("W-") else "error")
            for code in detail["expected_diagnostics"]
        }
        assert expected.items() <= reported.items(), f"{example.name}: {reported}"


def test_a_missing_name_is_a_404(client: TestClient):
    response = client.get("/api/examples/no-such-example")
    assert response.status_code == 404
    assert "no-such-example" in response.json()["detail"]


# ------------------------------------------------------------ name handling


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..%2f..%2fetc%2fpasswd",
        "valid/01_hello",
        "hello.c",
        "hello world",
        "Hello",
        "/etc/passwd",
        "valid\\01_hello.c",
        "..",
        "hello/../01_hello",
        "manifest.json",
    ],
)
def test_names_that_are_not_slugs_are_refused(client: TestClient, name: str):
    """None of these can have come from the manifest, so none may reach the filesystem.

    The status is 400 rather than 404 on purpose: a client sending a path is doing
    something wrong in a way a "no such example" reply would hide. ``manifest.json`` is the
    one name here that *is* a slug — it is simply not a program, so it is a 404 and the
    listing route is the only way to reach it.
    """
    response = client.get(f"/api/examples/{name}")
    assert response.status_code in (400, 404), name
    if response.status_code == 400:
        assert "lowercase" in response.json()["detail"]


def test_the_listing_route_is_what_a_trailing_slash_reaches(client: TestClient):
    """``/api/examples/`` is the listing, not an example named "".

    Worth pinning because it is the one name the validator never sees: the router matches it
    as the collection. What matters is that it returns the menu and not a file read.
    """
    response = client.get("/api/examples/")
    assert response.status_code == 200
    assert response.json()["count"] == len(EXAMPLES)


def test_the_slug_pattern_rejects_separators_and_traversal():
    for name in ("..", "../x", "a/b", "a\\b", ".hidden", "a.b", "a b", "A", "a--b", "-a"):
        assert SLUG.match(name) is None, name
    for name in ("hello", "dead-code", "01-hello", "a1", "x"):
        assert SLUG.match(name) is not None, name


def test_resolve_refuses_a_manifest_entry_that_escapes_the_directory():
    """Defence in depth: the manifest is a file a person can edit.

    The slug check already stops a bad *request*, but nothing stops a bad *manifest*. An
    entry pointing at ``../../../etc/passwd`` must be refused, and refused as a fault in
    the deployment rather than as a 404 that hides it.
    """
    for hostile in ("../../../etc/passwd", "valid/../../secrets.txt", "/etc/passwd"):
        with pytest.raises(UnsafeExamplePath):
            resolve_example_path(_entry(file=hostile))


def test_resolve_allows_a_file_inside_the_directory_even_if_its_name_looks_unusual():
    """Containment is the rule, not the spelling of the path.

    ``manifest.json`` is not a program but it *is* inside the directory, so it is the
    manifest's job to not list it. Refusing it here would be checking the wrong thing.
    """
    assert resolve_example_path(_entry(file="manifest.json")).name == "manifest.json"


def test_resolve_refuses_an_entry_naming_a_missing_file():
    with pytest.raises(UnsafeExamplePath):
        resolve_example_path(_entry(file="valid/99_not_a_real_file.c"))


def test_looking_up_a_bad_name_raises_rather_than_returning_nothing():
    with pytest.raises(InvalidExampleName):
        example_detail("../secrets")
    with pytest.raises(ExampleNotFound):
        example_detail("no-such-example")


def test_a_symlink_out_of_the_directory_is_not_followed(tmp_path: Path, monkeypatch):
    """Containment is checked after resolution, so a symlink cannot be used to escape.

    A symlink inside the examples directory pointing at a file elsewhere resolves to that
    elsewhere, and the check is on the resolved path, so it is refused. This is the case a
    check on the *joined* path would let through.
    """
    outside = tmp_path / "secret.c"
    outside.write_text("int main() { return 0; }\n", encoding="utf-8")
    root = tmp_path / "examples"
    (root / "valid").mkdir(parents=True)
    link = root / "valid" / "linked.c"
    try:
        link.symlink_to(outside)
    except OSError:  # pragma: no cover - Windows without developer mode
        pytest.skip("symlinks are not available here")
    monkeypatch.setattr(library, "EXAMPLES_ROOT", root)
    with pytest.raises(UnsafeExamplePath):
        library.resolve_example_path(_entry(file="valid/linked.c"))


# ------------------------------------------------------------ the manifest


def test_every_manifest_entry_is_a_slug_and_names_a_file_inside_the_directory():
    for example in EXAMPLES:
        assert SLUG.match(example.name), example.name
        path = resolve_example_path(example)
        assert path.suffix == ".c"
        assert path.is_relative_to(EXAMPLES_ROOT.resolve())


def test_the_manifest_has_both_kinds_of_program():
    assert any(example.valid for example in EXAMPLES)
    assert any(not example.valid for example in EXAMPLES)


def test_manifest_entries_carry_what_a_menu_needs():
    for example in EXAMPLES:
        assert example.title, example.name
        assert example.summary, example.name
        assert example.tags, example.name
        assert example.features, example.name
        assert example.suggested_mode in ("analyze", "optimize")


def test_an_unreadable_manifest_is_reported_as_a_fault(tmp_path: Path, monkeypatch):
    """A broken library is a deployment problem, and it must not look like an empty menu."""
    monkeypatch.setattr(library, "MANIFEST_PATH", tmp_path / "absent.json")
    with pytest.raises(UnsafeExamplePath):
        load_manifest()

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(library, "MANIFEST_PATH", broken)
    with pytest.raises(UnsafeExamplePath):
        load_manifest()

    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(json.dumps({"version": 1}), encoding="utf-8")
    monkeypatch.setattr(library, "MANIFEST_PATH", shapeless)
    with pytest.raises(UnsafeExamplePath):
        load_manifest()


def test_a_duplicate_manifest_entry_is_refused(tmp_path: Path, monkeypatch):
    """Two entries with one name would make the detail route's answer depend on ordering."""
    duplicate = tmp_path / "manifest.json"
    entry = {"name": "twice", "title": "t", "file": "valid/01_hello.c", "valid": True}
    duplicate.write_text(json.dumps({"examples": [entry, dict(entry)]}), encoding="utf-8")
    monkeypatch.setattr(library, "MANIFEST_PATH", duplicate)
    with pytest.raises(UnsafeExamplePath):
        load_manifest()


def test_a_manifest_entry_without_a_name_or_file_is_refused(tmp_path: Path, monkeypatch):
    for entry in ({"title": "t"}, {"name": "x"}, {"name": "x", "file": 3}):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"examples": [entry]}), encoding="utf-8")
        monkeypatch.setattr(library, "MANIFEST_PATH", path)
        with pytest.raises(UnsafeExamplePath):
            load_manifest()


def test_a_manifest_name_that_is_not_a_slug_is_refused(tmp_path: Path, monkeypatch):
    """The manifest is trusted to point at files, not to invent names that break URLs."""
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"examples": [{"name": "Not A Slug", "file": "valid/01_hello.c"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "MANIFEST_PATH", path)
    with pytest.raises(UnsafeExamplePath):
        load_manifest()


def test_the_route_reports_a_broken_library_as_a_server_fault(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(library, "MANIFEST_PATH", Path("does-not-exist.json"))
    response = client.get("/api/examples")
    assert response.status_code == 500
    assert "unavailable" in response.json()["detail"]


# ------------------------------------------------------------------ caching


def test_editing_an_example_is_visible_on_the_next_request(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """The file cache is keyed on mtime, so a corpus can be edited while the app runs."""
    root = tmp_path / "examples"
    (root / "valid").mkdir(parents=True)
    program = root / "valid" / "editable.c"
    program.write_text("int main() { return 1; }\n", encoding="utf-8")
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "name": "editable",
                        "title": "e",
                        "file": "valid/editable.c",
                        "valid": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(library, "EXAMPLES_ROOT", root)
    monkeypatch.setattr(library, "MANIFEST_PATH", manifest)

    assert "return 1" in client.get("/api/examples/editable").json()["source"]
    # Two writes inside the same filesystem tick can share an mtime, which would make this
    # assert nothing. The timestamp is moved forward explicitly so the test is about the
    # cache key, not about how fast the machine writes files.
    program.write_text("int main() { return 2; }\n", encoding="utf-8")
    future = program.stat().st_mtime + 10
    os.utime(program, (future, future))
    assert "return 2" in client.get("/api/examples/editable").json()["source"]


def test_an_oversized_example_is_refused(tmp_path: Path, monkeypatch):
    """A manifest pointing at something enormous is refused rather than read into memory."""
    root = tmp_path / "examples"
    (root / "valid").mkdir(parents=True)
    program = root / "valid" / "huge.c"
    program.write_text("/* x */\n" * (MAX_EXAMPLE_BYTES // 8 + 10), encoding="utf-8")
    monkeypatch.setattr(library, "EXAMPLES_ROOT", root)
    path = library.resolve_example_path(_entry(file="valid/huge.c"))
    library._read.cache_clear()
    with pytest.raises(UnsafeExamplePath):
        library._read(path, path.stat().st_mtime)


def test_summaries_are_dicts_without_source():
    for item in example_summaries():
        assert "source" not in item
        assert isinstance(item["tags"], list)
