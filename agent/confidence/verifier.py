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
  - Any Set A evidence item whose raw source is missing/invalid or can't be read sets
    source_evidence_complete=False -- false-low is acceptable here, false-high is not, so
    this blocks CONFIRMED rather than proceeding as though the verifier saw the complete
    source. Evidence size is NEVER a reason for this -- see "Evidence availability vs.
    request size" below (2026-09-02 correction).
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

Evidence availability vs. request size (2026-09-02 correction):
  A prior version of this file excluded any single evidence item over a fixed
  3000-character ceiling, reporting it as "unavailable" alongside genuine read failures.
  Live 16-case evaluation proved this false: ordinary, successfully-read Kubernetes
  evidence (describe_k8s_resource/get_k8s_logs output) routinely exceeds 3000 characters,
  so real root causes with real supporting evidence were being scored
  insufficient_evidence purely because one cited item was verbose -- not because anything
  was actually unreadable. Evidence availability now depends ONLY on whether the source
  could be retrieved (see _fetch_sanitized_source's three states below); it is never a
  function of size. The COMPLETE Set A + Set B text is sent to the verifier every time
  evidence is available. Size is judged exactly once, against the CONFIGURED model's real
  documented input-token capacity (agent.llm.max_context_tokens(), reported by the
  provider itself, and agent.llm.count_tokens() on the exact assembled request --
  see agent.llm.base.LLMClient and https://ai.google.dev/gemini-api/docs/tokens: "Make
  this call before sending input to check the size of your requests") -- never a
  character-count proxy, and never hardcoded for any one provider/model in this file.
  If the complete request doesn't fit, that is a distinct, explicit
  context_limit_exceeded state -- evidence is NOT described as unavailable, and its
  references are preserved for audit. See _check_request_fits_context().
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from agent.gcs_client import read_evidence

log = logging.getLogger("sre-agent.confidence.verifier")

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
    context_limit_exceeded: bool = False

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
            "context_limit_exceeded": self.context_limit_exceeded,
        }


# Reasons an evidence item can be unavailable -- read/reference failure only, NEVER size
# (2026-09-02 correction; see module docstring's "Evidence availability vs. request size").
_REASON_NO_REF = "no_ref"
_REASON_READ_ERROR = "read_error"
_UNAVAILABLE_REASON_TEXT = {
    _REASON_NO_REF: "has no valid evidence reference",
    _REASON_READ_ERROR: "could not be read from source storage",
}


def _fetch_sanitized_source(ev: dict) -> tuple[str, bool, str]:
    """Returns (text, available, reason). reason is "ok" when available, else one of
    _UNAVAILABLE_REASON_TEXT's keys. Availability depends ONLY on whether the source
    could actually be retrieved -- a missing/invalid raw_ref, a read that raised, or a
    read that returned nothing. It is never a function of size: a large-but-successfully-
    read item is available, in full, same as a small one. See the module docstring."""
    raw_ref = ev.get("raw_ref", "")
    if not raw_ref or not raw_ref.startswith("gs://"):
        return "", False, _REASON_NO_REF
    try:
        raw_data = read_evidence(raw_ref)
    except Exception as exc:
        log.warning("verifier: read_evidence(%s) raised: %s", raw_ref, exc)
        return "", False, _REASON_READ_ERROR
    if not raw_data:
        return "", False, _REASON_READ_ERROR
    # raw_data is whatever read_evidence() parsed from stored JSON -- normally a dict,
    # but corrupted/unexpected stored content (a bare list, string, or number) is a real
    # possibility, not just a hypothetical. .get() on anything else would raise and crash
    # verify_primary_claim() -- treat non-dict content as unusable, same bucket as a
    # read failure, never a crash.
    if not isinstance(raw_data, dict):
        log.warning("verifier: evidence at %s is not a JSON object (got %s)", raw_ref, type(raw_data).__name__)
        return "", False, _REASON_READ_ERROR
    sanitized = raw_data.get("sanitized", raw_data)
    try:
        return json.dumps(sanitized, indent=2), True, "ok"
    except (TypeError, ValueError) as exc:
        # Corrupted/unusable stored evidence (e.g. non-serializable content) is the
        # same practical bucket as a read failure -- readable-but-garbage is no more
        # usable than unreadable. Never let this raise into verify_primary_claim() and
        # crash the investigation.
        log.warning("verifier: sanitized evidence at %s is not serializable: %s", raw_ref, exc)
        return "", False, _REASON_READ_ERROR


def _build_evidence_block(label: str, ids: list, evidence_store: dict) -> tuple[str, bool]:
    lines = [f"{label}:"]
    all_available = True
    if not ids:
        lines.append("  (none)")
    for eid in ids:
        ev = evidence_store.get(eid, {})
        text, available, reason = _fetch_sanitized_source(ev)
        if not available:
            all_available = False
            why = _UNAVAILABLE_REASON_TEXT.get(reason, "is unavailable")
            lines.append(f"  [{eid}] (source evidence {why} -- NOT INCLUDED)")
            continue
        lines.append(f"  [{eid}]:")
        lines.append(text)
    return "\n".join(lines), all_available


def _check_request_fits_context(complete_request_text: str) -> tuple[bool | None, str | None]:
    """Returns (fits, error). error is None when sizing was determined successfully (fits
    is then a real True/False); error is a short diagnostic string when the sizing check
    itself could not be completed -- a runtime/API failure here (count_tokens or
    max_context_tokens raising, or either returning something unusable) must never crash
    the investigation, and must never be confused with either "evidence unavailable" or
    "context exceeded" (see verify_primary_claim's caller for how these three states stay
    distinct). Provider-neutral: uses only agent.llm's facade, never anything
    Gemini-specific -- a future adapter for another model only needs to implement
    LLMClient.count_tokens/max_context_tokens for this function to work unchanged.
    """
    from agent.llm import count_tokens, max_context_tokens

    try:
        total_tokens = count_tokens(complete_request_text)
        limit = max_context_tokens()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if not isinstance(total_tokens, int) or total_tokens <= 0:
        return None, f"count_tokens returned an unusable value: {total_tokens!r}"
    if not isinstance(limit, int) or limit <= 0:
        return None, f"max_context_tokens returned an unusable value: {limit!r}"

    return total_tokens <= limit, None


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

    # Built ONCE -- this exact string is both what gets token-counted below and what
    # gets sent to llm_json() if it fits. Never count an approximation and then send a
    # different prompt. (llm_json()'s own adapter may append a small fixed-size
    # response-format instruction before the actual wire call -- a ~15-token addition,
    # immaterial at a ~1M-token budget -- but the evidence content itself, which is what
    # actually varies in size, is identical between what's counted and what's sent.)
    user_prompt = VERIFIER_USER.format(
        claim_text=claim.text,
        incident_time_context=time_ctx_text,
        set_a=set_a_text,
        set_b=set_b_text,
    )

    fits, sizing_error = _check_request_fits_context(f"{VERIFIER_SYSTEM}\n\n{user_prompt}")
    if sizing_error is not None:
        # count_tokens/max_context_tokens themselves failed (raised, or returned
        # something unusable) -- a distinct, third failure mode from both "evidence
        # unavailable" and "context exceeded": we could not even determine whether the
        # request fits. Fail closed the same way an LLM-call exception does, without
        # blaming the evidence (it may well be fully available) and without claiming
        # the context was exceeded (that was never established).
        log.error(
            "verifier: context-sizing check failed for claim %s: %s",
            claim.claim_id, sizing_error,
        )
        return (
            VerifierResult(
                source_evidence_complete=source_evidence_complete,
                contradiction_check_complete=contradiction_check_complete,
                rationale=f"verifier context-sizing check failed: {sizing_error}",
            ),
            None,
        )
    if not fits:
        # The complete request (all available Set A + Set B evidence) exceeds the
        # configured model's documented input-token capacity. This is NOT "evidence
        # unavailable" -- every cited item may have been read successfully; it's the
        # combined request that doesn't fit. Evidence references are preserved so the
        # audit trail still shows what was actually cited.
        log.warning(
            "verifier: complete request exceeds model context for claim %s",
            claim.claim_id,
        )
        return (
            VerifierResult(
                source_evidence_complete=source_evidence_complete,
                contradiction_check_complete=contradiction_check_complete,
                context_limit_exceeded=True,
                rationale=(
                    "verification could not complete: the complete verifier request "
                    "(all available cited and collected evidence) exceeded the "
                    "configured model's documented context capacity"
                ),
                evidence_refs=list(cited_ids),
            ),
            None,
        )

    try:
        raw_result, usage = llm_json(VERIFIER_SYSTEM, user_prompt, max_tokens=600)
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

    # 2026-09-01 review, correction round 3: without a real incident_time_context, the
    # verifier has no legitimate basis to claim EITHER "relevant" or "conflicting" -- it
    # would be guessing. Enforced deterministically here, unconditionally overriding
    # whatever the LLM returned (never trusted, regardless of value) -- this is NOT
    # approximated from investigation.started_at or evidence collected_at, both of which
    # are agent/runtime timestamps (when THIS investigation ran its tool calls), never
    # the actual incident's own timestamp. Only a real incident_time_context earns a
    # non-"unknown" temporal_relevance.
    if not incident_time_context:
        result.temporal_relevance = "unknown"

    return result, usage
