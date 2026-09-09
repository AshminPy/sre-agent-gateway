"""Section 8 correction (2026-09-08): end-to-end proof that SREAgent._finalize_query()
itself -- not just _primary_claim_cites_uninspected_evidence() in isolation -- honors
the uninspected-evidence memory gate against the REAL shape investigate()/query()
actually produce.

Root cause this guards against: the gate function read `primary_causal_claim_id`/
`claims` from the top level of `result`, and had no `evidence_store` at all -- but
_finalize_investigation_result's real `return {...}` nests claim data under
`result["summary"]` and (before this fix) never exposed `evidence_store` at the top
level. The gate therefore ALWAYS returned False against a real production result,
so it could never block a write, regardless of whether cited evidence was actually
uninspected. tests/test_memory_uninspected_evidence_gate.py already covers the
predicate in isolation with the corrected shape; this file proves the WIRING at the
call site (agent/main.py::_finalize_query) actually uses it -- a test suite built
only against the predicate could not have caught the original bug, since the bug was
entirely in how the call site invoked it.

MODEL_ARMOR_TEMPLATE and EVAL_BUCKET are both unset by default (agent/main.py:38,46),
so _sanitize()/_save_to_gcs() no-op safely without further mocking.
"""
from unittest.mock import patch

from agent.main import SREAgent


def _real_shaped_result(*, primary_causal_claim_id, claims, evidence_store,
                         confidence_band="auto", status="done",
                         loop_exit_reason="confidence_sufficient", run_id="run_test_001"):
    """Builds the ACTUAL shape _finalize_investigation_result() returns (see its
    `return {...}` in agent/main.py) -- not a hand-shaped convenience dict. Every key
    a real caller could rely on is present, even if this test doesn't exercise it."""
    summary = {
        "primary_causal_claim_id": primary_causal_claim_id,
        "claims": claims,
        "outcome": "confirmed" if confidence_band == "auto" else "insufficient_evidence",
        "confidence_band": confidence_band,
        "likely_root_cause": "Pod crashed due to OOMKilled.",
        "investigation_completeness": {"score": 1.0},
        "root_cause_confidence": {"score": 1.0},
        "requires_human_review": confidence_band != "auto",
    }
    return {
        "schema_version": "2.0",
        "status": status,
        "confidence": 1.0 if confidence_band == "auto" else 0.2,
        "confidence_deprecated": True,
        "confidence_band": confidence_band,
        "outcome": summary["outcome"],
        "investigation_completeness": summary["investigation_completeness"],
        "root_cause_confidence": summary["root_cause_confidence"],
        "tool_calls": 2,
        "evidence_ids": list(evidence_store.keys()),
        "evidence_store": evidence_store,
        "run_id": run_id,
        "summary": summary,
        "executive_summary": "Pod crashed due to OOMKilled.",
        "rca_report": {"root_cause": "Pod crashed due to OOMKilled."},
        "working_theory": "OOMKilled",
        "errors": [],
        "requires_human_review": summary["requires_human_review"],
        "observability": {"loop_exit_reason": loop_exit_reason, "incident_type": "OOMKilled"},
    }


def _payload():
    return {"query": "Why did checkout-abc crash?", "cluster": "sre-test-cluster",
            "namespace": "test-incidents", "pod": "checkout-abc"}


def test_1_inspected_cited_evidence_memory_allowed():
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        evidence_store={"ev_001": {"inspection_status": "inspected"}},
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_called_once()
    save_memory.assert_called_once()


def test_2_uninspected_cited_evidence_persistent_memory_rejected():
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        evidence_store={"ev_001": {"inspection_status": "fail_open"}},
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory"):
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_not_called()


def test_3_uninspected_cited_evidence_in_process_memory_rejected():
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        evidence_store={"ev_001": {"inspection_status": "fail_open"}},
    )
    with patch.object(SREAgent, "_mb_store"), \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    save_memory.assert_not_called()


def test_4_mixed_evidence_primary_claim_cites_one_uninspected_item_rejected():
    """The primary claim cites BOTH an inspected and a fail_open item -- any single
    uninspected citation must reject the whole claim, not just be diluted by the
    inspected one."""
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001", "ev_002"]}],
        evidence_store={
            "ev_001": {"inspection_status": "inspected"},
            "ev_002": {"inspection_status": "fail_open"},
        },
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_not_called()
    save_memory.assert_not_called()


def test_5_unrelated_uninspected_evidence_not_cited_by_primary_claim_is_allowed():
    """Current intended policy: a fail-open item elsewhere in the investigation that
    the primary claim never actually cites must NOT block promotion."""
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        evidence_store={
            "ev_001": {"inspection_status": "inspected"},
            "ev_002": {"inspection_status": "fail_open"},
        },
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_called_once()
    save_memory.assert_called_once()


def test_6_no_evidence_no_trusted_memory_promotion():
    """No primary claim / no evidence at all (e.g. insufficient_evidence outcome,
    escalate confidence band) -- the uninspected-evidence gate itself must not crash
    and must not be the thing that (wrongly) allows a write; the existing
    confidence_band=="auto" gate is what correctly blocks Memory Bank here, and
    _save_memory (never confidence-gated) still runs since nothing here is
    evidence-integrity-suspect -- there is simply no primary claim to distrust."""
    result = _real_shaped_result(
        primary_causal_claim_id=None,
        claims=[],
        evidence_store={},
        confidence_band="escalate",
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_not_called()
    save_memory.assert_called_once()


def test_cluster_unresolved_skips_memory_entirely_regression():
    """Existing behavior (issue #73) must survive this change unmodified: a
    cluster_unresolved safe-stop skips memory persistence entirely, before the
    uninspected-evidence gate is ever reached."""
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        evidence_store={"ev_001": {"inspection_status": "inspected"}},
        loop_exit_reason="cluster_unresolved",
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_not_called()
    save_memory.assert_not_called()


def test_failed_status_skips_memory_entirely_regression():
    """Existing behavior must survive: status in (failed, blocked) skips memory
    persistence entirely."""
    result = _real_shaped_result(
        primary_causal_claim_id="claim_001",
        claims=[{"claim_id": "claim_001", "supporting_evidence_ids": ["ev_001"]}],
        evidence_store={"ev_001": {"inspection_status": "inspected"}},
        status="failed",
    )
    with patch.object(SREAgent, "_mb_store") as mb_store, \
         patch.object(SREAgent, "_save_memory") as save_memory:
        SREAgent._finalize_query(_payload(), result, session_id=None)
    mb_store.assert_not_called()
    save_memory.assert_not_called()
