"""agent/confidence/verifier.py — independent verification of the primary causal claim.

Architecture (frozen, 2026-09-01 confidence-architecture review):
  incident -> evidence collection -> primary causal claim -> independent verification
  against source evidence -> deterministic safety gates -> operational outcome.

The verifier is a SEPARATE model call from the one that generated the RCA, given ONLY the
primary causal claim's text plus its evidence -- never the full RCA, the reasoning trace,
the confidence score, or unrelated claims. It must not generate the RCA and then judge itself.

Two labeled evidence sets go into the prompt (see agent/prompts.py's VERIFIER_USER):
  Set A - CITED SUPPORTING EVIDENCE: exactly the primary claim's own supporting_evidence_ids.
          faithfulness/sufficiency may use ONLY this.
  Set B - OTHER USABLE COLLECTED EVIDENCE: every other successful (ok=True) evidence item
          from this investigation. May be used ONLY to check for a contradiction -- never to
          strengthen the claim.

Fail-closed rules (see agent/confidence/scorer.py's derive_outcome for how these gate the
outcome):
  - Any Set A evidence item whose raw source can't be read, or whose size would require
    truncation, sets source_evidence_complete=False -- false-low is acceptable here,
    false-high is not, so this blocks CONFIRMED rather than silently truncating and
    proceeding as though the verifier saw the complete source.
  - Any Set B evidence item that can't be fully read sets contradiction_check_complete=False
    -- caps the outcome at PROBABLE (an incomplete contradiction scan can't rule out a
    contradiction, so it can't earn full CONFIRMED trust), never blocks it down to
    INSUFFICIENT_EVIDENCE (Set A -- the claim's own support -- is what's actually being
    trusted; Set B incompleteness only means "we didn't get to double-check as much").
  - A verifier LLM call that times out, throws, or returns unparseable/invalid JSON sets
    verified_ok=False -- blocks both CONFIRMED and PROBABLE.
  - evidence_refs claimed for faithfulness/sufficiency must be a subset of Set A's IDs;
    an evidence_ref claimed as the cause of a contradiction must be in Set B. Either
    violated -> verified_ok=False (the model cited something it wasn't given).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from agent.gcs_client import read_evidence

log = logging.getLogger("sre-agent.confidence.verifier")

# Same head+tail truncation budget already used by rca_builder.py's
# _enriched_evidence_digest() for GCS-reread raw evidence -- reused, not reinvented.
# Here it's a hard ceiling, not a truncate-and-continue budget: exceeding it means
# source_evidence_complete=False, never a silently-truncated payload.
_MAX_RAW_CHARS_PER_ITEM = 3000

_VALID_CAUSAL_ASSERTION = {"specific_cause", "non_causal", "abstention"}
_VALID_FAITHFULNESS = {"supported", "partial", "unsupported"}
_VALID_SUFFICIENCY = {"sufficient", "insufficient"}
_VALID_CONTRADICTION = {"present", "absent"}
_VALID_TEMPORAL = {"relevant", "unknown", "conflicting"}


@dataclass
class VerifierResult:
    causal_assertion: str = "abstention"
    faithfulness: str = "unsupported"
    sufficiency: str = "insufficient"
    semantic_contradiction: str = "absent"
    temporal_relevance: str = "unknown"
    rationale: str = ""
    evidence_refs: list = field(default_factory=list)
    contradiction_evidence_ref: str = ""
    verified_ok: bool = False
    source_evidence_complete: bool = False
    contradiction_check_complete: bool = False

    def to_dict(self) -> dict:
        return {
            "causal_assertion": self.causal_assertion,
            "faithfulness": self.faithfulness,
            "sufficiency": self.sufficiency,
            "semantic_contradiction": self.semantic_contradiction,
            "temporal_relevance": self.temporal_relevance,
            "rationale": self.rationale,
            "evidence_refs": self.evidence_refs,
            "contradiction_evidence_ref": self.contradiction_evidence_ref,
            "verified_ok": self.verified_ok,
            "source_evidence_complete": self.source_evidence_complete,
            "contradiction_check_complete": self.contradiction_check_complete,
        }


def _fetch_sanitized_source(ev: dict) -> tuple[str, bool]:
    """Returns (text, complete). complete=False covers both "couldn't read it at all"
    and "it exists but is too large" -- both are incomplete; there is no partial credit
    for "we saw most of it" (2026-09-01 review, point 4: false-low acceptable, false-high
    is not)."""
    raw_ref = ev.get("raw_ref", "")
    if not raw_ref or not raw_ref.startswith("gs://"):
        return "", False
    try:
        raw_data = read_evidence(raw_ref)
    except Exception as exc:
        log.warning("verifier: read_evidence(%s) raised: %s", raw_ref, exc)
        return "", False
    if not raw_data:
        return "", False
    sanitized = raw_data.get("sanitized", raw_data)
    raw_str = json.dumps(sanitized, indent=2)
    if len(raw_str) > _MAX_RAW_CHARS_PER_ITEM:
        return "", False
    return raw_str, True


def _build_evidence_block(label: str, ids: list, evidence_store: dict) -> tuple[str, bool]:
    lines = [f"{label}:"]
    all_complete = True
    if not ids:
        lines.append("  (none)")
    for eid in ids:
        ev = evidence_store.get(eid, {})
        text, complete = _fetch_sanitized_source(ev)
        if not complete:
            all_complete = False
            lines.append(f"  [{eid}] (source evidence unavailable or too large for this pass -- NOT INCLUDED)")
            continue
        lines.append(f"  [{eid}]:")
        lines.append(text)
    return "\n".join(lines), all_complete


def _parse_result(raw_result: dict, cited_ids: list, other_ids: list) -> VerifierResult:
    causal_assertion = str(raw_result.get("causal_assertion", "")).lower()
    faithfulness = str(raw_result.get("faithfulness", "")).lower()
    sufficiency = str(raw_result.get("sufficiency", "")).lower()
    semantic_contradiction = str(raw_result.get("semantic_contradiction", "")).lower()
    temporal_relevance = str(raw_result.get("temporal_relevance", "")).lower()
    rationale = str(raw_result.get("rationale", ""))[:1000]
    evidence_refs = [e for e in (raw_result.get("evidence_refs") or []) if isinstance(e, str)]
    contradiction_ref = str(raw_result.get("contradiction_evidence_ref", "") or "")

    for value, valid, name in (
        (causal_assertion, _VALID_CAUSAL_ASSERTION, "causal_assertion"),
        (faithfulness, _VALID_FAITHFULNESS, "faithfulness"),
        (sufficiency, _VALID_SUFFICIENCY, "sufficiency"),
        (semantic_contradiction, _VALID_CONTRADICTION, "semantic_contradiction"),
        (temporal_relevance, _VALID_TEMPORAL, "temporal_relevance"),
    ):
        if value not in valid:
            return VerifierResult(rationale=f"verifier returned invalid {name}: {value!r}")

    cited_set = set(cited_ids)
    other_set = set(other_ids)
    if any(e not in cited_set for e in evidence_refs):
        return VerifierResult(
            rationale="verifier cited evidence outside the primary claim's own supporting_evidence_ids",
        )
    if semantic_contradiction == "present":
        if not contradiction_ref or contradiction_ref not in other_set:
            return VerifierResult(
                rationale="verifier reported a contradiction without a valid Set B evidence_id to audit it",
            )

    return VerifierResult(
        causal_assertion=causal_assertion,
        faithfulness=faithfulness,
        sufficiency=sufficiency,
        semantic_contradiction=semantic_contradiction,
        temporal_relevance=temporal_relevance,
        rationale=rationale,
        evidence_refs=evidence_refs,
        contradiction_evidence_ref=contradiction_ref,
        verified_ok=True,
    )


def verify_primary_claim(
    claim,
    evidence_store: dict,
    incident_time_context: dict | None = None,
):
    """The one entry point. Receives ONLY the claim + evidence filtered to its own cited
    IDs (Set A) and the rest of the investigation's successful evidence (Set B) -- never
    the full RCA, reasoning trace, or confidence score.

    Returns (VerifierResult, usage_or_None). usage is an agent.llm.base.LLMUsage dict when
    the LLM call actually completed (even if its content was invalid), or None when the
    call itself never returned (exception before any response) -- callers must only fold
    non-None usage into investigation token/cost totals.
    """
    from agent.llm import llm_json, llm_json_failed
    from agent.prompts import VERIFIER_SYSTEM, VERIFIER_USER

    cited_ids = list(claim.supporting_evidence_ids)
    other_ids = [
        eid for eid, ev in evidence_store.items()
        if eid not in cited_ids and ev.get("ok", True)
    ]

    set_a_text, source_evidence_complete = _build_evidence_block(
        "SET A -- CITED SUPPORTING EVIDENCE (the ONLY evidence you may use to judge "
        "faithfulness and sufficiency)",
        cited_ids, evidence_store,
    )
    set_b_text, contradiction_check_complete = _build_evidence_block(
        "SET B -- OTHER COLLECTED EVIDENCE (use ONLY to check for a contradiction -- "
        "never to support the claim)",
        other_ids, evidence_store,
    )
    time_ctx_text = (
        json.dumps(incident_time_context) if incident_time_context else "(no incident timing available)"
    )

    try:
        raw_result, usage = llm_json(
            VERIFIER_SYSTEM,
            VERIFIER_USER.format(
                claim_text=claim.text,
                incident_time_context=time_ctx_text,
                set_a=set_a_text,
                set_b=set_b_text,
            ),
            max_tokens=600,
        )
    except Exception as exc:
        log.error("verifier: llm_json call raised for claim %s: %s", claim.claim_id, exc)
        return (
            VerifierResult(
                source_evidence_complete=source_evidence_complete,
                contradiction_check_complete=contradiction_check_complete,
                rationale=f"verifier call raised: {exc}",
            ),
            None,
        )

    failure = llm_json_failed(raw_result)
    if failure:
        log.error("verifier: could not parse verifier response for claim %s: %s", claim.claim_id, failure)
        return (
            VerifierResult(
                source_evidence_complete=source_evidence_complete,
                contradiction_check_complete=contradiction_check_complete,
                rationale=f"verifier response could not be parsed: {failure}",
            ),
            usage,
        )

    result = _parse_result(raw_result, cited_ids, other_ids)
    result.source_evidence_complete = source_evidence_complete
    result.contradiction_check_complete = contradiction_check_complete
    return result, usage
