"""task_evaluator.py — loop-continuation judgment + deterministic investigation completeness.

The LLM judges whether enough evidence exists to stop looping (enough_evidence, working_theory,
evidence_gaps) — that operational decision is unchanged. It NO LONGER self-assigns a confidence
score: investigation_completeness is computed deterministically from AgentState by
agent.confidence.scorer, every call, using the versioned policy. Root-cause confidence (the
other axis) can't be computed here — it needs the claims rca_builder produces at the end of the
investigation, not before. See docs/confidence-framework-design.md.
"""
import logging

from agent.confidence import POLICY, score_investigation_completeness
from agent.gemini_client import llm_json
from agent.otel import log_node_tokens, trace_node
from agent.prompts import TASK_EVALUATOR_SYSTEM, TASK_EVALUATOR_USER
from agent.state import AgentState

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


def _completeness_update(state: AgentState) -> dict:
    """Computed every call — cheap, pure, deterministic. Stored for rca_builder to reuse."""
    completeness = score_investigation_completeness(state, POLICY)
    log.info(
        "task_evaluator investigation_completeness=%.2f band=%s gaps=%d",
        completeness["score"], completeness["band"], len(completeness["gaps"]),
    )
    return completeness


@trace_node("langgraph.task_evaluator")
def task_evaluator(state: AgentState) -> dict:
    step      = state["investigation"]["current_step"]
    min_steps = state["investigation"].get("min_steps", 2)
    log.info("node=task_evaluator step=%d run_id=%s", step, state["run_id"])

    eval_id = f"eval_{(step + 1):03d}"
    ctx     = state.get("resolved_context", {})
    completeness = _completeness_update(state)

    # Safety gate: never allow the loop to think it's done with zero evidence.
    if not state.get("evidence_ids"):
        tools_used = [h.get("tool") for h in state.get("tool_history", [])]
        gap = "no evidence extracted from tool output"
        if tools_used:
            gap = f"tool calls ran but no evidence was extracted: {', '.join(tools_used)}"
        log.warning("task_evaluator no evidence found; forcing enough_evidence=False")
        return {
            "evaluation_ids": [eval_id],
            "investigation":  {
                "enough_evidence": False,
                "completeness":    completeness,
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
                "completeness":    completeness,
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
        ),
        max_tokens=400,
    )
    log_node_tokens("task_evaluator", state["run_id"], step, usage)

    enough = result.get("enough_evidence", False)
    theory = result.get("working_theory", "")
    gaps   = result.get("evidence_gaps", [])

    log.info(
        "task_evaluator enough=%s completeness=%.2f tokens=%d",
        enough, completeness["score"], usage["tokens_total"],
    )

    current_tokens = state["investigation"].get("tokens_total", 0)
    current_cost   = state["investigation"].get("estimated_cost_usd", 0.0)

    return {
        "evaluation_ids": [eval_id],
        "investigation":  {
            "enough_evidence":    enough,
            "completeness":       completeness,
            "evidence_gaps":      gaps,
            "tokens_input":       state["investigation"].get("tokens_input", 0)  + usage["tokens_input"],
            "tokens_output":      state["investigation"].get("tokens_output", 0) + usage["tokens_output"],
            "tokens_total":       current_tokens + usage["tokens_total"],
            "estimated_cost_usd": round(current_cost + usage["cost_usd"], 6),
        },
        "working_theory": theory,
    }
