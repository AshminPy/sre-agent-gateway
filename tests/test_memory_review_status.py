"""Section 8 (2026-09-08): _mb_store() now persists a real status/run_id/policy_version
field (ADR-010's "decided but not built" gap), and _mb_recall() only returns
status=approved memories. Tests both directly against a fake Memory Bank client --
no real GCP call.
"""
from unittest.mock import MagicMock

import agent.main as main_mod
from agent.main import SREAgent, MEMORY_RECALL_UNAVAILABLE


def _fake_memory(fact: str, name: str = "reasoningEngines/1/memories/1"):
    m = MagicMock()
    m.memory.fact = fact
    m.memory.name = name
    return m


def _patch_mb(monkeypatch, retrieve_return):
    fake_client = MagicMock()
    fake_client.agent_engines.memories.retrieve.return_value = retrieve_return
    monkeypatch.setattr(SREAgent, "_mb_client", fake_client)
    monkeypatch.setattr(main_mod, "MEMORY_BANK_RESOURCE", "reasoningEngines/1")
    return fake_client


def test_mb_store_writes_status_pending_review_run_id_and_policy_version(monkeypatch):
    fake_client = _patch_mb(monkeypatch, [])  # no existing memories -> not a dedup skip
    SREAgent._mb_store("sre-lab", "test-incidents", "checkout-abc", "OOMKilled", 0.9, "OOMKilled", run_id="run_042")

    create_call = fake_client.agent_engines.memories.create.call_args
    fact = create_call.kwargs["fact"]
    assert "status=pending_review" in fact
    assert "run_id=run_042" in fact
    assert "policy_version=" in fact


def test_mb_recall_only_returns_approved_memories(monkeypatch):
    _patch_mb(monkeypatch, [
        _fake_memory("cluster=sre-lab pod=a status=pending_review root_cause=foo"),
        _fake_memory("cluster=sre-lab pod=b status=approved root_cause=bar confidence=0.9"),
        _fake_memory("cluster=sre-lab pod=c status=rejected root_cause=baz"),
    ])
    result = SREAgent._mb_recall("sre-lab", "test-incidents")
    assert "bar" in result
    assert "foo" not in result
    assert "baz" not in result


def test_mb_recall_all_pending_returns_honest_note_not_a_false_no_incidents_claim(monkeypatch):
    """The core correctness fix: returning "" here would render downstream as 'No prior
    similar incidents found' -- false when records exist and are simply unreviewed."""
    _patch_mb(monkeypatch, [
        _fake_memory("cluster=sre-lab pod=a status=pending_review root_cause=foo"),
    ])
    result = SREAgent._mb_recall("sre-lab", "test-incidents")
    assert result != ""
    assert result != MEMORY_RECALL_UNAVAILABLE
    assert "pending human review" in result
    assert "1" in result


def test_mb_recall_genuinely_zero_memories_returns_true_empty(monkeypatch):
    _patch_mb(monkeypatch, [])
    result = SREAgent._mb_recall("sre-lab", "test-incidents")
    assert result == ""


def test_mb_recall_pre_section_8_memory_with_no_status_field_is_excluded(monkeypatch):
    """A memory written before this fix has no status= field at all -- must be treated
    as unapproved (nothing was ever approved through a real process), not as trusted
    by default."""
    _patch_mb(monkeypatch, [
        _fake_memory("cluster=sre-lab pod=a incident_type=OOMKilled root_cause=old_memory confidence=0.9"),
    ])
    result = SREAgent._mb_recall("sre-lab", "test-incidents")
    assert "old_memory" not in result
    assert "pending human review" in result
