"""Proves agent/mcp_client.py's _call_catalog_source() is genuinely source-agnostic,
per the requirement that Elastic/Grafana/git MCP be addable later as plug-and-play
(a new catalog entry + a new adapter module, zero changes to mcp_client.py or
mcp_router.py). Uses a FAKE second source with its own fake adapter module -- if this
test needed to touch mcp_client.py to pass, the plug-and-play claim would be false.
"""
import sys
import types

import agent.mcp_client as mcp_client
import agent.source_catalog as catalog


def _install_fake_elastic_adapter(monkeypatch):
    fake_module = types.ModuleType("agent.sources.fake_elastic_adapter")

    def search(cluster_id, query_dsl, start_ts, end_ts):
        return {
            "source": "elastic", "cluster_id": cluster_id, "query_dsl": query_dsl,
            "retrieval_status": "ok", "hits": [],
        }

    fake_module.search = search
    monkeypatch.setitem(sys.modules, "agent.sources.fake_elastic_adapter", fake_module)

    patched = dict(catalog.SOURCE_CATALOG)
    patched["elastic"] = {
        "source_type": "elastic",
        "enabled": True,
        "allowed_clusters": [],
        "capabilities": frozenset({"application_logs"}),
        "approved_tools": frozenset({"search"}),
        "output_adapter": "agent.sources.fake_elastic_adapter",
        "primary_tool": "search",
        "query_field": "query_dsl",
        "query_field_hint": "an Elasticsearch DSL query",
        "query_limits": {"max_window_seconds": 3600, "max_result_series": 50, "timeout_s": 10},
    }
    monkeypatch.setattr(catalog, "SOURCE_CATALOG", patched)
    # ALLOWED_TOOLS is built once at mcp_client.py import time from whatever
    # SOURCE_CATALOG looked like then (agent/mcp_client.py:102) -- in a real
    # deployment a new catalog entry lands in the source file BEFORE the
    # process starts, so this reflects real startup behavior, not a gap this
    # test papers over.
    monkeypatch.setattr(mcp_client, "ALLOWED_TOOLS", mcp_client.ALLOWED_TOOLS | {"search"})
    return patched


def test_second_catalog_source_requires_zero_client_code_changes(monkeypatch):
    """A brand-new source (elastic), with its own adapter module and its own query
    field name, is dispatched correctly by the EXISTING, unmodified
    _call_catalog_source() -- no new if/elif branch was added for it."""
    _install_fake_elastic_adapter(monkeypatch)

    result = mcp_client.call_tool(
        "elastic", "search",
        {"cluster_id": "sre-lab", "query_dsl": "message:OOMKilled", "start_ts": 0, "end_ts": 100},
        cluster_name="sre-lab",
    )

    assert result["ok"] is True
    assert result["result"]["source"] == "elastic"
    assert result["result"]["query_dsl"] == "message:OOMKilled"
    assert result["mcp_source"] == "elastic"


def test_unapproved_tool_for_catalog_source_is_rejected(monkeypatch):
    """A tool absent from the source's own approved_tools must be rejected by
    _call_catalog_source()'s catalog-level check -- tested here by ALSO putting
    it in the module-wide ALLOWED_TOOLS allowlist, so this specifically exercises
    the catalog check rather than the earlier, unrelated global-allowlist check."""
    _install_fake_elastic_adapter(monkeypatch)
    monkeypatch.setattr(mcp_client, "ALLOWED_TOOLS", mcp_client.ALLOWED_TOOLS | {"list_indices"})

    result = mcp_client.call_tool(
        "elastic", "list_indices",  # not in this source's approved_tools ({"search"} only)
        {"cluster_id": "sre-lab"},
        cluster_name="sre-lab",
    )

    assert result["ok"] is False
    assert "not an approved tool" in result["error"]


def test_missing_adapter_module_fails_cleanly_not_unhandled_exception(monkeypatch):
    patched = dict(catalog.SOURCE_CATALOG)
    patched["broken_source"] = {
        "enabled": True, "allowed_clusters": [], "capabilities": frozenset(),
        "approved_tools": frozenset({"query"}),
        "output_adapter": "agent.sources.does_not_exist_adapter",
    }
    monkeypatch.setattr(catalog, "SOURCE_CATALOG", patched)
    monkeypatch.setattr(mcp_client, "ALLOWED_TOOLS", mcp_client.ALLOWED_TOOLS | {"query"})

    result = mcp_client.call_tool(
        "broken_source", "query", {"cluster_id": "sre-lab"}, cluster_name="sre-lab",
    )

    assert result["ok"] is False
    assert "adapter dispatch failed" in result["error"]
