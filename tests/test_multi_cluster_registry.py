"""Routing-layer tests for multi-cluster support (production-readiness backlog item #1).

Two things are proven here, deliberately kept separate from the Terraform-level test
(iac/agent/tests/clusters_json.tftest.hcl, which only proves the JSON *renders* correctly)
and from the real-connectivity test (invoke_agent.py against the live deployment):

1. `_build_cluster_registry()` correctly PARSES a clusters.json shaped exactly like what the
   new Terraform jsonencode(...) produces (mirrors the same 3-cluster scenario used in the
   Terraform test, for traceability between the two layers) — mocking only the GCS blob
   download, nothing else.
2. `resolve_cluster_routing()` correctly picks the RIGHT cluster out of several real
   candidates (not just the already-covered "2 clusters, ambiguous -> safe-stop" case in
   test_context_resolver.py) — exact_id, approved_alias, and project_env_namespace tiers
   each proven to select the correct one of three, plus one genuinely ambiguous case.
"""
import json
from unittest.mock import MagicMock

import agent.mcp_client as mcp_client_mod

# Mirrors the 3-cluster scenario in iac/agent/tests/clusters_json.tftest.hcl's
# "two_additional_synthetic_clusters_render_correctly" run, adapted with an alias and
# overlapping-namespace case added so all 5 routing tiers get exercised here.
CLUSTERS_JSON = json.dumps({
    "clusters": [
        {
            "name": "sre-test-cluster",
            "aliases": [],
            "project": "sreagent-demo",
            "region": "us-central1",
            "type": "gke",
            "environment": "production",
            "allowed_namespaces": [],
            "owner": "",
            "enabled": True,
        },
        {
            "name": "prod-cluster-east",
            "aliases": ["prod-east"],
            "project": "sreagent-demo",
            "region": "us-east1",
            "type": "gke",
            "environment": "staging",
            "allowed_namespaces": ["team-a"],
            "owner": "test-suite",
            "enabled": True,
        },
        {
            "name": "staging-cluster-west",
            "aliases": [],
            "project": "proj-staging",
            "region": "europe-west1",
            "type": "custom",
            "environment": "staging",
            "allowed_namespaces": ["team-b"],
            "owner": "test-suite",
            "enabled": True,
        },
    ]
})


def _mock_gcs_download(monkeypatch, json_text: str):
    """Patches google.cloud.storage.Client() so _build_cluster_registry()'s
    `blob.download_as_text()` returns json_text, without touching real GCS."""
    fake_blob = MagicMock()
    fake_blob.download_as_text.return_value = json_text
    fake_bucket = MagicMock()
    fake_bucket.blob.return_value = fake_blob
    fake_client = MagicMock()
    fake_client.bucket.return_value = fake_bucket
    monkeypatch.setattr(
        "google.cloud.storage.Client",
        lambda: fake_client,
    )
    monkeypatch.setenv("CLUSTER_CONFIG_BUCKET", "fake-bucket")


# ── Layer 1: does _build_cluster_registry() correctly parse a Terraform-shaped payload? ──

def test_build_cluster_registry_parses_all_three_terraform_shaped_entries(monkeypatch):
    _mock_gcs_download(monkeypatch, CLUSTERS_JSON)

    registry = mcp_client_mod._build_cluster_registry()

    assert set(registry.keys()) == {"sre-test-cluster", "prod-cluster-east", "staging-cluster-west"}


def test_build_cluster_registry_preserves_alias_and_type_fields(monkeypatch):
    _mock_gcs_download(monkeypatch, CLUSTERS_JSON)

    registry = mcp_client_mod._build_cluster_registry()

    assert registry["prod-cluster-east"]["aliases"] == ["prod-east"]
    assert registry["staging-cluster-west"]["cluster_type"] == "custom"
    # cluster_type == "custom" must route through the custom MCP, never GKE Remote MCP.
    assert registry["staging-cluster-west"]["mcp_primary"] == "k8s_mcp"
    assert registry["staging-cluster-west"]["mcp_fallback"] == "gke_remote_mcp"
    assert registry["sre-test-cluster"]["cluster_type"] == "gke"
    assert registry["sre-test-cluster"]["mcp_primary"] == "gke_remote_mcp"


def test_build_cluster_registry_preserves_allowed_namespaces_and_enabled(monkeypatch):
    _mock_gcs_download(monkeypatch, CLUSTERS_JSON)

    registry = mcp_client_mod._build_cluster_registry()

    assert registry["prod-cluster-east"]["allowed_namespaces"] == ["team-a"]
    assert registry["staging-cluster-west"]["allowed_namespaces"] == ["team-b"]
    assert all(c["enabled"] for c in registry.values())


# ── Layer 2: does resolve_cluster_routing() pick the RIGHT one of several candidates? ──

def _patch_registry(monkeypatch, registry: dict):
    monkeypatch.setattr(mcp_client_mod, "_get_cluster_registry", lambda: registry)


def _registry_dict() -> dict:
    """The parsed form of CLUSTERS_JSON, as _build_cluster_registry() would produce it —
    built directly (not via the mock) so routing tests don't depend on Layer 1 passing."""
    return {
        "sre-test-cluster": {
            "canonical_id": "sre-test-cluster", "aliases": [], "project": "sreagent-demo",
            "region": "us-central1", "cluster_type": "gke", "environment": "production",
            "allowed_namespaces": [], "owner": "", "enabled": True,
            "mcp_primary": "gke_remote_mcp", "mcp_fallback": "k8s_mcp", "mcp_url": "",
        },
        "prod-cluster-east": {
            "canonical_id": "prod-cluster-east", "aliases": ["prod-east"], "project": "sreagent-demo",
            "region": "us-east1", "cluster_type": "gke", "environment": "staging",
            "allowed_namespaces": ["team-a"], "owner": "test-suite", "enabled": True,
            "mcp_primary": "gke_remote_mcp", "mcp_fallback": "k8s_mcp", "mcp_url": "",
        },
        "staging-cluster-west": {
            "canonical_id": "staging-cluster-west", "aliases": [], "project": "proj-staging",
            "region": "europe-west1", "cluster_type": "custom", "environment": "staging",
            "allowed_namespaces": ["team-b"], "owner": "test-suite", "enabled": True,
            "mcp_primary": "k8s_mcp", "mcp_fallback": "gke_remote_mcp", "mcp_url": "",
        },
    }


def test_exact_id_hint_picks_the_correct_one_of_three(monkeypatch):
    _patch_registry(monkeypatch, _registry_dict())

    result = mcp_client_mod.resolve_cluster_routing(cluster_hint="prod-cluster-east")

    assert result["resolved"] is True
    assert result["cluster_name"] == "prod-cluster-east"
    assert result["method"] == "exact_id"


def test_approved_alias_picks_the_correct_one_of_three(monkeypatch):
    _patch_registry(monkeypatch, _registry_dict())

    # "prod-east" is not a canonical id anywhere in the registry — only resolvable via alias.
    result = mcp_client_mod.resolve_cluster_routing(cluster_hint="prod-east")

    assert result["resolved"] is True
    assert result["cluster_name"] == "prod-cluster-east"
    assert result["method"] == "approved_alias"


def test_project_env_namespace_picks_the_correct_one_of_three_when_unambiguous(monkeypatch):
    _patch_registry(monkeypatch, _registry_dict())

    # project="proj-staging" alone already narrows to exactly staging-cluster-west
    # (it's the only cluster in that project) — no cluster_hint/cluster_guess needed.
    result = mcp_client_mod.resolve_cluster_routing(
        project_hint="proj-staging", environment_hint="staging", namespace_hint="team-b",
    )

    assert result["resolved"] is True
    assert result["cluster_name"] == "staging-cluster-west"
    assert result["method"] == "project_env_namespace"


def test_environment_only_hint_is_ambiguous_across_two_clusters_and_safe_stops(monkeypatch):
    _patch_registry(monkeypatch, _registry_dict())

    # environment="staging" alone matches BOTH prod-cluster-east and staging-cluster-west —
    # must refuse to guess between them, not silently pick one.
    result = mcp_client_mod.resolve_cluster_routing(environment_hint="staging")

    assert result["resolved"] is False
    assert result["method"] == "unresolved"


def test_no_hints_at_all_across_three_clusters_safe_stops(monkeypatch):
    _patch_registry(monkeypatch, _registry_dict())

    result = mcp_client_mod.resolve_cluster_routing()

    assert result["resolved"] is False
    assert result["method"] == "unresolved"
