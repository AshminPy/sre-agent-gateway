"""Proves current behavior of `collected_at` (agent/nodes/evidence_extractor.py) and how the
scorer's `freshness` / `time_correlation` components (agent/confidence/scorer.py) use it.

Question under test: `collected_at` is stamped as collection time (when the tool call ran --
evidence_extractor.py:96, `collected_at = time.time()`), never parsed from any timestamp field
inside the underlying Kubernetes object/event/log itself (incident time). Does that mean a
STALE incident (e.g. a pod that crashed hours ago, or an old cached K8s Event object) scores
freshness=1.0 and time_correlation=1.0 purely because it was FETCHED just now, with no signal
anywhere in the scorer distinguishing "this evidence is about something that just happened"
from "this evidence is about something that happened hours ago and was queried just now"?

This file does not modify agent/ and does not change any weight, threshold, or formula --
it only exercises the existing code with a constructed counterexample and records the result.
"""
from __future__ import annotations

import time

import agent.nodes.evidence_extractor as evidence_extractor_mod
from agent.confidence.claim_builder import build_claims
from agent.confidence.policy import POLICY
from agent.confidence.scorer import score_investigation_completeness, score_root_cause_confidence

from tests.conftest import CLUSTER, make_evidence, make_state, make_tool_history_entry


# ── Part 1: evidence_extractor.py's collected_at is collection time, not incident time ──────

def test_collected_at_is_stamped_from_wall_clock_not_parsed_from_stale_raw_event_data(monkeypatch):
    """Runs the REAL evidence_extractor() node against raw tool output whose OWN content
    says the underlying Kubernetes Event last happened 5 hours ago (a genuinely stale,
    cached Event object -- Kubernetes only bumps `lastTimestamp`/`count` on a NEW
    occurrence, so an Event object being returned right now does not mean the thing it
    describes just happened). If collected_at reflected incident time, it would land near
    `five_hours_ago`. It must instead land near `time.time()` at call time -- proving the
    extractor never reads the raw payload's own timestamp fields at all.
    """
    monkeypatch.setattr(evidence_extractor_mod, "write_evidence", lambda *a, **k: "gs://bucket/ev_001.json")
    monkeypatch.setattr(evidence_extractor_mod, "redact", lambda raw: raw)

    five_hours_ago_epoch = time.time() - 5 * 3600
    five_hours_ago_iso = "2026-08-27T22:00:00Z"  # a fixed, clearly-stale ISO timestamp

    raw_stale_k8s_event = {
        "events": [{
            "reason": "OOMKilling",
            "lastTimestamp": five_hours_ago_iso,
            "firstTimestamp": five_hours_ago_iso,
            "count": 1,
            "message": "Memory cgroup out of memory: Killed process",
        }],
    }

    monkeypatch.setattr(
        evidence_extractor_mod, "llm_json",
        lambda *a, **k: (
            {
                "resource_type": "pod",
                "resource_id": "test-incidents/test-pod",
                "summary": f"OOMKilled event, lastTimestamp={five_hours_ago_iso}",
                "key_facts": [f"OOMKilling event last occurred at {five_hours_ago_iso} (5h ago)"],
            },
            {
                "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
                "reasoning_tokens": 0, "tool_tokens": 0, "total_tokens": 15,
                "billable_output_tokens": 5, "cost_usd": 0.0,
                "provider": "gemini", "model": "gemini-2.5-pro", "duration_s": 0.1,
            },
        ),
    )

    state = make_state("OOMKilled", {}, [])
    state["latest_tool_result"] = {
        "ok": True,
        "tool": "list_events",
        "mcp_source": "gke_remote_mcp",
        "result": raw_stale_k8s_event,
        "error": None,
    }

    before = time.time()
    result = evidence_extractor_mod.evidence_extractor(state)
    after = time.time()
    ev_entry = result["evidence_store"]["ev_001"]

    collected_at = ev_entry["collected_at"]

    # The raw payload literally contains a 5-hour-old lastTimestamp/firstTimestamp, and the
    # LLM-extracted summary/key_facts even say so in plain text -- yet collected_at must land
    # in the call's own wall-clock window, not anywhere near five_hours_ago_epoch.
    assert before <= collected_at <= after, (
        f"collected_at={collected_at} is not within the call's own wall-clock window "
        f"[{before}, {after}] -- expected pure time.time() stamping"
    )
    assert abs(collected_at - five_hours_ago_epoch) > 3600, (
        "collected_at must NOT be derived from the stale lastTimestamp in the raw event data"
    )
    # file:line evidence for this behavior: agent/nodes/evidence_extractor.py:96
    # `collected_at = time.time()` -- unconditional, no branch reads raw/sanitized/extracted
    # content for a timestamp anywhere in this file.


# ── Part 2: scorer.py's freshness / time_correlation only see collected_at ──────────────────

def _stale_incident_evidence_store(now: float) -> dict:
    """3 evidence items whose CONTENT (summary/key_facts) says the incident is old, but whose
    collected_at is `now` -- exactly what evidence_extractor.py really produces when an old
    incident is investigated right now (Part 1 above)."""
    return {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="Pod terminated 5 hours ago, OOMKilled, exit code 137",
            key_facts=["OOMKilled 5 hours ago", "exit code 137"],
            collected_at=now,
        ),
        "ev_002": make_evidence(
            "ev_002", "list_events",
            summary="OOMKilling event, lastTimestamp 5 hours ago",
            key_facts=["OOMKilling event 5h old"],
            collected_at=now,
        ),
        "ev_003": make_evidence(
            "ev_003", "get_previous_logs",
            summary="Previous container logs, container exited 5 hours ago",
            key_facts=["OOM 5h ago"],
            collected_at=now,
        ),
    }


def test_stale_incident_investigated_now_scores_full_freshness():
    """score_investigation_completeness's `freshness` component (scorer.py:107-129) computes
    `now - collected_at` per item. Since collected_at is collection time (Part 1) and this
    investigation runs right now, freshness scores 1.0 regardless of the fact that every
    evidence item's own content describes an incident that happened 5 hours ago -- there is
    no field anywhere in evidence_store that encodes incident age, only collection age.
    """
    now = time.time()
    evidence_store = _stale_incident_evidence_store(now)
    tool_history = [
        make_tool_history_entry(0, "describe_pod_detail"),
        make_tool_history_entry(1, "list_events"),
        make_tool_history_entry(2, "get_previous_logs"),
    ]
    state = make_state("OOMKilled", evidence_store, tool_history, started_at=now)

    result = score_investigation_completeness(state, POLICY)

    assert result["components"]["freshness"] == 1.0, (
        "freshness must NOT be 1.0 if the scorer had any real signal distinguishing "
        "incident age from collection age -- it is 1.0 here purely because collected_at "
        "is recent, even though the incident itself is 5 hours stale per the evidence text"
    )
    assert not any("freshness" in g for g in result["gaps"])


def test_stale_incident_investigated_now_scores_full_time_correlation():
    """score_root_cause_confidence's `time_correlation` component (scorer.py:290-319) measures
    the SPREAD between collected_at values of a claim's supporting evidence -- not their
    distance from when the underlying incident actually occurred. All 3 items were collected
    together, right now, about an incident that happened 5 hours ago -- time_correlation must
    still be 1.0, because the component has no incident-age input at all.
    """
    now = time.time()
    evidence_store = _stale_incident_evidence_store(now)
    claims = build_claims(
        {
            "claims": [{
                "text": "Container was OOMKilled 5 hours ago, exit code 137",
                "claim_type": "observed_fact",
                "supporting_evidence_ids": ["ev_001", "ev_002", "ev_003"],
            }],
        },
        ["ev_001", "ev_002", "ev_003"], evidence_store,
    )

    result = score_root_cause_confidence(
        claims=claims,
        contradictions=[],
        hypotheses=[],
        evidence_store=evidence_store,
        tool_history=[
            make_tool_history_entry(0, "describe_pod_detail"),
            make_tool_history_entry(1, "list_events"),
            make_tool_history_entry(2, "get_previous_logs"),
        ],
        resolved_context={"cluster_name": CLUSTER, "namespace": "test-incidents", "pod": "test-pod"},
        incident_type="OOMKilled",
        policy=POLICY,
    )

    assert result["components"]["time_correlation"] == 1.0, (
        "time_correlation scores full marks for a 5-hour-stale incident investigated just "
        "now -- it only measures spread between collected_at values, never incident age"
    )
    # Confirms the broader claim: this stale-incident, fresh-collection case reaches
    # high_confidence overall, same as a genuinely fresh incident would -- no component in
    # score_root_cause_confidence or score_investigation_completeness penalizes incident age.
    assert result["band"] == "high_confidence"
    assert result["score"] >= POLICY.band_thresholds["auto"]


def test_freshness_tracks_collection_recency_not_incident_recency_control_case():
    """Control/contrast case, using the SAME scorer code: a genuinely FRESH incident (just
    happened) whose evidence was collected late (collection delayed past the freshness
    window) scores freshness=0.0 -- the opposite of the stale-incident/fresh-collection case
    above. This isolates the variable: freshness moves with collection delay, not incident
    age, in both directions.
    """
    now = time.time()
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="Pod just entered OOMKilled state seconds ago",
            key_facts=["OOMKilled just now"],
            collected_at=now - POLICY.evidence_max_age_seconds - 100,  # collected late
        ),
    }
    tool_history = [make_tool_history_entry(0, "describe_pod_detail")]
    state = make_state("OOMKilled", evidence_store, tool_history, started_at=now)

    result = score_investigation_completeness(state, POLICY)

    assert result["components"]["freshness"] == 0.0, (
        "a fresh incident scores freshness=0.0 if collection was merely delayed -- confirms "
        "freshness is purely a collection-recency signal, decoupled from incident recency"
    )
