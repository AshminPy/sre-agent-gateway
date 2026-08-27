"""Deterministic scoring — pure functions, no LLM calls, no I/O, no randomness.

Same structured input always produces the same score. The model proposes evidence, claims,
and hypotheses; these functions are the only code allowed to turn that into a number.
"""
from __future__ import annotations

import time

from agent.confidence.evidence_domains import (
    classify_tool, domain_weight,
)
from agent.confidence.models import ClaimType, InvestigationOutcome
from agent.confidence.policy import ConfidencePolicy


def _evidence_domains_present(evidence_store: dict, tool_history: list) -> dict:
    """Map evidence_id -> EvidenceDomain, via the tool that produced it.

    issue #91: excludes failed tool calls. A failed call's evidence entry (empty
    summary/key_facts -- see evidence_extractor.py's error-path construction) was
    still being classified into a domain and could count toward
    required_evidence_coverage/independent_corroboration even though zero real
    evidence was ever retrieved. A failed call already correctly counts against
    tool_success (score_investigation_completeness) -- it must not ALSO count as
    domain coverage.
    """
    tool_by_step: dict = {}
    for h in tool_history:
        step = h.get("step")
        if step is not None:
            tool_by_step[step] = h.get("tool", "")

    domains: dict = {}
    for ev_id, ev in evidence_store.items():
        if not ev.get("ok", True):
            continue
        tool = ev.get("tool") or tool_by_step.get(ev.get("step"))
        domains[ev_id] = classify_tool(tool or "")
    return domains


def score_investigation_completeness(
    state: dict, policy: ConfidencePolicy,
) -> dict:
    """100% deterministic — computed from AgentState only, the LLM is never consulted here."""
    ctx = state.get("resolved_context", {}) or {}
    inv = state.get("investigation", {}) or {}
    evidence_store = state.get("evidence_store", {}) or {}
    tool_history = state.get("tool_history", []) or []
    incident_type = ctx.get("incident_type", "Unknown")

    components: dict = {}
    gaps: list = []

    # routing_confirmed: was the cluster explicitly identified, not silently defaulted?
    # (context_resolver.py sets cluster_explicitly_provided — see that file for the one signal
    # this component needs; the defaulting behavior itself is unchanged, only observed here.)
    routing_confirmed = 1.0 if ctx.get("cluster_explicitly_provided", False) else 0.0
    if not routing_confirmed:
        gaps.append("Cluster identity was not explicitly provided — resolver used a default")
    components["routing_confirmed"] = routing_confirmed

    # identity_confirmed: did MCP source selection actually happen and get used?
    selected_mcp = state.get("selected_mcp") or ctx.get("mcp_source", "")
    used_sources = {h.get("mcp_source") for h in tool_history if h.get("mcp_source")}
    identity_confirmed = 1.0 if selected_mcp and (not tool_history or used_sources) else 0.0
    if not identity_confirmed:
        gaps.append("MCP source selection could not be confirmed against tool call history")
    components["identity_confirmed"] = identity_confirmed

    # required_evidence_coverage: domains collected vs domains this incident type requires —
    # NOT evidence count. Duplicate items in the same domain don't inflate this.
    req = policy.evidence_requirement_for(incident_type)
    domains_present = set(_evidence_domains_present(evidence_store, tool_history).values())
    required = set(req.required_now)
    if required:
        covered = len(required & domains_present)
        coverage = covered / len(required)
        missing = required - domains_present
        if missing:
            gaps.append(
                "Missing required evidence domain(s): "
                + ", ".join(sorted(d.value for d in missing))
            )
    else:
        coverage = 1.0
    components["required_evidence_coverage"] = coverage

    # 2026-08-27: evidence_domains.classify_tool's docstring promised unmapped
    # tools are "logged as a gap by the scorer" -- nothing here ever did that, so
    # unclassifiable evidence was invisible in the reported gaps. It now shows up,
    # which also makes a missing _TOOL_DOMAIN table entry findable instead of
    # silently degrading every score that touches it.
    from agent.confidence.evidence_domains import EvidenceDomain
    unknown_ids = [
        ev_id for ev_id, d in _evidence_domains_present(evidence_store, tool_history).items()
        if d is EvidenceDomain.UNKNOWN
    ]
    if unknown_ids:
        gaps.append(
            f"{len(unknown_ids)} evidence item(s) came from an unclassifiable tool "
            f"({', '.join(sorted(unknown_ids))}) — they contribute no corroboration; "
            "check the _TOOL_DOMAIN table in evidence_domains.py"
        )

    # freshness: how long ago was each piece of evidence actually collected (issue #68 --
    # evidence_extractor.py now stamps a real collected_at per item; this used to be a single
    # investigation-start proxy applied uniformly to all evidence, which couldn't tell fresh
    # evidence from stale evidence within the same run). Evidence written before this fix has
    # no collected_at -- falls back to started_at for those specific items only, not the
    # whole store, so old and new evidence in the same run are judged correctly and
    # independently.
    started_at = inv.get("started_at")
    if evidence_store:
        now = time.time()
        ages = [
            now - (ev.get("collected_at") or started_at or now)
            for ev in evidence_store.values()
        ]
        stale = [a for a in ages if a > policy.evidence_max_age_seconds]
        freshness = 0.0 if stale else 1.0
        if stale:
            gaps.append(
                f"{len(stale)} evidence item(s) collected outside the configured freshness window"
            )
    else:
        freshness = 0.0
    components["freshness"] = freshness

    # tool_success: fraction of tool calls that succeeded. Zero calls attempted = 0 (can't
    # claim completeness with nothing tried), distinct from "evidence found" checked elsewhere.
    total_calls = len(tool_history)
    failed_calls = sum(1 for h in tool_history if not h.get("ok", True))
    if total_calls == 0:
        tool_success = 0.0
        gaps.append("No tool calls were attempted")
    else:
        tool_success = (total_calls - failed_calls) / total_calls
        if failed_calls:
            gaps.append(f"{failed_calls}/{total_calls} tool call(s) failed")
    components["tool_success"] = tool_success

    # iteration_budget: did we hit max_steps before covering required domains?
    current_step = inv.get("current_step", 0)
    max_steps = inv.get("max_steps", 5)
    if current_step >= max_steps and coverage < 1.0:
        iteration_budget = 0.0
        gaps.append("Maximum investigation steps reached before required evidence was collected")
    else:
        iteration_budget = 1.0
    components["iteration_budget"] = iteration_budget

    score = sum(
        components[k] * w for k, w in policy.completeness_weights.items()
    )
    score = round(min(1.0, max(0.0, score)), 4)

    if score >= 0.85:
        band = "complete"
    elif score >= 0.60:
        band = "complete_with_gaps"
    else:
        band = "incomplete"

    return {
        "score": score,
        "band": band,
        "components": {k: round(v, 3) for k, v in components.items()},
        "gaps": gaps,
        "evidence_domains_present": sorted(d.value for d in domains_present),
        "missing_required_domains": sorted(d.value for d in (required - domains_present)),
    }


def score_root_cause_confidence(
    claims: list,
    contradictions: list,
    hypotheses: list,
    evidence_store: dict,
    tool_history: list,
    resolved_context: dict,
    incident_type: str,
    policy: ConfidencePolicy,
) -> dict:
    """Deterministic given the LLM's proposed claims/hypotheses — the LLM proposes, this scores."""
    components: dict = {}
    reasons: list = []

    root_claims = [c for c in claims if c.claim_type != ClaimType.RECOMMENDATION]

    if not root_claims:
        return {
            "score": 0.0,
            "band": "insufficient_evidence",
            "components": {k: 0.0 for k in policy.confidence_weights},
            "reasons": ["No claims were made about the root cause"],
        }

    # direct_support: fraction of root claims that are OBSERVED_FACT rather than inference/hyp.
    # issue #66: claim_type is the LLM's own self-assigned label (only enum-validated in
    # claim_builder.py, never checked against evidence content) -- a claim self-labeled
    # OBSERVED_FACT must ALSO be independently grounded (grounding_status == "grounded",
    # set deterministically by _ground_claim) to count here. Otherwise the model could call
    # anything an "observed fact" and get full direct_support credit for it regardless of
    # whether the evidence actually supports it.
    fact_claims = [
        c for c in root_claims
        if c.claim_type == ClaimType.OBSERVED_FACT and c.grounding_status == "grounded"
    ]
    mislabeled_facts = [
        c for c in root_claims
        if c.claim_type == ClaimType.OBSERVED_FACT and c.grounding_status != "grounded"
    ]
    direct_support = len(fact_claims) / len(root_claims)
    components["direct_support"] = direct_support
    if direct_support < 1.0:
        not_direct = len(root_claims) - len(fact_claims)
        reasons.append(
            f"{not_direct}/{len(root_claims)} claim(s) are inference or hypothesis, "
            "or a self-labeled observed fact not independently grounded in evidence"
        )
    if mislabeled_facts:
        reasons.append(
            f"{len(mislabeled_facts)} claim(s) labeled 'observed_fact' by the model were not "
            "independently grounded -- treated as unverified, not direct support"
        )

    # independent_corroboration: distinct evidence DOMAINS behind the claims, domain-weighted
    # so related domains (current+previous logs) don't count as two independent sources.
    domain_map = _evidence_domains_present(evidence_store, tool_history)
    supporting_ids = {eid for c in root_claims for eid in c.supporting_evidence_ids}
    supporting_domains = {domain_map[eid] for eid in supporting_ids if eid in domain_map}
    weighted = sum(domain_weight(d, supporting_domains) for d in supporting_domains)
    independent_corroboration = min(1.0, weighted / 2.0)  # 2 weighted independent sources = full
    components["independent_corroboration"] = independent_corroboration
    if independent_corroboration < 1.0:
        reasons.append(
            f"Only {weighted:.1f} independently-weighted evidence source(s) behind the claim"
        )
    else:
        reasons.append(f"{len(supporting_domains)} independent evidence domain(s) corroborate the claim")

    # resource_identity_match: does supporting evidence match the resolved cluster/namespace/pod?
    # (fixed 2026-08-12, issue #67 -- this comment used to describe the intent while the code
    # below only checked cluster; namespace/pod are now actually compared against evidence's
    # resource_id field.)
    # A claim with NO supporting evidence gets zero here too — "matches" is meaningless (and
    # must not score positively) when there is nothing to match against. Matches
    # "a claim without valid supporting evidence must not contribute positively" beyond just
    # claim_grounding — every component must independently refuse to credit an empty claim.
    if not supporting_ids:
        resource_identity_match = 0.0
    else:
        mismatches = 0
        namespace_pod_mismatches = 0
        resolved_namespace = resolved_context.get("namespace", "")
        resolved_pod = resolved_context.get("pod", "")
        for eid in supporting_ids:
            ev = evidence_store.get(eid, {})
            if ev.get("cluster") and resolved_context.get("cluster_name"):
                if ev["cluster"] != resolved_context["cluster_name"]:
                    mismatches += 1
            # issue #67: this function's own comment claimed cluster/namespace/pod were all
            # checked, but only cluster ever was. evidence_extractor.py stores identity as a
            # combined "resource_id" (e.g. "namespace/pod-name"), not separate keys -- so this
            # checks containment, not equality. Lenient by design, matching the cluster check
            # above: only flags an EXPLICIT mismatch (both sides known, resource_id present,
            # neither the resolved namespace nor pod name appears in it) -- a legitimate
            # investigation touches evidence about the incident (events, node info) that may
            # not literally embed the pod name, and this must not penalize that.
            resource_id = ev.get("resource_id", "")
            if resource_id and (resolved_namespace or resolved_pod):
                namespace_ok = not resolved_namespace or resolved_namespace in resource_id
                pod_ok = not resolved_pod or resolved_pod in resource_id
                if not (namespace_ok and pod_ok):
                    namespace_pod_mismatches += 1
        resource_identity_match = 1.0 if not (mismatches or namespace_pod_mismatches) else max(
            0.0, 1.0 - 0.5 * (mismatches + namespace_pod_mismatches)
        )
        if mismatches:
            reasons.append(f"{mismatches} supporting evidence item(s) reference a different cluster")
        if namespace_pod_mismatches:
            reasons.append(
                f"{namespace_pod_mismatches} supporting evidence item(s) reference a different "
                "namespace/pod than the resolved investigation target"
            )
    components["resource_identity_match"] = resource_identity_match

    # time_correlation: how closely in time was this claim's supporting evidence actually
    # collected (issue #68 -- evidence_extractor.py now stamps a real collected_at per item).
    # Evidence gathered close together is more likely to reflect the SAME incident state;
    # evidence spread far apart risks mixing stale and fresh signals within one claim. No
    # per-evidence timestamp existed before this fix at all -- this was previously a flat
    # 1.0/0.0 with no real time signal behind it.
    if not supporting_ids:
        time_correlation = 0.0
    else:
        support_timestamps = [
            ts for eid in supporting_ids
            if (ts := evidence_store.get(eid, {}).get("collected_at")) is not None
        ]
        if len(support_timestamps) < 2:
            # 0 or 1 real timestamp (older evidence predating this fix, or a single-item
            # claim) — nothing to compare, neutral rather than fabricated.
            time_correlation = 1.0
        else:
            spread = max(support_timestamps) - min(support_timestamps)
            if spread <= policy.evidence_max_age_seconds:
                time_correlation = 1.0
            else:
                time_correlation = max(
                    0.0, 1.0 - (spread - policy.evidence_max_age_seconds) / policy.evidence_max_age_seconds
                )
                reasons.append(
                    f"Supporting evidence for this claim was collected {spread:.0f}s apart, "
                    "wider than the freshness window"
                )
    components["time_correlation"] = time_correlation

    # claim_grounding: mean support_strength across root claims (set by claim-building step,
    # itself derived from the existing _validate_citations-style phantom/overlap checks).
    claim_grounding = sum(c.support_strength for c in root_claims) / len(root_claims)
    components["claim_grounding"] = claim_grounding
    ungrounded = [c for c in root_claims if c.grounding_status != "grounded"]
    if ungrounded:
        reasons.append(
            f"{len(ungrounded)} claim(s) not fully grounded in cited evidence "
            f"({', '.join(sorted({c.grounding_status for c in ungrounded}))})"
        )

    base_score = sum(components[k] * w for k, w in policy.confidence_weights.items())

    # Penalties — subtracted after the weighted base, per design doc §6.
    contradiction_penalty = min(
        policy.contradiction_penalty_cap,
        len(contradictions) * policy.contradiction_penalty_per_item,
    )
    components["contradiction_penalty"] = contradiction_penalty
    if contradictions:
        reasons.append(f"{len(contradictions)} contradiction(s) detected against the proposed cause")

    active_alt = [h for h in hypotheses if h.status == "active" and h.supporting_evidence_ids]
    alt_penalty = policy.alternative_hypothesis_penalty if active_alt else 0.0
    components["alternative_hypothesis_penalty"] = alt_penalty
    if active_alt:
        reasons.append(f"{len(active_alt)} unresolved competing hypothesis/hypotheses with evidence")

    req = policy.evidence_requirement_for(incident_type)
    domains_present = set(domain_map.values())
    missing_required = set(req.required_now) - domains_present
    missing_penalty = min(
        policy.missing_evidence_penalty_cap,
        len(missing_required) * policy.missing_evidence_penalty_per_domain,
    )
    components["missing_evidence_penalty"] = missing_penalty
    if missing_required:
        reasons.append(
            "Missing required evidence for this incident type: "
            + ", ".join(sorted(d.value for d in missing_required))
        )

    score = base_score - contradiction_penalty - alt_penalty - missing_penalty

    # Hard caps — applied after the weighted score, independent of it. A high weighted average
    # can never buy its way past a real gate. This is what "no auto-post on a numeric threshold
    # alone" means in practice.
    if missing_required:
        score = min(score, policy.max_score_missing_critical_evidence)
    if contradictions:
        score = min(score, policy.max_score_unresolved_contradiction)
    if active_alt:
        score = min(score, policy.max_score_unresolved_hypothesis)

    score = round(min(1.0, max(0.0, score)), 4)

    if score >= policy.band_thresholds["auto"]:
        band = "high_confidence"
    elif score >= policy.band_thresholds["review"]:
        band = "review_required"
    elif score >= 0.35:
        band = "partial_evidence"
    else:
        band = "insufficient_evidence"

    return {
        "score": score,
        "band": band,
        "components": {k: round(v, 3) for k, v in components.items()},
        "reasons": reasons,
    }


def derive_outcome(
    completeness: dict,
    confidence: dict,
    contradictions: list,
    hypotheses: list,
    policy: ConfidencePolicy,
) -> str:
    """Deterministic outcome — never asked of the LLM. See design doc §7."""
    if contradictions and any(c.severity >= 0.5 for c in contradictions):
        return InvestigationOutcome.CONFLICTING_EVIDENCE.value

    if completeness["score"] < 0.35 or confidence["score"] == 0.0:
        return InvestigationOutcome.INSUFFICIENT_EVIDENCE.value

    active_alt = [h for h in hypotheses if h.status == "active" and h.supporting_evidence_ids]

    conf_score = confidence["score"]
    complete_enough = completeness["score"] >= 0.60

    if not complete_enough:
        return InvestigationOutcome.INSUFFICIENT_EVIDENCE.value

    if conf_score >= policy.band_thresholds["auto"] and not active_alt and not contradictions:
        return InvestigationOutcome.CONFIRMED.value
    if conf_score >= policy.band_thresholds["review"]:
        return InvestigationOutcome.PROBABLE.value
    if conf_score >= 0.35:
        return InvestigationOutcome.POSSIBLE.value
    return InvestigationOutcome.UNKNOWN.value


def confidence_band_from_scores(outcome: str) -> str:
    """Maps the new outcome back to the LEGACY 3-value confidence_band vocabulary
    (auto/review/escalate) that iac/agent/monitoring.tf's log-based metrics and alert
    policies filter on directly. Do not change these 3 values without also updating
    monitoring.tf — see docs/confidence-framework-design.md §11.
    """
    if outcome == InvestigationOutcome.CONFIRMED.value:
        return "auto"
    if outcome == InvestigationOutcome.PROBABLE.value:
        return "review"
    return "escalate"
