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
import time
from datetime import datetime, timezone

from agent.confidence import (
    POLICY,
    derive_outcome,
    score_root_cause_confidence,
)
from agent.confidence.claim_builder import build_claims, build_hypotheses, detect_contradictions
from agent.confidence.scorer import confidence_band_from_scores
from agent.llm import get_session_usage, llm_json
from agent.otel import get_trace_id_hex, log_node_tokens, trace_node
from agent.prompts import RCA_BUILDER_SYSTEM, RCA_BUILDER_USER
from agent.state import AgentState

log = logging.getLogger("sre-agent.rca_builder")

EVIDENCE_BUCKET = os.environ.get("EVIDENCE_BUCKET", "your-gcp-project-id-evidence")


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


def _derive_status(state: AgentState) -> str:
    """Top-level pass/fail signal for this run.

    PRODUCTION-LAUNCH-PLAN.md Priority 10: "a top-level status field (so the errors
    metric may never fire from RCA-path failures)". iac/agent/monitoring.tf's
    pre-existing `errors` log-based metric filters on jsonPayload.status=="error", but no
    log entry has ever set that field — so it could never fire, regardless of how many
    real failures occurred. "error" here means a real system/tooling failure was
    recorded during the run (routing safe-stop, tool failure, evidence-storage failure,
    abnormal loop exit — anything that appended to state["errors"]) — not a low-
    confidence-but-clean RCA outcome, which outcome/confidence_band already cover.
    """
    return "error" if state.get("errors") else "success"


def _evidence_storage_stats(state: AgentState) -> dict:
    """Evidence-storage (GCS write) success/failure visibility — PRODUCTION-LAUNCH-PLAN.md
    Priority 10. evidence_extractor.py sets ev_entry["gcs_write_failed"]=True per item
    when write_evidence() exhausts its retries (see agent/gcs_client.py)."""
    store  = state.get("evidence_store", {})
    failed = [ev_id for ev_id, ev in store.items() if ev.get("gcs_write_failed")]
    return {
        "evidence_storage_failed_count": len(failed),
        "evidence_storage_failed_ids":   failed,
        "evidence_storage_ok":           len(failed) == 0,
    }


def _write_observability_log(
    state: AgentState, rca: dict, usage: dict, tok: dict,
    verifier_usage: dict | None = None, verifier_result=None,
) -> None:
    """
    Write full structured audit event to Cloud Logging.
    ADR Appendix C — queryable by run_id, cluster, confidence_band.

    `tok` is the ALREADY-COMPUTED final investigation-level token/cost totals (from the
    caller's own accumulate_usage() chain) — 2026-09-01 (confidence-architecture review,
    point 7): this function used to recompute its own `tok` here via
    accumulate_usage(state["investigation"], usage), which only ever folded THIS node's
    own llm_json() call. When the primary-claim verifier also runs, that recomputation
    would silently exclude the verifier's usage from this log entry (while
    session_tokens_total below, coming from the shared LLM adapter, WOULD include it) —
    turning the entry's own documented cross-check ("these should equal... a mismatch
    means an LLM call happened outside the normal node flow") into a guaranteed false
    alarm on every verified investigation. Accepting the caller's already-correct `tok`
    instead of recomputing removes the possibility of the two diverging.
    """
    try:
        from google.cloud import logging as cloud_logging
        # issue #139 fix, confirmed live 2026-08-23. Root cause: this Cloud Logging write
        # 403'd ("unregistered in the Agent Registry") on 144 of 145 real attempts over 180
        # days when using the default gRPC transport -- Agent Gateway never resolved a
        # registry match for it (empty agentGatewayInfo), unlike every other working
        # destination (Trace, GKE, storage, custom MCP), which all show a real resolved
        # registry resource. Fix: _use_grpc=False (HTTP transport), matched by the
        # us-central1-logging registry entry's protocolBinding=HTTP_JSON (Cloud Trace's own
        # entry is untouched -- separate hostname, separate resource). Verified: 3/3 clean
        # live runs after the change, same fix applied to tool_executor.py/mcp_router.py/
        # gcs_client.py's identical calls.
        client   = cloud_logging.Client(project=os.environ.get("PROJECT_ID"), _use_grpc=False)
        logger_c = client.logger("sre-agent-investigations")

        inv = state["investigation"]
        ctx = state.get("resolved_context", {})

        tokens_total  = tok["tokens_total"]
        cost_usd      = tok["estimated_cost_usd"]

        # Session-level totals — cross-check: should equal tokens_total above. A
        # mismatch means an LLM call happened outside the normal node flow. Fetched
        # here (not after log_struct) so model_latency_s can be included in the entry
        # below. issue #74 (fixed): the adapter instance is cached process-wide and
        # reused across investigations -- agent/main.py's investigate() now calls
        # reset_session() at the start of every investigation, so this is genuinely
        # this investigation's own total, not a cross-investigation accumulation.
        session = get_session_usage()

        # Latency — PRODUCTION-LAUNCH-PLAN.md Priority 10 ("MCP/model/total latency —
        # per-tool duration_s is captured then discarded"). mcp_latency_s aggregates the
        # per-tool durations tool_executor.py already records in tool_history;
        # model_latency_s is the cumulative Gemini call time for THIS investigation
        # (gemini_client.get_session_usage, reset per-investigation as of issue #74 --
        # see the comment above); total_latency_s is measured wall-clock since
        # get_initial_state() set investigation["started_at"].
        mcp_latency_s = round(
            sum(h.get("duration_s", 0) or 0 for h in state.get("tool_history", [])), 3
        )
        started_at = inv.get("started_at")
        total_latency_s = round(time.time() - started_at, 3) if started_at else None

        evidence_storage = _evidence_storage_stats(state)

        from agent.llm import MODEL as _deployed_model_name

        entry = {
            # Run identity
            "run_id":             state["run_id"],
            "incident_id":        state["incident_id"],
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            # Cloud Trace correlation — "" when the OTel tracer is disabled/unavailable
            # (get_trace_id_hex() never raises). Join this log entry to its Cloud Trace
            # spans (trace_node's langgraph.* spans) via this id.
            "trace_id":           get_trace_id_hex(),

            # Top-level pass/fail — see _derive_status(). Restores the pre-existing
            # `errors` log-based metric (iac/agent/monitoring.tf), which filters on
            # jsonPayload.status=="error" but no entry ever set this field before.
            "status":             _derive_status(state),

            # PagerDuty incident id — placeholder until PRODUCTION-LAUNCH-PLAN.md
            # Priority 2 (PagerDuty incident integration) is built. Always None today;
            # wire from resolved_context once P2 lands so RCA logs correlate 1:1 with the
            # triggering PD incident.
            "pagerduty_incident_id": ctx.get("pagerduty_incident_id"),

            # Agent Gateway / Connect Gateway failure visibility — explicit placeholder
            # fields, not silently omitted, so the schema already has a slot for these
            # once the underlying capability is real:
            #   - connect_gateway_status: always None. Priority 3 (GKE Fleet Connect
            #     Gateway, on-prem connectivity) has NO code yet — mcp_client.py only
            #     reaches GKE directly + the custom Cloud Run MCP. Nothing to report.
            #   - agent_gateway_authz_mode: always None. iac/agent/agent_gateway.tf's IAP
            #     authz extension runs in DRY_RUN (logs decisions, never blocks — see that
            #     file's comment above google_network_services_authz_extension.iap), and
            #     the gateway does not emit any app-observable signal into the agent
            #     process today — the agent has no way to know at runtime whether a given
            #     call transited the gateway or how it was authorized. Wire this once the
            #     gateway is switched to enforce mode and/or its audit logs are joined in.
            "connect_gateway_status":    None,
            "agent_gateway_authz_mode":  None,

            # Incident context
            "incident_type":      ctx.get("incident_type", "Unknown"),
            "namespace":          ctx.get("namespace", ""),
            "pod":                ctx.get("pod", ""),

            # Multi-cluster routing — key for management demo
            "cluster":            ctx.get("cluster_name", ""),
            "cluster_region":     ctx.get("cluster_region", ""),
            "project_id":         ctx.get("project_id", ""),
            "mcp_source":         ctx.get("mcp_source", ""),
            "cluster_routing_method": ctx.get("cluster_routing_method", ""),
            "cluster_routing_reason": ctx.get("cluster_routing_reason", ""),

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

            # Real token tracking from Gemini metadata — tokens_total is Gemini's own
            # reported total (never a local input+output recomputation, see
            # agent/llm/accounting.py). Granular breakdown (candidates vs reasoning vs
            # cached vs tool-use) added additively — issue #63 PR 1.
            "tokens_input":         tok["tokens_input"],
            "tokens_cached_input":  tok["tokens_cached_input"],
            "tokens_output":        tok["tokens_output"],
            "tokens_candidates":    tok["tokens_candidates"],
            "tokens_reasoning":     tok["tokens_reasoning"],
            "tokens_tool_use":      tok["tokens_tool_use"],
            "tokens_total":         tokens_total,
            "estimated_cost_usd":   cost_usd,

            # Latency — see comment above where these are computed.
            "mcp_latency_s":      mcp_latency_s,
            "model_latency_s":    session["session_model_latency_s"],
            "total_latency_s":    total_latency_s,

            # Primary-claim verifier call (2026-09-01 confidence-architecture review,
            # point 7) — None/False fields when no primary claim was selected, so a
            # verifier call never ran for this investigation.
            "verifier_ran":       verifier_usage is not None,
            "verifier_latency_s": (verifier_usage or {}).get("duration_s"),
            "verifier_ok":        bool(verifier_result and verifier_result.verified_ok),

            # Evidence-storage (GCS) success/failure — see _evidence_storage_stats().
            "evidence_storage_ok":           evidence_storage["evidence_storage_ok"],
            "evidence_storage_failed_count": evidence_storage["evidence_storage_failed_count"],
            "evidence_storage_failed_ids":   evidence_storage["evidence_storage_failed_ids"],

            # Model info
            "model_name":         _deployed_model_name,
            "prompt_version":     "v2.0",
            "graph_version":      "v1.0",

            # Outcome — filled in after SRE reviews the RCA
            "validation_status":  "pending",
            "sre_feedback":       None,
            "sre_notes":          None,
            "gcs_enriched":       rca.get("gcs_enriched", False),

            # Session-level totals — cross-check: for single Agent Engine requests
            # these should equal tokens_total/model_latency_s above. A mismatch means an
            # LLM call happened outside the normal node flow.
            "session_tokens_total": session["session_tokens_total"],
            "session_cost_usd":     session["session_cost_usd"],
            "session_calls":        session["session_calls"],
        }

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

    # 2026-08-27: was `len(evidence_ids) == 0`, which counted evidence SLOTS
    # rather than usable evidence. A FAILED tool call still gets an ev_id and a
    # store entry (evidence_extractor's error path writes ok=False with empty
    # key_facts), so a run where every single tool call failed still produced
    # evidence_ids=["ev_001","ev_002","ev_003"] -> no_evidence=False -> this
    # safety gate never fired -> the LLM was asked to write a root cause from
    # three failure records.
    #
    # Fixing the Model Armor bug made this MORE reachable, not less: blocked
    # calls now correctly become ok=False failures, which is exactly the shape
    # that fills slots without carrying data.
    #
    # scorer.py's _evidence_domains_present already applies this same
    # `if not ev.get("ok", True): continue` filter (issue #91, so failed calls
    # can't count toward required_evidence_coverage). This gate was simply never
    # given the same treatment -- an inconsistency, not a deliberate difference.
    usable_evidence_ids = [
        ev_id for ev_id in evidence_ids
        if evidence_store.get(ev_id, {}).get("ok", True)
    ]
    no_evidence = len(usable_evidence_ids) == 0
    if evidence_ids and no_evidence:
        log.error(
            "rca_builder: %d evidence slot(s) exist but ALL are failed tool calls "
            "(run_id=%s) -- treating as no evidence. No root cause can be grounded.",
            len(evidence_ids), state["run_id"],
        )

    # Lazy GCS re-read for thin evidence. Previously gated on confidence_band=='escalate',
    # which didn't exist yet at this point in the old design — now gated on the deterministic
    # investigation_completeness computed by task_evaluator every loop iteration (state has it
    # already; no LLM call needed to decide this).
    #
    # task_evaluator never runs (so investigation.completeness is never set) when
    # input_normalizer or context_resolver safe-stops and graph.py routes straight here — e.g.
    # Task 1's ambiguous/unresolved cluster routing safe-stop. A bare {} has no "score" key,
    # which used to raise KeyError inside confidence.scorer.derive_outcome("completeness["score"]")
    # instead of yielding an insufficient_evidence outcome. Synthesize a proper zero-completeness
    # dict — same shape score_investigation_completeness() would produce — so downstream scoring
    # always has a real "score" key to read, regardless of which safe-stop path got here.
    completeness = inv.get("completeness") or {
        "score": 0.0,
        "band": "incomplete",
        "gaps": ["Investigation stopped before task_evaluator ran (safe-stop)"],
        "evidence_domains_present": [],
        "missing_required_domains": [],
    }
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

    # 2026-08-27: a failed/unconfigured Memory Bank recall used to arrive here as
    # "" and be rendered to the model as "No past investigations on record." --
    # an assertion that could lead it to reason "this is a novel incident" with
    # nothing backing that. Tell the model the truth: unknown, not none.
    # Local import beside its use (the auto-formatter strips distant imports).
    from agent.main import MEMORY_RECALL_UNAVAILABLE
    if memory_ctx == MEMORY_RECALL_UNAVAILABLE:
        memory_ctx_for_prompt = (
            "Prior investigations could NOT be checked — the memory store was "
            "unreachable. Do not assume this incident is novel or recurring."
        )
    else:
        memory_ctx_for_prompt = memory_ctx or "No past investigations on record."

    result, usage = llm_json(
        RCA_BUILDER_SYSTEM,
        RCA_BUILDER_USER.format(
            query=state["incident_envelope"].get("user_query", ""),
            incident_type=incident_type,
            theory=theory,
            memory_context=memory_ctx_for_prompt,
            evidence_digest=evidence_digest_str,
            # 2026-08-27: was json.dumps(evidence_ids) — the FULL list, which
            # offered the model failed/unextractable evidence IDs as things it
            # could cite. _ground_claim now scores such a citation 0.0, so the
            # damage is contained, but the cleaner fix is not to offer them at
            # all: the prompt says "Every claim MUST reference a specific
            # evidence_id", and it should only ever be handed IDs that carry data.
            evidence_ids=json.dumps(usable_evidence_ids),
            cluster=ctx.get("cluster_name", ""),
            region=ctx.get("cluster_region", ""),
            project=ctx.get("project_id", ""),
        ),
        # 2026-08-31: was 1536. Raised as part of root-causing the intermittent
        # "model's response could not be parsed" failure (cascading-001,
        # pending-001, mcp-gateway-failure-001 -- see docs/management/
        # confidence-genericity-review-2026-08-28.md). rca_builder's output schema
        # (RCA_BUILDER_USER) is the most verbose of any llm_json() call in this
        # codebase -- claims[], alternative_hypotheses_considered[], reasoning_trace[],
        # suggested_remediation[], each an array of multi-field objects -- and 1536
        # was the tightest budget relative to that shape of any node (compare
        # evidence_extractor=700, task_evaluator=400, task_planner/mcp_router=300 --
        # all far simpler schemas). A truncated response can never be repaired by
        # llm_json()'s brace-matching (a cut-off string/object is missing information,
        # not malformed text), so under-budgeting here was a guaranteed parse failure
        # for any multi-hop case with more than a few claims. agent/llm/gemini_adapter.py's
        # llm_json() now also detects and retries once on an actual MAX_TOKENS
        # truncation (belt-and-suspenders); this raise is the primary fix, since it
        # removes the truncation for the normal case instead of relying on the retry.
        max_tokens=3072,
    )
    log_node_tokens("rca_builder", state["run_id"], inv.get("current_step", 0), usage)

    # 2026-08-27: the model's response could not be parsed as JSON at all. This
    # used to arrive as a bare {} and sail straight through: likely_root_cause
    # became "", claims fell back to a single empty legacy claim, and the report
    # rendered as a confident-looking blank RCA with no indication the model call
    # had failed. Report the failure instead of dressing it up as an answer.
    # Local import beside its use -- the auto-formatter strips a top-level import
    # whose usage lands in a separate edit.
    from agent.llm import llm_json_failed
    llm_failure = llm_json_failed(result)
    if llm_failure:
        log.error(
            "rca_builder: llm_json could not parse the model response (run_id=%s): %s",
            state["run_id"], llm_failure,
        )
        result = {
            "likely_root_cause": (
                "The model's response could not be parsed, so no root cause was produced."
            ),
            "claims": [],
            "alternative_hypotheses_considered": [],
            "evidence_gaps": [
                f"RCA generation failed: {llm_failure}.",
                "This is a model/parsing failure, not an evidence failure — "
                "the collected evidence may be fine.",
            ],
            "reasoning_trace": [
                "The RCA model call returned a response that was not valid JSON.",
                "No claim can be made from an unparsed response.",
            ],
        }

    # Final safety gate: no USABLE evidence means no claims can be grounded, no
    # auto-approval. Two distinct ways to get here, reported distinctly -- the
    # old wording ("No evidence IDs were created") was simply false in the
    # all-failed case and would send the reader looking in the wrong place.
    if no_evidence:
        all_calls_failed = bool(evidence_ids)
        if all_calls_failed:
            failed_summary = "; ".join(
                f"{ev_id}: {evidence_store.get(ev_id, {}).get('summary', 'unknown failure')[:120]}"
                for ev_id in evidence_ids
            )
            result["likely_root_cause"] = (
                "Every tool call failed, so no evidence was retrieved and the root cause "
                "cannot be determined."
            )
            result["evidence_gaps"] = [
                f"All {len(evidence_ids)} tool call(s) failed — zero usable evidence.",
                f"Failures: {failed_summary}",
                "Fix the failing tool calls before trusting any RCA output.",
            ]
            result["reasoning_trace"] = [
                "Evidence slots exist, but every one records a failed tool call.",
                "A failed call carries no data, so nothing here can ground a claim.",
                "Reporting the tool failures instead of inferring a cause from them.",
            ]
        else:
            result["likely_root_cause"] = "No evidence was extracted, so root cause cannot be determined."
            result["evidence_gaps"] = [
                "No evidence IDs were created from tool output.",
                "Fix evidence extraction/state handoff before trusting RCA output.",
            ]
            result["reasoning_trace"] = [
                "The agent cannot prove a root cause without evidence IDs.",
                "Successful tool calls alone are not enough; their outputs must be extracted and cited.",
            ]
        result["claims"] = []
        result["alternative_hypotheses_considered"] = []

    # ── Build claims, hypotheses, contradictions — LLM proposes, code grounds/scores ──
    claims          = build_claims(result, evidence_ids, evidence_store, ctx)
    known_ids       = set(evidence_ids)
    hypotheses      = build_hypotheses(result, known_ids)
    contradictions  = detect_contradictions(claims, result, evidence_store, ctx)

    # ── Legacy root-cause confidence — DIAGNOSTIC/COMPATIBILITY ONLY (2026-09-01
    # confidence-architecture review, point 8). Still computed exactly as before (same
    # weighted components, same hard caps, unchanged) for the existing
    # result["root_cause_confidence"] field and any dashboard/report that reads it — but
    # it is no longer consulted by derive_outcome() below. It remains
    # POLICY.version == "1.0.0-uncalibrated" and must never be described as a probability.
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

    # ── Primary causal claim + independent verification (2026-09-01 confidence-
    # architecture review, frozen architecture: incident -> evidence collection ->
    # primary causal claim -> independent verification against source evidence ->
    # deterministic safety gates -> operational outcome). ──
    from agent.confidence.claim_builder import select_primary_causal_claim
    from agent.confidence.verifier import verify_primary_claim

    primary_claim = select_primary_causal_claim(claims, result)
    verifier_result = None
    # Separate from `inv` on purpose: accumulate_usage() returns a dict containing ONLY
    # the 8 token/cost fields (not a copy of `inv` with those fields updated) — reassigning
    # `inv` itself to that return value would silently drop current_step/started_at/etc.
    # for the rest of this function. Folded into the final accumulate_usage(..., usage)
    # call below instead, so both calls' usage lands in one correct final `tok`.
    verifier_tok = None
    verifier_usage = None

    if primary_claim is None:
        # Explicit, not a fallthrough (point 2, final review round): null/invalid index,
        # or a resolved claim with no usable evidence, is itself the answer — the verifier
        # is never called, there is nothing to verify.
        result["likely_root_cause"] = (
            result.get("likely_root_cause")
            or "No specific root cause was established from the available evidence."
        )
    else:
        # Minimal explicit incident-time context (point 5, final review round) — only
        # fields that actually exist, never the full resolved_context/AgentState.
        incident_time_context = {
            k: v for k, v in {
                "reported_at": ctx.get("incident_reported_at"),
                "incident_start": ctx.get("incident_start"),
                "incident_end": ctx.get("incident_end"),
            }.items() if v
        }
        verifier_result, verifier_usage = verify_primary_claim(
            primary_claim, evidence_store, incident_time_context or None,
        )
        if verifier_usage is not None:
            # Point 7 (final review round): the verifier is a real additional model call —
            # fold its usage into the SAME investigation totals as the RCA-generation call
            # below, via the same accumulate_usage() helper (issue #63's one shared
            # accumulator), not a side-channel log. Stored in verifier_tok, not `inv` itself
            # (see the note above) — the final accumulate_usage(..., usage) call further
            # down uses verifier_tok as its base when present, so both calls' usage lands
            # in one correct final total instead of either one silently disappearing.
            from agent.llm.accounting import accumulate_usage as _accumulate_usage
            verifier_tok = _accumulate_usage(inv, verifier_usage)
        # One authoritative user-facing root cause: derived directly from the verified
        # primary claim's own text, never an independently-generated second string
        # (point 2, prior review round — closes the paraphrase-divergence bug class
        # structurally, not by detecting it after the fact).
        result["likely_root_cause"] = primary_claim.text

    outcome = derive_outcome(
        primary_claim, verifier_result, completeness, contradictions, hypotheses,
        evidence_store, ctx, POLICY,
    )
    confidence_band = confidence_band_from_scores(outcome)
    confidence = root_cause_confidence["score"]

    result["primary_causal_claim_id"] = primary_claim.claim_id if primary_claim else None
    result["verifier_result"] = verifier_result.to_dict() if verifier_result else None

    # requires_human_review is now derived, not self-reported by the LLM — "auto" is the only
    # band that doesn't require it, and even that requires the gates below to have passed
    # (enforced by confidence_band_from_scores only ever returning "auto" for CONFIRMED, which
    # derive_outcome only returns for a fully-verified, ungated primary causal claim — see
    # agent/confidence/scorer.py's derive_outcome docstring for the complete gate list).
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
        # Deterministic cluster-routing decision from context_resolver.py — see
        # agent/mcp_client.py:resolve_cluster_routing() for the priority chain and
        # PRODUCTION-LAUNCH-PLAN.md Priority 5. Present even on a safe-stop (method
        # "unresolved") so reviewers can see exactly why routing refused to guess.
        "cluster_routing_method": ctx.get("cluster_routing_method", ""),
        "cluster_routing_reason": ctx.get("cluster_routing_reason", ""),
    }

    # Add real token tracking to RCA output. Regression fix (issue #63 PR 1): this used
    # to fold this node's own llm_json() call into ONLY tokens_total/estimated_cost_usd
    # here, never re-setting tokens_input/tokens_output to match. Because
    # AgentState.investigation is a shallow dict merge (operator.or_, see agent/state.py),
    # a partial return here left tokens_input/tokens_output stuck at whatever the
    # second-to-last node had accumulated — silently missing this node's own call.
    # accumulate_usage() always returns the complete field set, so that gap can't recur.
    #
    # 2026-09-01 (confidence-architecture review, point 7): folds on top of verifier_tok
    # when the verifier ran, not on top of the original `inv` — otherwise this node's own
    # llm_json() call would silently overwrite (not add to) the verifier's already-folded
    # totals, since accumulate_usage()'s base argument only ever contributes its OWN
    # existing token fields, and `inv` alone never had the verifier's usage in it.
    from agent.llm.accounting import accumulate_usage
    tok = accumulate_usage(verifier_tok if verifier_tok is not None else inv, usage)
    total_tokens = tok["tokens_total"]
    total_cost = tok["estimated_cost_usd"]

    log.info(
        "rca_builder outcome=%s confidence=%.2f band=%s completeness=%.2f cluster=%s "
        "contradictions=%d tokens=%d cost=$%.6f requires_review=%s gcs_enriched=%s",
        outcome, confidence, confidence_band, completeness.get("score", 0.0),
        ctx.get("cluster_name", ""), len(contradictions),
        total_tokens, total_cost, requires_review, gcs_enriched,
    )

    # Write full structured observability event to Cloud Logging
    _write_observability_log(state, result, usage, tok, verifier_usage, verifier_result)

    return {
        "final_summary": result,
        "investigation": {
            "status":             "done",
            # Legacy fields — final authoritative value, overwrites whatever task_evaluator set
            # during the loop (task_evaluator no longer sets these at all — see task_evaluator.py).
            "confidence":         confidence,
            "confidence_band":    confidence_band,
            **tok,
        },
    }
