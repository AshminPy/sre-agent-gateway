"""
Decides continue or exit using multi-signal production rules.
enough_evidence=True exits immediately UNLESS the deterministic completeness check
(computed from real AgentState, not the LLM's self-report) finds a required evidence
domain still missing and iteration budget remains (issue #69) -- in that case, one
more iteration is forced before enough_evidence can end the loop.
"""
import logging
import os
import time
from agent.state import AgentState
from agent.otel import trace_node

log = logging.getLogger("sre-agent.loop_controller")

# Hard cap on tokens consumed per run. Set to 0 to disable.
# Prevents runaway spend from large log payloads on concurrent runs.
# Terraform-sourced since issue #63 (iac/agent/variables.tf's max_tokens_per_run ->
# agent_engine.tf's MAX_TOKENS_PER_RUN) -- the "100000" default below only applies if
# that env var is ever unset, matching var.max_tokens_per_run's own default exactly so
# this was never a behavior change, only a source-of-truth move.
# Parse defensively — a bad env value must not crash agent startup at import.
try:
    _MAX_TOKENS = int(os.environ.get("MAX_TOKENS_PER_RUN", "100000"))
except (TypeError, ValueError):
    log.warning("MAX_TOKENS_PER_RUN is not a valid integer; defaulting to 100000")
    _MAX_TOKENS = 100000

# Admission control for starting another expensive round (task_planner ->
# mcp_router -> tool_executor -> evidence_extractor -> task_evaluator, each of
# which makes its own Gemini call). This is NOT max_duration_seconds (state.py,
# 540s) -- that stays an unrelated, unchanged, already-documented hard cap
# (see iac/agent/monitoring.tf's alert comments). This is a smaller, separate
# budget that exists to stay clear of the MANAGED Vertex AI Agent Engine
# request/stream boundary (observed ~300-300.6s server-side, issue #103) --
# not a local client deadline. It cannot interrupt a call already in flight;
# it only decides whether to start the NEXT one, so it is checked here
# between iterations, the same place _is_timed_out() already runs.
# Default AND max are both 200s -- an env override may only LOWER the budget,
# never raise it. The worst observed rca_builder duration under degraded
# conditions was 67.5s: 200 + 67.5 = 267.5s already leaves only ~33s of
# margin under the ~300s managed boundary, so 200 is the ceiling, not a
# starting point. A misconfigured value above 200 must not silently widen
# that margin away.
_SAFETY_BUDGET_DEFAULT = 200
_SAFETY_BUDGET_MAX     = 200
try:
    _SAFETY_BUDGET_SECONDS = int(os.environ.get("SAFETY_BUDGET_SECONDS", str(_SAFETY_BUDGET_DEFAULT)))
    if _SAFETY_BUDGET_SECONDS <= 0:
        log.warning(
            "SAFETY_BUDGET_SECONDS must be positive; defaulting to %ds", _SAFETY_BUDGET_DEFAULT
        )
        _SAFETY_BUDGET_SECONDS = _SAFETY_BUDGET_DEFAULT
    elif _SAFETY_BUDGET_SECONDS > _SAFETY_BUDGET_MAX:
        log.warning(
            "SAFETY_BUDGET_SECONDS=%d exceeds the %ds cap (would risk the managed "
            "~300s stream boundary); capping to %ds",
            _SAFETY_BUDGET_SECONDS, _SAFETY_BUDGET_MAX, _SAFETY_BUDGET_MAX,
        )
        _SAFETY_BUDGET_SECONDS = _SAFETY_BUDGET_MAX
except (TypeError, ValueError):
    log.warning(
        "SAFETY_BUDGET_SECONDS is not a valid integer; defaulting to %ds", _SAFETY_BUDGET_DEFAULT
    )
    _SAFETY_BUDGET_SECONDS = _SAFETY_BUDGET_DEFAULT


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


def _is_safety_budget_exceeded(state: AgentState) -> bool:
    """True once wall-clock elapsed exceeds SAFETY_BUDGET_SECONDS.

    Distinct from _is_timed_out()'s max_duration_seconds (540s, unchanged) --
    this is a smaller, earlier check meant to stop the loop from starting
    another expensive round (a full task_planner -> ... -> task_evaluator
    pass) when there is not enough headroom left before the managed Vertex AI
    Agent Engine request/stream boundary (issue #103). It does not, and
    cannot, interrupt a single call already in progress.
    """
    inv        = state["investigation"]
    started_at = inv.get("started_at", 0)
    return started_at > 0 and (time.time() - started_at) > _SAFETY_BUDGET_SECONDS


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
    safety_budget_exceeded = _is_safety_budget_exceeded(state)
    zero_facts      = _zero_new_facts(state)
    consec_failures = _consecutive_tool_failures(state)
    over_budget     = _token_budget_exceeded(state)

    # ── Exit decision — order matters ─────────────────────────────
    exit_reason = None
    missing_domains = state["investigation"].get("completeness", {}).get("missing_required_domains", [])

    if enough and missing_domains and step < max_steps:
        # issue #69: enough_evidence is the LLM's own self-report -- it was previously
        # honored unconditionally, even when task_evaluator's deterministic completeness
        # check (computed in the SAME call, from real AgentState) found required evidence
        # domains still missing. Real E2E proof: the agent's own evidence_gaps named a
        # specific unresolved gap, yet the loop still exited with confidence_sufficient.
        # Force one more iteration instead, as long as iteration budget remains.
        log.info(
            "loop_controller: evaluator said enough_evidence but completeness reports "
            "missing required domain(s) %s -- forcing one more iteration (%d/%d steps)",
            missing_domains, step, max_steps,
        )

    elif enough:
        # Evaluator confirmed sufficient evidence, and either no required domains are
        # missing or no iteration budget remains to chase them further — exit.
        exit_reason = "confidence_sufficient"

    elif safety_budget_exceeded:
        # Not enough headroom left before the managed ~300s Vertex AI Agent
        # Engine stream boundary (issue #103) — stop before starting another
        # expensive round rather than risk a raw stream-timeout with no
        # output. Checked before the 540s hard cap since it is meant to fire
        # earlier and more conservatively.
        exit_reason = "safety_budget_exceeded"

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

    # 2026-08-27: an upstream node in this SAME iteration can already have
    # declared the run failed (mcp_router when it cannot plan a tool call;
    # context_resolver when a cluster cannot be resolved). `investigation` merges
    # with operator.or_ and loop_controller runs LAST, so unconditionally writing
    # "done"/"running" here silently overwrote that verdict -- turning a real
    # failure into a normal completion with loop_exit_reason="tool_signaled_done".
    #
    # graph.py already documents this exact hazard for context_resolver and works
    # around it with a conditional edge that skips the loop entirely. mcp_router
    # has no such edge (it must still reach rca_builder through the loop), so the
    # guard belongs here.
    upstream_failed = state["investigation"].get("status") == "failed"
    if upstream_failed:
        status = "failed"
        exit_reason = state["investigation"].get("loop_exit_reason") or exit_reason
        done = True
        log.error(
            "loop_controller: preserving upstream FAILED status (exit_reason=%s) at "
            "step %d -- not overwriting it with a normal completion",
            exit_reason, step,
        )
    else:
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

    if exit_reason in ("stuck_detected", "zero_new_facts", "oscillation_detected", "timeout", "consecutive_tool_failures", "token_budget_exceeded", "safety_budget_exceeded"):
        updates["errors"] = [f"loop exited early: {exit_reason} at step {step}"]

    if step >= max_steps and not enough:
        updates["errors"] = updates.get("errors", []) + [
            f"max_steps={max_steps} reached without conclusive evidence"
        ]

    return updates
