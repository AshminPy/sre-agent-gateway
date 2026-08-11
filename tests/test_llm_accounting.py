"""Regression tests for accumulate_usage() (issue #63 PR 1).

The core bug: rca_builder.py (the final LangGraph node) folded its own LLM
call into tokens_total/estimated_cost_usd but never re-set tokens_input/
tokens_output — because AgentState.investigation is a shallow dict merge
(operator.or_), that left tokens_input/tokens_output stuck at the
second-to-last node's value, silently missing the final node's own call.
accumulate_usage() always returns the complete field set so this can't
recur, regardless of which node calls it last.
"""
from agent.llm.accounting import accumulate_usage
from agent.llm.base import LLMUsage


def _usage(**overrides) -> LLMUsage:
    base = LLMUsage(
        input_tokens=0, cached_input_tokens=0, output_tokens=0, reasoning_tokens=0,
        tool_tokens=0, total_tokens=0, billable_output_tokens=0, cost_usd=0.0,
        provider="gemini", model="gemini-2.5-pro", duration_s=0.0,
    )
    base.update(overrides)
    return base


def test_first_call_starts_from_zero_investigation():
    result = accumulate_usage({}, _usage(
        input_tokens=100, output_tokens=50, reasoning_tokens=20,
        billable_output_tokens=70, total_tokens=170, cost_usd=0.001,
    ))
    assert result["tokens_input"] == 100
    assert result["tokens_output"] == 70
    assert result["tokens_candidates"] == 50
    assert result["tokens_reasoning"] == 20
    assert result["tokens_total"] == 170
    assert result["estimated_cost_usd"] == 0.001


def test_accumulates_across_multiple_calls_every_field():
    inv = accumulate_usage({}, _usage(
        input_tokens=100, output_tokens=50, reasoning_tokens=20,
        billable_output_tokens=70, total_tokens=170, cost_usd=0.001,
    ))
    inv = accumulate_usage(inv, _usage(
        input_tokens=200, output_tokens=30, reasoning_tokens=10,
        billable_output_tokens=40, total_tokens=240, cost_usd=0.002,
    ))
    assert inv["tokens_input"] == 300
    assert inv["tokens_output"] == 110
    assert inv["tokens_candidates"] == 80
    assert inv["tokens_reasoning"] == 30
    assert inv["tokens_total"] == 410
    assert round(inv["estimated_cost_usd"], 6) == 0.003


def test_the_final_node_regression_every_call_returns_the_complete_field_set():
    """Simulates the exact bug: N nodes each call accumulate_usage and return its
    FULL output (never a partial subset) into a dict that's shallow-merged the
    same way AgentState.investigation is (operator.or_ semantics: last writer's
    keys win, other keys carry over unchanged). Proves tokens_input/tokens_output
    stay correct even after the "final" call, because accumulate_usage never
    omits them."""
    investigation: dict = {}

    # Node 1
    investigation = {**investigation, **accumulate_usage(investigation, _usage(
        input_tokens=50, output_tokens=20, billable_output_tokens=20, total_tokens=70, cost_usd=0.0005,
    ))}
    # Node 2 (the "final" node — the old bug returned only a subset here). total_tokens
    # is deliberately kept self-consistent with input+billable_output in THIS fixture
    # (unlike test_llm_gemini_adapter.py's dedicated test for a provider total that
    # legitimately diverges) so the reconciliation assertion below isolates exactly the
    # regression this test targets, not that separate behavior.
    investigation = {**investigation, **accumulate_usage(investigation, _usage(
        input_tokens=30, output_tokens=15, reasoning_tokens=25,
        billable_output_tokens=40, total_tokens=70, cost_usd=0.0009,
    ))}

    assert investigation["tokens_input"] == 80
    assert investigation["tokens_output"] == 60
    assert investigation["tokens_total"] == 140
    # The regression check: input + output must reconcile with total when every
    # call's own total_tokens is itself internally consistent (as in this test's
    # fixtures) — proving nothing silently dropped between calls.
    assert investigation["tokens_input"] + investigation["tokens_output"] == investigation["tokens_total"]
