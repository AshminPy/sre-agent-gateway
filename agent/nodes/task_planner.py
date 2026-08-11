"""task_planner.py — decides what evidence is needed next."""
import logging
from agent.state import AgentState
from agent.llm import llm_json
from agent.prompts import TASK_PLANNER_SYSTEM, TASK_PLANNER_USER
from agent.otel import trace_node, log_node_tokens

log = logging.getLogger("sre-agent.task_planner")


def _evidence_digest(state: AgentState) -> str:
    store = state.get("evidence_store", {})
    if not store:
        return "No evidence collected yet."
    lines = []
    for ev_id, ev in store.items():
        lines.append(f"[{ev_id}] {ev.get('source','?')}: {ev.get('summary','')[:150]}")
        for f in ev.get("key_facts", [])[:4]:
            lines.append(f"  • {str(f)[:100]}")
    return "\n".join(lines)


@trace_node("langgraph.task_planner")
def task_planner(state: AgentState) -> dict:
    step = state["investigation"]["current_step"]
    log.info("node=task_planner step=%d run_id=%s", step, state["run_id"])

    ctx           = state.get("resolved_context", {})
    incident_type = ctx.get("incident_type", "Unknown")
    namespace     = ctx.get("namespace", "test-incidents")
    pod           = ctx.get("pod", "")
    gaps          = state["investigation"].get("evidence_gaps", [])
    theory        = state.get("working_theory", "none yet")
    memory_ctx    = state.get("incident_envelope", {}).get("memory_context", "")

    result, usage = llm_json(
        TASK_PLANNER_SYSTEM,
        TASK_PLANNER_USER.format(
            incident_type=incident_type,
            namespace=namespace,
            pod=pod or "not specified",
            memory_context=memory_ctx or "No past investigations on record for this cluster/namespace.",
            evidence_digest=_evidence_digest(state),
            evidence_gaps="\n".join(gaps) if gaps else "none identified yet",
            working_theory=theory,
        ),
        max_tokens=300,
    )
    log_node_tokens("task_planner", state["run_id"], step, usage)

    task_plan   = result.get("task_plan",   f"Investigate {incident_type} in {namespace}")
    primary_gap = result.get("primary_gap", "pod status unknown")

    log.info(
        "task_planner plan=%s gap=%s tokens=%d",
        task_plan[:80], primary_gap[:80], usage["total_tokens"],
    )

    from agent.llm.accounting import accumulate_usage

    return {
        "investigation": {
            "task_plan":   task_plan,
            "primary_gap": primary_gap,
            **accumulate_usage(state["investigation"], usage),
        },
    }
