"""Section 7 (2026-09-08): closes docs/management/confidence-genericity-review-2026-08-28.md
#15.2 -- rca_builder.py's incident_time_context dict was correctly designed but never
actually populated anywhere, so verifier.temporal_relevance could never be anything but
"unknown" in any real investigation, which per scorer.py's derive_outcome() gate meant the
CONFIRMED outcome was unreachable in production. These tests prove the real chain -- payload
-> _prepare_investigation_envelope -> input_normalizer -> resolved_context -- actually
carries a usable value end to end, not just that each function individually accepts one.
"""
from unittest.mock import patch

import agent.main as main_mod
import agent.nodes.input_normalizer as input_normalizer_mod
from agent.nodes.input_normalizer import input_normalizer


def test_caller_supplied_incident_reported_at_passes_through_unmodified():
    payload = {"query": "pod crashlooping", "incident_reported_at": "2026-09-08T10:00:00Z"}
    *_, envelope = main_mod._prepare_investigation_envelope(payload)

    assert envelope["incident"]["reported_at"] == "2026-09-08T10:00:00Z"
    assert envelope["incident"]["reported_at_approximate"] is False


def test_caller_supplied_incident_time_alias_also_accepted():
    """Accepts either incident_reported_at or the shorter incident_time -- both are
    documented as accepted, real callers may reasonably use either name."""
    payload = {"query": "pod crashlooping", "incident_time": "2026-09-08T10:00:00Z"}
    *_, envelope = main_mod._prepare_investigation_envelope(payload)

    assert envelope["incident"]["reported_at"] == "2026-09-08T10:00:00Z"
    assert envelope["incident"]["reported_at_approximate"] is False


def test_missing_incident_time_defaults_to_labeled_approximate_receipt_time():
    """The core fix: previously this was silently empty forever. Now it's a real,
    honestly-labeled anchor instead of nothing -- never presented as caller-confirmed."""
    payload = {"query": "pod crashlooping"}
    *_, envelope = main_mod._prepare_investigation_envelope(payload)

    assert envelope["incident"]["reported_at"]  # non-empty
    assert envelope["incident"]["reported_at_approximate"] is True
    assert "approximate" in envelope["incident"]["reported_at"].lower()


def test_incident_start_end_pass_through_when_caller_supplies_them():
    payload = {
        "query": "pod crashlooping",
        "incident_start": "2026-09-08T09:00:00Z",
        "incident_end": "2026-09-08T10:00:00Z",
    }
    *_, envelope = main_mod._prepare_investigation_envelope(payload)

    assert envelope["incident"]["start"] == "2026-09-08T09:00:00Z"
    assert envelope["incident"]["end"] == "2026-09-08T10:00:00Z"


def test_incident_start_end_empty_when_caller_omits_them():
    """No reasonable default exists for a WINDOW (unlike a single anchor point) -- must
    stay empty, never fabricated, when the caller doesn't supply one."""
    payload = {"query": "pod crashlooping"}
    *_, envelope = main_mod._prepare_investigation_envelope(payload)

    assert envelope["incident"]["start"] == ""
    assert envelope["incident"]["end"] == ""


def _mock_llm_json(*a, **k):
    return (
        {"incident_type": "CrashLoopBackOff", "namespace": "", "pod": "", "deployment": "", "cluster_name": ""},
        {"input_tokens": 5, "cached_input_tokens": 0, "output_tokens": 5, "reasoning_tokens": 0,
         "tool_tokens": 0, "total_tokens": 10, "billable_output_tokens": 5, "cost_usd": 0.0,
         "provider": "gemini", "model": "gemini-2.5-flash", "duration_s": 0.1},
    )


def test_input_normalizer_threads_incident_time_fields_into_resolved_context(monkeypatch):
    """The second link: input_normalizer must carry these fields from the envelope into
    resolved_context, unchanged -- this is what rca_builder.py's ctx.get(...) calls read."""
    monkeypatch.setattr(input_normalizer_mod, "llm_json", _mock_llm_json)
    state = {
        "run_id": "run_test",
        "investigation": {"current_step": 0},
        "incident_envelope": {
            "user_query": "pod crashlooping",
            "resource_hints": {},
            "incident": {
                "severity": "high",
                "reported_at": "2026-09-08T10:00:00Z",
                "reported_at_approximate": False,
                "start": "2026-09-08T09:00:00Z",
                "end": "2026-09-08T10:00:00Z",
            },
        },
    }

    result = input_normalizer(state)

    ctx = result["resolved_context"]
    assert ctx["incident_reported_at"] == "2026-09-08T10:00:00Z"
    assert ctx["incident_reported_at_approximate"] is False
    assert ctx["incident_start"] == "2026-09-08T09:00:00Z"
    assert ctx["incident_end"] == "2026-09-08T10:00:00Z"


def test_end_to_end_payload_to_resolved_context_carries_a_real_anchor(monkeypatch):
    """Full chain, no caller-supplied time: proves a real investigation (not a
    hand-constructed test state) ends up with a non-empty incident_reported_at reaching
    resolved_context -- the exact field rca_builder.py's incident_time_context construction
    reads. Before this fix, this field was empty at every layer, always, in every real run."""
    monkeypatch.setattr(input_normalizer_mod, "llm_json", _mock_llm_json)
    with patch("agent.llm.reset_session"):
        *_, envelope = main_mod._prepare_investigation_envelope({"query": "pod crashlooping"})

    state = {
        "run_id": "run_test",
        "investigation": {"current_step": 0},
        "incident_envelope": envelope,
    }
    result = input_normalizer(state)

    ctx = result["resolved_context"]
    assert ctx["incident_reported_at"]  # non-empty -- this is the fix
    assert ctx["incident_reported_at_approximate"] is True
