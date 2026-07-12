"""task_evaluator.py — multi-signal confidence scoring."""
import logging
from agent.state import AgentState
from agent.gemini_client import llm_json
from agent.prompts import TASK_EVALUATOR_SYSTEM, TASK_EVALUATOR_USER
from agent.otel import trace_node, log_node_tokens

log = logging.getLogger("sre-agent.task_evaluator")


def _evidence_digest(state: AgentState) -> str:
    store = state.get("evidence_store", {})
    if not store:
        return "No evidence collected yet."
    lines = []
    for ev_id, ev in store.items():
        lines.append(f"[{ev_id}] {ev.get('summary','')[:150]}")
        for f in ev.get("key_facts", [])[:4]:
            lines.append(f"  • {f}")
    return "\n".join(lines)


@trace_node("langgraph.task_evaluator")
def task_evaluator(state: AgentState) -> dict:
    step      = state["investigation"]["current_step"]
    min_steps = state["investigation"].get("min_steps", 2)
    log.info("node=task_evaluator step=%d run_id=%s", step, state["run_id"])

    eval_id = f"eval_{(step + 1):03d}"
    ctx     = state.get("resolved_context", {})

    # Safety gate: never allow high confidence when no evidence exists.
    # This prevents the bad state seen in the test output:
    # "No evidence collected" + confidence=0.90 + human_review=false.
    if not state.get("evidence_ids"):
        tools_used = [h.get("tool") for h in state.get("tool_history", [])]
        gap = "no evidence extracted from tool output"
        if tools_used:
            gap = f"tool calls ran but no evidence was extracted: {', '.join(tools_used)}"
        log.warning("task_evaluator no evidence found; forcing confidence=0.0")
        return {
            "evaluation_ids": [eval_id],
            "investigation":  {
                "enough_evidence": False,
                "confidence":      0.0,
                "confidence_band": "escalate",
                "evidence_gaps":   [gap],
            },
            "working_theory": "No evidence available yet; RCA cannot be trusted.",
        }

    # Force continue until minimum steps done
    if step < min_steps and state["evidence_ids"]:
        log.info("task_evaluator forcing continue — %d/%d min steps", step, min_steps)
        return {
            "evaluation_ids": [eval_id],
            "investigation":  {
                "enough_evidence": False,
                "confidence":      0.2,
                "confidence_band": "escalate",
                "evidence_gaps":   ["minimum investigation steps not yet complete"],
            },
            "working_theory": f"Gathering evidence — {step}/{min_steps} minimum steps done",
        }

    tools_used = [h.get("tool") for h in state["tool_history"]]

    result, usage = llm_json(
        TASK_EVALUATOR_SYSTEM,
        TASK_EVALUATOR_USER.format(
            query=state["incident_envelope"].get("user_query", ""),
            incident_type=ctx.get("incident_type", "Unknown"),
            evidence_digest=_evidence_digest(state),
            tool_count=len(tools_used),
            tools_used=", ".join(tools_used),
            current_confidence=state["investigation"].get("confidence", 0.0),
        ),
        max_tokens=400,
    )
    log_node_tokens("task_evaluator", state["run_id"], step, usage)

    enough     = result.get("enough_evidence", False)
    try:
        confidence = float(result.get("confidence") or 0.0)
    except (ValueError, TypeError):
        confidence = 0.0
    theory     = result.get("working_theory", "")
    gaps       = result.get("evidence_gaps", [])

    # Deterministic coverage gate — prevent hallucinated high confidence.
    # LLM confidence cannot exceed what the evidence count supports.
    evidence_count   = len(state.get("evidence_ids", []))
    coverage_score   = min(1.0, evidence_count / 4.0)  # 4 items = full coverage (was 3 — raised for production)
    confidence_cap   = min(1.0, coverage_score + 0.25) # max 0.25 bonus above coverage
    if confidence > confidence_cap:
        log.warning(
            "task_evaluator: capping LLM confidence %.2f → %.2f (coverage_score=%.2f evidence=%d)",
            confidence, confidence_cap, coverage_score, evidence_count,
        )
        confidence = confidence_cap
        if not gaps:
            gaps = [f"Confidence capped: only {evidence_count}/4 required evidence items collected"]

    # Enforce confidence band thresholds
    if confidence >= 0.85:
        confidence_band = "auto"
    elif confidence >= 0.65:
        confidence_band = "review"
    else:
        confidence_band = "escalate"

    log.info(
        "task_evaluator enough=%s confidence=%.2f band=%s tokens=%d",
        enough, confidence, confidence_band, usage["tokens_total"],
    )

    current_tokens = state["investigation"].get("tokens_total", 0)
    current_cost   = state["investigation"].get("estimated_cost_usd", 0.0)

    return {
        "evaluation_ids": [eval_id],
        "investigation":  {
            "enough_evidence":    enough,
            "confidence":         confidence,
            "confidence_band":    confidence_band,
            "evidence_gaps":      gaps,
            "tokens_input":       state["investigation"].get("tokens_input", 0)  + usage["tokens_input"],
            "tokens_output":      state["investigation"].get("tokens_output", 0) + usage["tokens_output"],
            "tokens_total":       current_tokens + usage["tokens_total"],
            "estimated_cost_usd": round(current_cost + usage["cost_usd"], 6),
        },
        "working_theory": theory,
    }
