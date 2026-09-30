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


def test_empty_source_is_a_clean_syntax_error(client: TestClient):
    """Blank input still lexes and still reports partial results, but a program with no
    function declarations is a syntax error rather than a silent success.

    No AST is returned here: there is genuinely nothing to build a tree from.
    """
    body = compile_body(client, "   \n")
    assert body["success"] is False
    errors = [d for d in body["diagnostics"] if d["severity"] == "error"]
    assert [d["code"] for d in errors] == ["E-SYN-006"]
    assert errors[0]["stage"] == "parser"
    assert [t["type"] for t in body["tokens"]] == ["eof"]
    assert body["ast"] is None
    assert body["stages_run"] == ["tokens", "ast"]


def compile_body(client: TestClient, source: str) -> dict:
    return client.post("/api/compile", json={"source": source}).json()


def child(node: dict, role: str, index: int = 0) -> dict:
    """The `index`-th child of `node` filed under `role`."""
    matches = [c["node"] for c in node["children"] if c["role"] == role]
    return matches[index]


def test_compile_returns_ast(client: TestClient):
    body = compile_body(client, VALID)
    assert body["success"] is True
    ast = body["ast"]
    assert ast["kind"] == "Program"
    assert ast["id"] == "n1"

    function = child(ast, "function")
    assert function["kind"] == "FunctionDecl"
    assert function["attrs"] == {"name": "main", "return_type": "int"}

    block = child(function, "body")
    assert block["kind"] == "Block"

    declaration = child(block, "statement", 0)
    assert declaration["kind"] == "VarDecl"
    assert declaration["attrs"] == {"name": "x", "type_name": "int"}
    assert child(declaration, "init")["kind"] == "BinaryExpr"

    returned = child(block, "statement", 1)
    assert returned["kind"] == "Return"
    assert returned["span"]["start_line"] == 3

    ids = all_ids(ast)
    assert len(ids) == len(set(ids)), "AST ids must be unique"
    assert "" not in ids, "every node must carry an id"


def all_ids(node: dict) -> list[str]:
    found = [node["id"]]
    for entry in node["children"]:
        found.extend(all_ids(entry["node"]))
    return found


def test_ast_spans_point_at_the_real_source(client: TestClient):
    lines = VALID.splitlines()
    for token in compile_body(client, VALID)["tokens"]:
        if token["type"] == "eof":
            continue
        line = lines[token["span"]["start_line"] - 1]
        start = token["span"]["start_col"] - 1
        assert line[start : start + len(token["lexeme"])] == token["lexeme"]


def test_ast_omitted_when_parser_is_skipped(client: TestClient):
    """A lexer error stops the pipeline, so no AST is produced."""
    body = compile_body(client, "int main() {\n    int x = 1 $ 2;\n}\n")
    assert body["success"] is False
    assert body["ast"] is None
    assert body["tokens"] is not None
    assert body["stages_run"] == ["tokens"]


def test_parser_errors_still_return_the_partial_ast(client: TestClient):
    """A syntax error is recoverable: the tree built so far is still returned."""
    body = compile_body(client, "int main() {\n    int x = ;\n    return 0;\n}\n")
    assert body["success"] is False
    assert body["tokens"] is not None
    assert body["ast"] is not None
    errors = [d for d in body["diagnostics"] if d["severity"] == "error"]
    assert errors and all(d["stage"] == "parser" for d in errors)
    assert all(d["span"]["start_line"] >= 1 for d in errors)


def test_health_lists_registered_stages(client: TestClient):
    assert client.get("/api/health").json()["stages"] == [
        "tokens",
        "ast",
        "semantic",
        "ir",
        "cfg",
    ]


# ------------------------------------------------------- semantic and ir stages


def _compile(client: TestClient, source: str) -> dict:
    response = client.post("/api/compile", json={"source": source})
    assert response.status_code == 200, response.text
    return response.json()


def test_semantic_payload_is_returned(client: TestClient):
    body = _compile(client, "int f(int a, float b) { int c = 0; return a; }")
    assert body["success"] is True
    semantic = body["semantic"]
    assert semantic is not None
    assert semantic["summary"]["function_count"] == 1
    assert {symbol["name"] for symbol in semantic["symbols"]} == {"a", "b", "c"}


def test_semantic_payload_lists_ir_names(client: TestClient):
    body = _compile(client, "int f() { int i; return i; } int g() { int i; return i; }")
    names = [symbol["ir_name"] for symbol in body["semantic"]["symbols"]]
    assert names == ["i", "i_2"]


def test_ir_payload_is_returned(client: TestClient):
    body = _compile(client, "int f(int a) { return a + 1; }")
    ir = body["ir"]
    assert ir is not None
    assert ir["function_count"] == 1
    assert ir["functions"][0]["instructions"]


def test_ir_payload_includes_the_printed_tac(client: TestClient):
    """The UI shows TAC verbatim, so the payload carries the printer's own text."""
    body = _compile(client, "int f(int a) { return a + 1; }")
    text = body["ir"]["text"]
    assert "# f(int a) -> int" in text
    assert "t1 = a + 1" in text
    assert "return 0  [synthetic]" in text


def test_ir_instructions_carry_their_queries(client: TestClient):
    body = _compile(client, "int f(int a) { return a / 2; }")
    instruction = body["ir"]["functions"][0]["instructions"][0]
    assert set(instruction) >= {"uses", "defs", "has_side_effects", "may_trap", "ast_node_id"}


# --------------------------------------------------------------- cfg stage


def test_cfg_payload_has_one_graph_per_function(client: TestClient):
    body = _compile(
        client,
        "int f(int n) { int s = 0; while (n > 0) { s = s + n; n = n - 1; } return s; }"
        " int g() { return 1; }",
    )
    cfg = body["cfg"]
    assert set(cfg) == {"f", "g"}
    assert cfg["f"]["return_type"] == "int"
    assert cfg["f"]["block_count"] > 1
    assert cfg["f"]["has_loops"] is True
    # Two blocks even for a one-line function: the synthetic trailing `return 0` follows
    # the real `return`, and an instruction after a return always starts a new block.
    assert cfg["g"]["block_count"] == 2
    assert cfg["g"]["has_loops"] is False
    assert cfg["g"]["blocks"][1]["leader_reason"] == "follows return"
    assert cfg["g"]["blocks"][1]["reachable"] is False


def test_cfg_blocks_carry_their_edges_and_reasons(client: TestClient):
    body = _compile(client, "int f(int n) { if (n) { return 1; } return 0; }")
    graph = body["cfg"]["f"]
    reasons = {block["leader_reason"] for block in graph["blocks"]}
    assert "function entry" in reasons
    assert any(reason.startswith("target of if_false") for reason in reasons)
    assert graph["edges"]
    for edge in graph["edges"]:
        assert set(edge) == {"src", "dst", "kind", "is_back_edge"}
    assert graph["rpo"][0] == graph["entry"]
    assert graph["back_edges"] == []


def test_cfg_marks_back_edges_for_loops(client: TestClient):
    body = _compile(
        client,
        "int f(int n) { int s = 0; while (n > 0) { s = s + 1; n = n - 1; } return s; }",
    )
    graph = body["cfg"]["f"]
    assert graph["back_edges"]
    assert all(edge["is_back_edge"] for edge in graph["back_edges"])
    # The retreating edge is what makes this a loop, and it lands on a real block.
    for edge in graph["back_edges"]:
        assert edge["dst"] in {block["id"] for block in graph["blocks"]}
        assert edge["src"] != edge["dst"]


def test_cfg_reports_unreachable_blocks(client: TestClient):
    body = _compile(client, "int f() { return 1; int dead = 2; return dead; }")
    graph = body["cfg"]["f"]
    # B1 returns; B2 assigns the dead local and B3 returns it. Dead code is *kept* and
    # marked unreachable rather than deleted, because Task 5 has to be able to explain it.
    dead = [block["id"] for block in graph["blocks"] if not block["reachable"]]
    assert dead == ["B2", "B3"]
    assert graph["blocks"][0]["reachable"] is True


def test_cfg_is_absent_when_the_ir_stage_failed(client: TestClient):
    body = _compile(client, "int f() { return undeclared; }")
    assert body["ir"] is None
    assert body["cfg"] is None
    assert "cfg" in body["stages_skipped"]


def test_cfg_stage_is_deterministic(client: TestClient):
    body = _compile(client, "int f(int n) { while (n) { n = n - 1; } return n; }")
    assert _compile(client, "int f(int n) { while (n) { n = n - 1; } return n; }")[
        "cfg"
    ] == body["cfg"]


def test_semantic_errors_stop_the_ir_stage_but_keep_the_ast(client: TestClient):
    """Partial results are the point of the pipeline: the AST survives, IR does not."""
    body = _compile(client, "int f() { return undeclared; }")
    assert body["success"] is False
    assert body["ast"] is not None
    assert body["semantic"] is not None
    assert body["ir"] is None
    assert "ir" in body["stages_skipped"]
    assert [d["code"] for d in body["diagnostics"]] == ["E-SEM-001"]


def test_semantic_warnings_do_not_stop_the_pipeline(client: TestClient):
    body = _compile(client, "int f() { int unused = 1; return 0; }")
    assert body["success"] is True
    assert body["ir"] is not None
    assert [d["code"] for d in body["diagnostics"]] == ["W-SEM-002"]


def test_lexical_errors_stop_before_semantic(client: TestClient):
    body = _compile(client, "int f() { return 1 @ 2; }")
    assert body["semantic"] is None
    assert body["ir"] is None


def test_timings_include_the_new_stages(client: TestClient):
    body = _compile(client, "int f() { return 0; }")
    assert set(body["timings"]) == {"tokens", "ast", "semantic", "ir", "cfg"}


def test_compile_output_is_deterministic(client: TestClient):
    """Two identical requests must agree, apart from wall-clock timings."""
    payload = {
        "source": "int f(int n) { int s = 0;"
        " for (int i = 0; i < n; i = i + 1) { s = s + i; } return s; }"
    }
    first = _compile(client, payload["source"])
    second = _compile(client, payload["source"])
    first.pop("timings")
    second.pop("timings")
    assert first == second


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
