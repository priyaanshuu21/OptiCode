# OptiCode

A visual mini compiler and optimization laboratory. OptiCode compiles **MiniC**, a small
C-like language, through a real compiler pipeline and visualizes every stage from real
backend output.

```
Source → Lexer → Parser → AST → Semantic → TAC → Basic Blocks → CFG
       → Data Flow → Dead/Unreachable Analysis → Optimization (fixpoint + verification)
       → Optimized Source
```

Full documentation lives in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
(pipeline, invariants, API contracts, frontend) and [`docs/LANGUAGE.md`](docs/LANGUAGE.md)
(the MiniC language rules).

## Features

- **Full pipeline in one response.** `POST /api/compile` lexes, parses, checks,
  lowers to TAC, builds CFGs, runs three data-flow analyses, reports findings with
  explanations, and — in `optimize` mode — rewrites to a fixpoint, verifies the
  result against the original in the interpreter, and regenerates readable source.
- **Control-flow graphs.** React Flow + dagre layout, color-coded edges
  (`true` / `false` / `jump` / `fallthrough` / `return`), dashed back-edges,
  unreachable blocks badged, click-to-inspect with source cross-highlighting.
- **Data-flow explorer.** Liveness, reaching definitions, and constants with
  per-block IN/OUT facts and a stepper that replays the solver trace.
- **Findings with evidence.** Dead assignments, unreachable code, constant
  conditions, and reads-before-init, each with the evidence behind the claim;
  the editor dims unreachable code and marks dead writes with hover text.
- **Optimization report.** Before/after sizes, per-pass stats, every rewrite as
  an event, everything skipped with its reason, the interpreter-oracle verdict,
  and a side-by-side TAC diff.
- **Examples library.** 15 valid programs plus 14 negative cases in
  [`examples/`](examples/), described by `examples/manifest.json` and served by
  `GET /api/examples` and `GET /api/examples/{name}` (slug-checked and
  contained, so names cannot escape the directory). The UI loads them from a
  dropdown.

## Status

| Stage | State |
|---|---|
| Lexer | done |
| Parser and AST | done |
| Semantic analysis | done |
| TAC lowering | done |
| Basic blocks, CFG, interpreter | done |
| Data-flow analysis | done |
| Dead-code analysis | done |
| Optimizer | done |
| Codegen | done |
| Frontend shell and stage views | done |
| CFG, data-flow, findings, optimization views | done |
| Examples library (`examples/`, manifest, API, UI menu) | done |

`/api/compile` returns `tokens`, `ast`, `semantic`, `ir`, `cfg`, `dataflow`, and
`analysis`. The `optimization` payload appears when the request asks for `mode: "optimize"`,
which runs five passes to a fixpoint and then verifies the result by running both programs
in the interpreter. `codegen` comes back with it, holding the regenerated program as source
text, an annotated variant that shows removed lines as comments, and a change map saying what
was rewritten and which pass justified each one. Every rewrite is verified by re-compiling the
generated text and running the interpreter oracle against the original, so a rewrite that
changes behaviour is discarded rather than returned. `GET /api/examples` lists the curated
programs and `GET /api/examples/{name}` returns one with its source. See
`docs/ARCHITECTURE.md` for scope and `docs/LANGUAGE.md` for the language rules.

## Before / after

`examples/valid/12_constant_folding.c` contains a function the compiler can
evaluate without running anything:

```c
int folded_add() {
    return 2 + 3 * 4;
}
```

In `optimize` mode the report shows the fold as an event
(`2 + 3 * 4` → `14`, justified by constant folding), the regenerated source
returns the literal, and the interpreter oracle confirms both programs print
the same output — which is why the verification badge reads as matched runs
rather than trust. Expressions the compiler must not touch, like
`10 + x * 2` in `mixed_with_variable`, are listed under skipped with the
reason (`x` is only known at run time).

## Requirements

- Python 3.11+
- Node.js 20+ (developed against 22)

## Run the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn opticode.api.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/docs> for the interactive API documentation.

## Run the frontend

The dev server proxies `/api` to the backend, so run both:

```powershell
cd frontend
npm install
npm run dev
```

Then open <http://localhost:5173>. The proxy target defaults to
`http://127.0.0.1:8000` and can be overridden with `OPTICODE_API`; the deployed base URL comes
from `VITE_API_BASE_URL` and is empty by default, meaning same-origin.

The source editor compiles as you type, debounced, and each stage tab shows the corresponding
payload: the token stream, the AST, TAC with its data-flow facts, the CFG laid out with dagre,
and the diagnostics. Selecting anything in a stage highlights the source range it came from.

## Run the tests

```powershell
python -m pytest backend/tests -q
```

```powershell
cd frontend
npm test -- --run
npm run build
```

## Regenerate the frontend's view of the API

The frontend's types and its test fixtures are generated from the backend, not written by hand:

```powershell
cd frontend
npm run gen:openapi    # writes openapi.json from the live FastAPI app
npm run gen:types      # writes src/api/schema.d.ts from openapi.json
npm run gen:fixtures   # writes src/test/fixtures/*.json from the real compiler
```

Commit the regenerated files. A fixture is a recorded response from the real pipeline, which is
what keeps a view from being tested against a shape somebody remembered.

## Examples

Sample programs live in [`examples/`](examples/): 15 valid programs under
`examples/valid/` and 14 negative cases under `examples/invalid/`, all
described by [`examples/manifest.json`](examples/manifest.json). Each valid
example compiles through the full pipeline and each invalid one produces
exactly its declared diagnostics; run the check with:

```powershell
python backend/scripts/compile_examples.py [--dump]
```

The same files back `GET /api/examples` and `GET /api/examples/{name}`, so
the programs the compiler tests and the programs the UI menu offers are the
same files on disk.

The TAC golden dumps that pin lowering output live in
[`backend/tests/golden/`](backend/tests/golden/). Regenerate them on purpose after an
intentional lowering change with `python -m tests.regenerate_golden` — the diff is the
review record.
