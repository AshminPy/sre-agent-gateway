"""
rca_builder.py
Builds cited RCA with:
- Every claim references a specific evidence_id
- Cluster, region, project in output
- Real token tracking from Gemini usage_metadata
- Estimated cost per investigation
- Full structured observability event to Cloud Logging (ADR Appendix C)
- Deterministic two-axis confidence scoring (agent.confidence) — the LLM proposes claims,
  hypotheses, and root cause; this node is where root_cause_confidence, outcome, and the
  legacy confidence/confidence_band fields are computed. See
  docs/confidence-framework-design.md.
"""
import json
import logging
import os
from datetime import datetime, timezone

from agent.confidence import (
    POLICY,
    derive_outcome,
    score_root_cause_confidence,
)
from agent.confidence.claim_builder import build_claims, build_hypotheses, detect_contradictions
from agent.confidence.scorer import confidence_band_from_scores
from agent.gemini_client import get_session_usage, llm_json
from agent.otel import log_node_tokens, trace_node
from agent.prompts import RCA_BUILDER_SYSTEM, RCA_BUILDER_USER
from agent.state import AgentState

log = logging.getLogger("sre-agent.rca_builder")

EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "your-gcp-project-id-evidence")
GEMINI_MODEL    = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def _evidence_digest(state: AgentState) -> str:
    store = state.get("evidence_store", {})
    if not store:
        return "No evidence collected."
    lines = []
    for ev_id, ev in store.items():
        cluster_info = ""
        if ev.get("cluster"):
            cluster_info = f" [{ev.get('cluster')}/{ev.get('region','')}]"
        lines.append(f"[{ev_id}]{cluster_info} {ev.get('summary','')[:150]}")
        for f in ev.get("key_facts", [])[:4]:
            lines.append(f"  • {f}")
        lines.append(f"  raw_ref: {ev.get('raw_ref','')}")
    return "\n".join(lines)


def _enriched_evidence_digest(state: AgentState) -> tuple:
    """
    Re-reads full raw evidence from GCS for each evidence item and builds a
    richer digest. Only called when the outcome isn't confidently resolved.

    Returns: (digest_str, was_enriched)
    Falls back silently to the compressed digest if GCS is unavailable.
    """
    from agent.gcs_client import read_evidence as gcs_read

    store  = state.get("evidence_store", {})
    ev_ids = state.get("evidence_ids", [])

    if not store or not ev_ids:
        return _evidence_digest(state), False

    lines    = []
    enriched = False

    for ev_id in ev_ids:
        ev = store.get(ev_id, {})

        cluster_info = ""
        if ev.get("cluster"):
            cluster_info = f" [{ev.get('cluster')}/{ev.get('region','')}]"
        lines.append(f"[{ev_id}]{cluster_info} {ev.get('summary','')[:150]}")
        for f in ev.get("key_facts", [])[:4]:
            lines.append(f"  • {f}")

        raw_ref = ev.get("raw_ref", "")
        if raw_ref and raw_ref.startswith("gs://"):
            raw_data = gcs_read(raw_ref)
            if raw_data:
                sanitized = raw_data.get("sanitized", raw_data)
                raw_str   = json.dumps(sanitized, indent=2)
                # Head + tail budget — same pattern as evidence_extractor
                if len(raw_str) > 3000:
                    raw_str = (
                        raw_str[:1000]
                        + "\n...[middle truncated — full output at raw_ref below]\n"
                        + raw_str[-2000:]
                    )
                lines.append("  FULL RAW (GCS re-read for low-confidence RCA):")
                lines.append(raw_str)
                enriched = True

        lines.append(f"  raw_ref: {raw_ref}")

    return "\n".join(lines), enriched


def _write_observability_log(state: AgentState, rca: dict, usage: dict) -> None:
    """
    Write full structured audit event to Cloud Logging.
    ADR Appendix C — queryable by run_id, cluster, confidence_band.
    """
    try:
        from google.cloud import logging as cloud_logging
        client   = cloud_logging.Client()
        logger_c = client.logger("sre-agent-investigations")

        inv = state["investigation"]
        ctx = state.get("resolved_context", {})

        tokens_input  = inv.get("tokens_input", 0)  + usage.get("tokens_input", 0)
        tokens_output = inv.get("tokens_output", 0) + usage.get("tokens_output", 0)
        tokens_total  = tokens_input + tokens_output
        cost_usd      = round(inv.get("estimated_cost_usd", 0.0) + usage.get("cost_usd", 0.0), 6)

        entry = {
            # Run identity
            "run_id":             state["run_id"],
            "incident_id":        state["incident_id"],
            "timestamp":          datetime.now(timezone.utc).isoformat(),

            # Incident context
            "incident_type":      ctx.get("incident_type", "Unknown"),
            "namespace":          ctx.get("namespace", ""),
            "pod":                ctx.get("pod", ""),

            # Multi-cluster routing — key for management demo
            "cluster":            ctx.get("cluster_name", ""),
            "cluster_region":     ctx.get("cluster_region", ""),
            "project_id":         ctx.get("project_id", ""),
            "mcp_source":         ctx.get("mcp_source", ""),

            # Investigation metrics
            "iterations":         inv.get("current_step", 0),
            "loop_exit_reason":   inv.get("loop_exit_reason", "unknown"),
            "tools_called":       [h.get("tool") for h in state.get("tool_history", []) if h.get("ok")],
            "failed_tools_count": sum(1 for h in state.get("tool_history", []) if not h.get("ok")),
            "failed_tools": [
                {
                    "tool":       h.get("tool"),
                    "mcp_source": h.get("mcp_source"),
                    "error":      h.get("error"),
                    "step":       h.get("step"),
                    "cluster":    h.get("cluster"),
                }
                for h in state.get("tool_history", [])
                if not h.get("ok")
            ],
            "sources_skipped":    state.get("sources_skipped", []),

            # Evidence chain
            "evidence_ids":       state.get("evidence_ids", []),
            "evaluation_ids":     state.get("evaluation_ids", []),
            "gcs_evidence_path":  f"gs://{EVIDENCE_BUCKET}/{state['run_id']}/",

            # Confidence — legacy field names preserved for existing log-based metrics/alerts
            # (iac/agent/monitoring.tf filters on jsonPayload.confidence_band directly).
            "confidence_score":   inv.get("confidence", 0.0),
            "confidence_band":    inv.get("confidence_band", "escalate"),
            "requires_human_review": rca.get("requires_human_review", True),

            # New confidence framework fields — see docs/confidence-framework-design.md
            "schema_version":         rca.get("schema_version", "2.0"),
            "outcome":                rca.get("outcome", "unknown"),
            "policy_version":         rca.get("policy_version", POLICY.version),
            "investigation_completeness_score": rca.get("investigation_completeness", {}).get("score"),
            "root_cause_confidence_score":      rca.get("root_cause_confidence", {}).get("score"),
            "contradictions_count":  len(rca.get("contradictions", [])),
            "active_hypotheses_count": sum(
                1 for h in rca.get("hypotheses", []) if h.get("status") == "active"
            ),

            # Real token tracking from Gemini metadata
            "tokens_input":       tokens_input,
            "tokens_output":      tokens_output,
            "tokens_total":       tokens_total,
            "estimated_cost_usd": cost_usd,

            # Model info
            "model_name":         GEMINI_MODEL,
            "prompt_version":     "v2.0",
            "graph_version":      "v1.0",

            # Outcome — filled in after SRE reviews the RCA
            "validation_status":  "pending",
            "sre_feedback":       None,
            "sre_notes":          None,
            "gcs_enriched":       rca.get("gcs_enriched", False),
        }

        # Session-level totals — cross-check: for single Agent Engine requests
        # these should equal tokens_total above. A mismatch means an LLM call
        # happened outside the normal node flow.
        session = get_session_usage()
        entry["session_tokens_total"] = session["session_tokens_total"]
        entry["session_cost_usd"]     = session["session_cost_usd"]
        entry["session_calls"]        = session["session_calls"]

        logger_c.log_struct(entry, severity="INFO")
        log.info(
            "observability event written run_id=%s cluster=%s tokens=%d cost=$%.6f session_total=%d",
            state["run_id"], ctx.get("cluster_name", ""), tokens_total, cost_usd,
            session["session_tokens_total"],
        )

    except Exception as e:
        log.warning("Failed to write observability log: %s", e)


@trace_node("langgraph.rca_builder")
def rca_builder(state: AgentState) -> dict:
    log.info("node=rca_builder run_id=%s", state["run_id"])

    ctx             = state.get("resolved_context", {})
    inv             = state["investigation"]
    theory          = state.get("working_theory", "")
    evidence_ids    = state.get("evidence_ids", [])
    evidence_store  = state.get("evidence_store", {})
    incident_type   = ctx.get("incident_type", "Unknown")

    no_evidence = len(evidence_ids) == 0

    # Lazy GCS re-read for thin evidence. Previously gated on confidence_band=='escalate',
    # which didn't exist yet at this point in the old design — now gated on the deterministic
    # investigation_completeness computed by task_evaluator every loop iteration (state has it
    # already; no LLM call needed to decide this).
    completeness = inv.get("completeness", {}) or {}
    thin_evidence = completeness.get("score", 0.0) < POLICY.band_thresholds["review"]

    if thin_evidence and not no_evidence:
        evidence_digest_str, gcs_enriched = _enriched_evidence_digest(state)
        if gcs_enriched:
            log.info(
                "rca_builder: thin evidence — using GCS-enriched digest (run_id=%s)",
                state["run_id"],
            )
    else:
        evidence_digest_str = _evidence_digest(state)
        gcs_enriched        = False

    memory_ctx = state.get("incident_envelope", {}).get("memory_context", "")

    result, usage = llm_json(
        RCA_BUILDER_SYSTEM,
        RCA_BUILDER_USER.format(
            query=state["incident_envelope"].get("user_query", ""),
            incident_type=incident_type,
            theory=theory,
            memory_context=memory_ctx or "No past investigations on record.",
            evidence_digest=evidence_digest_str,
            evidence_ids=json.dumps(evidence_ids),
            cluster=ctx.get("cluster_name", ""),
            region=ctx.get("cluster_region", ""),
            project=ctx.get("project_id", ""),
        ),
        max_tokens=1536,
    )
    log_node_tokens("rca_builder", state["run_id"], inv.get("current_step", 0), usage)

    # Final safety gate: no evidence means no claims can be grounded, no auto-approval.
    if no_evidence:
        result["likely_root_cause"] = "No evidence was extracted, so root cause cannot be determined."
        result["claims"] = []
        result["alternative_hypotheses_considered"] = []
        result["evidence_gaps"] = [
            "No evidence IDs were created from tool output.",
            "Fix evidence extraction/state handoff before trusting RCA output.",
        ]
        result["reasoning_trace"] = [
            "The agent cannot prove a root cause without evidence IDs.",
            "Successful tool calls alone are not enough; their outputs must be extracted and cited.",
        ]

    # ── Build claims, hypotheses, contradictions — LLM proposes, code grounds/scores ──
    claims          = build_claims(result, evidence_ids, evidence_store)
    known_ids       = set(evidence_ids)
    hypotheses      = build_hypotheses(result, known_ids)
    contradictions  = detect_contradictions(claims, result, evidence_store, ctx)

    # ── Deterministic root-cause confidence — this call decides the score, not the LLM ──
    root_cause_confidence = score_root_cause_confidence(
        claims=claims,
        contradictions=contradictions,
        hypotheses=hypotheses,
        evidence_store=evidence_store,
        tool_history=state.get("tool_history", []),
        resolved_context=ctx,
        incident_type=incident_type,
        policy=POLICY,
    )

    outcome = derive_outcome(completeness, root_cause_confidence, contradictions, hypotheses, POLICY)
    confidence_band = confidence_band_from_scores(outcome)
    confidence = root_cause_confidence["score"]

    # requires_human_review is now derived, not self-reported by the LLM — "auto" is the only
    # band that doesn't require it, and even that requires the gates below to have passed
    # (enforced by confidence_band_from_scores only ever returning "auto" for CONFIRMED, which
    # derive_outcome only returns when there are zero unresolved contradictions/hypotheses).
    requires_review = confidence_band != "auto" or no_evidence

    result["schema_version"]            = "2.0"
    result["confidence_score"]          = confidence
    result["confidence_deprecated"]     = True  # legacy field, kept for compat — see design doc §11
    result["confidence_band"]           = confidence_band
    result["outcome"]                   = outcome
    result["policy_version"]            = POLICY.version
    result["investigation_completeness"] = completeness
    result["root_cause_confidence"]      = root_cause_confidence
    result["claims"]                     = [c.to_dict() for c in claims]
    result["hypotheses"]                 = [h.to_dict() for h in hypotheses]
    result["contradictions"]             = [c.to_dict() for c in contradictions]
    result["requires_human_review"]      = requires_review
    result["run_id"]                     = state["run_id"]
    result["evidence_chain"]             = evidence_ids
    result["sources_skipped"]            = state.get("sources_skipped", [])

    # SRE feedback fields — filled in after human review.
    # sre_feedback: 'correct' | 'partial' | 'wrong'
    # sre_notes: free text from reviewing SRE
    # validation_status: 'pending' until feedback is submitted
    result["sre_feedback"]      = None
    result["sre_notes"]         = None
    result["validation_status"] = "pending"
    result["gcs_enriched"]      = gcs_enriched
    result["tools_called"]      = [h.get("tool") for h in state.get("tool_history", []) if h.get("ok")]
    result["failed_tools"]      = [
        {
            "tool":       h.get("tool"),
            "mcp_source": h.get("mcp_source"),
            "error":      h.get("error"),
            "step":       h.get("step"),
            "cluster":    h.get("cluster"),
        }
        for h in state.get("tool_history", [])
        if not h.get("ok")
    ]

    actual_sources = []
    for h in state.get("tool_history", []):
        src = h.get("mcp_source")
        if src and src not in actual_sources:
            actual_sources.append(src)

    # Add cluster routing info to RCA output. Use actual MCP sources used,
    # not only the primary source selected during context resolution.
    result["investigation_context"] = {
        "cluster":            ctx.get("cluster_name", ""),
        "cluster_region":     ctx.get("cluster_region", ""),
        "project_id":         ctx.get("project_id", ""),
        "primary_mcp_source":  ctx.get("mcp_source", ""),
        "actual_mcp_sources":  actual_sources,
        "namespace":          ctx.get("namespace", ""),
    }

    # Add real token tracking to RCA output
    total_tokens = inv.get("tokens_total", 0)    + usage.get("tokens_total", 0)
    total_cost   = inv.get("estimated_cost_usd", 0.0) + usage.get("cost_usd", 0.0)

    log.info(
        "rca_builder outcome=%s confidence=%.2f band=%s completeness=%.2f cluster=%s "
        "contradictions=%d tokens=%d cost=$%.6f requires_review=%s gcs_enriched=%s",
        outcome, confidence, confidence_band, completeness.get("score", 0.0),
        ctx.get("cluster_name", ""), len(contradictions),
        total_tokens, total_cost, requires_review, gcs_enriched,
    )

    # Write full structured observability event to Cloud Logging
    _write_observability_log(state, result, usage)

    return {
        "final_summary": result,
        "investigation": {
            "status":             "done",
            # Legacy fields — final authoritative value, overwrites whatever task_evaluator set
            # during the loop (task_evaluator no longer sets these at all — see task_evaluator.py).
            "confidence":         confidence,
            "confidence_band":    confidence_band,
            "tokens_total":       total_tokens,
            "estimated_cost_usd": round(total_cost, 6),
        },
    }
