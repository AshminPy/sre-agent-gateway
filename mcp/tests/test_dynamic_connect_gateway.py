"""Section 5 hardening (2026-09-07): a genuinely NEW on-prem cluster no longer needs
its context added to the static, image-baked mcp/connect-gateway-kubeconfig.yaml (which
would require an image rebuild). When a registry entry sets fleet_project_number, the
Connect Gateway connection is built entirely at request time instead.

The already-live-validated sre-lab entry is NOT migrated to this path -- these tests
prove the NEW path works and prove the OLD (kube_context) path is completely unaffected,
per the Section 5 correction's "preserve the existing working cluster connection during
migration" requirement.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

import server


REGISTRY = {
    "dynamic-cluster": {
        "cluster_type": "custom", "enabled": True,
        "kube_context": "",
        "fleet_project_number": "123456789012",
        "fleet_membership": "dynamic-cluster",
        "allowed_namespaces": ["test-incidents"],
    },
    "dynamic-cluster-custom-membership": {
        "cluster_type": "custom", "enabled": True,
        "kube_context": "",
        "fleet_project_number": "999999999999",
        "fleet_membership": "actual-fleet-name",  # deliberately differs from the registry key
        "allowed_namespaces": [],
    },
    "legacy-static-cluster": {
        "cluster_type": "custom", "enabled": True,
        "kube_context": "connectgateway_proj_global_legacy-static-cluster",
        "fleet_project_number": "",
        "fleet_membership": "",
        "allowed_namespaces": [],
    },
    "broken-neither-configured": {
        "cluster_type": "custom", "enabled": True,
        "kube_context": "",
        "fleet_project_number": "",
        "fleet_membership": "",
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


def _fake_credentials():
    creds = MagicMock()
    creds.token = "fake-adc-access-token"
    creds.refresh = MagicMock()
    return creds


def test_dynamic_path_builds_correct_connect_gateway_host():
    """Host must match projects/{PROJECT_NUMBER}/locations/global/memberships/{MEMBERSHIP}
    -- the same format Google documents and the same one already proven live via the
    static kubeconfig (docs/connect-gateway-onprem.md)."""
    creds = _fake_credentials()
    with patch("google.auth.default", return_value=(creds, None)), \
         patch("google.auth.transport.requests.Request"), \
         patch("kubernetes.client.Configuration") as mock_config_cls, \
         patch("kubernetes.client.ApiClient") as mock_api_client, \
         patch("kubernetes.client.CoreV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AppsV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.BatchV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", return_value=MagicMock()):
        mock_configuration = MagicMock()
        mock_config_cls.return_value = mock_configuration

        server.get_k8s_clients("dynamic-cluster")

        assert mock_configuration.host == (
            "https://connectgateway.googleapis.com/v1/projects/123456789012"
            "/locations/global/memberships/dynamic-cluster"
        )
        assert mock_configuration.api_key == {"authorization": "Bearer fake-adc-access-token"}
        assert mock_configuration.verify_ssl is True
        mock_api_client.assert_called_once_with(mock_configuration)


def test_dynamic_path_uses_explicit_fleet_membership_when_it_differs_from_registry_key():
    """fleet_membership can legitimately differ from the registry's own cluster name --
    the URL must use the REAL Fleet membership name, not the registry key."""
    creds = _fake_credentials()
    with patch("google.auth.default", return_value=(creds, None)), \
         patch("google.auth.transport.requests.Request"), \
         patch("kubernetes.client.Configuration") as mock_config_cls, \
         patch("kubernetes.client.ApiClient"), \
         patch("kubernetes.client.CoreV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AppsV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.BatchV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", return_value=MagicMock()):
        mock_configuration = MagicMock()
        mock_config_cls.return_value = mock_configuration

        server.get_k8s_clients("dynamic-cluster-custom-membership")

        assert "actual-fleet-name" in mock_configuration.host
        assert "dynamic-cluster-custom-membership" not in mock_configuration.host
        assert "999999999999" in mock_configuration.host


def test_legacy_kube_context_path_is_completely_unaffected():
    """The already-live-validated static-kubeconfig path (sre-lab's real shape: only
    kube_context set, fleet_project_number empty) must still go through
    config.load_kube_config(), never the new dynamic branch -- proves this hardening
    did not touch the one thing already proven working live."""
    with patch("kubernetes.config.load_kube_config") as mock_load, \
         patch("google.auth.default") as mock_default, \
         patch("kubernetes.client.ApiClient", return_value=MagicMock()), \
         patch("kubernetes.client.CoreV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AppsV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.BatchV1Api", return_value=MagicMock()), \
         patch("kubernetes.client.AutoscalingV2Api", return_value=MagicMock()):

        server.get_k8s_clients("legacy-static-cluster")

        mock_load.assert_called_once_with(context="connectgateway_proj_global_legacy-static-cluster")
        mock_default.assert_not_called()  # the dynamic branch's ADC token mint must never run


def test_cluster_with_neither_kube_context_nor_fleet_project_number_fails_loudly():
    with pytest.raises(server.ClusterNotFoundError, match="neither kube_context nor fleet_project_number"):
        server.get_k8s_clients("broken-neither-configured")


def test_registry_loader_defaults_fleet_membership_to_cluster_name(monkeypatch):
    """_load_cluster_registry() (the real GCS-backed loader, not the test fixture above)
    must default fleet_membership to the registry entry's own name when Terraform leaves
    it blank -- matches today's real sre-lab-style entries where the two are identical."""
    import json as _json

    fake_blob = MagicMock()
    fake_blob.download_as_text.return_value = _json.dumps({
        "clusters": [{
            "name": "some-cluster", "type": "custom", "enabled": True,
            "fleet_project_number": "111111111111",
            "fleet_membership": "",  # left blank
        }]
    })
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket

    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "fake-bucket")
    with patch("google.cloud.storage.Client", return_value=fake_client):
        registry = server._load_cluster_registry()

    assert registry["some-cluster"]["fleet_membership"] == "some-cluster"
