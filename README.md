# OptiCode

A visual mini compiler and optimization laboratory. OptiCode compiles **MiniC**, a small
C-like language, through a real compiler pipeline and visualizes every stage from real
backend output.

```
Source → Lexer → Parser → AST → Semantic → TAC → Basic Blocks → CFG
       → Data Flow → Dead/Unreachable Analysis → Optimization (fixpoint + verification)
       → Optimized Source
```

Full documentation lands progressively in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
and [`docs/LANGUAGE.md`](docs/LANGUAGE.md).

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

`/api/compile` returns `tokens`, `ast`, `semantic`, `ir`, `cfg`, `dataflow`, and
`analysis`. The `optimization` payload appears when the request asks for `mode: "optimize"`,
which runs five passes to a fixpoint and then verifies the result by running both programs
in the interpreter. `codegen` comes back with it, holding the regenerated program as source
text, an annotated variant that shows removed lines as comments, and a change map saying what
was rewritten and which pass justified each one. Every rewrite is verified by re-compiling the
generated text and running the interpreter oracle against the original, so a rewrite that
changes behaviour is discarded rather than returned. See `docs/ARCHITECTURE.md` for scope and
`docs/LANGUAGE.md` for the language rules.

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
cd backend
.\.venv\Scripts\Activate.ps1
pytest
```

```powershell
cd frontend
npm test
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

Sample programs live in [`backend/examples/`](backend/examples/). Each one compiles
through the full pipeline; run the check with:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
python examples\compile_examples.py
```

The TAC golden dumps that pin lowering output live in
[`backend/tests/golden/`](backend/tests/golden/). Regenerate them on purpose after an
intentional lowering change with `python -m tests.regenerate_golden` — the diff is the
review record.
