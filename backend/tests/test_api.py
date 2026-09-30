"""API tests for the Task 1 surface: health, compile, limits, error handling."""

from __future__ import annotations

from fastapi.testclient import TestClient

from opticode import __version__
from opticode.api.main import create_app
from opticode.compiler import MAX_SOURCE_SIZE

VALID = "int main() {\n    int x = 1 + 2;\n    return x;\n}\n"


def test_health(client: TestClient):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert "tokens" in body["stages"]


def test_compile_returns_tokens(client: TestClient):
    response = client.post("/api/compile", json={"source": VALID, "mode": "analyze"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["diagnostics"] == []
    tokens = body["tokens"]
    assert tokens[0]["lexeme"] == "int"
    assert tokens[0]["span"]["start_line"] == 1
    assert tokens[-1]["type"] == "eof"
    assert "tokens" in body["stages_run"]
    assert body["timings"]["tokens"] >= 0


def test_tokens_match_source_spans(client: TestClient):
    source = "int main() {\n    return 0;\n}\n"
    body = client.post("/api/compile", json={"source": source}).json()
    lines = source.splitlines()
    for token in body["tokens"]:
        if token["type"] == "eof":
            continue
        line_index = token["span"]["start_line"] - 1
        line = lines[line_index]
        start = token["span"]["start_col"] - 1
        assert line[start : start + len(token["lexeme"])] == token["lexeme"]


def test_invalid_source_returns_diagnostics_with_spans(client: TestClient):
    source = "int main() {\n    int x = 1 $ 2;\n}\n"
    body = client.post("/api/compile", json={"source": source}).json()
    assert body["success"] is False
    assert body["tokens"] is not None, "partial results must still be returned"
    error = next(d for d in body["diagnostics"] if d["severity"] == "error")
    assert error["code"] == "E-LEX-005"
    assert error["span"]["start_line"] == 2
    assert error["stage"] == "lexer"


def test_oversize_source_is_rejected(client: TestClient):
    huge = "int a;\n" * (MAX_SOURCE_SIZE // 7 + 100)
    assert len(huge) > MAX_SOURCE_SIZE
    body = client.post("/api/compile", json={"source": huge}).json()
    assert body["success"] is False
    assert body["tokens"] is None
    assert body["diagnostics"][0]["code"] == "E-INP-001"
    assert body["stages_run"] == []


def test_empty_source_is_an_info_not_a_crash(client: TestClient):
    body = client.post("/api/compile", json={"source": "   \n"}).json()
    assert body["success"] is True
    assert [d["code"] for d in body["diagnostics"]] == ["E-INP-002"]
    assert [t["type"] for t in body["tokens"]] == ["eof"]


def test_internal_errors_become_structured_diagnostics(monkeypatch):
    """A crashing stage must produce a diagnostic with no stack trace."""
    import opticode.compiler as compiler_module

    def boom(state):
        raise RuntimeError("kaboom: secret internal detail")

    pipeline = compiler_module.build_pipeline()
    pipeline._stages[0] = ("tokens", pipeline._stages[0][1], boom)
    monkeypatch.setattr(compiler_module, "build_pipeline", lambda: pipeline)

    result = compiler_module.compile_source(VALID)
    assert result.success is False
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code_str == "E-INT-001"
    assert "Traceback" not in diagnostic.message
    assert "kaboom" in diagnostic.message
    assert result.tokens is None


def test_api_turns_internal_error_into_diagnostic(monkeypatch):
    import opticode.api.main as app_module

    def boom(*_args, **_kwargs):
        raise ValueError("explode")

    monkeypatch.setattr(app_module, "compile_source", boom)
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        body = test_client.post("/api/compile", json={"source": VALID}).json()
    assert body["success"] is False
    assert body["diagnostics"][0]["code"] == "E-INT-001"
    assert "Traceback" not in body["diagnostics"][0]["message"]


def test_cors_allows_local_frontend(client: TestClient):
    response = client.options(
        "/api/compile",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_openapi_schema_is_valid(client: TestClient):
    schema = client.get("/openapi.json").json()
    assert "/api/compile" in schema["paths"]
    assert "/api/health" in schema["paths"]


def test_invalid_request_body_is_rejected(client: TestClient):
    response = client.post("/api/compile", json={"mode": "analyze"})
    assert response.status_code == 422
