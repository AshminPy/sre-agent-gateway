"""
Integration test — real calls through GKE Fleet Connect Gateway to the
sre-lab kind cluster (Task 4's on-prem-connectivity proof), exercising the
server's tool functions end-to-end (validation -> k8s API call -> redact ->
trim -> audit log), not mocks.

Skipped automatically when the Connect Gateway kubeconfig context isn't
available (e.g. CI without gcloud creds) — this is a real-cluster smoke
test, not something that should block every commit; run explicitly with:

    cd mcp && K8S_MCP_KUBE_CONTEXT=connectgateway_sreagent-t2-demo_global_sre-lab \
        pytest tests/test_live_connect_gateway.py -v -s
"""
import asyncio
import json
import os
import subprocess

import pytest

CONTEXT = os.environ.get(
    "K8S_MCP_KUBE_CONTEXT", "connectgateway_sreagent-t2-demo_global_sre-lab"
)


def _context_available() -> bool:
    try:
        out = subprocess.run(
            ["kubectl", "config", "get-contexts", "-o", "name"],
            capture_output=True, text=True, timeout=10,
        )
        return CONTEXT in out.stdout.splitlines()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _context_available(),
    reason=f"kubeconfig context '{CONTEXT}' not available in this environment",
)


@pytest.fixture(autouse=True)
def _set_context(monkeypatch):
    monkeypatch.setenv("K8S_MCP_KUBE_CONTEXT", CONTEXT)
    monkeypatch.delenv("GKE_CLUSTER_ENDPOINT", raising=False)
    monkeypatch.delenv("GKE_CA_CERT_GCS_PATH", raising=False)


def _call(server_mod, name, args):
    async def _run():
        res = await server_mod.mcp._call_tool_mcp(name, args)
        blocks = res[0] if isinstance(res, tuple) else res
        blocks = getattr(blocks, "content", blocks)
        text = blocks[0].text if blocks else None
        try:
            return json.loads(text)
        except Exception:
            return text
    return asyncio.run(_run())


def test_live_list_pods_kube_system():
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "list_pods", {"namespace": "kube-system"})
    assert "pods" in result
    assert result["count"] > 0, "kube-system must have real pods on a live cluster"


def test_live_describe_deployment_coredns():
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "describe_deployment",
                    {"namespace": "kube-system", "deployment_name": "coredns"})
    assert result["name"] == "coredns"
    assert result["replicas"]["desired"] >= 1


def test_live_daemonset_kube_proxy():
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "describe_daemonset", {"namespace": "kube-system", "name": "kube-proxy"})
    assert result["name"] == "kube-proxy"
    assert "status" in result


def test_live_service_kubernetes():
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "describe_service", {"namespace": "default", "service_name": "kubernetes"})
    assert result["type"] == "ClusterIP"


def test_live_configmap_with_dotted_name_regression():
    """The exact case that caught the DNS-1123-subdomain-vs-label validation bug."""
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "get_configmap", {"namespace": "kube-system", "name": "kube-root-ca.crt"})
    assert "data" in result
    assert "ca.crt" in result["data"]


def test_live_list_nodes_returns_structured_forbidden_not_a_crash():
    """`view` ClusterRole excludes cluster-scoped Nodes (documented in
    docs/connect-gateway-onprem.md) — must come back as a clean {"error": ...}
    dict, never an unhandled exception."""
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "list_nodes", {})
    assert result.get("ok") is False
    assert "error" in result
    assert "orbidden" in result["error"] or "403" in result["error"]


def test_live_invalid_namespace_rejected_before_reaching_cluster():
    import server
    server.get_k8s_clients.cache_clear()
    result = _call(server, "list_pods", {"namespace": "../../etc"})
    assert result.get("ok") is False
    assert "not a valid Kubernetes namespace" in result["error"]
