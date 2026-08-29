"""Regression test, 2026-08-29: the deterministic missing-evidence-domain signal (computed by
score_investigation_completeness, stored at investigation["completeness"]["missing_required_domains"])
used to never reach task_planner's prompt -- loop_controller forces a retry off it, but the
planner only ever saw evidence_gaps, the LLM's own free-text field, which carries no guarantee
of naming the same domain.

Real repro documented in docs/management/confidence-genericity-review-2026-08-28.md #15.3: a
forced retry on a real oomkilled-001-shaped state (LLM's own evidence_gaps empty, deterministic
missing_required_domains=["previous_logs"]) sent a planner prompt that never mentioned
previous_logs at all -- the extra iteration ran blind.
"""
import agent.nodes.task_planner as task_planner_mod
from agent.nodes.task_planner import task_planner

USAGE = {
    "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
    "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
    "billable_output_tokens": 5, "cost_usd": 0.0,
    "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
}


def _capture(store):
    def _fn(system, user, **kwargs):
        store["user"] = user
        return {"task_plan": "collect previous logs", "primary_gap": "previous_logs"}, USAGE
    return _fn


def test_deterministic_missing_domain_reaches_the_planner_prompt(monkeypatch):
    captured = {}
    monkeypatch.setattr(task_planner_mod, "llm_json", _capture(captured))

    state = {
        "run_id": "run_test",
        "resolved_context": {
            "incident_type": "OOMKilled", "namespace": "test-incidents", "pod": "oomkilled-pod",
        },
        "investigation": {
            "current_step": 2,
            "evidence_gaps": [],  # the LLM's own field, empty -- matches the real repro
            "completeness": {"missing_required_domains": ["previous_logs"]},
        },
        "working_theory": "OOMKilled",
        "incident_envelope": {"user_query": "pod oomkilled-pod is OOMKilled repeatedly"},
        "evidence_store": {},
    }
    task_planner(state)

    assert "previous_logs" in captured["user"], (
        "the deterministically-confirmed missing domain must reach the planner prompt even "
        "when the LLM's own evidence_gaps field is empty"
    )


def test_no_missing_domains_renders_as_none_not_empty_string(monkeypatch):
    captured = {}
    monkeypatch.setattr(task_planner_mod, "llm_json", _capture(captured))

    state = {
        "run_id": "run_test",
        "resolved_context": {"incident_type": "Unknown", "namespace": "test-incidents", "pod": ""},
        "investigation": {"current_step": 0, "evidence_gaps": [], "completeness": {}},
        "working_theory": "none yet",
        "incident_envelope": {"user_query": "x"},
        "evidence_store": {},
    }
    task_planner(state)

    assert "Deterministically confirmed missing evidence domain(s)" in captured["user"]
    assert "none" in captured["user"]
