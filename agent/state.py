"""
GCP AgentState — extends AWS prototype with:
- evidence_id + raw_ref (GCS write per evidence item)
- run_id + incident_id (full audit chain)
- mcp_source (which MCP was selected)
- confidence_band (auto/review/escalate)
- evaluation_ids (one per evaluator decision)
- loop_exit_reason (why the loop stopped)
- sources_skipped (audit trail)
- cluster + region in state (multi-cluster routing)
- token tracking + cost estimation
"""
from __future__ import annotations
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict


def _append(existing: list, new: list) -> list:
    return existing + new


class AgentState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────
    incident_envelope:  Dict[str, Any]

    # ── Run identity ──────────────────────────────────────────────
    run_id:             str
    incident_id:        str

    # ── Resolved context ──────────────────────────────────────────
    resolved_context:   Annotated[Dict[str, Any], operator.or_]
    # includes: namespace, pod, cluster_name, cluster_region,
    #           mcp_endpoint, mcp_source, mcp_fallback,
    #           incident_type, project_id

    # ── Investigation control ─────────────────────────────────────
    investigation:      Annotated[Dict[str, Any], operator.or_]
    # includes: status, current_step, max_steps, min_steps,
    #           enough_evidence, confidence, confidence_band,
    #           loop_exit_reason, evidence_gaps, task_plan,
    #           primary_gap, tokens_input, tokens_output,
    #           tokens_total, estimated_cost_usd

    # ── MCP routing ───────────────────────────────────────────────
    selected_mcp:       Optional[str]
    current_action:     Optional[Dict[str, Any]]

    # Temporary handoff from tool_executor to evidence_extractor.
    # This must be part of AgentState or LangGraph may drop it before
    # the conditional edge can route to evidence_extractor.
    latest_tool_result: Optional[Dict[str, Any]]

    sources_skipped:    Annotated[List[str], _append]

    # ── Evidence — IDs and compressed facts ONLY ──────────────────
    # Raw MCP output NEVER enters state — lives in GCS only
    evidence_ids:       Annotated[List[str], _append]
    evidence_store:     Annotated[Dict[str, Any], operator.or_]
    # { ev_001: { facts, summary, raw_ref, source, tool, cluster } }

    # ── Evaluation chain ──────────────────────────────────────────
    evaluation_ids:     Annotated[List[str], _append]

    # ── Tool history — compact, NO raw output ─────────────────────
    tool_history:       Annotated[List[Dict[str, Any]], _append]
    errors:             Annotated[List[str], _append]

    # ── Output ────────────────────────────────────────────────────
    working_theory:     str
    final_summary:      Optional[Dict[str, Any]]


def make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    import random
    import string
    suffix = "".join(random.choices(string.ascii_lowercase, k=4))
    return f"run_{ts}_{suffix}"


def get_initial_state(incident_envelope: Dict[str, Any]) -> AgentState:
    import time
    run_id      = make_run_id()
    incident_id = incident_envelope.get("source_event_id", f"inc_{run_id}")
    return {
        "incident_envelope":  incident_envelope,
        "run_id":             run_id,
        "incident_id":        incident_id,
        "resolved_context":   {},
        "investigation": {
            "status":               "running",
            "current_step":         0,
            "max_steps":            5,
            "min_steps":            2,
            "enough_evidence":      False,
            "confidence":           0.0,
            "confidence_band":      "escalate",
            "loop_exit_reason":     None,
            "evidence_gaps":        [],
            "task_plan":            "",
            "primary_gap":          "",
            "tokens_input":         0,
            "tokens_output":        0,
            "tokens_total":         0,
            "estimated_cost_usd":   0.0,
            "started_at":           time.time(),
            "max_duration_seconds": 540,
        },
        "selected_mcp":       None,
        "current_action":     None,
        "latest_tool_result": None,
        "sources_skipped":    [],
        "evidence_ids":       [],
        "evidence_store":     {},
        "evaluation_ids":     [],
        "tool_history":       [],
        "errors":             [],
        "working_theory":     "",
        "final_summary":      None,
    }
