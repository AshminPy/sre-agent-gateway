"""Section 5 redesign: one shared custom MCP Cloud Run deployment now serves multiple
on-prem clusters (see server.py's resolve_cluster()/get_k8s_clients()), replacing the
old single @lru_cache(maxsize=1) global client (issue #86 -- "single-cluster support
in disguise"). These tests prove the redesign's core safety property: two different
clusters, including ones with IDENTICAL namespace/pod names, never share a connection
or mix up evidence -- concurrently or sequentially.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import server


REGISTRY = {
    "cluster-a": {
        "cluster_type": "custom", "enabled": True,
        "kube_context": "connectgateway_proj_global_cluster-a",
        "allowed_namespaces": ["test-incidents"],
    },
    "cluster-b": {
        "cluster_type": "custom", "enabled": True,
        "kube_context": "connectgateway_proj_global_cluster-b",
        "allowed_namespaces": ["test-incidents"],
    },
    "cluster-c-disabled": {
        "cluster_type": "custom", "enabled": False,
        "kube_context": "connectgateway_proj_global_cluster-c",
        "allowed_namespaces": [],
    },
    "cluster-d-gke": {
        "cluster_type": "gke", "enabled": True,
        "kube_context": "",
        "allowed_namespaces": [],
    },
}


@pytest.fixture(autouse=True)
def _patch_registry_and_clear_cache(monkeypatch):
    monkeypatch.setattr(server, "_CLUSTER_REGISTRY_CACHE", dict(REGISTRY))
    monkeypatch.setattr(server, "_CLUSTER_REGISTRY_LOADED_AT", time.time() + 3600)
    server.get_k8s_clients.cache_clear()
    yield
    server.get_k8s_clients.cache_clear()


# ── resolve_cluster() validation ──────────────────────────────────────

def test_resolve_cluster_accepts_known_enabled_custom_cluster():
    entry = server.resolve_cluster("cluster-a")
    assert entry["kube_context"] == "connectgateway_proj_global_cluster-a"


def test_resolve_cluster_rejects_unknown_cluster_id():
    with pytest.raises(server.ClusterNotFoundError):
        server.resolve_cluster("nonexistent-cluster")


def test_resolve_cluster_rejects_disabled_cluster():
    with pytest.raises(server.ClusterNotFoundError):
        server.resolve_cluster("cluster-c-disabled")


def test_resolve_cluster_rejects_gke_type_cluster():
    """GKE clusters route through GKE Remote MCP, never this server -- accepting one
    here would let a caller redirect this process at a cluster it isn't authorized to
    reach through this path."""
    with pytest.raises(server.ClusterNotFoundError):
        server.resolve_cluster("cluster-d-gke")


def test_resolve_cluster_rejects_empty_string():
    with pytest.raises(server.ClusterNotFoundError):
        server.resolve_cluster("")


def test_resolve_cluster_rejects_none():
    with pytest.raises(server.ClusterNotFoundError):
        server.resolve_cluster(None)


# ── get_k8s_clients() per-cluster caching and isolation ────────────────

def _fake_kube_client_factory():
    """Returns a fresh MagicMock standing in for kubernetes.client.ApiClient's
    4-tuple of API objects, distinguishable by identity (id())."""
    return (MagicMock(name="CoreV1"), MagicMock(name="AppsV1"),
            MagicMock(name="BatchV1"), MagicMock(name="AutoscalingV2"))


def test_different_clusters_get_different_client_objects():
    """The core fix: two distinct cluster_ids must never resolve to the same cached
    client tuple -- this is exactly what the old @lru_cache(maxsize=1) got wrong."""
    with patch("kubernetes.config.load_kube_config") as mock_load, \
         patch("kubernetes.client.ApiClient") as mock_api_client, \
         patch("kubernetes.client.CoreV1Api", side_effect=lambda ac: MagicMock(name=f"CoreV1-{id(ac)}")), \
         patch("kubernetes.client.AppsV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.BatchV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", side_effect=lambda ac: MagicMock()):
        mock_api_client.side_effect = lambda: MagicMock(name="ApiClient")

        clients_a = server.get_k8s_clients("cluster-a")
        clients_b = server.get_k8s_clients("cluster-b")

        assert clients_a is not clients_b
        assert clients_a[0] is not clients_b[0]  # the CoreV1Api objects themselves differ
        # Confirms each cluster was connected via its OWN kube_context, not a shared one.
        contexts_used = [c.kwargs.get("context") for c in mock_load.call_args_list]
        assert "connectgateway_proj_global_cluster-a" in contexts_used
        assert "connectgateway_proj_global_cluster-b" in contexts_used


def test_same_cluster_id_reuses_cached_client():
    """Caching must still work per-cluster -- calling the same cluster_id twice must
    not reconnect, matching the original single-cluster cache's intent, just now keyed
    correctly."""
    with patch("kubernetes.config.load_kube_config") as mock_load, \
         patch("kubernetes.client.ApiClient", side_effect=lambda: MagicMock()), \
         patch("kubernetes.client.CoreV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.AppsV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.BatchV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", side_effect=lambda ac: MagicMock()):

        first = server.get_k8s_clients("cluster-a")
        second = server.get_k8s_clients("cluster-a")

        assert first is second
        assert mock_load.call_count == 1  # only connected once, not per call


def test_unknown_cluster_id_never_falls_back_to_a_different_cluster():
    """An invalid cluster_id must raise, never silently resolve to some other real
    cluster (e.g. the first registry entry, or whatever was cached last)."""
    with pytest.raises(server.ClusterNotFoundError):
        server.get_k8s_clients("totally-unregistered-cluster")


def test_registered_cluster_with_missing_kube_context_fails_loudly_not_silently():
    """A validated, enabled 'custom' cluster with no kube_context configured is a real
    misconfiguration -- must raise, never fall through to the local-kubeconfig default
    (that would silently connect a validated cluster_id to a DIFFERENT cluster)."""
    with patch.object(server, "_get_cluster_registry", return_value={
        "broken-cluster": {"cluster_type": "custom", "enabled": True,
                            "kube_context": "", "allowed_namespaces": []},
    }):
        with pytest.raises(server.ClusterNotFoundError, match="neither kube_context nor fleet_project_number"):
            server.get_k8s_clients("broken-cluster")


# ── Concurrent isolation — the assignment's explicit acceptance test ───
#
# Exercises the REAL get_k8s_clients() (real lru_cache, real resolve_cluster()
# call, real branch logic) under genuine concurrent access -- only the
# kubernetes SDK calls themselves (config.load_kube_config, client.CoreV1Api,
# etc.) are mocked, tagged with the kube_context they were built for, so a
# cross-cluster mix-up would be directly observable in the returned client
# identity, not just asserted away by a higher-level mock.

_last_context_by_thread: dict = {}  # thread ident -> kube_context, written/read by the same thread only


def _tagged_core_v1(api_client):
    """Stands in for kubernetes.client.CoreV1Api(api_client) -- tags the mock
    with whichever kube_context load_kube_config was last called with FOR
    THIS api_client, so each returned client can prove which cluster it was
    actually built for."""
    v1 = MagicMock(name="CoreV1")
    v1.built_for_context = getattr(api_client, "_built_for_context", None)
    return v1


def test_concurrent_requests_to_different_clusters_never_mix_connections():
    """Fires real concurrent calls to get_k8s_clients() for TWO different clusters,
    many times each, and confirms every single result is tagged with the SAME
    kube_context that specific cluster_id maps to in the registry -- never the
    other cluster's context, under genuine thread concurrency (functools.lru_cache
    is documented thread-safe; this test is the evidence for THIS server's usage
    of it, not a reason to skip testing it)."""
    results = {}
    errors = []
    context_by_cluster = {c: e["kube_context"] for c, e in REGISTRY.items() if e["kube_context"]}

    def fake_load_kube_config(context=None):
        # Stash the context on a thread-local-ish marker via the next ApiClient
        # instance's attribute -- ApiClient() takes no args here, so we tag via
        # a module-level "last context per thread" dict keyed by thread ident,
        # read back immediately by the ApiClient patch below (same call stack,
        # no real concurrency gap between the two calls in get_k8s_clients()).
        _last_context_by_thread[threading.get_ident()] = context

    def fake_api_client():
        ac = MagicMock(name="ApiClient")
        ac._built_for_context = _last_context_by_thread.get(threading.get_ident())
        return ac

    def worker(cluster_id, iteration):
        try:
            clients = server.get_k8s_clients(cluster_id)
            results[(cluster_id, iteration)] = clients[0].built_for_context
        except Exception as e:  # pragma: no cover - failure path surfaced via errors list
            errors.append((cluster_id, iteration, e))

    with patch("kubernetes.config.load_kube_config", side_effect=fake_load_kube_config), \
         patch("kubernetes.client.ApiClient", side_effect=fake_api_client), \
         patch("kubernetes.client.CoreV1Api", side_effect=_tagged_core_v1), \
         patch("kubernetes.client.AppsV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.BatchV1Api", side_effect=lambda ac: MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", side_effect=lambda ac: MagicMock()):

        threads = []
        for i in range(10):
            threads.append(threading.Thread(target=worker, args=("cluster-a", i)))
            threads.append(threading.Thread(target=worker, args=("cluster-b", i)))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

    assert not errors, f"worker threads raised: {errors}"
    assert len(results) == 20

    for (cluster_id, iteration), built_for_context in results.items():
        expected = context_by_cluster[cluster_id]
        assert built_for_context == expected, (
            f"cluster_id={cluster_id} iteration={iteration} got a client built for "
            f"context={built_for_context!r}, expected {expected!r} -- cross-cluster "
            "connection mixing under concurrency"
        )
