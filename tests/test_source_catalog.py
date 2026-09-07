"""Section 6: capability-based MCP/data-source selection.

Core acceptance requirement, tested first and explicitly: with no source enabled
(today's real catalog state), routing behavior must be byte-for-byte identical to
before agent/source_catalog.py existed.
"""
import agent.source_catalog as catalog


def test_no_enabled_source_preserves_kubernetes_only_behavior():
    """The one entry in today's real catalog (prometheus) is disabled -- select_
    additional_source() must return None for ANY task_plan/primary_gap, so
    mcp_router.py's Section 6 block is always a no-op today."""
    assert catalog.SOURCE_CATALOG["prometheus"]["enabled"] is False
    result = catalog.select_additional_source(
        "sre-lab", "Check historical CPU usage trend for this pod", "metric trend unknown"
    )
    assert result is None


def test_get_enabled_sources_for_cluster_excludes_disabled_entries():
    assert catalog.get_enabled_sources_for_cluster("sre-lab") == {}


def test_capability_needed_matches_known_metric_keywords():
    assert catalog.capability_needed("investigate cpu usage over the last hour", "") == \
        catalog.CAPABILITY_HISTORICAL_METRICS


def test_capability_needed_matches_deployment_change_keywords():
    assert catalog.capability_needed("", "need deployment history for this rollout") == \
        catalog.CAPABILITY_DEPLOYMENT_CHANGES


def test_capability_needed_returns_none_for_ordinary_k8s_gap():
    """The overwhelming majority of investigations (pod status, logs, events) must
    never match any additional-source keyword -- this is the main guard against
    Section 6 changing behavior for normal Kubernetes investigations."""
    assert catalog.capability_needed("check pod status", "container exit code unknown") is None
    assert catalog.capability_needed("get current logs", "crash reason unknown") is None


def test_select_additional_source_returns_none_when_capability_matches_but_none_enabled():
    """A real metrics-shaped gap with the ONLY matching source disabled must still
    return None -- capability match alone is never enough, the source must also
    be enabled and authorized."""
    result = catalog.select_additional_source(
        "sre-lab", "historical metric analysis needed", ""
    )
    assert result is None


def test_select_additional_source_works_once_enabled(monkeypatch):
    """Proves the mechanism itself is real -- flip the entry on (as a real
    deployment would after completing live validation) and confirm selection
    actually fires for a matching gap and is excluded for a non-matching one."""
    patched = dict(catalog.SOURCE_CATALOG)
    patched["prometheus"] = {**patched["prometheus"], "enabled": True}
    monkeypatch.setattr(catalog, "SOURCE_CATALOG", patched)

    result = catalog.select_additional_source("sre-lab", "cpu usage over the last hour", "")
    assert result is not None
    assert result["source_id"] == "prometheus"
    assert result["matched_capability"] == catalog.CAPABILITY_HISTORICAL_METRICS

    assert catalog.select_additional_source("sre-lab", "check pod status", "") is None


def test_select_additional_source_respects_allowed_clusters(monkeypatch):
    patched = dict(catalog.SOURCE_CATALOG)
    patched["prometheus"] = {
        **patched["prometheus"], "enabled": True, "allowed_clusters": ["other-cluster"],
    }
    monkeypatch.setattr(catalog, "SOURCE_CATALOG", patched)

    # Not authorized for sre-lab -- must be excluded even though enabled and the
    # capability matches.
    assert catalog.select_additional_source("sre-lab", "cpu usage over the last hour", "") is None
    result = catalog.select_additional_source("other-cluster", "cpu usage over the last hour", "")
    assert result is not None
