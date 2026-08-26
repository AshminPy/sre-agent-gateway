"""Regression tests: a Model Armor block must be a FAILED tool call, never ok=True.

Real incident, run_20260826_211251_rlwe (2026-08-26). Model Armor's floor setting
was running inspect_and_block=true. Its pi_and_jailbreak filter matched
(MEDIUM_AND_ABOVE) on a describe_k8s_resource RESPONSE -- ordinary pod-spec text,
a false positive. Model Armor discarded the real body and returned its own notice
IN-BAND, over a normal HTTP 200:

    "Model Armor: Response violates content security configurations.
     However, the operation was successful."

call_tool saw a 200 with a non-empty body and returned ok=True. The notice was then
stored as ev_002. Downstream, tool_success scored 1.0 and investigation_completeness
scored 1.00 -- the agent believed it had the pod spec. It did not. With no real
evidence but a prompt saying the pod "cannot pull its image", the LLM fabricated an
image name (`nginx:1.14.2-nonexistent`), an error string (`manifest not found`), and
a root cause (`ImagePullBackOff`) -- none of which appear anywhere in the stored
evidence -- and cited ev_003 (cluster info, no image in it) as the source.

Same failure SHAPE as issue #70's NotFound case: HTTP 200, non-empty body, but the
body is not the data that was asked for.

The fix is at the transport boundary on purpose. Once call_tool returns ok=False,
every downstream layer already does the right thing without further change:
  * tool_executor records ok=False + blocked=True in tool_history, appends to
    state["errors"], and emits a sre-agent-tool-failures Cloud Logging entry
  * evidence_extractor writes an error record with empty key_facts instead of
    storing the notice as usable evidence
  * scorer counts it in failed_calls, so tool_success and completeness drop honestly
"""
import agent.mcp_client as mcp_client_mod
from agent.mcp_client import _is_model_armor_blocked_result, call_tool

BLOCK_NOTICE = (
    "Model Armor: Response violates content security configurations. "
    "However, the operation was successful."
)


def test_detects_the_exact_notice_observed_in_production():
    assert _is_model_armor_blocked_result(BLOCK_NOTICE) is True


def test_detects_the_notice_when_nested_in_a_structured_body():
    assert _is_model_armor_blocked_result({"output": BLOCK_NOTICE}) is True


def test_real_pod_spec_content_is_not_flagged():
    content = {"output": "Name: imagepull-pod\nImage: gcr.io/google-containers/nonexistent-image:v99.9.9"}
    assert _is_model_armor_blocked_result(content) is False


def test_both_markers_required_so_a_workload_named_model_armor_is_not_flagged():
    # A cluster legitimately running a workload called "model-armor" must not have its
    # output discarded. One marker alone is not enough.
    content = {"output": "Name: model-armor-proxy   Status: Running   Restarts: 0"}
    assert _is_model_armor_blocked_result(content) is False


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


def _sse_body(content) -> str:
    import json as json_mod
    return f'data: {{"jsonrpc": "2.0", "result": {{"structuredContent": {json_mod.dumps(content)}}}}}\n'


def _setup(monkeypatch, responses: list):
    _FakeHttpxClient._queue = [_FakeResponse(200, _sse_body(r)) for r in responses]
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {"sre-test-cluster": {"project": "p", "region": "us-central1", "namespace": "test-incidents"}},
    )


def test_blocked_response_returns_failed_call_not_success(monkeypatch):
    """The exact production regression: HTTP 200 + block notice must NOT be ok=True."""
    _setup(monkeypatch, [{"output": BLOCK_NOTICE}])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "imagepull-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["blocked_by"] == "model_armor"
    assert "MODEL_ARMOR_BLOCKED" in result["error"]
    # The notice must never be handed onward as if it were tool output.
    assert "result" not in result


def test_blocked_response_does_not_burn_a_fallback_retry(monkeypatch):
    """The floor setting is project-wide, so the custom-MCP fallback would be sanitized
    identically. Retrying only adds latency -- exactly one HTTP call must be made."""
    _setup(monkeypatch, [{"output": BLOCK_NOTICE}])
    call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "imagepull-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_FakeHttpxClient.calls) == 1


def test_unblocked_response_still_succeeds_normally(monkeypatch):
    """Guard against the check being too broad and failing healthy calls."""
    _setup(monkeypatch, [{"output": "Name: imagepull-pod   Image: nginx:1.14.2"}])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "imagepull-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is True
    assert result["result"] == {"output": "Name: imagepull-pod   Image: nginx:1.14.2"}
