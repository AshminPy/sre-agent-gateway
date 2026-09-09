"""Section 2 correction (2026-09-08): the deployed multi-cluster custom MCP must fail
CLOSED for cluster selection when its registry (CLUSTER_CONFIG_BUCKET) is configured
but unreadable -- never fall through to K8S_MCP_KUBE_CONTEXT/a placeholder client,
which would silently connect the requested cluster_id to whatever cluster that env
var happens to name (sre-lab, in the deployed environment). See
mcp/server.py::resolve_cluster()'s docstring for the exact mechanism.

These tests exercise the REAL server.resolve_cluster()/server.get_k8s_clients(), not
a re-implementation -- the registry itself is faked via CLUSTER_CONFIG_BUCKET +
google.cloud.storage.Client (same pattern test_dynamic_connect_gateway.py already
uses for _load_cluster_registry()), not via monkeypatching the cache dict directly,
so a real GCS-read failure is the thing actually exercised.
"""
import json as _json
import time
from unittest.mock import MagicMock, patch

import pytest

import server


REGISTRY_JSON = _json.dumps({
    "clusters": [
        {"name": "sre-lab", "type": "custom", "enabled": True,
         "kube_context": "connectgateway_proj_global_sre-lab",
         "allowed_namespaces": ["test-incidents"]},
        {"name": "sre-lab-2", "type": "custom", "enabled": True,
         "kube_context": "connectgateway_proj_global_sre-lab-2",
         "allowed_namespaces": ["test-incidents"]},
    ]
})


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Every test starts from a clean slate -- no cached registry, no cached client,
    no load-failed flag carried over from a previous test."""
    monkeypatch.setattr(server, "_CLUSTER_REGISTRY_CACHE", {})
    monkeypatch.setattr(server, "_CLUSTER_REGISTRY_LOADED_AT", 0.0)
    monkeypatch.setattr(server, "_CLUSTER_REGISTRY_LOAD_FAILED", False)
    server.get_k8s_clients.cache_clear()
    yield
    server.get_k8s_clients.cache_clear()


def _fake_gcs_client(payload_json: str = None, raise_exc: Exception = None):
    fake_blob = MagicMock()
    if raise_exc:
        fake_blob.download_as_text.side_effect = raise_exc
    else:
        fake_blob.download_as_text.return_value = payload_json
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    return fake_client


def test_registry_success_resolves_correct_cluster(monkeypatch):
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(REGISTRY_JSON)):
        entry = server.resolve_cluster("sre-lab-2")
    assert entry["kube_context"] == "connectgateway_proj_global_sre-lab-2"


def test_registry_unavailable_is_a_safe_failure_naming_the_cluster_id(monkeypatch):
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(raise_exc=RuntimeError("GCS unreachable"))):
        with pytest.raises(server.ClusterNotFoundError, match="sre-lab-2"):
            server.resolve_cluster("sre-lab-2")


def test_registry_unavailable_zero_fallback_to_another_cluster(monkeypatch):
    """The actual blocker scenario: request sre-lab-2, registry read fails -- must
    NEVER resolve to sre-lab (or any other cluster) via K8S_MCP_KUBE_CONTEXT."""
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    monkeypatch.setenv("K8S_MCP_KUBE_CONTEXT", "connectgateway_proj_global_sre-lab")
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(raise_exc=RuntimeError("GCS unreachable"))):
        with pytest.raises(server.ClusterNotFoundError):
            server.resolve_cluster("sre-lab-2")
        # get_k8s_clients must also refuse -- never silently build a client against
        # K8S_MCP_KUBE_CONTEXT's cluster (sre-lab) for a request that named sre-lab-2.
        with pytest.raises(server.ClusterNotFoundError):
            server.get_k8s_clients("sre-lab-2")
    # Confirm nothing was cached for the failed cluster_id.
    assert "sre-lab-2" not in server._K8S_CLIENT_CACHE


def test_registry_recovers_without_redeployment(monkeypatch):
    """Same process, same cache objects -- registry read fails, then succeeds on a
    later call (simulating the outage clearing). No restart, no code reload."""
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(raise_exc=RuntimeError("GCS down"))):
        with pytest.raises(server.ClusterNotFoundError):
            server.resolve_cluster("sre-lab-2")
    # Registry cache is still empty ({}), so the very next call retries the load --
    # confirmed by resolve_cluster.py's own `if not registry` retry-every-call
    # behavior when the cache is falsy.
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(REGISTRY_JSON)):
        entry = server.resolve_cluster("sre-lab-2")
    assert entry["kube_context"] == "connectgateway_proj_global_sre-lab-2"


def test_no_poisoned_cache_after_registry_failure(monkeypatch):
    """A registry failure while resolving sre-lab-2 must not corrupt or evict an
    already-cached, healthy sre-lab client."""
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    with patch("kubernetes.config.load_kube_config"), \
         patch("kubernetes.client.ApiClient", return_value=MagicMock()), \
         patch("kubernetes.client.CoreV1Api", return_value=MagicMock(name="sre-lab-core")), \
         patch("kubernetes.client.AppsV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.BatchV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", return_value=MagicMock()), \
         patch("google.cloud.storage.Client", return_value=_fake_gcs_client(REGISTRY_JSON)):
        healthy = server.get_k8s_clients("sre-lab")

    # Force the registry cache to look stale so the next call genuinely re-attempts
    # the GCS load (a real successful load, like the one above, would otherwise sit
    # inside its 300s TTL and never even try again) -- then simulate that re-attempt
    # failing while resolving a DIFFERENT cluster.
    server._CLUSTER_REGISTRY_LOADED_AT = 0.0
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(raise_exc=RuntimeError("GCS down"))):
        with pytest.raises(server.ClusterNotFoundError):
            server.get_k8s_clients("sre-lab-2")

    # sre-lab's own cached, healthy entry must be untouched.
    assert server._K8S_CLIENT_CACHE["sre-lab"][0] is healthy


def test_credential_token_refresh_path_rebuilds_after_ttl(monkeypatch):
    """The stale-bearer-token fix: a cached client older than _K8S_CLIENT_TOKEN_TTL
    must be rebuilt (a fresh credentials.refresh() call), not reused forever."""
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    build_calls = {"n": 0}

    def _fake_build(cluster_id):
        build_calls["n"] += 1
        return (MagicMock(name=f"build-{build_calls['n']}"),) * 4

    monkeypatch.setattr(server, "_build_k8s_clients", _fake_build)

    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(REGISTRY_JSON)):
        first = server.get_k8s_clients("sre-lab")
        assert build_calls["n"] == 1

        # Still within TTL -- must reuse, not rebuild.
        second = server.get_k8s_clients("sre-lab")
        assert second is first
        assert build_calls["n"] == 1

        # Simulate TTL expiry by back-dating the cache entry.
        clients, _created_at = server._K8S_CLIENT_CACHE["sre-lab"]
        server._K8S_CLIENT_CACHE["sre-lab"] = (clients, time.time() - server._K8S_CLIENT_TOKEN_TTL - 1)

        third = server.get_k8s_clients("sre-lab")
        assert third is not first
        assert build_calls["n"] == 2


def test_single_cluster_local_dev_path_unchanged(monkeypatch):
    """CLUSTER_CONFIG_BUCKET genuinely unset (true local dev, never true in a deployed
    environment) must still hit the permissive placeholder -- this fix must not break
    that intentional, documented compatibility path."""
    monkeypatch.delenv("CLUSTER_CONFIG_BUCKET", raising=False)
    entry = server.resolve_cluster("anything")
    assert entry == server._NO_REGISTRY_PLACEHOLDER_ENTRY
    assert server._CLUSTER_REGISTRY_LOAD_FAILED is False


def test_a_to_b_to_a_isolation_with_real_registry_backed_resolution(monkeypatch):
    """End-to-end A->B->A using the real GCS-backed registry loader (not a
    monkeypatched cache dict) -- proves resolve_cluster() picks the right entry every
    time across alternating requests, matching the exact sequence Section 6/7's live
    two-kind-cluster test will exercise."""
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "real-bucket")
    with patch("google.cloud.storage.Client", return_value=_fake_gcs_client(REGISTRY_JSON)):
        a1 = server.resolve_cluster("sre-lab")
        b1 = server.resolve_cluster("sre-lab-2")
        a2 = server.resolve_cluster("sre-lab")
        b2 = server.resolve_cluster("sre-lab-2")
    assert a1["kube_context"] == a2["kube_context"] == "connectgateway_proj_global_sre-lab"
    assert b1["kube_context"] == b2["kube_context"] == "connectgateway_proj_global_sre-lab-2"
