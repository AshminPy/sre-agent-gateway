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

from agent.confidence.models import Claim, ClaimType, Contradiction, Hypothesis, RemediationItem

_CITATION_STOP = {
    "this", "that", "with", "from", "have", "been", "were", "they",
    "what", "when", "which", "also", "more", "than", "some", "into",
}

# issue #65: these SRE/K8s terms are so common across unrelated incidents that sharing
# ONE of them between a claim and its cited evidence is not meaningful support -- e.g.
# "the pod is CrashLooping" and "the pod is Pending" both contain "pod" while describing
# completely different situations. Excluded from the strong-overlap check below; a claim
# whose ONLY shared words are in this set gets partial credit, not full grounding.
_DOMAIN_GENERIC = {
    "pod", "pods", "node", "nodes", "container", "containers", "cluster",
    "error", "errors", "failed", "failure", "status", "state", "event",
    "events", "namespace", "deployment", "deployments", "service",
    "services", "resource", "resources", "log", "logs", "issue", "problem",
    "running", "pending", "restart", "restarts", "restarting",
}

_VALID_CLAIM_TYPES = {t.value for t in ClaimType}

# 2026-08-29: negation-aware grounding, minimal scope -- see
# docs/management/confidence-genericity-review-2026-08-28.md #15.5. A real live example was
# constructed there: evidence "restart count: 0, no CrashLoopBackOff detected" against a claim
# "the pod is in CrashLoopBackOff, restarted repeatedly" shared the token "crashloopbackoff" and
# scored grounded/1.0 -- the overlap check had no way to know the evidence was DENYING what the
# claim asserts, not confirming it. This does not attempt general negation/entailment handling
# (that's the semantic-grounding redesign explicitly out of scope) -- only the narrow, common
# SRE-evidence shape of "the tool reported the state did NOT occur."
_NEGATION_MARKERS = {
    "no", "not", "never", "none", "without", "isnt", "doesnt", "wasnt",
    "hasnt", "cannot", "cant", "didnt", "nor",
}


def _keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())} - _CITATION_STOP


def _negated_keywords(text: str, window: int = 4) -> set:
    """Keywords appearing within `window` tokens after a negation marker -- the text is
    denying these, not asserting them, so they must not count as positive overlap."""
    tokens = re.findall(r"[a-z']+", text.lower())
    negated: set = set()
    for i, tok in enumerate(tokens):
        if re.sub(r"'", "", tok) in _NEGATION_MARKERS:
            for t in tokens[i + 1: i + 1 + window]:
                w = re.sub(r"[^a-z]", "", t)
                if len(w) >= 4:
                    negated.add(w)
    return negated


def _contextual_generic_keywords(resolved_context: dict | None) -> set:
    """Keywords derived from the investigation's OWN resolved namespace/pod/cluster.

    2026-08-29, docs/management/confidence-genericity-review-2026-08-28.md #15.5: every piece
    of evidence in an investigation trivially shares the resolved namespace/pod/cluster name --
    sharing it is not evidence of TOPICAL relevance to a specific claim. Real example: a claim
    about a Service selector mismatch and evidence about an unrelated ConfigMap scored
    grounded/1.0 purely because both mentioned the shared namespace "test-incidents". Excluded
    the same way _DOMAIN_GENERIC excludes generic K8s vocabulary -- these are generic to THIS
    investigation, not evidence of anything specific to the claim.
    """
    if not resolved_context:
        return set()
    words: set = set()
    for key in ("namespace", "pod", "cluster_name", "cluster"):
        val = resolved_context.get(key)
        if val:
            words |= _keywords(str(val))
    return words


def _ground_claim(
    claim: Claim, known_evidence_ids: set, evidence_store: dict,
    resolved_context: dict | None = None,
) -> None:
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

    # 2026-08-27: a FAILED tool call's evidence entry carries no data at all
    # (evidence_extractor's error path writes ok=False, key_facts=[], and a
    # "Tool failed: ..." summary). Citing one is citing nothing, so it must be
    # treated like citing nothing -- not scored against the failure message's
    # own wording. Before this, a claim citing only failed evidence scored
    # no_overlap/0.1 purely by accident of that message's vocabulary, and would
    # have scored higher had the wording happened to overlap the claim.
    # Same `ok` filter scorer.py already applies for domain coverage (issue #91).
    cited_usable = {eid for eid in cited if evidence_store.get(eid, {}).get("ok", True)}
    if not cited_usable:
        claim.grounding_status = "failed_evidence_only"
        claim.support_strength = 0.0
        return

    claim_words = _keywords(claim.text)
    facts_words: set = set()
    facts_text_all = ""
    for eid in cited_usable:
        ev = evidence_store.get(eid, {})
        facts_text = " ".join(ev.get("key_facts", []) + [ev.get("summary", "")])
        facts_words |= _keywords(facts_text)
        facts_text_all += " " + facts_text

    # 2026-08-27: an evidence item that yields NO keywords cannot support anything.
    # This used to fall through to the final `else` below and be scored
    # "grounded" at full strength 1.0 -- the code contradicted its own comment
    # ("no basis to fully credit either"). Verified: a claim citing an item with
    # empty key_facts and empty summary returned grounded/1.0. That is the same
    # absence-treated-as-success pattern as the Model Armor incident.
    if not facts_words:
        claim.grounding_status = "empty_evidence"
        claim.support_strength = 0.0
        return

    overlap = claim_words & facts_words

    # 2026-08-29: a shared word the evidence explicitly NEGATES is not support -- it's the
    # evidence denying the claim. Checked before the no_overlap/grounded branches below so a
    # fully-negated overlap is distinguished from silence (no_overlap) and from real support
    # (grounded/weak_overlap). See docs/management/confidence-genericity-review-2026-08-28.md
    # #15.5.
    negated = _negated_keywords(facts_text_all) & overlap
    if negated and negated == overlap:
        claim.grounding_status = "contradicted"
        claim.support_strength = 0.0
        return
    overlap -= negated

    if claim_words and facts_words and not overlap:
        claim.grounding_status = "no_overlap"
        claim.support_strength = 0.1
        return

    # issue #65: overlap that's ENTIRELY generic SRE/K8s vocabulary (both mention "pod",
    # nothing more specific) is materially weaker support than overlap that includes a
    # specific identifier/detail (an error code, a named resource, a distinguishing term)
    # -- full credit used to be granted for either case identically.
    #
    # 2026-08-29: also excludes tokens from the investigation's OWN resolved namespace/pod/
    # cluster (_contextual_generic_keywords) -- every piece of evidence in an investigation
    # trivially shares those, so sharing them alone is not evidence of topical relevance to
    # THIS claim. See docs/management/confidence-genericity-review-2026-08-28.md #15.5.
    contextual_generic = _contextual_generic_keywords(resolved_context)
    if overlap and (overlap - _DOMAIN_GENERIC - contextual_generic):
        claim.grounding_status = "grounded"
        claim.support_strength = 1.0
    elif overlap:
        claim.grounding_status = "weak_overlap"
        claim.support_strength = 0.4
    else:
        # Reachable only when the CLAIM yields no keywords (facts_words is
        # guaranteed non-empty by the empty_evidence guard above) -- an empty or
        # all-stopword claim text. 2026-08-27: this branch used to award
        # "grounded" at full strength 1.0, directly contradicting its own comment
        # ("no basis to fully credit either"). A claim that says nothing cannot
        # be supported by evidence, so it gets no credit.
        claim.grounding_status = "empty_claim"
        claim.support_strength = 0.0


def build_claims(
    rca_result: dict, evidence_ids: list, evidence_store: dict,
    resolved_context: dict | None = None,
) -> list:
    """rca_result is the LLM's proposed JSON (extended RCA_BUILDER_USER schema — see
    agent/prompts.py). A MISSING 'claims' key falls back to a single claim built from the
    legacy likely_root_cause string, so this never breaks on an old-shaped LLM response. An
    EXPLICITLY EMPTY 'claims' list (e.g. the no-evidence safety path in rca_builder.py) is
    respected as zero claims, not silently reinterpreted via the legacy fallback — the caller
    meant "no claims," not "old response shape."

    resolved_context is optional (defaults to None, same as every existing caller before
    2026-08-29) -- passed through to _ground_claim so its namespace/pod/cluster tokens are
    excluded from counting as meaningful overlap (see _contextual_generic_keywords). Omitting
    it just means that specific exclusion doesn't apply -- no other behavior changes.
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
            _ground_claim(claim, known_ids, evidence_store, resolved_context)
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
        _ground_claim(claim, known_ids, evidence_store, resolved_context)
        claims.append(claim)

    return claims


def select_primary_causal_claim(claims: list, rca_result: dict, evidence_store: dict | None = None):
    """Resolves the model-proposed primary_causal_claim_index into an actual Claim,
    doing ONLY mechanical validation -- never judging whether the claim's content
    'sounds causal'. There is no safe deterministic rule for that (2026-09-01
    confidence-architecture review, point 1); the independent verifier's own
    causal_assertion field (agent/confidence/verifier.py) makes that judgment instead,
    against the claim's actual cited evidence.

    primary_causal_claim_index may be null/None -- an explicit, legitimate model
    abstention ("the evidence does not establish a specific cause"), not an error.
    When present, it is the 1-based position in the model's OWN originally-proposed
    claims[] array (build_claims()'s `for i, rc in enumerate(raw_claims, start=1)` binds
    `i` BEFORE the `isinstance(rc, dict)` skip-check on the next line, so a dropped
    malformed entry does NOT renumber survivors -- raw position 3 always becomes
    claim_003 regardless of what happened at position 2). Resolved here by that stable
    claim_id, not by re-indexing the (possibly shorter) returned `claims` list, so this
    stays correct even if build_claims()'s loop structure changes later.

    Returns None for: null index, non-integer/out-of-range index, a claim_id that
    doesn't exist in `claims` (e.g. the model's index pointed at an entry so malformed
    it was dropped before ever getting a Claim built for it), a claim whose type is
    RECOMMENDATION or HYPOTHESIS (never eligible to be "the" causal claim), a claim
    with no supporting_evidence_ids at all (nothing for the verifier to check), OR
    (2026-09-01 review, correction round 2) a claim citing evidence that is missing from
    evidence_store or whose entry has ok=False. A failed tool call still gets a raw_ref
    (its GCS-written error record is technically readable), so without this specific
    check the verifier could be handed only error text and never know it wasn't real
    evidence -- this is a deterministic PRE-verifier check precisely so that never
    happens; the verifier is not even called for a claim that fails it (see
    rca_builder.py's call site).
    """
    raw_index = rca_result.get("primary_causal_claim_index")
    if raw_index is None:
        return None
    try:
        idx = int(raw_index)
    except (TypeError, ValueError):
        return None
    if idx < 1:
        return None

    target_id = f"claim_{idx:03d}"
    by_id = {c.claim_id: c for c in claims}
    claim = by_id.get(target_id)
    if claim is None:
        return None
    if claim.claim_type in (ClaimType.RECOMMENDATION, ClaimType.HYPOTHESIS):
        return None
    if not claim.supporting_evidence_ids:
        return None
    if evidence_store is not None:
        for eid in claim.supporting_evidence_ids:
            ev = evidence_store.get(eid)
            if ev is None or not ev.get("ok", True):
                return None
    return claim


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


# Section 7 (2026-09-08): only fires on an EXPLICIT "pod X" / "deployment X" / etc. mention
# naming a resource that doesn't match anything actually collected -- deliberately narrow.
# A generic hyphen-scan over the whole action text would false-positive on ordinary English
# compound words ("read-only", "high-confidence"); requiring a resource-type keyword right
# before the name is the precise, low-noise signal a human reviewer would also use.
_RESOURCE_MENTION_RE = re.compile(
    r"\b(?:pod|deployment|namespace|service|replicaset|node|container)\s+"
    r"['\"`]?([a-z0-9][a-z0-9-]{1,61}[a-z0-9])['\"`]?",
    re.IGNORECASE,
)


def _check_identifier_warning(action: str, known_resources: set) -> str:
    """Section 7: "Validate resource identifiers... where practical." Not a full parser --
    a bounded, conservative check that only flags a resource name the action text explicitly
    names that doesn't match anything this investigation actually collected. Advisory, never
    blocks the item; false negatives (a bad name it misses) are acceptable, false positives
    that cry wolf on every remediation are not."""
    if not known_resources:
        return ""
    known_lower = {r.lower() for r in known_resources if r}
    for match in _RESOURCE_MENTION_RE.finditer(action):
        name = match.group(1).lower()
        if any(name == r or name in r or r in name for r in known_lower):
            continue
        return (
            f"references '{match.group(1)}', which doesn't match any resource collected "
            "in this investigation — verify before use"
        )
    return ""


def normalize_remediation_items(
    rca_result: dict, primary_claim, resolved_context: dict,
) -> list:
    """Coerces the LLM's suggested_remediation into structured RemediationItem objects.

    Accepts both the new structured-object schema (RCA_BUILDER_USER's current prompt) and
    plain strings (defensive -- a model that ignores the schema, or an older stored RCA
    being re-rendered, must not crash this function).

    tied_to_primary_cause is set deterministically here, never trusted from the model: True
    only when primary_claim is not None (a real, verified root cause exists). A remediation
    cannot honestly claim to address "the supported cause" when there isn't one -- this is
    the Section 7 requirement "Tie each recommendation to the supported cause or label it as
    a diagnostic next step," enforced by code, not by asking the model nicely.
    """
    raw = rca_result.get("suggested_remediation")
    if not isinstance(raw, list):
        return []

    known_resources = {
        v for v in (
            resolved_context.get("namespace"),
            resolved_context.get("pod"),
            resolved_context.get("deployment"),
            resolved_context.get("cluster_name"),
        ) if v
    }
    has_verified_cause = primary_claim is not None

    items: list = []
    for raw_item in raw[:10]:  # bounded -- render layer only shows the first 5 anyway
        if isinstance(raw_item, str):
            action = raw_item.strip()
            if not action:
                continue
            items.append(RemediationItem(
                action=action[:300],
                tied_to_primary_cause=has_verified_cause,
                item_type="remediation" if has_verified_cause else "diagnostic_next_step",
                identifier_warning=_check_identifier_warning(action, known_resources),
            ))
            continue
        if not isinstance(raw_item, dict):
            continue
        action = str(raw_item.get("action", "")).strip()
        if not action:
            continue
        model_says_diagnostic = str(raw_item.get("type", "")).lower() == "diagnostic_next_step"
        tied = has_verified_cause and not model_says_diagnostic
        items.append(RemediationItem(
            action=action[:300],
            tied_to_primary_cause=tied,
            item_type="remediation" if tied else "diagnostic_next_step",
            prerequisites=str(raw_item.get("prerequisites") or "")[:200],
            affected_scope=str(raw_item.get("affected_scope") or "")[:200],
            expected_benefit=str(raw_item.get("expected_benefit") or "")[:200],
            risk=str(raw_item.get("risk") or "not assessed")[:200],
            recovery_verification=str(raw_item.get("recovery_verification") or "")[:200],
            rollback=str(raw_item.get("rollback") or "not applicable")[:200],
            identifier_warning=_check_identifier_warning(action, known_resources),
        ))
    return items
