"""Regression tests for cascading-001's wrong RCA root cause (2026-08-31).

describe_k8s_resource and get_k8s_resource are the two GKE Remote MCP tools whose
`name` field is REQUIRED (toolspec.json) -- unlike list_k8s_events (issue #70), there
is no unscoped/broadened retry available for them. Before this fix, a NotFound
response for either tool fell straight through call_tool's generic `content is not
None -> ok: True` branch, so a wrong/guessed resource name (e.g. "order-api" instead
of the real generated pod/ReplicaSet name, which always carries a random hash
suffix) became "evidence" that the resource doesn't exist at all.

This is the confirmed live symptom from docs/management/confidence-genericity-
review-2026-08-28.md: cascading-001 answered "The order-api Deployment and its
ReplicaSet are missing from the cluster" when order-api was running the entire time.
Same failure SHAPE as issue #70's list_k8s_events bug (see
tests/test_mcp_client_broaden_retry.py and docs/testing/
e2e-honest-baseline-2026-08-09-notification-relay.md), just on the two tools that
bug's fix never covered.
"""
import agent.mcp_client as mcp_client_mod
from agent.mcp_client import call_tool


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


def test_describe_k8s_resource_not_found_is_a_failed_call_not_evidence(monkeypatch):
    _setup(monkeypatch, [
        {"output": 'Error from server (NotFound): Deployment "order-api" not found'},
    ])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"resourceType": "deployment", "name": "order-api", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is False, (
        "a name-scoped NotFound must never be treated as confirmed evidence of "
        "absence -- describe_k8s_resource has no broadened retry, so the name may "
        "simply be a wrong guess"
    )
    assert "NAME_SCOPED_NOT_FOUND" in result["error"]
    assert "order-api" in result["error"]
    # Exactly one HTTP call -- no retry attempted (none is possible; `name` is required).
    assert len(_FakeHttpxClient.calls) == 1


def test_get_k8s_resource_not_found_is_a_failed_call_not_evidence(monkeypatch):
    _setup(monkeypatch, [
        {"output": 'Error from server (NotFound): ReplicaSet "order-api-7f9c8d" not found'},
    ])
    result = call_tool(
        "gke_remote_mcp", "get_k8s_resource",
        {"resourceType": "replicaset", "name": "order-api-7f9c8d", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is False
    assert "NAME_SCOPED_NOT_FOUND" in result["error"]
    assert len(_FakeHttpxClient.calls) == 1


def test_describe_k8s_resource_real_content_still_ok_true(monkeypatch):
    """Not a blanket regression -- a real, found resource must still return ok=True."""
    _setup(monkeypatch, [{"output": "Deployment order-api: 2/2 replicas ready"}])
    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"resourceType": "deployment", "name": "order-api", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is True
    assert result["result"] == {"output": "Deployment order-api: 2/2 replicas ready"}


def test_list_k8s_events_not_found_behavior_unchanged_by_this_fix(monkeypatch):
    """issue #70's list_k8s_events broadened-retry path must be untouched -- this fix
    only adds a NEW branch for describe_k8s_resource/get_k8s_resource, gated on
    tool_name, so it must never intercept list_k8s_events."""
    _setup(monkeypatch, [
        {"output": 'Error from server (NotFound): Pod "notification-relay" not found'},
        {"output": "3 events in namespace test-incidents"},
    ])
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events",
        {"name": "notification-relay", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert result["ok"] is True
    assert result["broadened_after_not_found"] == "notification-relay"
