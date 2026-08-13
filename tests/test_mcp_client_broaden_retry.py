"""Regression tests for issue #70: a name-scoped list_k8s_events call that returns
NotFound must retry once, unscoped, before the caller accepts non-existence as fact.

Real E2E proof (docs/testing/e2e-honest-baseline-2026-08-09-notification-relay.md): a
Deployment-name hint that didn't match the real generated pod name caused both
Pod and ReplicaSet lookups to 404, and the agent wrongly concluded the workload
didn't exist -- when it was healthy but failing its readiness probe.

GKE Remote MCP returns HTTP 200 with the kubectl-style error TEXT embedded in the
result body for a not-found resource (not a structured error, not a non-200 status):
{"output": "Error from server (NotFound): Pod \"notification-relay\" not found"}.
"""
import agent.mcp_client as mcp_client_mod
from agent.mcp_client import _is_not_found_result, call_tool


def test_is_not_found_result_detects_the_real_kubectl_style_error_text():
    content = {"output": 'Error from server (NotFound): Pod "notification-relay" not found'}
    assert _is_not_found_result(content) is True


def test_is_not_found_result_false_for_real_content():
    content = {"output": "pod-abc123 Running"}
    assert _is_not_found_result(content) is False


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeHttpxClient:
    """Returns a queued sequence of responses, one per call -- proves call_tool made
    exactly the expected number of real HTTP calls, in order."""
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


def _sse_body(content: dict) -> str:
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


def test_not_found_name_scoped_call_retries_unscoped_and_returns_broadened_result(monkeypatch):
    _setup(monkeypatch, [
        {"output": 'Error from server (NotFound): Pod "notification-relay" not found'},
        {"output": "3 events in namespace test-incidents"},
    ])
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events",
        {"name": "notification-relay", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_FakeHttpxClient.calls) == 2
    assert _FakeHttpxClient.calls[0].get("name") == "notification-relay"
    assert "name" not in _FakeHttpxClient.calls[1]  # broadened: name filter dropped
    assert result["ok"] is True
    assert result["broadened_after_not_found"] == "notification-relay"
    assert result["result"] == {"output": "3 events in namespace test-incidents"}


def test_not_found_result_never_retries_a_second_time(monkeypatch):
    # Both the original AND the broadened retry return NotFound -- must stop after one
    # retry, not loop.
    _setup(monkeypatch, [
        {"output": 'Error from server (NotFound): Pod "x" not found'},
        {"output": 'Error from server (NotFound): still nothing'},
    ])
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events",
        {"name": "x", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_FakeHttpxClient.calls) == 2  # not 3+
    assert result["broadened_after_not_found"] == "x"


def test_real_content_on_first_try_does_not_trigger_a_retry(monkeypatch):
    _setup(monkeypatch, [{"output": "2 events"}])
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events",
        {"name": "real-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_FakeHttpxClient.calls) == 1
    assert "broadened_after_not_found" not in result


def test_not_found_on_an_unscoped_call_does_not_retry_no_name_to_broaden(monkeypatch):
    _setup(monkeypatch, [{"output": 'Error from server (NotFound): namespace empty'}])
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events",
        {"namespace": "test-incidents"},  # no name -- already unscoped
        cluster_name="sre-test-cluster",
    )
    assert len(_FakeHttpxClient.calls) == 1
    assert "broadened_after_not_found" not in result
