"""Regression tests for issue #72:
1. The custom-MCP fallback used to only fire on a non-200 HTTP status -- network
   exceptions and empty/unparseable responses returned their error directly with no
   fallback attempt, contradicting the module's own documented fallback behavior.
2. get_k8s_cluster_info and list_k8s_api_resources both mapped to list_pods --
   semantically invalid (list_pods lists pods in a namespace; neither GKE tool has
   anything to do with that), and list_pods doesn't even accept the pod_name arg the
   fallback path used to force-send.
3. fallback_args force-included pod_name for every fallback target, even ones (like
   list_pods) that don't accept it at all.
"""
import agent.mcp_client as mcp_client_mod
from agent.mcp_client import (
    CUSTOM_K8S_TOOLS,
    _CUSTOM_TOOLS_ACCEPTING_POD_NAME,
    _map_to_custom_tool,
    _try_custom_mcp_fallback,
    call_tool,
)


# ── _map_to_custom_tool: the two invalid mappings must be gone ─────────────────────

def test_cluster_info_has_no_fallback_mapping_not_list_pods():
    assert _map_to_custom_tool("get_k8s_cluster_info") is None


def test_list_api_resources_has_no_fallback_mapping_not_list_pods():
    assert _map_to_custom_tool("list_k8s_api_resources") is None


def test_remaining_mappings_all_target_tools_that_accept_pod_name():
    # Sanity check tying the two fixes together: every GKE tool that DOES still map
    # to a custom tool maps to one that genuinely accepts pod_name (confirmed against
    # mcp/server.py's real @guarded(name_fields=("pod_name",)) signatures).
    for gke_tool in ("list_k8s_events", "describe_k8s_resource", "get_k8s_resource", "get_k8s_logs"):
        target = _map_to_custom_tool(gke_tool)
        assert target in CUSTOM_K8S_TOOLS
        assert target in _CUSTOM_TOOLS_ACCEPTING_POD_NAME


# ── _try_custom_mcp_fallback: pod_name only included when the target accepts it ────

def test_fallback_excludes_pod_name_when_target_tool_does_not_accept_it(monkeypatch):
    monkeypatch.setattr(mcp_client_mod, "_map_to_custom_tool", lambda t: "list_pods")
    captured = {}
    monkeypatch.setattr(
        mcp_client_mod, "call_tool",
        lambda fb, tool, args, run_id, cluster: captured.update(args) or {"ok": True},
    )
    _try_custom_mcp_fallback(
        is_gke_remote=True, cluster_info={}, tool_name="get_k8s_cluster_info",
        namespace="test-incidents", pod_name="my-pod", run_id="r", cluster_name="c",
    )
    assert "pod_name" not in captured
    assert captured["namespace"] == "test-incidents"


def test_fallback_includes_pod_name_when_target_tool_accepts_it(monkeypatch):
    monkeypatch.setattr(mcp_client_mod, "_map_to_custom_tool", lambda t: "describe_pod_detail")
    captured = {}
    monkeypatch.setattr(
        mcp_client_mod, "call_tool",
        lambda fb, tool, args, run_id, cluster: captured.update(args) or {"ok": True},
    )
    _try_custom_mcp_fallback(
        is_gke_remote=True, cluster_info={}, tool_name="describe_k8s_resource",
        namespace="test-incidents", pod_name="my-pod", run_id="r", cluster_name="c",
    )
    assert captured["pod_name"] == "my-pod"


def test_fallback_returns_none_when_no_mapped_tool_exists():
    result = _try_custom_mcp_fallback(
        is_gke_remote=True, cluster_info={}, tool_name="get_k8s_cluster_info",
        namespace="test-incidents", pod_name="", run_id="r", cluster_name="c",
    )
    assert result is None


def test_fallback_returns_none_for_non_gke_remote_calls():
    result = _try_custom_mcp_fallback(
        is_gke_remote=False, cluster_info={}, tool_name="list_k8s_events",
        namespace="test-incidents", pod_name="x", run_id="r", cluster_name="c",
    )
    assert result is None


# ── End-to-end: fallback now fires on empty response and network exceptions too ───

class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _RaisingHttpxClient:
    """First call raises a network exception; second call (the fallback) succeeds."""
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, endpoint, json, headers):
        _RaisingHttpxClient.calls.append((endpoint, json["params"]["name"]))
        if len(_RaisingHttpxClient.calls) == 1:
            raise ConnectionError("connection reset by peer")
        return _FakeResponse(200, 'data: {"jsonrpc": "2.0", "result": {"structuredContent": {"output": "3 events"}}}\n')


def test_network_exception_now_triggers_fallback_not_a_bare_error(monkeypatch):
    _RaisingHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _RaisingHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_client_mod, "_get_identity_token", lambda url: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {
            "sre-test-cluster": {
                "project": "p", "region": "us-central1", "namespace": "test-incidents",
                "mcp_url": "https://custom-mcp.example", "mcp_fallback": "k8s_mcp",
            },
        },
    )
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events", {"name": "my-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_RaisingHttpxClient.calls) == 2  # original (raised) + fallback (succeeded)
    assert result["ok"] is True
    assert result["mcp_source"] == "k8s_mcp"


class _EmptyThenRealHttpxClient:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, endpoint, json, headers):
        _EmptyThenRealHttpxClient.calls.append(endpoint)
        if len(_EmptyThenRealHttpxClient.calls) == 1:
            return _FakeResponse(200, 'data: {"jsonrpc": "2.0", "result": {}}\n')  # empty
        return _FakeResponse(200, 'data: {"jsonrpc": "2.0", "result": {"structuredContent": {"output": "3 events"}}}\n')


def test_empty_response_now_triggers_fallback_not_a_bare_error(monkeypatch):
    _EmptyThenRealHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _EmptyThenRealHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_client_mod, "_get_identity_token", lambda url: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {
            "sre-test-cluster": {
                "project": "p", "region": "us-central1", "namespace": "test-incidents",
                "mcp_url": "https://custom-mcp.example", "mcp_fallback": "k8s_mcp",
            },
        },
    )
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events", {"name": "my-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_EmptyThenRealHttpxClient.calls) == 2
    assert result["ok"] is True
    assert result["mcp_source"] == "k8s_mcp"
