"""Proof of a fixed bug: missing-evidence checks used to be keyed on ALL evidence collected
for the whole investigation, not on the specific evidence that actually supports the
root-cause claim being scored.

Originally written as a follow-up correction to
docs/management/confidence-genericity-review-2026-08-28.md (that report's first pass did not
cover this gap). Confirmed as a real false-high mechanism (report §15.1) and fixed 2026-08-29:

  - score_investigation_completeness(): scorer.py:75 -- UNCHANGED, deliberately. This
        component has no claim argument at all (it runs before claims exist in the
        pipeline -- see report §15.4's pipeline-ordering finding), so "coverage" stays
        investigation-wide by construction. See test_completeness_required_evidence_
        coverage_ignores_claim_scope below -- still-current, intentional behavior.
  - score_root_cause_confidence():      scorer.py -- FIXED. `missing_required` is now
        computed from `supporting_domains` (the claim's own cited evidence, already built
        for independent_corroboration) instead of the whole evidence_store's domain_map.

Contrast: independent_corroboration (same function) already scoped correctly to
`supporting_ids = {eid for c in root_claims for eid in c.supporting_evidence_ids}` before
mapping to domains. The missing-evidence check now reuses that exact same scoped set.

This file changes nothing under agent/ itself -- it exercises the real, now-fixed functions.
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


def test_root_cause_confidence_missing_evidence_penalty_is_correctly_scoped_to_the_claim():
    """The fix, proven with real numbers. Before 2026-08-29 this test proved the opposite
    (penalty=0.0, score=0.875/high_confidence) -- see git history for the original
    counterexample this replaces."""
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

    print("\n--- score_root_cause_confidence() real output (post-fix) ---")
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
    # thing that can explain the final score here is the missing-evidence check under
    # test, not the already-documented direct_support gate (§3a) or a lucky
    # resource-identity/time-correlation roll.
    assert result["components"]["direct_support"] == 1.0
    assert result["components"]["resource_identity_match"] == 1.0
    assert result["components"]["time_correlation"] == 1.0
    assert result["components"]["claim_grounding"] == 1.0
    # independent_corroboration correctly scopes to what THIS claim cites -- one domain
    # (KUBERNETES_EVENTS) -- so it is NOT at ceiling.
    assert result["components"]["independent_corroboration"] == 0.5

    # required_now for "_default" is (KUBERNETES_STATUS,) -- policy.py:106-113 (confirmed
    # by test_contrast_... below). The claim's own supporting domain is only
    # KUBERNETES_EVENTS. Post-fix, missing_required is computed from the claim's own
    # supporting_domains, not the whole evidence_store -- ev_001 (KUBERNETES_STATUS,
    # cited by NOTHING here) no longer counts, so KUBERNETES_STATUS correctly reads as
    # missing for THIS claim.
    assert result["components"]["missing_evidence_penalty"] == 0.10
    assert any("Missing required evidence" in r for r in result["reasons"])

    # The hard cap (max_score_missing_critical_evidence == 0.65) now correctly engages --
    # a claim backed by one uncorroborated domain cannot reach the top band regardless of
    # how high its other components are.
    assert result["score"] == POLICY.max_score_missing_critical_evidence
    assert result["band"] == "review_required"

    print(
        f"\nFixed: {result['score']} ({result['band']}) -- correctly capped at "
        f"review_required, not the pre-fix 0.875/high_confidence."
    )


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
