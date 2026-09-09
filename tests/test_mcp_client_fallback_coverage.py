"""Section 3 correction (2026-09-08): replaces the old fallback-coverage tests.

Root cause: this repo used to auto-fall-back from GKE Remote MCP to the custom
k8s_mcp (and vice versa) on failure. Two real problems made that dangerous:
1. mcp/server.py's resolve_cluster() now rejects any cluster_id whose registry
   type isn't 'custom' (Section 5 redesign) -- so a GKE-Remote-failure fallback to
   k8s_mcp could never succeed for a real GKE cluster; it was dead code advertising
   behavior that no longer existed.
2. A custom cluster with no configured MCP URL fell back to gke_remote_mcp -- which
   would send an on-prem cluster's name to Google's GKE API as if it were a GKE
   cluster, a genuine wrong-MCP risk (latent whenever ENABLE_CUSTOM_MCP is off).

Both fallback paths (and _try_custom_mcp_fallback, _map_to_custom_tool,
_CUSTOM_TOOLS_ACCEPTING_POD_NAME, _CUSTOM_TOOLS_WITHOUT_NAMESPACE, which existed
only to support them) are removed. These tests prove the replacement behavior:
every real failure mode of a resolved cluster's OWN MCP source returns an honest
failure naming the real cause, and never silently retries against the other MCP
type or a different cluster.
"""
import agent.mcp_client as mcp_client_mod
from agent.mcp_client import call_tool


class _FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _RaisingHttpxClient:
    """Always raises a network exception -- there must be no second (fallback) call."""
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, endpoint, json, headers):
        _RaisingHttpxClient.calls.append((endpoint, json["params"]["name"]))
        raise ConnectionError("connection reset by peer")


def _registry_gke_cluster():
    return {
        "sre-test-cluster": {
            "project": "p", "region": "us-central1", "namespace": "test-incidents",
            "cluster_type": "gke",
        },
    }


def test_gke_network_exception_is_honest_failure_no_fallback_attempted(monkeypatch):
    _RaisingHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _RaisingHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", _registry_gke_cluster)
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events", {"name": "my-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_RaisingHttpxClient.calls) == 1  # exactly one attempt, no fallback retry
    assert result["ok"] is False
    assert result["mcp_source"] == "gke_remote_mcp"
    assert "connection reset" in result["error"]


class _EmptyResponseHttpxClient:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, endpoint, json, headers):
        _EmptyResponseHttpxClient.calls.append(endpoint)
        return _FakeResponse(200, 'data: {"jsonrpc": "2.0", "result": {}}\n')


def test_gke_empty_response_is_honest_failure_no_fallback_attempted(monkeypatch):
    _EmptyResponseHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _EmptyResponseHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", _registry_gke_cluster)
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events", {"name": "my-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_EmptyResponseHttpxClient.calls) == 1
    assert result["ok"] is False
    assert result["mcp_source"] == "gke_remote_mcp"
    assert result["error"] == "Empty response"


class _NonOkHttpxClient:
    calls: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, endpoint, json, headers):
        _NonOkHttpxClient.calls.append(endpoint)
        return _FakeResponse(503, "Service Unavailable")


def test_gke_non_200_is_honest_failure_no_fallback_attempted(monkeypatch):
    _NonOkHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _NonOkHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", _registry_gke_cluster)
    result = call_tool(
        "gke_remote_mcp", "list_k8s_events", {"name": "my-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )
    assert len(_NonOkHttpxClient.calls) == 1
    assert result["ok"] is False
    assert result["mcp_source"] == "gke_remote_mcp"
    assert "503" in result["error"]


def test_custom_cluster_with_no_url_fails_honestly_never_tries_gke_remote():
    """The other direction: a resolved custom/on-prem cluster with no configured MCP
    URL must not silently route to gke_remote_mcp."""
    result = call_tool(
        "k8s_mcp", "list_pods", {"namespace": "test-incidents"},
        cluster_name="sre-lab-with-no-url",
    )
    assert result["ok"] is False
    assert result["mcp_source"] == "k8s_mcp"
    assert "No URL configured" in result["error"]
    assert "refusing to route to a different MCP type" in result["error"]
