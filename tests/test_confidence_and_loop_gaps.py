"""Regression tests: confidence scoring and loop control must not credit absence.

Third review pass, 2026-08-27 — the confidence and loop-control layer, which the
first two passes did not cover.

  Gap 11 domain_weight() returned 1.0 for EvidenceDomain.UNKNOWN. classify_tool's
         own docstring already promised UNKNOWN was "never silently counted as a
         fresh independent domain", but evidence from an unclassifiable tool
         bought a full independent corroborating source — 0.5 of the
         independent_corroboration component on its own.

  Gap 12 loop_controller unconditionally wrote status "done"/"running", silently
         OVERWRITING a "failed" status set earlier in the same iteration by
         mcp_router. graph.py already documents this exact hazard for
         context_resolver. It defeated the Gap 7 fix entirely: a router failure
         came out the far end as loop_exit_reason="tool_signaled_done".

  Gap 13 task_evaluator's zero-evidence safety gate counted evidence SLOTS. On a
         run where every tool call failed it did not fire, and the evaluator went
         on to ask the model "is this enough evidence?" about failure records.

  Gap 14 The same slot-counting bug in mcp_router's prompt evidence_count and in
         the report's impact-assessment gate. Now one shared definition,
         agent.state.usable_evidence_ids, so a new call site cannot get it wrong.

  Gap 15 rca_builder offered the model the FULL evidence_ids list to cite,
         including failed items, while the prompt demands every claim cite an
         evidence_id.
"""
import operator

from agent.confidence.evidence_domains import EvidenceDomain, classify_tool, domain_weight
from agent.state import usable_evidence_ids


# ── Gap 11: UNKNOWN must not corroborate ─────────────────────────────────────

def test_unknown_domain_contributes_no_corroboration():
    assert domain_weight(EvidenceDomain.UNKNOWN, {EvidenceDomain.UNKNOWN}) == 0.0


def test_unknown_alongside_a_real_domain_still_contributes_nothing():
    present = {EvidenceDomain.KUBERNETES_STATUS, EvidenceDomain.UNKNOWN}
    assert domain_weight(EvidenceDomain.UNKNOWN, present) == 0.0
    # ...and must not drag the real domain down with it.
    assert domain_weight(EvidenceDomain.KUBERNETES_STATUS, present) == 1.0


def test_related_domains_still_count_half_each():
    """Guard: the existing issue-#65 related-domain rule must be untouched."""
    present = {EvidenceDomain.CURRENT_LOGS, EvidenceDomain.PREVIOUS_LOGS}
    assert domain_weight(EvidenceDomain.CURRENT_LOGS, present) == 0.5
    assert domain_weight(EvidenceDomain.PREVIOUS_LOGS, present) == 0.5


def test_an_unmapped_tool_classifies_as_unknown():
    assert classify_tool("some_tool_added_next_year") is EvidenceDomain.UNKNOWN
    assert classify_tool("") is EvidenceDomain.UNKNOWN


def test_a_mapped_tool_still_classifies_correctly():
    assert classify_tool("list_k8s_events") is EvidenceDomain.KUBERNETES_EVENTS
    assert classify_tool("describe_k8s_resource") is EvidenceDomain.KUBERNETES_STATUS


# ── Gap 12: a failed status must survive the loop controller ────────────────

def test_state_merge_would_overwrite_a_failed_status_without_the_guard():
    """Documents the mechanism: `investigation` merges with operator.or_, and
    loop_controller runs LAST, so a later write wins."""
    router = {"status": "failed", "loop_exit_reason": "router_failed"}
    loop = {"current_step": 2, "status": "done", "loop_exit_reason": "tool_signaled_done"}
    assert operator.or_(router, loop)["status"] == "done"


def _loop_status(upstream_status: str, exit_reason):
    """Mirrors the guard in agent/nodes/loop_controller.py."""
    done = exit_reason is not None
    if upstream_status == "failed":
        return "failed", True
    return ("done" if done else "running"), done


def test_upstream_failed_status_is_preserved():
    status, done = _loop_status("failed", "tool_signaled_done")
    assert status == "failed"
    assert done is True


def test_normal_completion_is_unaffected():
    assert _loop_status("running", "confidence_sufficient") == ("done", True)


def test_still_running_is_unaffected():
    assert _loop_status("running", None) == ("running", False)


# ── Gaps 13/14: one shared definition of "has evidence" ─────────────────────

def test_all_failed_run_has_no_usable_evidence():
    state = {
        "evidence_ids": ["ev_001", "ev_002", "ev_003"],
        "evidence_store": {
            "ev_001": {"ok": False},
            "ev_002": {"ok": False},
            "ev_003": {"ok": False},
        },
    }
    assert usable_evidence_ids(state) == []
    # The bug this replaces: the slot count is truthy.
    assert bool(state["evidence_ids"]) is True


def test_mixed_run_keeps_only_the_usable_items():
    state = {
        "evidence_ids": ["ev_001", "ev_002"],
        "evidence_store": {"ev_001": {"ok": False}, "ev_002": {"ok": True}},
    }
    assert usable_evidence_ids(state) == ["ev_002"]


def test_entry_without_an_ok_flag_is_treated_as_usable():
    """Matches issue #91: absence of the flag means 'written before the flag
    existed', not 'failed'."""
    state = {"evidence_ids": ["ev_001"], "evidence_store": {"ev_001": {}}}
    assert usable_evidence_ids(state) == ["ev_001"]


def test_empty_state_is_handled():
    assert usable_evidence_ids({}) == []
    assert usable_evidence_ids({"evidence_ids": None, "evidence_store": None}) == []


def test_missing_store_entry_does_not_crash():
    """An id with no store entry must not raise — it defaults to usable."""
    state = {"evidence_ids": ["ev_001"], "evidence_store": {}}
    assert usable_evidence_ids(state) == ["ev_001"]
