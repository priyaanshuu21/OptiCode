#!/usr/bin/env python3
"""Emit the OpenAPI document the frontend generates its types from.

The frontend has no hand-written copy of the API contract, because a hand-written copy is a
copy that goes stale. This script asks FastAPI for the spec it produces from the live Pydantic
models and writes it to ``frontend/openapi.json``, which ``npm run gen:types`` turns into
TypeScript. Regenerating is therefore a two-step commit: run this, run the npm script, and any
disagreement between the two shows up as a diff.

Usage::

    python scripts/generate_openapi.py            # write frontend/openapi.json
    python scripts/generate_openapi.py --check    # fail if the file is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: ``backend/scripts/generate_openapi.py`` -> repository root is two levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "frontend" / "openapi.json"


def render() -> str:
    """The OpenAPI document as text, with stable ordering so diffs are readable."""
    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from opticode.api.main import app

    document = app.openapi()
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if frontend/openapi.json is out of date instead of writing it",
    )
    args = parser.parse_args()

    text = render()
    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT} does not exist; run without --check", file=sys.stderr)
            return 1
        if OUTPUT.read_text(encoding="utf-8") != text:
            print(f"{OUTPUT} is out of date; regenerate it", file=sys.stderr)
            return 1
        print(f"{OUTPUT} is up to date")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
