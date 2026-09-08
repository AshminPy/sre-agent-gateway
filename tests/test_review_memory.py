"""Section 8 (2026-09-08): the smallest supported human-review operation for Memory
Bank records. Tests the pure fact-string logic directly -- _change_status()'s live
Memory Bank calls (get/delete/create) are exercised via a fake client, never real GCP.
"""
from unittest.mock import MagicMock

from scripts.review_memory import _parse_fact, _rebuild_fact, _change_status


def test_parse_fact_matches_agent_main_behavior():
    fact = "cluster=sre-lab namespace=test-incidents pod=checkout-abc status=pending_review run_id=run_001"
    parsed = _parse_fact(fact)
    assert parsed["cluster"] == "sre-lab"
    assert parsed["status"] == "pending_review"
    assert parsed["run_id"] == "run_001"


def test_rebuild_fact_replaces_existing_status_preserving_other_fields():
    fact = "cluster=sre-lab pod=x status=pending_review run_id=run_001 confidence=0.90"
    new_fact = _rebuild_fact(fact, "approved")
    parsed = _parse_fact(new_fact)
    assert parsed["status"] == "approved"
    assert parsed["cluster"] == "sre-lab"
    assert parsed["run_id"] == "run_001"
    assert parsed["confidence"] == "0.90"
    assert "reviewed_by" in parsed


def test_rebuild_fact_appends_status_when_missing_pre_section_8_record():
    """A memory written before this fix has no status= field at all -- must not crash,
    must add one rather than requiring a re.sub match that won't find anything."""
    fact = "cluster=sre-lab pod=x incident_type=OOMKilled root_cause=foo confidence=0.90"
    new_fact = _rebuild_fact(fact, "approved")
    parsed = _parse_fact(new_fact)
    assert parsed["status"] == "approved"
    assert parsed["cluster"] == "sre-lab"


def test_rebuild_fact_includes_reason_when_given():
    fact = "cluster=sre-lab pod=x status=pending_review"
    new_fact = _rebuild_fact(fact, "rejected", reason="root cause was wrong, config was actually fine")
    assert "reason=root_cause_was_wrong,_config_was_actually_fine" in new_fact


def test_change_status_deletes_old_and_creates_new_with_updated_status(monkeypatch):
    fake_memory = MagicMock()
    fake_memory.fact = "cluster=sre-lab pod=x status=pending_review run_id=run_001"
    fake_memory.scope = {"cluster": "sre-lab", "namespace": "test-incidents"}

    fake_client = MagicMock()
    fake_client.agent_engines.memories.get.return_value = fake_memory

    monkeypatch.setattr(
        "scripts.review_memory._client_and_resource",
        lambda: (fake_client, "reasoningEngines/123"),
    )

    args = MagicMock(name="args_approve")
    args.name = "reasoningEngines/123/memories/456"
    args.reason = ""

    _change_status(args, "approved")

    fake_client.agent_engines.memories.delete.assert_called_once_with(name="reasoningEngines/123/memories/456")
    create_call = fake_client.agent_engines.memories.create.call_args
    assert create_call.kwargs["name"] == "reasoningEngines/123"
    assert "status=approved" in create_call.kwargs["fact"]
    assert create_call.kwargs["scope"] == {"cluster": "sre-lab", "namespace": "test-incidents"}
