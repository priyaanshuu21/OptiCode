"""Compile every example in-process and print its TAC.

The examples are the compiler's own regression corpus: each one exercises a feature, and
compiling them here catches a lowering regression long before a user sees it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opticode.analysis import analyze_findings, analyze_functions  # noqa: E402
from opticode.analysis.findings import diagnostics_for  # noqa: E402
from opticode.cfg import build_program_cfgs  # noqa: E402
from opticode.ir import check_program, lower, print_program  # noqa: E402
from opticode.parser import parse  # noqa: E402
from opticode.semantic import analyze  # noqa: E402

root = Path(__file__).resolve().parent
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

    warnings = [d for d in result.diagnostics if d.severity.value == "warning"]
    warnings += [d for d in diagnostics_for(report) if d.severity.value == "warning"]
    counts = sum(len(f.instructions) for f in lowered.program.functions)
    removable = sum(1 for f in report.findings if f.action.value == "will_be_removed")
    status = "ok " if not warnings else "warn"
    print(f"{status} {path.name:28} funcs={len(lowered.program.functions)} "
          f"instructions={counts} findings={len(report.findings)} removable={removable}")
    for d in warnings:
        line = d.span.start_line if d.span else 0
        print(f"       {d.code_str} L{line} {d.message}")

    if "--dump" in sys.argv:
        print(print_program(lowered.program))

print(f"\n{len(examples) - failures}/{len(examples)} examples compiled")
if failures:
    raise SystemExit(1)
