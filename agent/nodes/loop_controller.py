"""
Decides continue or exit using multi-signal production rules.
enough_evidence=True ALWAYS exits immediately.
"""
import logging
import os
import time
from agent.state import AgentState
from agent.otel import trace_node

log = logging.getLogger("sre-agent.loop_controller")

# Hard cap on tokens consumed per run. Set to 0 to disable.
# Prevents runaway spend from large log payloads on concurrent runs.
# Parse defensively — a bad env value must not crash agent startup at import.
try:
    _MAX_TOKENS = int(os.environ.get("MAX_TOKENS_PER_RUN", "100000"))
except (TypeError, ValueError):
    log.warning("MAX_TOKENS_PER_RUN is not a valid integer; defaulting to 100000")
    _MAX_TOKENS = 100000


def _is_stuck(state: AgentState) -> bool:
    """Stuck = same tool + same args called successfully twice in a row."""
    history = state.get("tool_history", [])
    if len(history) < 2:
        return False
    last     = history[-1]
    previous = history[-2]
    return (
        last.get("tool") == previous.get("tool") and
        last.get("args") == previous.get("args") and
        last.get("ok") and previous.get("ok")
    )


def _is_oscillating(state: AgentState) -> bool:
    """Oscillating = A→B→A→B pattern in last 4 successful tool calls."""
    ok_history = [h for h in state.get("tool_history", []) if h.get("ok")]
    if len(ok_history) < 4:
        return False
    a, b, c, d = ok_history[-4], ok_history[-3], ok_history[-2], ok_history[-1]
    return (
        a.get("tool") == c.get("tool") and
        b.get("tool") == d.get("tool") and
        a.get("tool") != b.get("tool")
    )


def _is_timed_out(state: AgentState) -> bool:
    """Timeout = wall-clock elapsed exceeds max_duration_seconds."""
    inv          = state["investigation"]
    started_at   = inv.get("started_at", 0)
    max_duration = inv.get("max_duration_seconds", 300)
    return started_at > 0 and (time.time() - started_at) > max_duration


def _consecutive_tool_failures(state: AgentState) -> bool:
    """Returns True if the last 2 tool calls both failed (ok=False).
    Exits with a truthful reason instead of running to max_steps on broken MCP.
    """
    history = state.get("tool_history", [])
    if len(history) < 2:
        return False
    return not history[-1].get("ok") and not history[-2].get("ok")


def _token_budget_exceeded(state: AgentState) -> bool:
    """Returns True when accumulated tokens exceed MAX_TOKENS_PER_RUN.
    Set MAX_TOKENS_PER_RUN=0 env var to disable. Default 100k tokens.
    """
    if _MAX_TOKENS <= 0:
        return False
    return state["investigation"].get("tokens_total", 0) > _MAX_TOKENS


def _zero_new_facts(state: AgentState) -> bool:
    """Zero new facts = last 2 consecutive SUCCESSFUL tools both returned empty key_facts.
    One empty result is allowed — some tools legitimately return nothing (e.g. no events).
    Two consecutive empty results means the agent is stuck in a dry zone.
    Failed tool evidence (ok=False) is excluded — a failure followed by an empty
    success should not trigger this exit.
    """
    ev_ids = state.get("evidence_ids", [])
    store  = state.get("evidence_store", {})
    ok_ev_ids = [eid for eid in ev_ids if store.get(eid, {}).get("ok", True)]
    if len(ok_ev_ids) < 2:
        return False
    last_two = ok_ev_ids[-2:]
    return all(
        len(store.get(ev_id, {}).get("key_facts", [])) == 0
        for ev_id in last_two
    )


@trace_node("langgraph.loop_controller")
def loop_controller(state: AgentState) -> dict:
    log.info("node=loop_controller run_id=%s", state["run_id"])

    step        = state["investigation"]["current_step"] + 1
    max_steps   = state["investigation"]["max_steps"]
    min_steps   = state["investigation"].get("min_steps", 2)
    enough      = state["investigation"]["enough_evidence"]
    tool_done   = state.get("current_action", {}).get("tool") == "done"
    stuck           = _is_stuck(state)
    oscillating     = _is_oscillating(state)
    timed_out       = _is_timed_out(state)
    zero_facts      = _zero_new_facts(state)
    consec_failures = _consecutive_tool_failures(state)
    over_budget     = _token_budget_exceeded(state)

    # ── Exit decision — order matters ─────────────────────────────
    exit_reason = None

    if enough:
        # Evaluator confirmed sufficient evidence — always exit
        exit_reason = "confidence_sufficient"

    elif timed_out:
        # Wall-clock timeout — always exit regardless of other signals
        exit_reason = "timeout"

    elif over_budget:
        # Token hard cap hit — exit before next iteration burns more
        exit_reason = "token_budget_exceeded"

    elif step >= max_steps:
        # Hard cap — always exit
        exit_reason = "max_iterations"

    elif tool_done and step >= min_steps:
        # Router said done AND minimum steps are complete — exit
        exit_reason = "tool_signaled_done"

    elif tool_done and step < min_steps:
        # Router said done BUT min_steps not met — CONTINUE
        log.info(
            "loop_controller: router said done but min_steps not met (%d/%d) — continuing",
            step, min_steps,
        )
        exit_reason = None  # keep running

    elif consec_failures and step >= min_steps:
        exit_reason = "consecutive_tool_failures"

    elif oscillating:
        exit_reason = "oscillation_detected"

    elif stuck:
        exit_reason = "stuck_detected"

    elif zero_facts:
        exit_reason = "zero_new_facts"

    done   = exit_reason is not None
    status = "done" if done else "running"

    log.info(
        "loop_controller step=%d/%d status=%s exit_reason=%s enough=%s",
        step, max_steps, status, exit_reason, enough,
    )

    updates: dict = {
        "investigation": {
            "current_step":     step,
            "status":           status,
            "loop_exit_reason": exit_reason,
        },
    }

    if exit_reason in ("stuck_detected", "zero_new_facts", "oscillation_detected", "timeout", "consecutive_tool_failures", "token_budget_exceeded"):
        updates["errors"] = [f"loop exited early: {exit_reason} at step {step}"]

    if step >= max_steps and not enough:
        updates["errors"] = updates.get("errors", []) + [
            f"max_steps={max_steps} reached without conclusive evidence"
        ]

    return updates
