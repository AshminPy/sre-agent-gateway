"""
main.py — Gemini Enterprise Agent Runtime entrypoint.

CRITICAL for Agent Runtime:
- No module-level code that can fail (no graph compile at import)
- All initialization happens in set_up() which Agent Runtime calls after deps install
- query() is called for each investigation request

Mirrors AWS src/main.py pattern:
  AWS:  @app.entrypoint on BedrockAgentCoreApp
  GCP:  query() method on ReasoningEngine / SREAgent class

Payload schema:
  {
    "query":      "Pod imagepull-pod is in ImagePullBackOff",
    "namespace":  "test-incidents",
    "pod":        "imagepull-pod",
    "cluster":    "sre-test-cluster",
    "severity":   "high"
  }
"""
import json
import logging
import os
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sre-agent.server")

PROJECT_ID           = os.environ.get("PROJECT_ID", "your-gcp-project-id")
REGION               = os.environ.get("REGION", "us-central1")
MEMORY_BANK_RESOURCE = os.environ.get("MEMORY_BANK_RESOURCE", "")  # full resource path of the memory bank agent engine
MODEL_ARMOR_TEMPLATE = os.environ.get("MODEL_ARMOR_TEMPLATE", "")
EVAL_BUCKET          = os.environ.get("EVAL_BUCKET", "")

# Graph is compiled lazily — not at import time
# This prevents Agent Runtime container startup failures
_graph = None


def _get_graph():
    """Lazy graph initialization — safe for Agent Runtime."""
    global _graph
    if _graph is None:
        # Load env only when needed
        from dotenv import load_dotenv
        _env_path = os.path.join(os.path.dirname(__file__), ".env")
        if os.path.exists(_env_path):
            load_dotenv(_env_path)

        from agent.graph import compile_graph
        _graph = compile_graph()
        log.info("LangGraph compiled and ready")
    return _graph


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value or default)
    except Exception:
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except Exception:
        return default


def _extract_root_cause(summary: dict) -> str:
    """Single source of the root-cause text — built once, referenced everywhere it's rendered.
    Fixes the previous bug where the executive summary and the RCA report each independently
    pulled and re-rendered the full root-cause text, producing visible duplication in the
    output. See docs/confidence-framework-design.md §12 / PRODUCTION-LAUNCH-PLAN.md P7.
    """
    return str(
        summary.get("likely_root_cause")
        or summary.get("root_cause")
        or summary.get("incident_summary")
        or "See full summary for details."
    )


def _build_executive_summary(summary: dict, inv: dict, ctx: dict) -> str:
    """Plain-English one-paragraph summary for non-technical stakeholders.

    Deliberately a SHORT reference to the root cause, not the full text — the detailed
    reasoning and evidence chain live only in the RCA report section (_build_rca_report).
    """
    incident_type = ctx.get("incident_type") or summary.get("incident_type") or "Unknown incident"
    root_cause = _extract_root_cause(summary)
    root_cause_brief = root_cause[:120].rstrip(".")
    if len(root_cause) > 120:
        root_cause_brief += "…"
    remediation = (
        summary.get("suggested_remediation")
        or summary.get("recommendation")
        or summary.get("next_steps")
        or "No specific remediation provided — review agent output."
    )
    confidence_band = (inv.get("confidence_band") or summary.get("confidence_band") or "unknown").upper()
    outcome = str(summary.get("outcome", "")).upper()
    requires_review = summary.get("requires_human_review", True)
    review_note = "Human review recommended before actioning." if requires_review else "No immediate human review required."
    outcome_note = f" Outcome: {outcome}." if outcome else ""
    return (
        f"Incident type: {incident_type}. "
        f"Root cause: {root_cause_brief}. "
        f"Confidence: {confidence_band}.{outcome_note} "
        f"Recommended action: {str(remediation)[:300].rstrip('.')}. "
        f"{review_note} "
        "See the Root Cause Analysis section below for the full evidence-backed reasoning."
    )


def _build_rca_report(
    payload: dict,
    summary: dict,
    inv: dict,
    ctx: dict,
    obs_event: dict,
    evidence_ids: list,
) -> str:
    """Structured RCA report for management, incident tickets, and audit trails."""
    from datetime import datetime, timezone

    generated_at  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_id_full = obs_event.get("run_id") or ""
    report_id = (
        "sre-" + run_id_full.replace("run_", "rca-").replace("_", "-")
        if run_id_full
        else "sre-rca-unknown"
    )

    cluster       = obs_event.get("cluster")   or payload.get("cluster", "")
    namespace     = obs_event.get("namespace") or payload.get("namespace", "")
    pod           = (obs_event.get("pod") or payload.get("pod", "") or payload.get("deployment", ""))
    severity      = (payload.get("severity", "unknown") or "unknown").upper()
    incident_type = ctx.get("incident_type") or summary.get("incident_type") or "Unknown"

    root_cause = _extract_root_cause(summary)

    remediation_raw = (
        summary.get("suggested_remediation")
        or summary.get("recommendation")
        or summary.get("next_steps")
        or []
    )
    remediation_items = (
        [str(r) for r in remediation_raw]
        if isinstance(remediation_raw, list)
        else [str(remediation_raw)]
    ) if remediation_raw else []

    contributing_factors = summary.get("contributing_factors") or []
    if isinstance(contributing_factors, str):
        contributing_factors = [contributing_factors]

    prevention_measures = summary.get("prevention_measures") or []
    if isinstance(prevention_measures, str):
        prevention_measures = [prevention_measures]

    confidence      = _safe_float(inv.get("confidence", summary.get("confidence_score", 0.0)))
    confidence_band = (inv.get("confidence_band") or summary.get("confidence_band") or "escalate").upper()

    tool_calls      = obs_event.get("tools_called", 0)
    evidence_count  = obs_event.get("evidence_count", len(evidence_ids))
    latency_ms      = obs_event.get("latency_ms", 0)
    latency_s       = f"{latency_ms / 1000:.1f}s" if latency_ms else "unknown"
    # current_step is the real field investigation.py/loop_controller.py actually maintain
    # (see agent/state.py) -- investigation_loops/loop_count were never real fields, always 0.
    inv_loops       = _safe_int(inv.get("current_step", 0))
    tokens_total    = obs_event.get("tokens_total", 0)
    estimated_cost  = _safe_float(obs_event.get("estimated_cost_usd", 0.0))

    # Build evidence lookup from reasoning_trace — each item references ev_ids like "(ev_001)"
    import re as _re
    reasoning_trace = summary.get("reasoning_trace", []) or []
    if isinstance(reasoning_trace, str):
        try:
            reasoning_trace = json.loads(reasoning_trace)
        except Exception:
            reasoning_trace = [reasoning_trace]
    ev_descriptions: dict = {}
    for trace_item in reasoning_trace:
        trace_str = str(trace_item)
        for ref in _re.findall(r"ev_\d+", trace_str):
            ev_descriptions.setdefault(ref, []).append(trace_str)

    memory_ctx  = payload.get("memory_context", "") or ""
    memory_note = (
        f"Prior context recalled ({len(memory_ctx)} chars) — agent used past investigations"
        if memory_ctx
        else "No prior similar incidents found in Memory Bank"
    )

    if confidence_band == "AUTO":
        review_note  = "AUTO — no human review required"
        status_line  = "INVESTIGATION COMPLETE — AWAITING HUMAN ACTION"
    elif confidence_band == "REVIEW":
        review_note  = "REVIEW — human review recommended before actioning"
        status_line  = "INVESTIGATION COMPLETE — HUMAN REVIEW REQUIRED"
    else:
        review_note  = "ESCALATE — low confidence, manual investigation needed"
        status_line  = "INVESTIGATION COMPLETE — ESCALATION REQUIRED"

    SEP = "━" * 64

    def wrap(text: str, width: int = 60, indent: str = "  ") -> list:
        words, lines, buf = text.split(), [], []
        for w in words:
            if sum(len(x) + 1 for x in buf) + len(w) > width:
                lines.append(indent + " ".join(buf))
                buf = [w]
            else:
                buf.append(w)
        if buf:
            lines.append(indent + " ".join(buf))
        return lines

    L = [
        SEP,
        "  SRE INCIDENT ROOT CAUSE ANALYSIS REPORT",
        SEP,
        "",
        "  INCIDENT DETAILS",
        f"    Report ID     : {report_id}",
        f"    Generated At  : {generated_at}",
        f"    Investigation : {latency_s}  |  Tool calls: {tool_calls}  |  Loops: {inv_loops}",
        f"    Cluster       : {cluster or 'unknown'}",
        f"    Namespace     : {namespace or 'unknown'}",
        f"    Workload      : {pod or incident_type or 'unknown'}",
        f"    Severity      : {severity}",
        f"    Incident Type : {incident_type}",
        "",
        SEP,
        "  1.  EXECUTIVE SUMMARY",
        SEP,
    ]
    L.extend(wrap(_build_executive_summary(summary, inv, ctx), indent="  "))
    L.append("")

    L += [
        SEP,
        "  2.  ROOT CAUSE ANALYSIS",
        SEP,
        "  Primary Cause:",
    ]
    L.extend(wrap(str(root_cause)[:400], indent="    "))
    L.append("")

    if evidence_ids:
        L.append(f"  Evidence ({evidence_count} items):")
        for eid in evidence_ids:
            descs = ev_descriptions.get(eid, [])
            if descs:
                for desc in descs[:2]:
                    clean = _re.sub(r"\s*\(ev_\d+(?:,\s*ev_\d+)*\)", "", desc).strip()
                    L.append(f"    [{eid}] {clean[:120]}")
            else:
                L.append(f"    [{eid}] (referenced in investigation)")
        L.append("")

    if contributing_factors:
        L.append("  Contributing Factors:")
        for f in contributing_factors[:5]:
            L.append(f"    • {str(f)[:100]}")
        L.append("")

    L.append(f"  Similar Past Incidents : {memory_note}")
    L.append("")

    completeness = summary.get("investigation_completeness", {}) or {}
    rcc          = summary.get("root_cause_confidence", {}) or {}
    contradictions = summary.get("contradictions", []) or []
    hypotheses     = summary.get("hypotheses", []) or []
    outcome        = summary.get("outcome", "unknown")

    L += [
        SEP,
        "  3.  CONFIDENCE BREAKDOWN",
        SEP,
        f"  Outcome                    : {str(outcome).upper()}",
        f"  Investigation Completeness : {completeness.get('score', 0.0):.2f}  ({completeness.get('band', 'unknown')})",
        f"  Root-Cause Confidence      : {rcc.get('score', 0.0):.2f}  ({rcc.get('band', 'unknown')})",
    ]
    if completeness.get("gaps"):
        L.append("  Completeness gaps:")
        for g in completeness["gaps"][:5]:
            L.append(f"    • {str(g)[:110]}")
    if rcc.get("reasons"):
        L.append("  Root-cause reasons:")
        for r in rcc["reasons"][:5]:
            L.append(f"    • {str(r)[:110]}")
    if contradictions:
        L.append(f"  Contradictions detected ({len(contradictions)}):")
        for c in contradictions[:5]:
            L.append(f"    • {str(c.get('description',''))[:110]}")
    active_hyp = [h for h in hypotheses if h.get("status") == "active"]
    if active_hyp:
        L.append(f"  Unresolved alternative hypotheses ({len(active_hyp)}):")
        for h in active_hyp[:3]:
            L.append(f"    • {str(h.get('description',''))[:110]}")
    L.append(f"  Policy version              : {summary.get('policy_version', 'unknown')}")
    L.append("")

    # Issue #61: these used to be hardcoded claims ("DEGRADED", "Dependent services may be
    # affected") printed unconditionally, whether or not any evidence backed them. Nothing
    # in the pipeline actually tracks real service/pod health as a structured fact yet (see
    # #95, the real fix — a deterministic status check, not built here) -- so the honest
    # fallback is to say plainly this wasn't independently checked, gated on whether the
    # investigation actually reached a real conclusion (outcome confirmed/probable, backed
    # by real evidence) versus one that didn't (insufficient_evidence/unknown/conflicting).
    if outcome in ("confirmed", "probable") and evidence_ids:
        service_status = "Not independently checked — see evidence chain above for pod/service state"
        first_last_seen = "See k8s event timestamps in the evidence above, where collected"
        user_impact = "Not independently assessed — see evidence chain above"
    else:
        service_status = "Not determined — investigation did not reach a confirmed conclusion"
        first_last_seen = "Not determined — insufficient evidence collected"
        user_impact = "Not determined — insufficient evidence collected"

    L += [
        SEP,
        "  4.  IMPACT ASSESSMENT",
        SEP,
        f"  Workload        : {pod or incident_type}",
        f"  Incident Type   : {incident_type}",
        f"  Service Status  : {service_status}",
        f"  First/Last Seen : {first_last_seen}",
        f"  User Impact     : {user_impact}",
        "  MTTR            : not yet tracked — pending future enhancement",
        "",
        SEP,
        "  5.  REMEDIATION",
        SEP,
    ]

    if remediation_items:
        L.append("  Immediate Actions:")
        kubectl_cmds = []
        for i, item in enumerate(remediation_items[:5], 1):
            item_str = str(item).strip()
            L.append(f"    {i}. {item_str[:150]}")
            if "kubectl" in item_str.lower():
                kubectl_cmds.append(item_str)
        if kubectl_cmds:
            L.append("")
            L.append("  Commands:")
            for cmd in kubectl_cmds[:3]:
                L.append(f"    $ {cmd[:120]}")
        L.append("")

    if prevention_measures:
        L.append("  Long-term Prevention:")
        for m in prevention_measures[:5]:
            L.append(f"    • {str(m)[:120]}")
        L.append("")

    from agent.gemini_client import MODEL as _deployed_model
    L += [
        SEP,
        "  6.  INVESTIGATION METADATA",
        SEP,
        f"  AI Confidence   : {confidence * 100:.0f}%  (Band: {confidence_band})",
        f"  Human Review    : {review_note}",
        f"  Memory Bank     : {memory_note}",
        f"  Model           : {_deployed_model} via Vertex AI Agent Engine",
    ]
    if tokens_total:
        L.append(f"  Tokens          : {tokens_total}  (est. cost: ${estimated_cost:.6f})")
    L += [
        "",
        SEP,
        f"  {status_line}",
        SEP,
        f"  Generated by SRE Agent  |  {report_id}  |  {generated_at}",
    ]

    return "\n".join(L)


def investigate(payload: dict) -> dict:
    """
    Core investigation function.
    Called by Agent Runtime query() and by run.py locally.
    """
    started_at = time.time()

    query      = payload.get("query", "")
    namespace  = payload.get("namespace", "test-incidents")
    pod        = payload.get("pod", "")
    cluster    = payload.get("cluster", "sre-test-cluster")
    deployment = payload.get("deployment", "")
    severity   = payload.get("severity", "unknown")

    if not query:
        return {"error": "query is required", "status": "failed"}

    try:
        from agent.state import get_initial_state
        from agent.otel import get_tracer, set_span_attributes, flush_traces

        envelope = {
            "source_type":     payload.get("source_type", "manual"),
            "source_event_id": payload.get("source_event_id", ""),
            "user_query":      query,
            "resource_hints": {
                "cluster":    cluster,
                "namespace":  namespace,
                "pod":        pod,
                "deployment": deployment,
            },
            "incident": {
                "severity": severity,
                "title":    payload.get("title", query[:80]),
                "service":  payload.get("service", ""),
            },
            "memory_context": payload.get("memory_context", ""),
        }

        tracer = get_tracer()
        if tracer is not None:
            with tracer.start_as_current_span("sre_agent.investigation") as span:
                set_span_attributes(span, {
                    "sre.query": query[:250],
                    "sre.cluster.requested": cluster,
                    "sre.namespace.requested": namespace,
                    "sre.pod.requested": pod,
                    "sre.deployment.requested": deployment,
                    "sre.severity": severity,
                    "sre.source_type": envelope.get("source_type", "manual"),
                })
                from agent.graph import GRAPH_RECURSION_LIMIT
                graph  = _get_graph()
                state  = get_initial_state(envelope)
                result = graph.invoke(state, config={"recursion_limit": GRAPH_RECURSION_LIMIT})
                inv_for_span = result.get("investigation", {}) or {}
                ctx_for_span = result.get("resolved_context", {}) or {}
                set_span_attributes(span, {
                    "sre.run_id": result.get("run_id", ""),
                    "sre.cluster": ctx_for_span.get("cluster_name", cluster),
                    "sre.namespace": ctx_for_span.get("namespace", namespace),
                    "sre.pod": ctx_for_span.get("pod", pod),
                    "sre.incident_type": ctx_for_span.get("incident_type", ""),
                    "sre.status": inv_for_span.get("status", ""),
                    "sre.confidence": _safe_float(inv_for_span.get("confidence", 0.0)),
                    "sre.confidence_band": inv_for_span.get("confidence_band", ""),
                    "sre.tool_calls": len(result.get("tool_history", []) or []),
                    "sre.evidence_count": len(result.get("evidence_ids", []) or []),
                    "sre.tokens_total": _safe_int(inv_for_span.get("tokens_total", 0)),
                    "sre.estimated_cost_usd": _safe_float(inv_for_span.get("estimated_cost_usd", 0.0)),
                })
        else:
            from agent.graph import GRAPH_RECURSION_LIMIT
            graph  = _get_graph()
            state  = get_initial_state(envelope)
            result = graph.invoke(state, config={"recursion_limit": GRAPH_RECURSION_LIMIT})

        summary = result.get("final_summary", {}) or {}
        inv     = result["investigation"]
        ctx     = result.get("resolved_context", {}) or {}
        errors  = result.get("errors", []) or []
        evidence_ids = result.get("evidence_ids", []) or []
        tool_history = result.get("tool_history", []) or []
        latency_ms = int((time.time() - started_at) * 1000)

        # Try several possible places because token fields may live in different
        # state keys depending on which node produced them.
        usage = result.get("usage", {}) or result.get("token_usage", {}) or {}
        tokens_input = _safe_int(
            inv.get("tokens_input")
            or summary.get("tokens_input")
            or usage.get("tokens_input")
            or usage.get("input_tokens")
            or result.get("tokens_input")
        )
        tokens_output = _safe_int(
            inv.get("tokens_output")
            or summary.get("tokens_output")
            or usage.get("tokens_output")
            or usage.get("output_tokens")
            or result.get("tokens_output")
        )
        tokens_total = _safe_int(
            inv.get("tokens_total")
            or summary.get("tokens_total")
            or usage.get("tokens_total")
            or usage.get("total_tokens")
            or result.get("tokens_total")
        )

        # If only node-level totals exist in logs/state, use that if present.
        if tokens_total == 0:
            tokens_total = _safe_int(result.get("total_tokens") or inv.get("tokens") or summary.get("tokens"))

        estimated_cost_usd = _safe_float(
            inv.get("estimated_cost_usd")
            or summary.get("estimated_cost_usd")
            or usage.get("estimated_cost_usd")
            or result.get("estimated_cost_usd")
        )

        confidence = _safe_float(inv.get("confidence", summary.get("confidence_score", 0.0)))
        confidence_band = inv.get("confidence_band", summary.get("confidence_band", "escalate"))

        # PRODUCTION-LAUNCH-PLAN.md Priority 10 fields (2026-08-07 fix): these were built
        # and unit-tested in rca_builder.py/_write_observability_log's OWN separate
        # "sre-agent-investigations" log entry, but never threaded into THIS event —
        # the one iac/agent/monitoring.tf's unresolved_cluster and investigation_latency
        # log-based metrics actually filter on. Confirmed missing via a real live agent
        # invocation + a direct Cloud Logging query before this fix (jsonPayload had none
        # of these keys), not assumed from code review alone.
        from agent.gemini_client import get_session_usage
        from agent.otel import get_trace_id_hex

        mcp_latency_s = round(sum(h.get("duration_s", 0) or 0 for h in tool_history), 3)
        session_usage = get_session_usage()
        evidence_store = result.get("evidence_store", {}) or {}
        evidence_storage_ok = not any(ev.get("gcs_write_failed") for ev in evidence_store.values())

        # ============================================================
        # Structured observability log — one JSON event per agent run
        # Cloud Logging can parse this as jsonPayload when emitted to stdout.
        # Use this later for log-based metrics and Cloud Monitoring charts.
        # ============================================================
        obs_event = {
            "event_type": "sre_agent_run",
            "run_id": result.get("run_id", ""),
            "trace_id": get_trace_id_hex(),
            "incident_type": ctx.get("incident_type", summary.get("incident_type", "")),
            "project_id": ctx.get("project_id", PROJECT_ID),
            "cluster": ctx.get("cluster_name", ctx.get("cluster", cluster)),
            "cluster_region": ctx.get("cluster_region", ctx.get("region", "")),
            "cluster_routing_method": ctx.get("cluster_routing_method", ""),
            "cluster_routing_reason": ctx.get("cluster_routing_reason", ""),
            "namespace": ctx.get("namespace", namespace),
            "pod": ctx.get("pod", pod),
            "deployment": ctx.get("deployment", deployment),
            "primary_mcp_source": ctx.get("primary_mcp_source", ctx.get("mcp_source", "")),
            "selected_mcp": result.get("selected_mcp", ""),
            "tools_called": len(tool_history),
            "evidence_count": len(evidence_ids),
            "evidence_ids": evidence_ids,
            "evidence_storage_ok": evidence_storage_ok,
            "confidence": confidence,
            "confidence_band": confidence_band,
            # New confidence framework fields — additive, does not change any existing field
            # name/value read by iac/agent/monitoring.tf's log-based metrics or alerts.
            "outcome": summary.get("outcome", "unknown"),
            "policy_version": summary.get("policy_version", ""),
            "investigation_completeness_score": (summary.get("investigation_completeness") or {}).get("score"),
            "root_cause_confidence_score": (summary.get("root_cause_confidence") or {}).get("score"),
            "contradictions_count": len(summary.get("contradictions") or []),
            "status": inv.get("status", "unknown"),
            "loop_exit_reason": inv.get("loop_exit_reason", result.get("loop_exit_reason")),
            "human_review": bool(summary.get("requires_human_review", True)),
            "latency_ms": latency_ms,
            "mcp_latency_s": mcp_latency_s,
            "model_latency_s": session_usage.get("session_model_latency_s"),
            "total_latency_s": round(latency_ms / 1000, 3),
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "tokens_total": tokens_total,
            "estimated_cost_usd": estimated_cost_usd,
            "error_count": len(errors),
            "pagerduty_incident_id": ctx.get("pagerduty_incident_id"),
            # Placeholders, not silently omitted — mirrors rca_builder.py's own comment:
            # connect_gateway_status has no code producing a real value yet (Priority 3
            # has no app-observable signal into the agent process); agent_gateway_authz_mode
            # is always None while the gateway's IAP authz extension runs in DRY_RUN.
            "connect_gateway_status": None,
            "agent_gateway_authz_mode": None,
        }

        # stdout JSON line for Cloud Logging jsonPayload parsing.
        print(json.dumps(obs_event, separators=(",", ":")), flush=True)

        # Human-readable fallback log line.
        log.info(
            "observability event written run_id=%s cluster=%s tokens=%s cost=$%.6f latency_ms=%s",
            obs_event["run_id"],
            obs_event["cluster"],
            obs_event["tokens_total"],
            obs_event["estimated_cost_usd"],
            obs_event["latency_ms"],
        )

        flush_traces(timeout_millis=5000)

        return {
            "schema_version":     "2.0",
            "status":             inv["status"],
            "confidence":         inv.get("confidence", 0.0),
            "confidence_deprecated": True,
            "confidence_band":    inv.get("confidence_band",
                                  summary.get("confidence_band", "escalate")),
            "outcome":            summary.get("outcome", "unknown"),
            "investigation_completeness": summary.get("investigation_completeness", {}),
            "root_cause_confidence":      summary.get("root_cause_confidence", {}),
            "tool_calls":         len(tool_history),
            "evidence_ids":       evidence_ids,
            "run_id":             result.get("run_id", ""),
            "summary":            summary,
            "executive_summary":  _build_executive_summary(summary, inv, ctx),
            "rca_report":         _build_rca_report(payload, summary, inv, ctx, obs_event, evidence_ids),
            "working_theory":     result.get("working_theory", ""),
            "errors":             errors,
            "requires_human_review": summary.get("requires_human_review", True),
            "observability":      obs_event,
        }

    except Exception as exc:
        log.exception("investigation failed: %s", exc)
        return {"error": str(exc), "status": "failed"}


class SREAgent:
    """
    Vertex AI Agent Engine entrypoint.

    Enterprise features wired here:
    - Model Armor  : sanitizes input (prompt injection, PII) and output (PII redaction)
    - Memory       : remembers past investigations per cluster/namespace
    - Sessions     : propagates session_id for multi-turn investigation tracking

    Agent Runtime lifecycle:
    1. Container starts → set_up() called once (compile graph, init clients)
    2. query() called per investigation request
    """

    # Class-level state (shared across all query() calls in one container)
    _armor_client = None          # Model Armor REST client
    _mb_client    = None          # Vertex AI Memory Bank client (None if ENGINE_ID not set)
    _memory: list  = []           # In-process fallback memory (last 20 investigations)
    _MAX_MEMORY    = 20

    @classmethod
    def set_up(cls) -> None:
        """Called once when Agent Runtime container starts."""
        log.info("SREAgent.set_up() — initializing graph and enterprise clients...")
        _get_graph()

        # ── Model Armor client ────────────────────────────────────────
        if MODEL_ARMOR_TEMPLATE:
            try:
                from google.cloud import modelarmor_v1
                from google.api_core.client_options import ClientOptions
                cls._armor_client = modelarmor_v1.ModelArmorClient(
                    client_options=ClientOptions(
                        api_endpoint=f"modelarmor.{REGION}.rep.googleapis.com"
                    )
                )
                log.info("Model Armor ready: %s", MODEL_ARMOR_TEMPLATE)
            except ImportError:
                log.warning("google-cloud-modelarmor not installed — safety filter disabled")
            except Exception as exc:
                log.warning("Model Armor init failed (%s) — safety filter disabled", exc)
        else:
            log.info("MODEL_ARMOR_TEMPLATE not set — safety filter disabled")

        # ── Vertex AI Memory Bank client ──────────────────────────────
        # Requires MEMORY_BANK_RESOURCE env var (set by Terraform after memory bank is created).
        # Until set, agent falls back to in-process _memory list.
        if MEMORY_BANK_RESOURCE:
            try:
                import vertexai
                vertexai.init(project=PROJECT_ID, location=REGION)
                from vertexai import Client as VertexClient
                cls._mb_client = VertexClient(project=PROJECT_ID, location=REGION)
                log.info("Memory Bank ready: %s", MEMORY_BANK_RESOURCE)
            except Exception as exc:
                log.warning("Memory Bank init failed (%s) — using in-process memory only", exc)
        else:
            log.info("MEMORY_BANK_RESOURCE not set — Memory Bank disabled, using in-process memory")

        log.info("SREAgent ready")

    # ── Model Armor helpers ───────────────────────────────────────────

    @classmethod
    def _sanitize(cls, text: str, is_output: bool = False) -> tuple:
        """Sanitize text through Model Armor. Returns (sanitized_text, was_blocked)."""
        if not cls._armor_client or not MODEL_ARMOR_TEMPLATE:
            return text, False
        try:
            from google.cloud import modelarmor_v1
            data = modelarmor_v1.DataItem(text=text)
            if is_output:
                resp = cls._armor_client.sanitize_model_response(
                    modelarmor_v1.SanitizeModelResponseRequest(
                        name=MODEL_ARMOR_TEMPLATE, model_response_data=data
                    )
                )
            else:
                resp = cls._armor_client.sanitize_user_prompt(
                    modelarmor_v1.SanitizeUserPromptRequest(
                        name=MODEL_ARMOR_TEMPLATE, user_prompt_data=data
                    )
                )
            blocked = (
                resp.sanitization_result.filter_match_state
                == modelarmor_v1.FilterMatchState.MATCH_FOUND
            )
            # sanitized_data removed in google-cloud-modelarmor >= 0.4;
            # Model Armor blocks or passes — it does not rewrite text
            sanitized = text
            if blocked:
                log.warning(
                    "Model Armor blocked %s (prompt injection/PII/malicious URL)",
                    "output" if is_output else "input",
                )
            return sanitized, blocked
        except Exception as exc:
            # Log at ERROR so a log-based alert can fire on fail-open events.
            log.error(
                "model_armor_fail_open event=sanitize_error is_output=%s error=%s",
                is_output, exc,
            )
            if not is_output:
                # Input sanitization failed — fail closed: reject the request.
                # A malicious prompt must not proceed without sanitization.
                return text, True
            # Output sanitization failed — flag-but-deliver: the RCA is
            # still valuable, but must be reviewed by a human.
            return text, False

    # ── GCS persistence ───────────────────────────────────────────────

    @classmethod
    def _save_to_gcs(cls, run_id: str, payload: dict, result: dict) -> None:
        """Persist full RCA to GCS. Fails silently — never blocks the response."""
        if not EVAL_BUCKET:
            return
        try:
            from google.cloud import storage
            bucket_name = EVAL_BUCKET.removeprefix("gs://")
            record = {
                "run_id":    run_id,
                "timestamp": time.time(),
                "request": {
                    "query":     payload.get("query", "")[:500],
                    "cluster":   payload.get("cluster", ""),
                    "namespace": payload.get("namespace", ""),
                    "pod":       payload.get("pod", ""),
                    "severity":  payload.get("severity", ""),
                },
                "result": result,
            }
            client = storage.Client()
            blob   = client.bucket(bucket_name).blob(f"runs/{run_id}.json")
            blob.upload_from_string(
                json.dumps(record, indent=2, default=str),
                content_type="application/json",
            )
            log.info("RCA saved → gs://%s/runs/%s.json", bucket_name, run_id)
        except Exception as exc:
            log.warning("GCS save failed (%s) — RCA not persisted", exc)

    # ── Memory helpers ────────────────────────────────────────────────

    @classmethod
    def _mb_store(cls, cluster: str, namespace: str, pod: str, root_cause: str, confidence: float, incident_type: str = "") -> None:
        """Write investigation RCA to Vertex AI Memory Bank (persistent across sessions).

        Each memory stores: cluster, namespace, pod, incident_type, root_cause, confidence, and
        status=pending_review so SREs can validate and delete inaccurate memories
        (prevents RCA poisoning). Scoped by cluster+namespace for precise retrieval.

        Dedup key: pod + incident_type. Stable identifiers from K8s — not LLM text — so
        the same scenario is never written twice regardless of how Gemini phrases the RCA.

        Min IAM: aiplatform.memories.generate (roles/aiplatform.user)
        """
        if not cls._mb_client or not MEMORY_BANK_RESOURCE:
            return
        try:
            # Dedup: skip write if same pod+incident_type already stored
            dedup_key = f"pod={pod} incident_type={incident_type}"
            existing = list(cls._mb_client.agent_engines.memories.retrieve(
                name=MEMORY_BANK_RESOURCE,
                scope={"cluster": cluster, "namespace": namespace},
            ))
            for m in existing:
                if dedup_key in m.memory.fact:
                    log.info("Memory Bank: duplicate RCA skipped for %s/%s/%s (%s)", cluster, namespace, pod, incident_type)
                    return

            fact = (
                f"cluster={cluster} namespace={namespace} pod={pod} "
                f"incident_type={incident_type} "
                f"root_cause={root_cause[:300]} "
                f"confidence={confidence:.2f}"
            )
            cls._mb_client.agent_engines.memories.create(
                name=MEMORY_BANK_RESOURCE,
                fact=fact,
                scope={"cluster": cluster, "namespace": namespace},
            )
            log.info("Memory Bank: stored RCA for %s/%s (confidence=%.2f)", cluster, namespace, confidence)
        except Exception as exc:
            log.warning("Memory Bank store failed (%s) — skipped", exc)

    @classmethod
    def _parse_fact(cls, fact: str) -> dict:
        """Extract key=value pairs from a stored fact string into a dict."""
        parsed = {}
        for token in fact.split():
            if "=" in token:
                k, _, v = token.partition("=")
                parsed[k] = v
        return parsed

    @classmethod
    def _mb_recall(cls, cluster: str, namespace: str) -> str:
        """Retrieve past RCAs from Vertex AI Memory Bank for this cluster+namespace.

        Returns a formatted string injected into the agent's context before investigation.
        Also emits a structured recall event to Cloud Logging so recall frequency can be
        tracked per incident_type — high recall count = recurring issue needing a permanent fix.
        """
        if not cls._mb_client or not MEMORY_BANK_RESOURCE:
            return ""
        try:
            memories = list(cls._mb_client.agent_engines.memories.retrieve(
                name=MEMORY_BANK_RESOURCE,
                scope={"cluster": cluster, "namespace": namespace},
            ))
            if not memories:
                return ""

            recalled = memories[:3]
            lines = [f"  {m.memory.fact}" for m in recalled]
            log.info("Memory Bank: recalled %d memories for %s/%s", len(lines), cluster, namespace)

            # Structured recall event — one JSON line per recalled memory.
            # Cloud Logging parses stdout as jsonPayload automatically.
            # Use this to count recurring incidents: high recall count = needs permanent fix.
            for m in recalled:
                fields = cls._parse_fact(m.memory.fact)
                print(json.dumps({
                    "event_type":    "memory_bank_recall",
                    "cluster":       cluster,
                    "namespace":     namespace,
                    "pod":           fields.get("pod", ""),
                    "incident_type": fields.get("incident_type", ""),
                    "confidence":    fields.get("confidence", ""),
                }, separators=(",", ":")), flush=True)

            return "Past incidents on this cluster/namespace (validate during investigation):\n" + "\n".join(lines)
        except Exception as exc:
            log.warning("Memory Bank recall failed (%s) — skipped", exc)
            return ""

    @classmethod
    def _save_memory(cls, query: str, root_cause: str, cluster: str, namespace: str) -> None:
        """Append investigation summary to in-process fallback memory (last 20 entries)."""
        cls._memory.append({
            "query":      query[:120],
            "root_cause": (root_cause or "")[:250],
            "cluster":    cluster,
            "namespace":  namespace,
            "ts":         time.time(),
        })
        if len(cls._memory) > cls._MAX_MEMORY:
            cls._memory = cls._memory[-cls._MAX_MEMORY:]

    @classmethod
    def _recall_memory(cls, cluster: str, namespace: str) -> str:
        """Return context from in-process fallback memory (used when Memory Bank is not set up)."""
        if not cls._memory:
            return ""
        relevant = [
            m for m in cls._memory
            if m.get("cluster") == cluster or m.get("namespace") == namespace
        ]
        if not relevant:
            return ""
        lines = [
            f"  [{m['cluster']}/{m['namespace']}] {m['root_cause']}"
            for m in relevant[-3:]
        ]
        return "Recent incidents on this cluster/namespace:\n" + "\n".join(lines)

    # ── Main query entry point ────────────────────────────────────────

    @classmethod
    def query(cls, **kwargs) -> dict:
        """Called by Agent Runtime for each investigation request.

        Extra fields (not passed to investigate()):
          session_id : caller-assigned session ID for multi-turn tracking
          prompt     : JSON string sent by vertexai.Client.evals.run_inference()
        """
        payload = dict(kwargs or {})

        # run_inference() sends a 'prompt' key with a JSON string — parse it
        if "prompt" in payload and not payload.get("query"):
            try:
                import json as _json
                data = _json.loads(str(payload.pop("prompt")))
                for _k in ("query", "cluster", "namespace", "pod", "severity"):
                    if _k in data:
                        payload.setdefault(_k, data[_k])
            except (ValueError, TypeError):
                payload["query"] = str(payload.pop("prompt", ""))

        query_text = payload.get("query", "")
        cluster    = payload.get("cluster", "sre-test-cluster")
        namespace  = payload.get("namespace", "test-incidents")
        session_id = payload.pop("session_id", None)

        log.info(
            "SREAgent.query() cluster=%s session=%s query=%s",
            cluster, session_id or "none", query_text[:80],
        )

        # 1. Model Armor — sanitize input (blocks prompt injection in k8s data)
        sanitized_query, blocked = cls._sanitize(query_text)
        if blocked:
            return {
                "status":     "blocked",
                "error":      "Input blocked by safety filter (prompt injection or harmful content detected)",
                "session_id": session_id,
            }
        if sanitized_query != query_text:
            payload["query"] = sanitized_query

        # 2. Memory — inject past investigation context before LLM reasoning
        # Try Vertex AI Memory Bank first (persistent); fall back to in-process list
        memory_ctx = cls._mb_recall(cluster, namespace) or cls._recall_memory(cluster, namespace)
        if memory_ctx:
            payload["memory_context"] = memory_ctx
            log.info("Memory context injected (%d chars)", len(memory_ctx))

        # 3. Run investigation
        result = investigate(payload)

        # 3.5 Persist RCA to GCS (never blocks response — fails silently)
        run_id = result.get("run_id", "")
        if run_id:
            cls._save_to_gcs(run_id, payload, result)

        # 4. Memory — save this investigation's root cause for future recall
        #
        # Gate added 2026-08-04: previously this wrote to persistent Memory Bank for ANY
        # non-failed/non-blocked result, with no confidence or validation check — despite
        # _mb_store's own docstring claiming RCA-poisoning prevention (it only dedups by
        # pod+incident_type, not confidence). A low-confidence, escalate-band RCA could be
        # written and later recalled as if it were validated fact. Now gated on
        # confidence_band == "auto", which (via agent.confidence.scorer.confidence_band_from_scores)
        # only happens for outcome == "confirmed": strong direct evidence, independently
        # corroborated, no unresolved contradiction, no unresolved competing hypothesis. This
        # is a real validation gate, not human sign-off — a genuine human-approval pipeline
        # (using the existing but currently-unused sre_feedback/validation_status fields) is a
        # further improvement, not built here. See docs/confidence-framework-design.md §12.
        if result.get("status") not in ("failed", "blocked"):
            summary    = result.get("summary", {}) or {}
            # Reuses _extract_root_cause (str()-safe) instead of a second, unguarded copy of
            # the same extraction — that duplicate copy is what raised "unhashable type:
            # 'slice'" in production 2026-08-04 when the LLM returned a non-string value for
            # likely_root_cause under the more complex confidence-framework prompt.
            root_cause = _extract_root_cause(summary)[:300]
            pod        = payload.get("pod", "")
            confidence = _safe_float(result.get("confidence", 0.0))
            confidence_band = result.get("confidence_band", "escalate")
            obs = result.get("observability", {})
            incident_type = obs.get("incident_type", "") if isinstance(obs, dict) else ""
            # Persist to Vertex AI Memory Bank (cross-session) and in-process list (same session)
            if confidence_band == "auto":
                cls._mb_store(cluster, namespace, pod, root_cause, confidence, incident_type)
            else:
                log.info(
                    "Memory Bank write skipped — confidence_band=%s (only 'auto' writes persist)",
                    confidence_band,
                )
            cls._save_memory(query_text, root_cause, cluster, namespace)

        # 5. Model Armor — sanitize output (redacts PII that leaked from k8s logs)
        #
        # Issue #32 fix: _sanitize() never rewrites text either way (Model Armor blocks or
        # passes, it does not redact in place — see that function's own comment) — so
        # `sanitized_out != summary_text` can NEVER be true, and the previous code discarded
        # the `blocked` return value entirely. A real MATCH_FOUND on agent output (PII,
        # malicious content) produced only a log line: no redaction, no block, no
        # requires_human_review change, no field in the response. Now: a blocked output
        # withholds the summary content and forces human review instead of silently
        # delivering it.
        summary_obj = result.get("summary")
        if summary_obj is not None:
            summary_text = json.dumps(summary_obj) if isinstance(summary_obj, dict) else str(summary_obj)
            sanitized_out, output_blocked = cls._sanitize(summary_text, is_output=True)
            if output_blocked:
                log.warning("Model Armor blocked agent output for run_id=%s — withholding summary content", run_id)
                result["summary"] = {
                    "likely_root_cause": "[WITHHELD — flagged by Model Armor output safety filter]",
                    "confidence_score": 0.0,
                    "confidence_band": "escalate",
                    "requires_human_review": True,
                    "content_flagged": True,
                }
                result["requires_human_review"] = True
                result["content_flagged"] = True
            elif sanitized_out != summary_text:
                try:
                    result["summary"] = json.loads(sanitized_out)
                except (ValueError, TypeError):
                    result["summary"] = sanitized_out

        # 6. Attach session_id so callers can correlate multi-turn investigations
        if session_id:
            result["session_id"] = session_id

        return result


# ── Local entrypoint — use run.py for full CLI experience ─────────
if __name__ == "__main__":
    payload = {
        "query":     "Pod imagepull-pod in namespace test-incidents is in ImagePullBackOff state.",
        "namespace": "test-incidents",
        "pod":       "imagepull-pod",
        "cluster":   "sre-test-cluster",
        "severity":  "high",
    }
    result = investigate(payload)
    print(json.dumps(result, indent=2))
