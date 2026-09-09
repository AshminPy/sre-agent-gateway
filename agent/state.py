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
    # set by input_normalizer (pre-resolution, never a default/guess):
    #           cluster_hint (verified — from caller/alert resource_hints),
    #           cluster_guess (unverified — LLM free-text extraction),
    #           project_hint, environment_hint
    # set by context_resolver (deterministic routing — see
    #   agent/mcp_client.py:resolve_cluster_routing() and PRODUCTION-LAUNCH-PLAN.md
    #   Priority 5 — never defaults/guesses a cluster; unresolved -> safe-stop):
    #           cluster_explicitly_provided, cluster_routing_method
    #           ("exact_id" | "verified_alert_metadata" | "approved_alias" |
    #           "project_env_namespace" | "unresolved"), cluster_routing_reason

    # ── Investigation control ─────────────────────────────────────
    investigation:      Annotated[Dict[str, Any], operator.or_]
    # includes: status, current_step, max_steps, min_steps,
    #           enough_evidence, confidence, confidence_band (legacy, LEGACY-COMPATIBLE —
    #           see agent/confidence — final value set by rca_builder from
    #           root_cause_confidence, not by task_evaluator),
    #           completeness (new — investigation_completeness dict, set every
    #           task_evaluator call, see agent/confidence/scorer.py),
    #           loop_exit_reason (includes "cluster_unresolved" — set by context_resolver
    #           on a routing safe-stop; graph.py routes status=="failed" straight to
    #           rca_builder, skipping task_planner/mcp_router/tool_executor entirely),
    #           evidence_gaps, task_plan,
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
    # { ev_001: { facts, summary, raw_ref, tool, mcp_source, cluster } }

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
            # Wall-clock epoch above is for logs/reporting (rca_builder.py's
            # total_latency_s, main.py's latency_ms) -- never used for elapsed-time
            # decisions. This graph has no checkpointer (agent/graph.py's g.compile()
            # takes no checkpointer arg) so an investigation never resumes in a
            # different process; time.monotonic() is safe for its whole lifetime and
            # immune to wall-clock jumps (NTP adjustment, DST, manual clock changes)
            # that would otherwise corrupt loop_controller.py's budget/timeout checks.
            "started_at_monotonic": time.monotonic(),
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


def usable_evidence_ids(state: dict) -> list:
    """Evidence IDs that actually carry data — the ONLY correct way to ask
    "does this investigation have evidence?".

    2026-08-27. `evidence_ids` counts SLOTS, not evidence. evidence_extractor
    appends an ev_id for a FAILED tool call and for a FAILED extraction too, so
    `if state["evidence_ids"]` is true even when every single call failed and
    nothing was retrieved. Several decision points were written that way and all
    of them silently passed on a run with zero real evidence:

        rca_builder      no-evidence safety gate  (would write an RCA from failures)
        task_evaluator   zero-evidence safety gate (would ask "is this enough?")
        task_evaluator   min-steps gate
        mcp_router       evidence_count in the model's prompt (over-stated)
        report renderer  Evidence section + impact-assessment gating

    scorer.py already had the right idea for domain coverage (issue #91's
    `if not ev.get("ok", True): continue`); this makes that one definition
    reusable instead of re-derived, so the next new call site cannot get it
    wrong by writing the obvious-but-incorrect `if evidence_ids:`.

    An entry with no explicit `ok` is treated as usable, matching issue #91 --
    absence of the flag means "written before the flag existed", not "failed".
    """
    store = state.get("evidence_store", {}) or {}
    return [
        ev_id for ev_id in (state.get("evidence_ids", []) or [])
        if store.get(ev_id, {}).get("ok", True)
    ]
