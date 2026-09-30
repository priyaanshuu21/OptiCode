"""Every example in ``examples/`` must compile cleanly through the real pipeline.

This is the end-to-end guard: if lowering ever breaks for a construct the examples use,
this fails before anyone opens the frontend.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opticode.api.main import app
from opticode.ir import check_program, lower, print_program
from opticode.parser import parse
from opticode.semantic import analyze

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*.c"))
EXAMPLE_NAMES = [path.name for path in EXAMPLES]


def test_every_example_is_discovered():
    """A silently empty example set would make this whole file vacuous."""
    assert len(EXAMPLES) >= 13


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("path", EXAMPLES, ids=EXAMPLE_NAMES)
def test_example_parses_without_errors(path: Path):
    program, diagnostics = parse(path.read_text(encoding="utf-8"))
    assert program is not None
    errors = [d for d in diagnostics if d.severity.value == "error"]
    assert not errors, [f"{d.code_str} {d.message}" for d in errors]


@pytest.mark.parametrize("path", EXAMPLES, ids=EXAMPLE_NAMES)
def test_example_passes_semantic_analysis(path: Path):
    program, _ = parse(path.read_text(encoding="utf-8"))
    result = analyze(program)
    errors = [d for d in result.diagnostics if d.severity.value == "error"]
    assert not errors, [f"{d.code_str} {d.message}" for d in errors]


@pytest.mark.parametrize("path", EXAMPLES, ids=EXAMPLE_NAMES)
def test_example_lowers_and_satisfies_invariants(path: Path):
    program, _ = parse(path.read_text(encoding="utf-8"))
    result = analyze(program)
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    check_program(lowered.program)


@pytest.mark.parametrize("path", EXAMPLES, ids=EXAMPLE_NAMES)
def test_example_compiles_through_the_api(client: TestClient, path: Path):
    response = client.post("/api/compile", json={"source": path.read_text(encoding="utf-8")})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, [d["message"] for d in body["diagnostics"]]
    assert body["ir"] is not None
    assert body["ir"]["function_count"] >= 1


def test_examples_cover_the_language():
    """Guard against the corpus drifting down to one trivial program."""
    combined = "\n".join(path.read_text(encoding="utf-8") for path in EXAMPLES)
    for feature in (
        "while",
        "for",
        "break",
        "continue",
        "if",
        "else",
        "return",
        "printf",
        "scanf",
        "float",
        "&&",
        "||",
        "!=",
        "<=",
        "%",
        "+=",
        "++",
    ):
        assert feature in combined, f"no example exercises '{feature}'"


def test_examples_include_the_optimization_targets():
    """The optimizer needs programs that actually contain the patterns it removes."""
    names = {path.name for path in EXAMPLES}
    for required in (
        "01_hello.c",
        "06_io.c",
        "10_unreachable.c",
        "11_dead_assignments.c",
        "12_constant_folding.c",
        "13_constant_propagation.c",
    ):
        assert required in names, f"missing example {required}"


def test_unreachable_and_dead_assignment_examples_are_nontrivial():
    """The dead-code examples must contain dead code, or they prove nothing."""
    unreachable = (EXAMPLES_DIR / "10_unreachable.c").read_text(encoding="utf-8")
    dead = (EXAMPLES_DIR / "11_dead_assignments.c").read_text(encoding="utf-8")
    assert "return x + 1;" in unreachable and "return dead;" in unreachable
    assert dead.count("=") > 10


@pytest.mark.parametrize("path", EXAMPLES, ids=EXAMPLE_NAMES)
def test_lowering_is_deterministic_across_runs(path: Path):
    """The same source must always produce byte-identical TAC."""
    source = path.read_text(encoding="utf-8")

    def dump() -> str:
        program, _ = parse(source)
        lowered = lower(analyze(program))
        assert lowered.ok
        return print_program(lowered.program)

    assert dump() == dump()
