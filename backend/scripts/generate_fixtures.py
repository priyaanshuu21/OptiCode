#!/usr/bin/env python3
"""Freeze real compiler output into JSON fixtures for the frontend tests.

The frontend tests must not need a running backend, a Python interpreter, or a network. They
also must not be testing a hand-written approximation of what the compiler produces, because
the one bug that matters in a visualization is a shape mismatch, and a hand-written shape is
exactly what would hide it.

So this script calls the real compiler over the real example programs and writes the actual
JSON responses to ``frontend/src/test/fixtures/``. The frontend tests then assert against those
files, which means a change to a payload shape shows up as a test failure rather than as a view
that silently renders nothing.

Usage::

    python backend/scripts/generate_fixtures.py
    python backend/scripts/generate_fixtures.py --check   # fail if out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "backend" / "examples"
FIXTURES = REPO_ROOT / "frontend" / "src" / "test" / "fixtures"

#: Which example programs to freeze, and why each one is worth freezing.
#:
#: Chosen for payload shape rather than for being interesting programs. Each one produces at
#: least one payload element the others do not, so together they cover every branch of every
#: serializer the frontend renders.
CASES = [
    # The simplest program that still produces tokens, an AST, TAC, a CFG, and data flow.
    ("hello", "01_hello.c", "analyze"),
    # Loops produce back edges, so the Blocks view has a cycle to lay out and the data-flow
    # view has a fixpoint to show rather than a straight line.
    ("loops", "03_loops.c", "optimize"),
    # scanf and printf give the TAC view real side effects and a read instruction, and the
    # unverified-oracle path shows up because helper functions take arguments.
    ("io", "06_io.c", "optimize"),
    # Mixed int and float arithmetic: the constants, narrowing, and format strings are all
    # different types, which is where a view that assumes every value is a number falls over.
    ("mixed_types", "08_mixed_types.c", "optimize"),
    # Unreachable and dead code, so diagnostics of several severities are present.
    ("dead_code", "11_dead_assignments.c", "optimize"),
]

#: A program the semantic stage rejects. The frontend has to render a failed compile, and
#: this is the only way to test that path without hand-writing a broken response.
#:
#: Written inline rather than added to ``examples/`` on purpose: every file there is a program
#: that *compiles*, because that directory is the showcase. A broken program among them would
#: be a permanent puzzle, so the source lives here where the reason it exists is on the line
#: above it.
ERROR_SOURCE = """\
int main() {
    int total = compute(unknown_name);
    printf("%d\\n", total);
    return 0;
}
"""


def _cases() -> list[tuple[str, str | None, str, str]]:
    """``(name, example filename or None, source, mode)`` for every fixture."""
    cases: list[tuple[str, str | None, str, str]] = [
        (name, filename, "", mode) for name, filename, mode in CASES
    ]
    cases.append(("error", None, ERROR_SOURCE, "analyze"))
    return cases


def responses() -> dict[str, object]:
    """One response document per case, keyed by fixture name."""
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from fastapi.testclient import TestClient

    from opticode.api.main import app

    client = TestClient(app)
    out: dict[str, object] = {}
    for name, filename, inline, mode in _cases():
        if filename is not None:
            source_path = EXAMPLES / filename
            if not source_path.exists():
                raise SystemExit(f"missing example {source_path}")
            source = source_path.read_text(encoding="utf-8")
        else:
            source = inline
        response = client.post("/api/compile", json={"source": source, "mode": mode})
        body = response.json()

        # Timings are wall-clock and would make the fixture churn on every run. The stage
        # *names* are kept, because a view that decides which tabs to show needs them.
        body["timings"] = {stage: 0.0 for stage in body.get("timings", {})}
        out[name] = {"source": source, "mode": mode, "response": body}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the fixtures are out of date instead of writing them",
    )
    args = parser.parse_args()

    FIXTURES.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, document in responses().items():
        path = FIXTURES / f"{name}.json"
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                print(f"{path} is out of date; regenerate it", file=sys.stderr)
                return 1
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path)

    if args.check:
        print(f"All {len(_cases())} fixtures are up to date")
        return 0
    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
