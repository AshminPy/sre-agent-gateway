"""
context_resolver.py

Resolves target cluster and MCP source strategy.

Design:
- GKE Remote MCP is primary.
- Custom K8s MCP is fallback.

Cluster resolution is deterministic and NEVER guesses. It delegates to
agent/mcp_client.py:resolve_cluster_routing() for the priority chain:
  1. exact_id                — verified hint matches a canonical cluster id exactly
  2. verified_alert_metadata — verified hint matches a canonical id case-insensitively
  3. approved_alias          — hint (or, absent a hint, the LLM's free-text guess)
                                matches a registered alias
  4. project_env_namespace   — project/environment/namespace hints uniquely identify one
                                enabled cluster
  5. human safe-stop         — none of the above resolved a cluster; the investigation
                                stops (investigation.status = "failed") instead of
                                defaulting to any cluster. graph.py routes a failed status
                                straight to rca_builder, skipping tool calls entirely.

The routing method + reason are recorded in resolved_context and surfaced in RCA output
(see agent/nodes/rca_builder.py's investigation_context) for observability.
"""

import logging
import os

from agent.state import AgentState
from agent.mcp_client import resolve_cluster, resolve_cluster_routing
from agent.otel import trace_node

log = logging.getLogger("sre-agent.context_resolver")


@trace_node("langgraph.context_resolver")
def context_resolver(state: AgentState) -> dict:
    log.info("node=context_resolver run_id=%s", state["run_id"])

    ctx = state.get("resolved_context", {})

    cluster_hint     = (ctx.get("cluster_hint") or "").strip()
    cluster_guess    = (ctx.get("cluster_guess") or "").strip()
    namespace_hint   = (ctx.get("namespace") or "").strip()
    project_hint     = (ctx.get("project_hint") or "").strip()
    environment_hint = (ctx.get("environment_hint") or "").strip()

    routing = resolve_cluster_routing(
        cluster_hint=cluster_hint,
        cluster_guess=cluster_guess,
        namespace_hint=namespace_hint,
        project_hint=project_hint,
        environment_hint=environment_hint,
    )

    if not routing["resolved"]:
        message = f"context_resolver: cluster could not be safely determined — {routing['reason']}"
        log.error(message)
        # Safe stop — no guessed cluster, no cluster_name set, investigation marked failed.
        # graph.py's conditional edge after context_resolver routes this straight to
        # rca_builder instead of task_planner/mcp_router/tool_executor.
        return {
            "errors": [message],
            "investigation": {
                "status": "failed",
                "loop_exit_reason": "cluster_unresolved",
            },
            "resolved_context": {
                **ctx,
                "cluster_explicitly_provided": False,
                "cluster_routing_method": routing["method"],
                "cluster_routing_reason": routing["reason"],
            },
        }

    cluster_name = routing["cluster_name"]

    try:
        cluster_info = resolve_cluster(cluster_name)
    except ValueError as exc:
        # Should not happen — resolve_cluster_routing only ever resolves to a cluster that
        # exists in this same registry — but never trust an unresolvable name regardless.
        message = f"context_resolver: routed cluster '{cluster_name}' could not be resolved — {exc}"
        log.error(message)
        return {
            "errors": [message],
            "investigation": {
                "status": "failed",
                "loop_exit_reason": "cluster_unresolved",
            },
        }

    resolved_cluster = cluster_info.get("cluster_name", cluster_name)
    project_id = cluster_info.get("project", os.environ.get("PROJECT_ID", "your-gcp-project-id"))
    region = cluster_info.get("region", "us-central1")

    primary = cluster_info.get("mcp_primary", "gke_remote_mcp")
    fallback = cluster_info.get("mcp_fallback", "k8s_mcp")

    log.info(
        "context_resolver cluster=%s method=%s project=%s region=%s primary=%s fallback=%s",
        resolved_cluster,
        routing["method"],
        project_id,
        region,
        primary,
        fallback,
    )

    return {
        "resolved_context": {
            **ctx,
            "cluster_name": resolved_cluster,
            "cluster_region": region,
            "project_id": project_id,
            "cluster_explicitly_provided": True,
            "cluster_routing_method": routing["method"],
            "cluster_routing_reason": routing["reason"],

            # Primary/fallback source strategy.
            "primary_mcp_source": primary,
            "mcp_source": primary,
            "mcp_fallback": fallback,

            # Keep investigation fields.
            "incident_type": ctx.get("incident_type", "Unknown"),
            "namespace": ctx.get("namespace", ""),
            "pod": ctx.get("pod", ""),
            "deployment": ctx.get("deployment", ""),
            "severity": ctx.get("severity", "unknown"),
        }
    }
