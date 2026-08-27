"""Regression tests: a failed result must never be usable as evidence.

All four gaps here are the SAME bug class as the Model Armor incident
(run_20260826_211251_rlwe, fixed in PR #199): something that is NOT data gets
treated as data, becomes evidence, and the LLM reasons over it.

Found 2026-08-27 by reviewing the data path -- how tool output becomes evidence,
and how evidence becomes the report.

  Gap 1  MCP `isError: true` was never read anywhere in agent/. Per the spec,
         tool EXECUTION errors come back with isError=true and the error text
         sitting in `content`, exactly where real data sits. call_tool returned
         ok=True and that error string became evidence. Only the narrow
         _is_not_found_result() substring check caught any of them.
         https://modelcontextprotocol.io/specification/2025-06-18/server/tools

  Gap 2  rca_builder's `no_evidence` gate counted evidence SLOTS, not usable
         evidence. Every tool call could fail and the gate still would not fire.

  Gap 3  llm_json() returned a bare {} when the model's response was unparseable
         -- indistinguishable from a legitimately empty result, so the failure
         was completely invisible.

  Gap 4  _ground_claim awarded "grounded" at FULL strength 1.0 to a claim citing
         evidence with no content, contradicting its own comment.

  (A fifth, lower-severity issue is covered too: JSON-RPC protocol errors were
   discarded and replaced with a generic "Empty response", throwing away the
   server's own diagnostic message.)
"""
import agent.mcp_client as mcp_client_mod
from agent.confidence.claim_builder import _ground_claim
from agent.confidence.models import Claim, ClaimType
from agent.llm import llm_json_failed
from agent.llm.base import LLM_JSON_PARSE_FAILED_KEY
from agent.mcp_client import _parse_response, call_tool


# ── Gap 1 + protocol errors: _parse_response reads the whole envelope ─────────

def _sse(payload: str) -> str:
    return f"data: {payload}\n"


def test_tool_execution_error_is_flagged():
    """The spec's own example of a tool execution error."""
    body = _sse(
        '{"jsonrpc":"2.0","id":4,"result":{"content":[{"type":"text",'
        '"text":"Failed to fetch weather data: API rate limit exceeded"}],'
        '"isError":true}}'
    )
    content, protocol_error, is_tool_error = _parse_response(body)
    assert is_tool_error is True
    assert protocol_error is None
    assert "rate limit" in str(content)


def test_successful_tool_result_is_not_flagged():
    """Guard against the isError check firing on healthy results."""
    body = _sse(
        '{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text",'
        '"text":"Name: imagepull-pod"}],"isError":false}}'
    )
    content, protocol_error, is_tool_error = _parse_response(body)
    assert is_tool_error is False
    assert protocol_error is None
    assert "imagepull-pod" in str(content)


def test_result_with_no_iserror_key_is_treated_as_success():
    """isError is optional; absence must not be read as failure."""
    body = _sse(
        '{"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text",'
        '"text":"Name: imagepull-pod"}]}}'
    )
    _, _, is_tool_error = _parse_response(body)
    assert is_tool_error is False


def test_protocol_error_message_is_preserved_not_discarded():
    """The spec's own example of a protocol error."""
    body = _sse(
        '{"jsonrpc":"2.0","id":3,"error":{"code":-32602,'
        '"message":"Unknown tool: invalid_tool_name"}}'
    )
    content, protocol_error, is_tool_error = _parse_response(body)
    assert content is None
    assert "Unknown tool: invalid_tool_name" in protocol_error
    assert "-32602" in protocol_error
    assert is_tool_error is False


def test_real_content_wins_over_an_earlier_error_frame():
    """A stream can carry an error frame and then real data; data wins."""
    body = (
        _sse('{"jsonrpc":"2.0","error":{"code":-32000,"message":"transient"}}')
        + _sse('{"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"real data"}]}}')
    )
    content, protocol_error, _ = _parse_response(body)
    assert "real data" in str(content)
    assert protocol_error is None


# ── Gap 1 end-to-end: call_tool must return ok=False ─────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeHttpxClient:
    _queue: list = []
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, endpoint, json, headers):
        _FakeHttpxClient.calls.append(json["params"]["arguments"])
        return _FakeHttpxClient._queue.pop(0)


def _setup(monkeypatch, bodies: list):
    _FakeHttpxClient._queue = [_FakeResponse(200, b) for b in bodies]
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {"sre-test-cluster": {"project": "p", "region": "us-central1",
                                      "namespace": "test-incidents"}},
    )


def test_call_tool_returns_failed_for_a_tool_execution_error(monkeypatch):
    """The regression: this used to be ok=True with the error text as `result`."""
    _setup(monkeypatch, [_sse(
        '{"jsonrpc":"2.0","result":{"content":[{"type":"text",'
        '"text":"Error from server (Forbidden): pods is forbidden"}],"isError":true}}'
    )])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "imagepull-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is False
    assert "MCP_TOOL_ERROR" in result["error"]
    assert "forbidden" in result["error"].lower()
    # The error text must NOT be handed onward as if it were tool output.
    assert "result" not in result


def test_call_tool_surfaces_the_protocol_error_message(monkeypatch):
    """Used to be swallowed and reported as the generic 'Empty response'."""
    _setup(monkeypatch, [_sse(
        '{"jsonrpc":"2.0","error":{"code":-32602,"message":"Invalid arguments"}}'
    )])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "imagepull-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is False
    assert "Invalid arguments" in result["error"]
    assert result["error"] != "Empty response"


def test_healthy_call_still_succeeds(monkeypatch):
    """Guard against the new checks breaking the normal path."""
    _setup(monkeypatch, [_sse(
        '{"jsonrpc":"2.0","result":{"content":[{"type":"text",'
        '"text":"Name: imagepull-pod  Image: nginx:1.14.2"}]}}'
    )])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "imagepull-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is True
    assert "imagepull-pod" in str(result["result"])


# ── Gap 4: claim grounding must not credit contentless evidence ──────────────

def _claim(text, cites):
    return Claim(claim_id="c1", text=text, claim_type=ClaimType.OBSERVED_FACT,
                 supporting_evidence_ids=cites)


def test_claim_citing_only_failed_evidence_gets_zero_credit():
    store = {"ev_001": {"ok": False, "key_facts": [],
                        "summary": "Tool failed: MODEL_ARMOR_BLOCKED"}}
    c = _claim("The pod image is nginx:1.14.2-nonexistent", ["ev_001"])
    _ground_claim(c, {"ev_001"}, store)
    assert c.grounding_status == "failed_evidence_only"
    assert c.support_strength == 0.0


def test_claim_citing_contentless_evidence_gets_zero_credit():
    """The verified regression: this returned grounded / 1.0."""
    store = {"ev_001": {"ok": True, "key_facts": [], "summary": ""}}
    c = _claim("The pod is in ImagePullBackOff", ["ev_001"])
    _ground_claim(c, {"ev_001"}, store)
    assert c.grounding_status == "empty_evidence"
    assert c.support_strength == 0.0


def test_empty_claim_text_gets_zero_credit():
    store = {"ev_001": {"ok": True, "key_facts": ["imagepull-pod ImagePullBackOff"],
                        "summary": "pod events"}}
    c = _claim("", ["ev_001"])
    _ground_claim(c, {"ev_001"}, store)
    assert c.support_strength == 0.0


def test_real_evidence_still_grounds_a_real_claim():
    """Guard against the new zero-credit paths swallowing healthy claims."""
    store = {"ev_001": {"ok": True,
                        "key_facts": ["imagepull-pod ImagePullBackOff manifest not found"],
                        "summary": "pod events"}}
    c = _claim("The pod imagepull-pod is in ImagePullBackOff", ["ev_001"])
    _ground_claim(c, {"ev_001"}, store)
    assert c.grounding_status == "grounded"
    assert c.support_strength == 1.0


def test_a_mix_of_failed_and_real_evidence_still_grounds():
    """A failed citation alongside a real one must not sink the claim."""
    store = {
        "ev_001": {"ok": False, "key_facts": [], "summary": "Tool failed: blocked"},
        "ev_002": {"ok": True,
                   "key_facts": ["imagepull-pod ImagePullBackOff manifest not found"],
                   "summary": "pod events"},
    }
    c = _claim("The pod imagepull-pod is in ImagePullBackOff", ["ev_001", "ev_002"])
    _ground_claim(c, {"ev_001", "ev_002"}, store)
    assert c.grounding_status == "grounded"
    assert c.support_strength == 1.0


# ── Gap 3: an unparseable model response must be detectable ──────────────────

def test_llm_json_failure_is_detectable():
    failed = {LLM_JSON_PARSE_FAILED_KEY: "no JSON object in model response"}
    assert llm_json_failed(failed) == "no JSON object in model response"


def test_a_real_llm_result_is_not_reported_as_failed():
    assert llm_json_failed({"enough_evidence": True}) == ""


def test_a_legitimately_empty_llm_result_is_not_reported_as_failed():
    """{} means 'the model returned an empty object', which is NOT a parse failure."""
    assert llm_json_failed({}) == ""


def test_existing_callers_are_unaffected_by_the_marker():
    """All six call sites read specific keys with defaults; the marker must not
    change what they see."""
    failed = {LLM_JSON_PARSE_FAILED_KEY: "malformed JSON from model"}
    assert failed.get("enough_evidence", False) is False
    assert failed.get("claims") is None
    assert failed.get("working_theory", "") == ""
