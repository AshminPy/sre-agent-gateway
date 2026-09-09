"""Unit tests for agent.confidence.claim_builder.select_primary_causal_claim() —
2026-09-01 confidence-architecture review.

Scope: ONLY mechanical validation (does the referenced claim exist, is its type
eligible, does it have supporting evidence). Never a text/keyword judgment about
whether the claim's content "sounds causal" — that judgment belongs to the
independent verifier's causal_assertion field (see test_claim_verifier.py), not here.
"""
from __future__ import annotations

from agent.confidence.claim_builder import build_claims, select_primary_causal_claim
from tests.conftest import make_evidence


def _evidence_store():
    return {
        "ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["OOMKilled"]),
    }


def test_null_index_is_a_valid_explicit_abstention_not_an_error():
    result = {"primary_causal_claim_index": None, "claims": [
        {"text": "x", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_missing_index_key_entirely_also_resolves_to_none():
    result = {"claims": [
        {"text": "x", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_valid_index_resolves_the_correct_claim():
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "the real cause", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    claim = select_primary_causal_claim(claims, result)
    assert claim is not None
    assert claim.text == "the real cause"
    assert claim.claim_id == "claim_001"


def test_out_of_range_index_resolves_to_none():
    result = {"primary_causal_claim_index": 5, "claims": [
        {"text": "x", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_non_integer_index_resolves_to_none():
    result = {"primary_causal_claim_index": "not-a-number", "claims": [
        {"text": "x", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_recommendation_claim_type_is_never_eligible():
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "restart the pod", "claim_type": "recommendation", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_hypothesis_claim_type_is_never_eligible():
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "maybe it's a network issue", "claim_type": "hypothesis", "supporting_evidence_ids": ["ev_001"]},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_claim_citing_failed_evidence_ok_false_is_never_eligible():
    """2026-09-01 review, correction round 2, point 3: a failed tool call still gets a
    raw_ref (its GCS-written error record is technically readable), so this check must
    be mechanical and explicit -- not left to the verifier to somehow notice the cited
    'evidence' is just error text."""
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "OOMKilled", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
    ]}
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=[], ok=False)}
    claims = build_claims(result, ["ev_001"], evidence_store)
    assert select_primary_causal_claim(claims, result, evidence_store) is None


def test_claim_citing_phantom_missing_evidence_id_is_never_eligible():
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "OOMKilled", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_999"]},
    ]}
    # ev_999 is never in evidence_store at all -- build_claims() itself already scores
    # this "phantom_evidence"/support_strength=0.0, but select_primary_causal_claim must
    # independently refuse it too, not rely on the claim's grounding_status alone.
    evidence_store = {"ev_001": make_evidence("ev_001", "describe_pod_detail", key_facts=["x"])}
    claims = build_claims(result, ["ev_001"], evidence_store)
    assert select_primary_causal_claim(claims, result, evidence_store) is None


def test_evidence_store_check_is_skipped_when_not_provided():
    """Backward-compat: callers that don't pass evidence_store (e.g. existing unit tests
    that only care about index/type resolution) get the pre-existing mechanical checks
    only -- the new evidence-validity check is additive, opt-in via the parameter, never
    a silent behavior change for a caller that hasn't been updated to pass it. The real
    production call site (rca_builder.py) always passes it."""
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "OOMKilled", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_999"]},
    ]}
    claims = build_claims(result, ["ev_999"], {})
    claim = select_primary_causal_claim(claims, result)
    assert claim is not None


def test_claim_with_no_supporting_evidence_is_never_eligible():
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "OOMKilled", "claim_type": "observed_fact", "supporting_evidence_ids": []},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert select_primary_causal_claim(claims, result) is None


def test_index_shift_regression_id_based_lookup_survives_a_dropped_malformed_entry():
    """Corrected understanding (final review round, point 6): build_claims()'s
    `for i, rc in enumerate(raw_claims, start=1)` binds `i` BEFORE the
    `isinstance(rc, dict)` skip check, so a dropped malformed entry at raw position 2
    does NOT renumber survivors — raw position 3 keeps claim_003 regardless. This test
    does not prove a live renumbering bug (there isn't one today); it guards the
    ID-based lookup MECHANISM itself, so resolution stays correct even if build_claims()'s
    loop structure changes later — matching every other cross-reference in this system
    (supporting_evidence_ids references ev_id, never evidence list position)."""
    result = {
        "primary_causal_claim_index": 3,
        "claims": [
            {"text": "first claim", "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"]},
            "not-a-dict-malformed-entry",
            {"text": "third claim -- the real cause", "claim_type": "observed_fact",
             "supporting_evidence_ids": ["ev_001"]},
        ],
    }
    claims = build_claims(result, ["ev_001"], _evidence_store())
    # build_claims() already dropped the malformed entry -- confirm today's real shape
    # before asserting on it, rather than assuming.
    ids = [c.claim_id for c in claims]
    assert ids == ["claim_001", "claim_003"], (
        f"expected build_claims() to preserve raw positional IDs (skipping claim_002), "
        f"got {ids} -- if this changed, select_primary_causal_claim's ID-based lookup "
        f"should still resolve correctly regardless"
    )
    claim = select_primary_causal_claim(claims, result)
    assert claim is not None
    assert claim.text == "third claim -- the real cause"


# ── Section 4 correction (2026-09-08): entity-grounding exclusion ──────────────────
#
# Real fabrication case (T01-crashloop-gke, PHASE1_EVIDENCE_LOG.md): the RCA claimed
# the crashing container was "crashloop-container" -- pattern-matched from the pod
# name -- while every piece of cited evidence gave the real name as "crasher". The
# claim still scored "grounded" on general word overlap (error/termination/exit code
# all matched real evidence text) and was selected as primary, so the fabricated name
# became result["likely_root_cause"] verbatim. These tests prove that can no longer
# happen: a claim whose own grounding computation flags an unsupported entity is
# excluded from ever becoming the primary causal claim, the same way a
# RECOMMENDATION-type or no-evidence claim already was.

def test_crashloop_container_fabrication_reproduction_is_never_eligible():
    """Exact reproduction of the recorded case: real evidence names the container
    'crasher', the claim invents 'crashloop-container'."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "list_k8s_events",
            summary="Back-off restarting failed container crasher in pod crashloop-pod",
            key_facts=["container crasher", "exit code 1", "Error"],
        ),
    }
    result = {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": (
                "The last termination of container 'crashloop-container' was due to "
                "an error, resulting in exit code 1"
            ),
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "unsupported_entity"
    assert select_primary_causal_claim(claims, result, evidence_store) is None


def test_valid_container_name_present_in_evidence_is_accepted():
    """The other half of the same fork: a claim naming the REAL container (present in
    its own cited evidence) must not be penalized -- this fix must not become a
    false-positive machine."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "list_k8s_events",
            summary="Back-off restarting failed container crasher in pod crashloop-pod",
            key_facts=["container crasher", "exit code 1", "Error"],
        ),
    }
    result = {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "The container 'crasher' terminated with an error, exit code 1",
            "claim_type": "observed_fact",
            "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "grounded"
    claim = select_primary_causal_claim(claims, result, evidence_store)
    assert claim is not None
    assert claim.text == "The container 'crasher' terminated with an error, exit code 1"


def test_multi_container_pod_each_container_checked_against_its_own_evidence():
    """Two real containers exist (app + sidecar) -- a claim citing evidence that
    mentions BOTH must be accepted; a claim inventing a THIRD name must not."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="Pod checkout-abc123 has containers checkout-app and istio-proxy",
            key_facts=["container checkout-app", "container istio-proxy", "both Running"],
        ),
    }
    valid_result = {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "Container checkout-app is healthy alongside sidecar istio-proxy",
            "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(valid_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "grounded"

    fabricated_result = {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "Container checkout-worker crashed due to a memory leak",
            "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(fabricated_result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "unsupported_entity"
    assert select_primary_causal_claim(claims, fabricated_result, evidence_store) is None


def test_sidecar_only_evidence_rejects_invented_app_container_name():
    """Evidence only ever names the sidecar (istio-proxy) -- a claim asserting a
    specific, never-observed application container name must be rejected, not
    guessed."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "get_current_logs",
            summary="istio-proxy sidecar logs show connection refused",
            key_facts=["container istio-proxy", "connection refused"],
        ),
    }
    result = {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "Container payment-worker failed to connect to the database",
            "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "unsupported_entity"
    assert select_primary_causal_claim(claims, result, evidence_store) is None


def test_no_evidence_result_still_resolves_to_none_unaffected_by_entity_check():
    """Existing behavior (no supporting evidence at all) must survive unmodified --
    the entity-grounding check only applies once there IS cited evidence to check
    against; a claim with none is excluded by the pre-existing check first."""
    result = {"primary_causal_claim_index": 1, "claims": [
        {"text": "Container app-server-x9 crashed", "claim_type": "observed_fact",
         "supporting_evidence_ids": []},
    ]}
    claims = build_claims(result, ["ev_001"], _evidence_store())
    assert claims[0].grounding_status == "ungrounded"
    assert select_primary_causal_claim(claims, result) is None


def test_known_k8s_reason_fabrication_is_also_caught():
    """The second entity class this check covers: a specific K8s status reason
    (CrashLoopBackOff) asserted as fact when evidence never says it."""
    evidence_store = {
        "ev_001": make_evidence(
            "ev_001", "describe_pod_detail",
            summary="Pod status Running, 0 restarts, all containers healthy",
            key_facts=["status Running", "restarts 0"],
        ),
    }
    result = {
        "primary_causal_claim_index": 1,
        "claims": [{
            "text": "The pod is in CrashLoopBackOff due to repeated container failures",
            "claim_type": "observed_fact", "supporting_evidence_ids": ["ev_001"],
        }],
    }
    claims = build_claims(result, ["ev_001"], evidence_store)
    assert claims[0].grounding_status == "unsupported_entity"
    assert select_primary_causal_claim(claims, result, evidence_store) is None
