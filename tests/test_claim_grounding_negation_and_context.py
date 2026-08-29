"""Regression tests for the minimal grounding fix, 2026-08-29 -- negation detection and
resolved-context (namespace/pod/cluster) token exclusion. Scope, per explicit instruction: only
what's needed to safely let direct_support give supported_inference claims partial credit
(tests/test_direct_support_inference_credit.py), NOT a general semantic-grounding redesign.

Covers the review's 5 adversarial cases
(docs/management/confidence-genericity-review-2026-08-28.md #15.5), using clean, self-authored
fixtures (not the review's own ephemeral, uncommitted test script) so results are unambiguous:

  1. Plausible but incorrect inference, correct-fact vocabulary -- NOT fixed here, documented
     as a known residual limit (would need real entailment/semantic checking).
  2. Evidence CONTRADICTS the claim -- fixed (negation detection).
  3. Semantically unrelated evidence, shared only via investigation-context vocabulary -- fixed
     (contextual-generic exclusion).
  4. Strongly, genuinely well-supported claim (control) -- must keep working.
  5. Correct claim, paraphrased, low lexical overlap -- NOT fixed here (documented limit); this
     test asserts today's real (imperfect) behavior so a future change doesn't silently drift.
"""
from agent.confidence.claim_builder import _ground_claim
from agent.confidence.models import Claim, ClaimType

RESOLVED_CONTEXT = {
    "cluster_name": "sre-test-cluster", "namespace": "test-incidents", "pod": "target-pod",
}


def _claim(text, claim_type=ClaimType.SUPPORTED_INFERENCE, supporting=("ev_001",)):
    return Claim(
        claim_id="claim_001", text=text, claim_type=claim_type,
        supporting_evidence_ids=list(supporting),
    )


def _evidence(summary, key_facts=()):
    return {"ok": True, "summary": summary, "key_facts": list(key_facts)}


def test_case2_contradicting_evidence_is_not_grounded():
    # Evidence explicitly denies the claim's central assertion.
    claim = _claim("The pod target-pod is in CrashLoopBackOff and has restarted repeatedly.")
    evidence_store = {
        "ev_001": _evidence(
            "Pod status check: restart count is 0, no CrashLoopBackOff detected.",
        ),
    }
    _ground_claim(claim, {"ev_001"}, evidence_store, RESOLVED_CONTEXT)
    assert claim.grounding_status == "contradicted"
    assert claim.support_strength == 0.0


def test_case3_coincidental_namespace_overlap_is_not_grounded():
    # The ONLY shared vocabulary is the investigation's own namespace/pod name -- the claim's
    # actual subject (a Service selector mismatch) has zero presence in this evidence, which
    # is genuinely about an unrelated ConfigMap.
    claim = _claim(
        "The Service selector for notification-svc does not match the pod's labels, "
        "resulting in zero endpoints."
    )
    evidence_store = {
        "ev_001": _evidence(
            "ConfigMap app-config in namespace test-incidents was not found for pod target-pod.",
        ),
    }
    _ground_claim(claim, {"ev_001"}, evidence_store, RESOLVED_CONTEXT)
    assert claim.grounding_status != "grounded"
    assert claim.support_strength < 1.0


def test_case4_directly_well_supported_claim_stays_grounded():
    claim = _claim(
        "The pod imagepull-pod is in ImagePullBackOff because the image tag "
        "nonexistent-image:v99 does not exist in the registry."
    )
    evidence_store = {
        "ev_001": _evidence(
            "ImagePullBackOff: manifest unknown for nonexistent-image:v99, tag not found in registry.",
        ),
    }
    _ground_claim(claim, {"ev_001"}, evidence_store, RESOLVED_CONTEXT)
    assert claim.grounding_status == "grounded"
    assert claim.support_strength == 1.0


def test_case1_fabricated_mechanism_sharing_correct_vocabulary_is_a_known_residual_limit():
    """Documents current (imperfect) behavior deliberately -- NOT fixed in this pass. A claim
    that invents an unevidenced mechanism, phrased using the same vocabulary as a genuinely
    correct fact, can still score grounded/1.0. Real entailment/semantic checking would be
    needed to catch this -- explicitly out of scope (see module docstring). If this ever starts
    failing, it means the behavior changed; update deliberately, don't just fix the assertion."""
    claim = _claim(
        "The pod target-pod was OOMKilled due to a slow memory leak introduced in the "
        "latest application code deploy."
    )
    evidence_store = {
        "ev_001": _evidence("Container terminated: reason OOMKilled, exit code 137."),
    }
    _ground_claim(claim, {"ev_001"}, evidence_store, RESOLVED_CONTEXT)
    # Documented limitation: still scores grounded/1.0 despite "memory leak" and "latest
    # application code deploy" being entirely unevidenced by ev_001.
    assert claim.grounding_status == "grounded"
    assert claim.support_strength == 1.0


def test_case5_correct_paraphrase_low_overlap_is_a_known_residual_limit():
    """Documents current (imperfect) behavior deliberately -- NOT fixed in this pass. A true,
    accurate claim that paraphrases its evidence rather than repeating its wording can still
    score no_overlap/0.1. Fixing this needs a semantic-similarity signal, not raw token
    overlap -- explicitly out of scope."""
    claim = _claim(
        "The requested container image tag could not be located in the configured registry."
    )
    evidence_store = {
        "ev_001": _evidence("manifest unknown: manifest unknown"),
    }
    _ground_claim(claim, {"ev_001"}, evidence_store, RESOLVED_CONTEXT)
    assert claim.grounding_status == "no_overlap"
    assert claim.support_strength == 0.1


def test_resolved_context_omitted_does_not_crash_and_preserves_old_behavior():
    # Every pre-2026-08-29 caller passes no resolved_context -- must still work unchanged.
    claim = _claim("The pod target-pod is in ImagePullBackOff.")
    evidence_store = {"ev_001": _evidence("Pod target-pod status: ImagePullBackOff.")}
    _ground_claim(claim, {"ev_001"}, evidence_store)
    assert claim.grounding_status == "grounded"


def test_partial_negation_does_not_wipe_out_genuinely_supported_overlap():
    # A claim with TWO distinct assertions, evidence confirms one and denies the other --
    # must not be blanket-marked "contradicted" (that would be a new false-low). The negated
    # word is dropped from consideration; the remaining genuine overlap still counts.
    claim = _claim(
        "The pod target-pod is in ImagePullBackOff and has 0 restarts."
    )
    evidence_store = {
        "ev_001": _evidence(
            "Pod target-pod status: ImagePullBackOff confirmed. Restart count is not zero -- 3 restarts observed.",
        ),
    }
    _ground_claim(claim, {"ev_001"}, evidence_store, RESOLVED_CONTEXT)
    assert claim.grounding_status == "grounded"
