"""Regression test for issue #211: get_k8s_logs silently dropped the `container`
argument, even when supplied -- on a multi-container pod (any Istio/Envoy/Linkerd
sidecar-injected workload), this meant there was no way to target the app
container specifically. Real repro against a live 2-container test pod: with
no container forwarded, Kubernetes' own API "Defaulted container 'istio-proxy'
out of: istio-proxy, app" and returned only the sidecar's logs.
"""
from agent.mcp_client import _build_gke_args

PARENT = "projects/p/locations/us-central1/clusters/c"


def test_get_k8s_logs_forwards_container_when_supplied():
    payload = _build_gke_args(
        "get_k8s_logs", {"container": "app", "tailLines": 50}, PARENT, "test-incidents", "my-pod",
    )
    assert payload.get("container") == "app", (
        "container must reach the real API request -- dropping it silently defaults "
        "to whichever container Kubernetes picks first, which is often a sidecar"
    )


def test_get_k8s_logs_omits_container_key_when_not_supplied():
    """A single-container pod (the common case) must not send an empty/None
    container key that could itself confuse the API -- omit it entirely."""
    payload = _build_gke_args("get_k8s_logs", {"tailLines": 50}, PARENT, "test-incidents", "my-pod")
    assert "container" not in payload


def test_get_k8s_logs_forwards_previous_and_tail_alongside_container():
    """The container fix must not regress the existing previous/tail handling."""
    payload = _build_gke_args(
        "get_k8s_logs", {"container": "app", "previous": True, "tail": 200}, PARENT, "test-incidents", "my-pod",
    )
    assert payload["container"] == "app"
    assert payload["previous"] is True
    assert payload["tail"] == "200"
