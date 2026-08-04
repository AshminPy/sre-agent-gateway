"""Turns the RCA-builder LLM's proposed output into scored Claim/Contradiction/Hypothesis
objects. The LLM proposes; every field that affects a score (support_strength,
grounding_status, contradiction severity) is set by deterministic code here, never copied
from what the model claims about itself.

Extends the existing citation-validity approach in rca_builder.py's _validate_citations
(phantom-ID + keyword-overlap checks) rather than replacing it — same technique, now
structured per-claim instead of one pass/fail gate over the whole root cause string.
"""
from __future__ import annotations

import re

from agent.confidence.models import Claim, ClaimType, Contradiction, Hypothesis

_CITATION_STOP = {
    "this", "that", "with", "from", "have", "been", "were", "they",
    "what", "when", "which", "also", "more", "than", "some", "into",
}

_VALID_CLAIM_TYPES = {t.value for t in ClaimType}


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())} - _CITATION_STOP


def _ground_claim(claim: Claim, known_evidence_ids: set, evidence_store: dict) -> None:
    """Sets claim.grounding_status and claim.support_strength in place — deterministic."""
    cited = set(claim.supporting_evidence_ids)
    phantoms = cited - known_evidence_ids
    if phantoms:
        claim.grounding_status = "phantom_evidence"
        claim.support_strength = 0.0
        return

    if not cited:
        # A claim with no supporting evidence cannot contribute positively — matches
        # "a claim without valid supporting evidence must not contribute positively".
        claim.grounding_status = "ungrounded"
        claim.support_strength = 0.0
        return

    claim_words = _keywords(claim.text)
    facts_words: set = set()
    for eid in cited:
        ev = evidence_store.get(eid, {})
        facts_text = " ".join(ev.get("key_facts", []) + [ev.get("summary", "")])
        facts_words |= _keywords(facts_text)

    if claim_words and facts_words and not (claim_words & facts_words):
        claim.grounding_status = "no_overlap"
        claim.support_strength = 0.1
        return

    claim.grounding_status = "grounded"
    claim.support_strength = 1.0


def build_claims(
    rca_result: dict, evidence_ids: list, evidence_store: dict,
) -> list:
    """rca_result is the LLM's proposed JSON (extended RCA_BUILDER_USER schema — see
    agent/prompts.py). A MISSING 'claims' key falls back to a single claim built from the
    legacy likely_root_cause string, so this never breaks on an old-shaped LLM response. An
    EXPLICITLY EMPTY 'claims' list (e.g. the no-evidence safety path in rca_builder.py) is
    respected as zero claims, not silently reinterpreted via the legacy fallback — the caller
    meant "no claims," not "old response shape."
    """
    known_ids = set(evidence_ids)
    raw_claims = rca_result.get("claims")

    claims: list = []
    if isinstance(raw_claims, list):
        for i, rc in enumerate(raw_claims, start=1):
            if not isinstance(rc, dict):
                continue
            claim_type_raw = str(rc.get("claim_type", "hypothesis")).lower()
            claim_type = (
                ClaimType(claim_type_raw)
                if claim_type_raw in _VALID_CLAIM_TYPES
                else ClaimType.HYPOTHESIS
            )
            claim = Claim(
                claim_id=f"claim_{i:03d}",
                text=str(rc.get("text", ""))[:300],
                claim_type=claim_type,
                supporting_evidence_ids=[
                    e for e in rc.get("supporting_evidence_ids", []) if isinstance(e, str)
                ],
                contradicting_evidence_ids=[
                    e for e in rc.get("contradicting_evidence_ids", []) if isinstance(e, str)
                ],
            )
            _ground_claim(claim, known_ids, evidence_store)
            claims.append(claim)
    else:
        # Legacy fallback: no structured claims from the model — build exactly one claim from
        # likely_root_cause, same grounding logic as before (matches old _validate_citations
        # behavior, just now producing a real Claim object instead of a bare pass/fail).
        root_cause = str(rca_result.get("likely_root_cause", ""))
        cited = set(re.findall(r"\bev_\d+\b", root_cause))
        claim = Claim(
            claim_id="claim_001",
            text=root_cause[:300],
            claim_type=ClaimType.SUPPORTED_INFERENCE,
            supporting_evidence_ids=sorted(cited),
        )
        _ground_claim(claim, known_ids, evidence_store)
        claims.append(claim)

    return claims


def build_hypotheses(rca_result: dict, known_evidence_ids: set) -> list:
    raw = rca_result.get("alternative_hypotheses_considered")
    if not isinstance(raw, list):
        return []
    hyps: list = []
    for i, rh in enumerate(raw, start=1):
        if not isinstance(rh, dict):
            continue
        status = str(rh.get("status", "active")).lower()
        if status not in ("active", "weakened", "eliminated", "selected"):
            status = "active"
        hyps.append(Hypothesis(
            hypothesis_id=f"hyp_{i:03d}",
            description=str(rh.get("description", ""))[:300],
            supporting_evidence_ids=[
                e for e in rh.get("supporting_evidence_ids", [])
                if isinstance(e, str) and e in known_evidence_ids
            ],
            contradicting_evidence_ids=[
                e for e in rh.get("contradicting_evidence_ids", [])
                if isinstance(e, str) and e in known_evidence_ids
            ],
            missing_evidence=[str(m) for m in rh.get("missing_evidence", [])],
            status=status,
        ))
    return hyps


def detect_contradictions(
    claims: list, rca_result: dict, evidence_store: dict, resolved_context: dict,
) -> list:
    """Two sources, combined:
    1. Deterministic structural check — supporting evidence tagged with a different
       cluster than resolved_context. Code-only, no LLM involved.
    2. LLM self-reported contradicting_evidence_ids per claim (model proposes which evidence
       conflicts with its own claim) — the app decides the resulting severity/penalty, the
       model only flags the candidate pair. See design doc §3 for the documented limitation
       that this is a self-report, not an independent adversarial check.

    wrong_time_window detection is NOT implemented — evidence_store does not currently carry
    a per-item timestamp (see docs/confidence-framework-design.md known limitations). Not
    faked here; simply not produced until that data exists.
    """
    contradictions: list = []
    idx = 1

    expected_cluster = resolved_context.get("cluster_name")
    for claim in claims:
        for eid in claim.supporting_evidence_ids:
            ev = evidence_store.get(eid, {})
            ev_cluster = ev.get("cluster")
            if expected_cluster and ev_cluster and ev_cluster != expected_cluster:
                contradictions.append(Contradiction(
                    contradiction_id=f"contra_{idx:03d}",
                    claim_id=claim.claim_id,
                    description=(
                        f"Evidence {eid} references cluster '{ev_cluster}', "
                        f"investigation is scoped to '{expected_cluster}'"
                    ),
                    evidence_id_a=eid,
                    evidence_id_b="",
                    kind="wrong_resource",
                    severity=0.7,
                ))
                idx += 1

    known_ids = set(evidence_store.keys())
    for claim in claims:
        for eid in claim.contradicting_evidence_ids:
            if eid not in known_ids:
                continue  # phantom contradiction reference — ignore, don't fabricate a finding
            contradictions.append(Contradiction(
                contradiction_id=f"contra_{idx:03d}",
                claim_id=claim.claim_id,
                description=f"Model-flagged: evidence {eid} conflicts with claim '{claim.text[:80]}'",
                evidence_id_a=eid,
                evidence_id_b="",
                kind="semantic",
                severity=0.5,
            ))
            idx += 1

    return contradictions
