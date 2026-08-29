"""Proof, not a fix: missing-evidence checks are keyed on ALL evidence collected for the
whole investigation, not on the specific evidence that actually supports the root-cause
claim being scored.

Requested as a follow-up correction to docs/management/confidence-genericity-review-2026-08-28.md
(that report did not cover this gap). Scope: agent/confidence/scorer.py only, both call
sites that build `domains_present` from the full domain_map instead of from a claim's own
`supporting_evidence_ids`:

  - score_investigation_completeness(): scorer.py:75
        domains_present = set(_evidence_domains_present(evidence_store, tool_history).values())
  - score_root_cause_confidence():      scorer.py:350
        domains_present = set(domain_map.values())

Contrast: independent_corroboration (scorer.py:231-236, same function) gets this right —
it scopes to `supporting_ids = {eid for c in root_claims for eid in c.supporting_evidence_ids}`
before mapping to domains. The missing-evidence check three components below it does not
reuse that scoped set; it recomputes `domains_present` from `domain_map` (the whole
evidence_store), so a required domain satisfied by ANY tool call in the investigation —
even one the claim never cites — clears `required_now`, zeroes `missing_evidence_penalty`,
and avoids the `max_score_missing_critical_evidence` hard cap (scorer.py:368-369).

This file changes nothing under agent/ — it is a deterministic reproduction, run against
the real functions, of behavior that is already live today.
"""
from __future__ import annotations

import time

from agent.confidence.claim_builder import build_claims
from agent.confidence.evidence_domains import EvidenceDomain
from agent.confidence.policy import POLICY
from agent.confidence.scorer import (
    score_investigation_completeness,
    score_root_cause_confidence,
)

# incident_type deliberately NOT one of policy.py's 3 explicit keys (OOMKilled,
# ImagePullBackOff, CrashLoopBackOff) -- falls through to "_default", whose
# required_now is exactly (KUBERNETES_STATUS,) per policy.py:106-113.
INCIDENT_TYPE = "NodePressureEviction"


def _build_state_and_claim():
    now = time.time()

    # ev_001: classifies as KUBERNETES_STATUS via _TOOL_DOMAIN["list_pods"]
    # (evidence_domains.py:33). This is the ONLY evidence satisfying _default's
    # required_now domain -- and the claim below will NOT cite it.
    ev_001 = {
        "ok": True,
        "tool": "list_pods",
        "mcp_source": "k8s_mcp",
        "cluster": "test-cluster",
        "region": "us-central1",
        "collected_at": now,
        "resource_id": "workloads/unrelated-pod",
        "summary": "Pod unrelated-pod is Running, 0 restarts",
        "key_facts": ["Pod unrelated-pod status Running", "restart_count 0"],
    }

    # ev_002: classifies as KUBERNETES_EVENTS via _TOOL_DOMAIN["list_events"]
    # (evidence_domains.py:37). This is what the root-cause claim ACTUALLY cites.
    ev_002 = {
        "ok": True,
        "tool": "list_events",
        "mcp_source": "k8s_mcp",
        "cluster": "test-cluster",
        "region": "us-central1",
        "collected_at": now,
        "resource_id": "workloads/target-pod",
        "summary": "Event: node pressure eviction triggered for target-pod",
        "key_facts": ["EvictionByNodePressure target-pod", "node disk pressure detected"],
    }

    evidence_store = {"ev_001": ev_001, "ev_002": ev_002}
    evidence_ids = ["ev_001", "ev_002"]

    tool_history = [
        {"step": 1, "tool": "list_pods", "mcp_source": "k8s_mcp", "ok": True},
        {"step": 2, "tool": "list_events", "mcp_source": "k8s_mcp", "ok": True},
    ]

    resolved_context = {
        "cluster_explicitly_provided": True,
        "cluster_name": "test-cluster",
        "namespace": "workloads",
        "pod": "target-pod",
        "mcp_source": "k8s_mcp",
    }

    state = {
        "resolved_context": resolved_context,
        "investigation": {"started_at": now, "current_step": 2, "max_steps": 5},
        "evidence_store": evidence_store,
        "tool_history": tool_history,
    }

    # Root-cause claim built through the REAL claim_builder pipeline (not hand-set
    # grounding_status/support_strength), citing ONLY ev_002 -- the KUBERNETES_EVENTS
    # item -- never ev_001, the KUBERNETES_STATUS item that happens to satisfy
    # _default's required_now.
    #
    # claim_type is deliberately OBSERVED_FACT, not SUPPORTED_INFERENCE, and the text is
    # written to overlap ev_002's own wording so claim_builder grounds it at full strength.
    # This is NOT dodging realism -- the prior review's own case data (imagepull-001,
    # configmap-001 etc., confidence-genericity-review-2026-08-28.md §3a) shows real root-
    # cause claim sets are normally a MIX of OBSERVED_FACT and SUPPORTED_INFERENCE claims.
    # It isolates the missing-evidence-scoping bug under test from the ALREADY-DOCUMENTED
    # direct_support claim-type gate (same report, §3a) -- with a SUPPORTED_INFERENCE claim
    # here, direct_support would be 0.0 regardless of this bug, and the missing-evidence
    # effect being tested would be invisible under that much larger, separately-tracked drag.
    rca_result = {
        "claims": [
            {
                "claim_type": "observed_fact",
                "text": "target-pod was evicted due to node disk pressure (NodePressure eviction).",
                "supporting_evidence_ids": ["ev_002"],
            }
        ],
    }
    claims = build_claims(rca_result, evidence_ids, evidence_store)
    assert claims[0].supporting_evidence_ids == ["ev_002"]

    return state, claims, evidence_store, tool_history, resolved_context


def test_claim_never_cites_the_only_required_domain_evidence():
    """Sanity check on the fixture itself: the claim's own citations do not include the
    KUBERNETES_STATUS item, and the KUBERNETES_STATUS item is about a DIFFERENT pod
    entirely -- it structurally cannot be what actually grounds this claim."""
    state, claims, evidence_store, _, _ = _build_state_and_claim()
    claim = claims[0]
    assert "ev_001" not in claim.supporting_evidence_ids
    assert evidence_store["ev_001"]["resource_id"] != evidence_store["ev_002"]["resource_id"]


def test_completeness_required_evidence_coverage_ignores_claim_scope():
    """score_investigation_completeness() has no claim argument at all -- it can only ever
    look at the whole evidence_store. Documented here as the baseline: this component is
    investigation-wide by construction, so 'coverage' means 'domain was collected
    somewhere', never 'domain supports a specific claim'."""
    state, claims, evidence_store, tool_history, resolved_context = _build_state_and_claim()
    result = score_investigation_completeness(state, POLICY)

    assert result["components"]["required_evidence_coverage"] == 1.0
    assert result["missing_required_domains"] == []
    assert not any("Missing required evidence domain" in g for g in result["gaps"])


def test_root_cause_confidence_missing_evidence_penalty_is_zero_despite_unscoped_claim():
    """The actual counterexample. Q1-Q4 from the review request, proven with real numbers."""
    state, claims, evidence_store, tool_history, resolved_context = _build_state_and_claim()

    result = score_root_cause_confidence(
        claims=claims,
        contradictions=[],
        hypotheses=[],
        evidence_store=evidence_store,
        tool_history=state["tool_history"],
        resolved_context=resolved_context,
        incident_type=INCIDENT_TYPE,
        policy=POLICY,
    )

    print("\n--- score_root_cause_confidence() real output ---")
    print(f"score = {result['score']}  band = {result['band']}")
    print(f"components = {result['components']}")
    print(f"reasons = {result['reasons']}")

    # Ground truth: the claim is grounded (via the REAL claim_builder pipeline, full
    # support_strength) and its own supporting evidence is exactly ev_002 --
    # KUBERNETES_EVENTS -- never ev_001, the KUBERNETES_STATUS item.
    assert claims[0].grounding_status == "grounded"
    assert claims[0].support_strength == 1.0
    assert claims[0].supporting_evidence_ids == ["ev_002"]

    # Every OTHER component is at (or near) its ceiling, by construction -- so the only
    # thing that can explain a high final score here is the missing-evidence checks
    # under test, not the already-documented direct_support gate (§3a) or a lucky
    # resource-identity/time-correlation roll.
    assert result["components"]["direct_support"] == 1.0
    assert result["components"]["resource_identity_match"] == 1.0
    assert result["components"]["time_correlation"] == 1.0
    assert result["components"]["claim_grounding"] == 1.0
    # independent_corroboration correctly scopes to what THIS claim cites (scorer.py:
    # 231-236) -- one domain (KUBERNETES_EVENTS) -- so it is NOT at ceiling. This is the
    # correct, scoped behavior the missing-evidence checks below do not share.
    assert result["components"]["independent_corroboration"] == 0.5

    # Q1: does required_now get satisfied even though the claim never cites that evidence?
    # required_now for "_default" is (KUBERNETES_STATUS,) -- policy.py:106-113 (confirmed
    # by test_contrast_... below: INCIDENT_TYPE falls through to "_default"). The claim's
    # own supporting domain is only KUBERNETES_EVENTS (asserted above). Yet scorer.py:350
    # (`domains_present = set(domain_map.values())`) pulls from the WHOLE domain_map --
    # both ev_001 (KUBERNETES_STATUS, cited by NOTHING here) and ev_002 -- so
    # KUBERNETES_STATUS reads as present and required_now is seen as fully satisfied.
    # Q2: is missing_evidence_penalty == 0 in this case?
    assert result["components"]["missing_evidence_penalty"] == 0.0

    # Q3: does the hard cap (max_score_missing_critical_evidence == 0.65, scorer.py:368-369)
    # get avoided? Real weighted base score here is 0.875 (0.30 + 0.125 + 0.20 + 0.15 +
    # 0.10), no contradiction/alt-hypothesis penalties apply, and no cap engages --
    # the actual score clears 0.65 outright, landing in the TOP band.
    assert result["score"] == 0.875
    assert result["score"] > POLICY.max_score_missing_critical_evidence
    assert result["band"] == "high_confidence"

    # Q4: is this an unjustifiably high score for a claim actually backed by just one
    # (weighted-0.5, uncorroborated) domain? Prove it by recomputing what the score WOULD
    # be if the missing-evidence check reused independent_corroboration's own scoping
    # (supporting_ids -> domains, matching scorer.py:231-236's pattern) instead of the
    # whole-investigation domain_map. No agent/ code is changed to do this -- it's the
    # same arithmetic scorer.py already performs, applied to the correctly-scoped set.
    claim_supporting_domains = {
        EvidenceDomain.KUBERNETES_EVENTS  # only domain claims[0] actually cites
    }
    correctly_scoped_missing = set(POLICY.evidence_requirements["_default"].required_now) \
        - claim_supporting_domains
    assert correctly_scoped_missing == {EvidenceDomain.KUBERNETES_STATUS}
    correctly_scoped_penalty = min(
        POLICY.missing_evidence_penalty_cap,
        len(correctly_scoped_missing) * POLICY.missing_evidence_penalty_per_domain,
    )
    correctly_scoped_score = result["score"] - result["components"]["missing_evidence_penalty"] \
        + correctly_scoped_penalty
    # ...and the hard cap WOULD engage, since correctly_scoped_missing is non-empty:
    correctly_scoped_score = min(correctly_scoped_score, POLICY.max_score_missing_critical_evidence)

    assert correctly_scoped_penalty == 0.10
    assert round(correctly_scoped_score, 4) == 0.65

    print(
        f"\nAs actually scored today: {result['score']} ({result['band']}) "
        f"vs. claim-scoped hypothetical: {round(correctly_scoped_score, 4)} "
        "(would cap at review_required's threshold, one full band lower) "
        f"-- band_thresholds={POLICY.band_thresholds}"
    )
    # One full band's worth of unjustified score, purely from evidence the claim itself
    # never cites: high_confidence (>=0.85, no human review) vs. review_required (0.65).
    assert result["score"] >= POLICY.band_thresholds["auto"]
    assert round(correctly_scoped_score, 4) < POLICY.band_thresholds["review"] + 1e-9
    assert round(correctly_scoped_score, 4) == POLICY.band_thresholds["review"]


def test_contrast_if_the_claim_scope_were_honored_penalty_would_apply():
    """Proves this is a real gap, not a scoring quirk: if missing_required were computed
    from the CLAIM's own supporting domains (matching independent_corroboration's already-
    correct pattern) instead of the whole evidence_store, KUBERNETES_STATUS would show as
    genuinely missing for this claim, and both the penalty and the hard cap WOULD engage.
    This test calls no code under agent/ differently -- it recomputes the alternative by
    hand from the same real domain_map the scorer produced, to make the contrast concrete.
    """
    state, claims, evidence_store, tool_history, resolved_context = _build_state_and_claim()
    claim = claims[0]

    # What the scorer actually uses (scorer.py:350) -- unscoped.
    from agent.confidence.scorer import _evidence_domains_present
    domain_map = _evidence_domains_present(evidence_store, state["tool_history"])
    unscoped_domains_present = set(domain_map.values())

    # What independent_corroboration already correctly does (scorer.py:232-233) -- scoped
    # to the claim's own citations.
    scoped_domains_present = {
        domain_map[eid] for eid in claim.supporting_evidence_ids if eid in domain_map
    }

    required_now = POLICY.evidence_requirements["_default"].required_now
    assert set(required_now) == {EvidenceDomain.KUBERNETES_STATUS}

    unscoped_missing = set(required_now) - unscoped_domains_present
    scoped_missing = set(required_now) - scoped_domains_present

    assert unscoped_missing == set()  # what the real scorer sees today -- no gap
    assert scoped_missing == {EvidenceDomain.KUBERNETES_STATUS}  # what a claim-scoped check would see
