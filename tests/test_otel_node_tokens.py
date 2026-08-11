"""Regression test for log_node_tokens() reading the OLD usage schema
(tokens_input/tokens_output/tokens_total) after agent/llm's LLMUsage schema
(input_tokens/billable_output_tokens/total_tokens, see agent/llm/base.py)
replaced it -- every per-node token log line was silently reporting all
zeros, since usage.get("tokens_input", 0) etc. never matched any real key.
Found reviewing issue #63 PR 1, 2026-08-11.
"""
import json

from agent.otel import log_node_tokens


def _real_llm_usage(**overrides) -> dict:
    """A real-shaped LLMUsage dict, as agent.llm.llm_json() actually returns."""
    base = dict(
        input_tokens=49,
        cached_input_tokens=5,
        output_tokens=72,
        reasoning_tokens=93,
        tool_tokens=3,
        total_tokens=214,
        billable_output_tokens=165,  # 72 + 93
        cost_usd=0.001,
        provider="gemini",
        model="gemini-2.5-pro",
        duration_s=1.2,
    )
    base.update(overrides)
    return base


def test_log_node_tokens_reads_the_normalized_llmusage_schema_not_zero(capsys):
    log_node_tokens("rca_builder", "run_test_001", 3, _real_llm_usage())
    line = capsys.readouterr().out.strip()
    event = json.loads(line)

    assert event["event_type"] == "node_token_usage"
    assert event["node"] == "rca_builder"
    assert event["run_id"] == "run_test_001"
    assert event["step"] == 3

    # The actual regression: none of these may silently read as 0 when the
    # real usage dict clearly has non-zero values.
    assert event["tokens_input"] == 49
    assert event["tokens_output"] == 165, "must be billable_output_tokens (candidates+reasoning), not 0"
    assert event["tokens_total"] == 214


def test_log_node_tokens_covers_reasoning_cached_and_tool_use_fields(capsys):
    """Explicit coverage for the granular fields, not just input/output/total."""
    log_node_tokens("mcp_router", "run_test_002", 1, _real_llm_usage(
        cached_input_tokens=17, reasoning_tokens=40, tool_tokens=8,
        output_tokens=20, billable_output_tokens=60, total_tokens=100, input_tokens=25,
    ))
    event = json.loads(capsys.readouterr().out.strip())

    assert event["tokens_cached_input"] == 17
    assert event["tokens_reasoning"] == 40
    assert event["tokens_tool_use"] == 8
    assert event["tokens_candidates"] == 20
    assert event["tokens_total"] == 100


def test_log_node_tokens_never_crashes_on_a_missing_field(capsys):
    """Defensive: a malformed/partial usage dict should degrade to 0 per field,
    not raise -- log_node_tokens is a side-effect-only observability call and
    must never break the investigation it's reporting on."""
    log_node_tokens("task_planner", "run_test_003", 0, {})
    event = json.loads(capsys.readouterr().out.strip())

    assert event["tokens_input"] == 0
    assert event["tokens_output"] == 0
    assert event["tokens_reasoning"] == 0
    assert event["tokens_cached_input"] == 0
    assert event["tokens_tool_use"] == 0
    assert event["tokens_total"] == 0
