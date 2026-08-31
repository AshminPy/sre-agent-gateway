"""Workstream 2: custom/fallback MCP (k8s_mcp) traffic must get the same Model
Armor inspection as GKE Remote MCP -- docs/management/CURRENT-STATE.md flagged
"custom/fallback MCP traffic is not Model Armor-inspected" as a real gap.

Root cause (confirmed against iac/agent/model_armor.tf): the project-level
Model Armor floor setting only supports integrated_services =
["GOOGLE_MCP_SERVER", "AI_PLATFORM"] -- there is no "custom Cloud Run MCP"
integration in the Model Armor API's own schema, so the custom-MCP path can
never be covered by that infra-level control. This is closed at the app layer
in agent/mcp_client.py's call_tool(), reusing the same SREAgent._sanitize()
helper agent/main.py already uses.

Key acceptance criterion (must not regress): the sanitize wrap runs ONLY on
the custom-MCP (non-gke_remote) branch. GKE Remote MCP already has native
floor-setting inspection (see test_mcp_client_model_armor_block.py) -- running
this too would double-inspect the same response.
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
        _FakeHttpxClient.calls.append(endpoint)
        return _FakeHttpxClient._queue.pop(0)


def _sse_body(content) -> str:
    import json as json_mod
    return f'data: {{"jsonrpc": "2.0", "result": {{"structuredContent": {json_mod.dumps(content)}}}}}\n'


def _setup_custom_mcp(monkeypatch, responses: list):
    """Route a non-gke_remote call_tool() through the custom (k8s_mcp) branch."""
    _FakeHttpxClient._queue = [_FakeResponse(200, _sse_body(r)) for r in responses]
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_identity_token", lambda url: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {
            "sre-test-cluster": {
                "project": "p", "region": "us-central1", "namespace": "test-incidents",
                "mcp_url": "https://custom-mcp.example",
            },
        },
    )


def _setup_gke_remote(monkeypatch, responses: list):
    _FakeHttpxClient._queue = [_FakeResponse(200, _sse_body(r)) for r in responses]
    _FakeHttpxClient.calls = []
    monkeypatch.setattr(mcp_client_mod.httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(mcp_client_mod, "_get_access_token", lambda: "fake-token")
    monkeypatch.setattr(
        mcp_client_mod, "_get_cluster_registry",
        lambda: {"sre-test-cluster": {"project": "p", "region": "us-central1", "namespace": "test-incidents"}},
    )


def _fake_sanitize_factory(calls: list, blocked: bool):
    """Builds a fake matching SREAgent._sanitize's real classmethod signature/contract:
    returns (text, was_blocked) and never rewrites text -- see agent/main.py."""
    def fake_sanitize(cls, text, is_output=False):
        calls.append({"text": text, "is_output": is_output})
        return text, blocked
    return classmethod(fake_sanitize)


# ── (a) custom-MCP path invokes the sanitize wrap ──────────────────────────

def test_custom_mcp_clean_response_invokes_sanitize_and_passes_through(monkeypatch):
    from agent.main import SREAgent
    calls: list = []
    monkeypatch.setattr(SREAgent, "_sanitize", _fake_sanitize_factory(calls, blocked=False))
    _setup_custom_mcp(monkeypatch, [{"output": "Name: my-pod  Status: Running"}])

    result = call_tool(
        "k8s_mcp", "list_pods", {"namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )

    assert len(calls) == 1
    assert calls[0]["is_output"] is True
    assert result["ok"] is True
    assert result["result"] == {"output": "Name: my-pod  Status: Running"}


def test_custom_mcp_flagged_response_is_blocked_not_returned_as_evidence(monkeypatch):
    from agent.main import SREAgent
    calls: list = []
    monkeypatch.setattr(SREAgent, "_sanitize", _fake_sanitize_factory(calls, blocked=True))
    _setup_custom_mcp(monkeypatch, [{"output": "some response body"}])

    result = call_tool(
        "k8s_mcp", "list_pods", {"namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )

    assert len(calls) == 1
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["blocked_by"] == "model_armor"
    assert "MODEL_ARMOR_BLOCKED" in result["error"]
    # The flagged body must never be handed onward as if it were tool output.
    assert "result" not in result


# ── (b) gke_remote path does NOT invoke it (no double-inspection) ─────────

def test_gke_remote_path_never_invokes_the_custom_mcp_sanitize_wrap(monkeypatch):
    from agent.main import SREAgent
    calls: list = []
    # Even if Model Armor were configured to always flag, the gke_remote branch
    # must never call this wrap -- it is already covered by the native floor
    # setting inspection on that endpoint.
    monkeypatch.setattr(SREAgent, "_sanitize", _fake_sanitize_factory(calls, blocked=True))
    _setup_gke_remote(monkeypatch, [{"output": "Name: my-pod  Status: Running"}])

    result = call_tool(
        "gke_remote_mcp", "describe_k8s_resource",
        {"name": "my-pod", "namespace": "test-incidents"},
        cluster_name="sre-test-cluster",
    )

    assert len(calls) == 0
    assert result["ok"] is True
    assert result["result"] == {"output": "Name: my-pod  Status: Running"}
