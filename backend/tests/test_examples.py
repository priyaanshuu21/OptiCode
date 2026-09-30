"""The example corpus, end to end.

This file is the guard on the one directory a person browses. It has four jobs, and each one
catches a different class of regression:

* **Every program in ``examples/valid`` compiles.** A lowering bug for a construct the
  examples use fails here rather than in the frontend.
* **Every program in ``examples/invalid`` fails, for the reason its manifest entry claims.**
  Without the reason check, a corpus of broken programs would stay green if the compiler
  started rejecting everything — including the valid ones, which is what stops that from
  looking like success.
* **The manifest and the directory agree.** Both ways: an entry pointing at a missing file is
  a dead menu item, and a file nobody listed is a program nothing serves.
* **Optimizing does not change behaviour.** Every valid program is run before and after, and
  the observable results are compared. That is the differential check, and it is the reason
  the corpus is worth keeping.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from opticode.api.examples import (
    EXAMPLES_ROOT,
    Example,
    load_manifest,
    resolve_example_path,
)
from opticode.api.main import app
from opticode.compiler import compile_source
from opticode.ir import check_program, interpret, lower, print_program
from opticode.optimizer import optimize
from opticode.parser import parse
from opticode.semantic import analyze

EXAMPLES: list[Example] = list(load_manifest())
EXAMPLE_NAMES = [example.name for example in EXAMPLES]
VALID = [example for example in EXAMPLES if example.valid]
INVALID = [example for example in EXAMPLES if not example.valid]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def source_of(example: Example) -> str:
    return resolve_example_path(example).read_text(encoding="utf-8")


# --------------------------------------------------------------- the corpus


def test_the_corpus_is_not_vacuous():
    """A silently empty corpus would make this whole file pass without testing anything."""
    assert len(VALID) >= 10, f"only {len(VALID)} compiling examples"
    assert len(INVALID) >= 5, f"only {len(INVALID)} invalid examples"


def test_every_entry_names_a_file_inside_the_examples_directory():
    for example in EXAMPLES:
        path = resolve_example_path(example)
        assert path.is_file()
        assert EXAMPLES_ROOT.resolve() in path.resolve().parents or path.parent == EXAMPLES_ROOT.resolve()


def test_every_program_on_disk_is_listed_in_the_manifest():
    """A file nobody serves is a program nothing tests, and it looks intentional."""
    listed = {example.file for example in EXAMPLES}
    on_disk = {
        path.relative_to(EXAMPLES_ROOT).as_posix()
        for path in sorted(EXAMPLES_ROOT.rglob("*.c"))
    }
    assert on_disk == listed, f"unlisted: {sorted(on_disk - listed)}, missing: {sorted(listed - on_disk)}"


def test_manifest_names_are_unique_and_usable_as_url_slugs():
    assert len(set(EXAMPLE_NAMES)) == len(EXAMPLE_NAMES)
    for name in EXAMPLE_NAMES:
        assert name == name.lower()
        assert " " not in name and "/" not in name and "." not in name


def test_valid_and_invalid_entries_are_in_the_right_directory():
    """`valid/` must compile and `invalid/` must not, or the split is a lie."""
    for example in VALID:
        assert example.file.startswith("valid/"), example.file
    for example in INVALID:
        assert example.file.startswith("invalid/"), example.file


# ------------------------------------------------------- compiling examples


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_NAMES)
def test_every_example_reaches_the_stage_it_is_supposed_to(example: Example):
    """Valid programs lower; invalid ones stop with an error before lowering.

    Asserting `success` alone would let a program that fails for the *wrong* reason count as
    invalid, which is how a corpus quietly stops testing what its names claim.
    """
    result = compile_source(source_of(example), mode="analyze")
    errors = [d for d in result.diagnostics if d.severity.value == "error"]
    if example.valid:
        assert result.success, [f"{d.code_str} {d.message}" for d in errors]
        assert result.ir is not None
    else:
        assert errors, f"{example.name} is listed as invalid but compiled cleanly"
        assert result.ir is None, f"{example.name} is listed as invalid but still lowered"


@pytest.mark.parametrize("example", EXAMPLES, ids=EXAMPLE_NAMES)
def test_every_example_produces_exactly_its_declared_diagnostics(example: Example):
    """The manifest's `expected_diagnostics` is a claim, and this is where it is checked.

    Not "at least these codes": a program declared to produce one error and producing three
    means the corpus has drifted from the document that describes it, and a test that only
    checks membership would not notice.
    """
    result = compile_source(source_of(example), mode="analyze")
    actual = sorted({d.code_str for d in result.diagnostics})
    assert actual == sorted(set(example.expected_diagnostics)), (
        f"{example.name}: manifest says {sorted(set(example.expected_diagnostics))}, "
        f"compiler produced {actual}"
    )


@pytest.mark.parametrize("example", VALID, ids=[e.name for e in VALID])
def test_valid_examples_lower_and_satisfy_invariants(example: Example):
    program, _ = parse(source_of(example))
    assert program is not None
    lowered = lower(analyze(program))
    assert lowered.ok, lowered.errors
    check_program(lowered.program)


@pytest.mark.parametrize("example", VALID, ids=[e.name for e in VALID])
def test_valid_examples_compile_through_the_api(client: TestClient, example: Example):
    response = client.post(
        "/api/compile",
        json={"source": source_of(example), "mode": "optimize", "stdin": example.stdin or None},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True, [d["message"] for d in body["diagnostics"]]
    assert body["ir"] is not None
    assert body["optimization"] is not None


@pytest.mark.parametrize("example", VALID, ids=[e.name for e in VALID])
def test_lowering_is_deterministic_across_runs(example: Example):
    """The same source must always produce byte-identical TAC."""
    source = source_of(example)

    def dump() -> str:
        program, _ = parse(source)
        lowered = lower(analyze(program))
        assert lowered.ok
        return print_program(lowered.program)

    assert dump() == dump()


# -------------------------------------------------- differential verification


@pytest.mark.parametrize("example", VALID, ids=[e.name for e in VALID])
def test_optimizing_preserves_observable_behaviour(example: Example):
    """Run the original and the optimized program and compare what a user could see.

    Compared on every parameterless entry point, over the example's own stdin plus the
    oracle's standard inputs. This is the corpus's real purpose: a pass that deletes
    something it should not shows up here as a behavioural difference rather than as a
    smaller instruction count.
    """
    program, _ = parse(source_of(example))
    lowered = lower(analyze(program))
    assert lowered.ok, lowered.errors
    result = optimize(lowered.program)
    if not result.changed:
        pytest.skip(f"{example.name} has nothing to optimize")

    for entry in program.functions:
        if entry.params:
            continue
        for stdin in {example.stdin, "", "0", "3 4 5 6 7"}:
            before = interpret(lowered.program, entry=entry.name, stdin=stdin)
            after = interpret(result.optimized, entry=entry.name, stdin=stdin)
            assert before.same_observable_behaviour_as(after), (
                f"{example.name}: {entry.name} behaves differently on stdin {stdin!r} "
                f"({before.status.value} vs {after.status.value})"
            )


@pytest.mark.parametrize("example", VALID, ids=[e.name for e in VALID])
def test_optimized_ir_still_satisfies_the_structural_invariants(example: Example):
    program, _ = parse(source_of(example))
    lowered = lower(analyze(program))
    assert lowered.ok
    result = optimize(lowered.program)
    check_program(result.optimized)


@pytest.mark.parametrize("example", VALID, ids=[e.name for e in VALID])
def test_the_optimizer_never_grows_a_program(example: Example):
    """Instruction count is the measure every pass is strictly reducing under."""
    program, _ = parse(source_of(example))
    lowered = lower(analyze(program))
    assert lowered.ok
    result = optimize(lowered.program)
    assert result.stats_after.instructions <= result.stats_before.instructions
    assert result.removed_instructions == (
        result.stats_before.instructions - result.stats_after.instructions
    )


# ------------------------------------------------------------- language cover


def test_examples_cover_the_language():
    """Guard against the corpus drifting down to one trivial program."""
    combined = "\n".join(source_of(example) for example in VALID)
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
    names = {example.file for example in VALID}
    for required in (
        "valid/06_io.c",
        "valid/10_unreachable.c",
        "valid/11_dead_assignments.c",
        "valid/12_constant_folding.c",
        "valid/13_constant_propagation.c",
    ):
        assert required in names, f"missing example {required}"


def test_every_tag_is_used_by_at_least_one_example():
    """An unused tag is a category that no example belongs to."""
    tags = {tag for example in EXAMPLES for tag in example.tags}
    for tag in ("error", "optimization", "loops", "types", "analysis"):
        assert tag in tags, f"no example carries the '{tag}' tag"


def test_every_example_declares_at_least_one_feature():
    for example in EXAMPLES:
        assert example.features, f"{example.name} declares no features"
        assert example.summary, f"{example.name} has no summary"


def test_valid_examples_declare_a_suggested_mode():
    for example in EXAMPLES:
        assert example.suggested_mode in ("analyze", "optimize")
