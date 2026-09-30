"""Compile and optimize every example in-process, then print its TAC.

The examples are the compiler's own regression corpus: each one exercises a feature, and
compiling them here catches a lowering regression long before a user sees it. Each is then
optimized and the result is checked structurally and by execution, so a pass that changes
behaviour fails here instead of reaching a user.

The leading status column says what the optimizer did:

* ``ok``   - smaller, and verified to behave identically
* ``warn`` - smaller, but the oracle could not run it (no parameterless entry point)
* ``skip`` - smaller, and verification was skipped for the same reason
* ``same`` - nothing to optimize

Usage::

    python backend/scripts/compile_examples.py [--dump]
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from opticode.analysis import analyze_findings, analyze_functions  # noqa: E402
from opticode.analysis.findings import diagnostics_for  # noqa: E402
from opticode.cfg import build_program_cfgs  # noqa: E402
from opticode.ir import check_program, lower, print_program  # noqa: E402
from opticode.optimizer import OptimizationError, optimize  # noqa: E402
from opticode.parser import parse  # noqa: E402
from opticode.semantic import analyze  # noqa: E402

root = REPO_ROOT / "examples" / "valid"
examples = sorted(root.glob("*.c"))
print(f"{len(examples)} examples\n")

failures = 0
for path in examples:
    source = path.read_text(encoding="utf-8")
    program, parse_diagnostics = parse(source)
    errors = [d for d in parse_diagnostics if d.severity.value == "error"]
    if program is None or errors:
        print(f"FAIL {path.name}: parse")
        for d in errors:
            print(f"       {d.code_str} {d.message}")
        failures += 1
        continue

    result = analyze(program)
    sem_errors = [d for d in result.diagnostics if d.severity.value == "error"]
    if sem_errors:
        print(f"FAIL {path.name}: semantic")
        for d in sem_errors:
            print(f"       {d.code_str} {d.message}")
        failures += 1
        continue

    lowered = lower(result)
    if not lowered.ok:
        print(f"FAIL {path.name}: lower {lowered.errors}")
        failures += 1
        continue

    try:
        check_program(lowered.program)
    except Exception as error:  # noqa: BLE001
        print(f"FAIL {path.name}: invariants {error}")
        failures += 1
        continue

    graphs = build_program_cfgs(lowered.program)
    report = analyze_findings(
        lowered.program,
        cfgs=graphs,
        analyses=analyze_functions(lowered.program, cfgs=graphs),
        ast=program,
        source=source,
    )

    # Optimize, then check the result both structurally and by execution. The oracle is
    # what makes this example corpus worth running: a pass that breaks behaviour shows up
    # here as a mismatch against the original, rather than as a wrong answer for a user.
    try:
        optimized = optimize(lowered.program)
    except OptimizationError as error:
        print(f"FAIL {path.name}: optimize {error}")
        failures += 1
        continue
    if optimized.changed:
        try:
            check_program(optimized.optimized)
        except Exception as error:  # noqa: BLE001
            print(f"FAIL {path.name}: optimized invariants {error}")
            failures += 1
            continue
        verification = optimized.verification
        if verification is not None and not verification.verified:
            print(f"FAIL {path.name}: behaviour changed {verification.first_difference}")
            failures += 1
            continue

    warnings = [d for d in result.diagnostics if d.severity.value == "warning"]
    warnings += [d for d in diagnostics_for(report) if d.severity.value == "warning"]
    counts = sum(len(f.instructions) for f in lowered.program.functions)
    removable = sum(1 for f in report.findings if f.action.value == "will_be_removed")
    after = optimized.stats_after.instructions
    verification = optimized.verification
    if optimized.changed and verification is not None and verification.skipped:
        verdict = "skip"
    elif optimized.changed and verification is not None and verification.verified:
        verdict = "ok"
    elif optimized.changed:
        verdict = "warn"
    else:
        verdict = "same"
    print(
        f"{verdict} {path.name:28} funcs={len(lowered.program.functions)} "
        f"instructions={counts}->{after} findings={len(report.findings)} "
        f"removable={removable}"
    )
    for d in warnings:
        line = d.span.start_line if d.span else 0
        print(f"       {d.code_str} L{line} {d.message}")

    if "--dump" in sys.argv:
        print(print_program(lowered.program))
        if optimized.changed:
            print(f"--- optimized {path.name} ---")
            print(print_program(optimized.optimized))

print(f"\n{len(examples) - failures}/{len(examples)} examples compiled and optimized")
if failures:
    raise SystemExit(1)
