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

The project is being built task by task. See `docs/ARCHITECTURE.md` for current scope.

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

Sample programs live in [`examples/`](examples/).
