"""
context_resolver.py

Resolves target cluster and MCP source strategy.

Design:
- GKE Remote MCP is primary.
- Custom K8s MCP is fallback.
"""

import logging
import os

from agent.state import AgentState
from agent.mcp_client import resolve_cluster
from agent.otel import trace_node

log = logging.getLogger("sre-agent.context_resolver")


@trace_node("langgraph.context_resolver")
def context_resolver(state: AgentState) -> dict:
    log.info("node=context_resolver run_id=%s", state["run_id"])

    ctx = state.get("resolved_context", {})

    cluster_name = (
        ctx.get("cluster_name")
        or ctx.get("cluster")
        or "sre-test-cluster"
    )

    # Recorded for the confidence framework's routing_confirmed signal (see
    # agent/confidence/scorer.py) — does NOT change the default-cluster fallback behavior
    # itself, only makes it observable to the completeness score instead of a log line only.
    cluster_explicitly_provided = bool(ctx.get("cluster_name") or ctx.get("cluster"))

    if not cluster_explicitly_provided:
        log.warning(
            "context_resolver: unknown cluster, defaulting to %s",
            cluster_name,
        )

    try:
        cluster_info = resolve_cluster(cluster_name)
    except ValueError as exc:
        log.error("context_resolver: cluster not in registry — %s", exc)
        return {
            "errors": [f"Unknown cluster '{cluster_name}' — update clusters.json in GCS. {exc}"],
            "investigation": {"status": "failed"},
        }

    resolved_cluster = cluster_info.get("cluster_name", cluster_name)
    project_id = cluster_info.get("project", os.environ.get("PROJECT_ID", "your-gcp-project-id"))
    region = cluster_info.get("region", "us-central1")

    primary = cluster_info.get("mcp_primary", "gke_remote_mcp")
    fallback = cluster_info.get("mcp_fallback", "k8s_mcp")

    log.info(
        "context_resolver cluster=%s project=%s region=%s primary=%s fallback=%s",
        resolved_cluster,
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
            "cluster_explicitly_provided": cluster_explicitly_provided,

            # Primary/fallback source strategy.
            "primary_mcp_source": primary,
            "mcp_source": primary,
            "mcp_fallback": fallback,

            # Keep investigation fields.
            "incident_type": ctx.get("incident_type", "Unknown"),
            "namespace": ctx.get("namespace", "test-incidents"),
            "pod": ctx.get("pod", ""),
            "deployment": ctx.get("deployment", ""),
            "severity": ctx.get("severity", "unknown"),
        }
    }
