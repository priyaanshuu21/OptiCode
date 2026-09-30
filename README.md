# OptiCode

A visual mini compiler and optimization laboratory. OptiCode compiles **MiniC**, a small
C-like language, through a real compiler pipeline and visualizes every stage from real
backend output.

```
Source → Lexer → Parser → AST → Semantic → TAC → Basic Blocks → CFG
       → Data Flow → Dead/Unreachable Analysis → Optimization → Optimized Source
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
| Basic blocks, CFG, interpreter | not started |
| Analysis, optimizer, codegen, frontend | not started |

`/api/compile` currently returns `tokens`, `ast`, `semantic`, and `ir`. Later stages run
automatically as they land. See `docs/ARCHITECTURE.md` for scope and
`docs/LANGUAGE.md` for the language rules.

## Requirements

- Python 3.11+
- (later) Node.js 20+

## Run the backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
uvicorn opticode.api.main:app --reload --port 8000
```

Then open <http://127.0.0.1:8000/docs> for the interactive API documentation.

## Run the tests

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest
```

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
