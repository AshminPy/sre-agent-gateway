"""
agent/llm/accounting.py — one shared helper for folding an LLM call's usage
into an investigation's running totals.

Issue #63 root cause: every LangGraph node duplicated this accumulation
inline, and the final node (rca_builder) only updated tokens_total/
estimated_cost_usd while forgetting tokens_input/tokens_output — because
AgentState.investigation is a shallow dict merge (operator.or_, see
agent/state.py), a node that returns a partial subset of these keys leaves
the others stale. accumulate_usage() always returns the COMPLETE set, every
call, so that failure mode can't recur regardless of which node calls it
last.
"""
from __future__ import annotations

from agent.llm.base import LLMUsage


def accumulate_usage(investigation: dict, usage: LLMUsage) -> dict:
    """Returns the full updated token/cost field set for an investigation dict,
    folding in one more LLM call's usage. Always returns every field — never a
    partial subset — so LangGraph's shallow dict-merge on AgentState.investigation
    can never leave a stale sub-field behind.

    tokens_output accumulates billable_output_tokens (candidates + reasoning —
    what actually bills at the output rate), not output_tokens alone.
    tokens_candidates/tokens_reasoning preserve the breakdown separately.
    tokens_total accumulates the provider's own reported total per call, never
    a local input+output recomputation.
    """
    return {
        "tokens_input": investigation.get("tokens_input", 0) + usage["input_tokens"],
        "tokens_cached_input": investigation.get("tokens_cached_input", 0) + usage["cached_input_tokens"],
        "tokens_output": investigation.get("tokens_output", 0) + usage["billable_output_tokens"],
        "tokens_candidates": investigation.get("tokens_candidates", 0) + usage["output_tokens"],
        "tokens_reasoning": investigation.get("tokens_reasoning", 0) + usage["reasoning_tokens"],
        "tokens_tool_use": investigation.get("tokens_tool_use", 0) + usage["tool_tokens"],
        "tokens_total": investigation.get("tokens_total", 0) + usage["total_tokens"],
        "estimated_cost_usd": round(
            investigation.get("estimated_cost_usd", 0.0) + usage["cost_usd"], 6
        ),
    }
