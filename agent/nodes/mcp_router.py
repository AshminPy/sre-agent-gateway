"""
Selects ONE MCP source AND ONE tool per iteration.
Enforces tool allowlist.
Passes evidence_count to prompt so router knows when it must keep collecting.
"""
import logging
from agent.state import AgentState
from agent.llm import llm_json
from agent.mcp_client import (
    MCP_REGISTRY, _get_cluster_registry,
    GKE_REMOTE_TOOLS, CUSTOM_K8S_TOOLS,
    get_tools_for_source, _CUSTOM_TOOLS_WITHOUT_NAMESPACE,
)
from agent.prompts import (
    MCP_ROUTER_PHASE2_SYSTEM, MCP_ROUTER_PHASE2_USER,
    MCP_ROUTER_ADDITIONAL_SOURCE_SYSTEM, MCP_ROUTER_ADDITIONAL_SOURCE_USER,
)
from agent.source_catalog import select_additional_source
from agent.otel import trace_node, log_node_tokens

log = logging.getLogger("sre-agent.mcp_router")


def _tool_call_log(tool_history: list) -> str:
    if not tool_history:
        return "No tools called yet."
    lines = []
    for i, h in enumerate(tool_history, 1):
        ok     = "✅" if h.get("ok") else "❌"
        source = h.get("mcp_source", "?")
        args   = {k: v for k, v in h.get("args", {}).items()}
        arg_str = ", ".join(f"{k}={v}" for k, v in args.items())
        line = f"[{i}] {ok} {source}.{h.get('tool','?')}({arg_str}) {h.get('duration_s','?')}s"
        if not h.get("ok"):
            line += f" — {str(h.get('error',''))[:60]}"
        lines.append(line)
    return "\n".join(lines)


def _evidence_digest(state: AgentState) -> str:
    store = state.get("evidence_store", {})
    if not store:
        return "No evidence collected yet."
    lines = []
    for ev_id, ev in store.items():
        lines.append(f"[{ev_id}] {ev.get('summary','')[:150]}")
        for f in ev.get("key_facts", [])[:3]:
            lines.append(f"  • {f}")
    return "\n".join(lines)


def _build_forbidden_list(tool_history: list) -> str:
    """All successfully-run (tool, args) combos — injected into Phase 2 prompt."""
    seen = []
    for h in tool_history:
        if h.get("ok"):
            args    = h.get("args", {})
            arg_str = ", ".join(f"{k}={v}" for k, v in sorted(args.items()))
            seen.append(f"  - {h.get('tool','?')}({arg_str})")
    return "\n".join(seen) if seen else "  None"


def _is_duplicate(tool: str, args: dict, tool_history: list) -> bool:
    for h in tool_history:
        if h.get("ok") and h.get("tool") == tool and h.get("args") == args:
            return True
    return False


def _log_routing_failure(run_id: str, cluster_name: str, reason: str) -> None:
    """Structured Cloud Logging event for an mcp_router-level routing safe-stop — the
    cluster was already resolved by context_resolver but has no usable/enabled registry
    entry by the time mcp_router runs (registry went stale, or the cluster was disabled
    mid-run). Distinct from context_resolver's "unresolved" cluster safe-stop (that one
    means no cluster could be identified at all; this one means an identified cluster
    could not be routed to an MCP). Same dedicated-logName pattern as
    tool_executor.py's sre-agent-tool-failures — see PRODUCTION-LAUNCH-PLAN.md
    Priority 10 ("routing failures" alert).
    """
    try:
        import os

        from google.cloud import logging as cloud_logging

        # issue #139 fix, confirmed live 2026-08-23 -- see tool_executor.py's identical
        # comment for the full evidence. _use_grpc=False is the confirmed fix, 3/3 clean
        # live runs after the change.
        cloud_logging.Client(project=os.environ.get("PROJECT_ID"), _use_grpc=False).logger("sre-agent-routing-failures").log_struct(
            {
                "event":   "mcp_routing_failure",
                "run_id":  run_id,
                "cluster": cluster_name,
                "reason":  reason,
            },
            severity="ERROR",
        )
    except Exception:
        # Never let observability logging break the investigation.
        pass


@trace_node("langgraph.mcp_router")
def mcp_router(state: AgentState) -> dict:
    step = state["investigation"]["current_step"]
    log.info("node=mcp_router step=%d run_id=%s", step, state["run_id"])

    ctx             = state.get("resolved_context", {})
    incident_type   = ctx.get("incident_type", "Unknown")
    namespace       = ctx.get("namespace", "test-incidents")
    pod             = ctx.get("pod", "")
    task_plan       = state["investigation"].get("task_plan", "")
    primary_gap     = state["investigation"].get("primary_gap", "")
    # issue #207: without the original report, a blank Pod gave this prompt nothing
    # to scope tool arguments to -- it defaulted to an unscoped, namespace-wide call
    # and picked up whatever looked loudest, not necessarily the actual target.
    user_query      = state.get("incident_envelope", {}).get("user_query", "")
    # 2026-08-27: was len(evidence_ids), which counts SLOTS. Failed calls and
    # failed extractions fill slots, so this over-stated how much evidence the
    # agent held -- and it goes straight into the router's prompt, telling the
    # model it has evidence it does not have.
    from agent.state import usable_evidence_ids
    evidence_count  = len(usable_evidence_ids(state))

    # ── Phase 1: deterministic MCP source selection (no LLM tokens) ─
    # GKE clusters → gke_remote_mcp first. On-prem → k8s_mcp first.
    cluster_name = ctx.get("cluster_name", "")
    cluster_info = _get_cluster_registry().get(cluster_name, {}) if cluster_name else {}

    # Never guess an MCP destination. An empty cluster_name (context_resolver safe-stopped
    # and should have already short-circuited the graph to rca_builder — this is a defense-
    # in-depth check, not the primary gate) or a cluster_name missing from the registry
    # (registry empty/stale, or the cluster was deleted/disabled since context_resolver ran)
    # means we do not know which MCP to route to. Stop — do not default to gke_remote_mcp.
    # See PRODUCTION-LAUNCH-PLAN.md Priority 5.
    if not cluster_info or not cluster_info.get("enabled", True):
        reason = (
            f"No registry entry for cluster '{cluster_name}'" if not cluster_info
            else f"Cluster '{cluster_name}' is registered but disabled"
        )
        log.error("mcp_router SAFE-STOP: %s — refusing to guess an MCP destination", reason)
        _log_routing_failure(state["run_id"], cluster_name, reason)
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
            "errors": [f"mcp_router: {reason} — cannot route safely, investigation stopped."],
        }

    # ── Section 6: capability-based additional-source check ────────────
    # Runs BEFORE the deterministic Kubernetes-only Phase 1 below. With every
    # catalog entry disabled (today's real state, see agent/source_catalog.py),
    # select_additional_source() always returns None and this block is a
    # complete no-op -- the Kubernetes routing below is byte-for-byte the same
    # as before this existed. Only when a real source is enabled AND
    # authorized for this cluster AND the planner's own stated gap matches one
    # of its declared capabilities does this branch ever fire.
    additional_source = select_additional_source(cluster_name, task_plan, primary_gap)
    if additional_source is not None:
        limits = additional_source.get("query_limits", {})
        max_window = limits.get("max_window_seconds", 3600)
        action, usage = llm_json(
            MCP_ROUTER_ADDITIONAL_SOURCE_SYSTEM.format(
                source_id=additional_source["source_id"],
                approved_tools=", ".join(sorted(additional_source.get("approved_tools", []))),
                max_window_seconds=max_window,
            ),
            MCP_ROUTER_ADDITIONAL_SOURCE_USER.format(
                user_query=user_query or "not provided",
                source_id=additional_source["source_id"],
                matched_capability=additional_source["matched_capability"],
                namespace=namespace,
                pod=pod or "not specified",
                task_plan=task_plan,
                primary_gap=primary_gap,
                evidence_count=evidence_count,
                evidence_digest=_evidence_digest(state),
                max_window_seconds=max_window,
            ),
            max_tokens=200,
        )
        log_node_tokens("mcp_router", state["run_id"], step, usage)

        from agent.llm import llm_json_failed
        if llm_json_failed(action) or not action or not action.get("promql"):
            log.warning(
                "mcp_router: additional-source query construction failed for source=%s -- "
                "falling through to Kubernetes routing instead of failing the whole step",
                additional_source["source_id"],
            )
        else:
            import time as _time
            from agent.llm.accounting import accumulate_usage
            window = min(int(action.get("window_seconds") or max_window), max_window)
            now = _time.time()
            log.info(
                "mcp_router → source=%s tool=query_range promql=%s window=%ds",
                additional_source["source_id"], action["promql"], window,
            )
            return {
                "selected_mcp": additional_source["source_id"],
                "current_action": {
                    "tool": "query_range",
                    "arguments": {
                        "cluster_id": cluster_name,
                        "promql": action["promql"],
                        "start_ts": now - window,
                        "end_ts": now,
                    },
                    "mcp_source": additional_source["source_id"],
                    "reason": action.get("reason", ""),
                },
                "investigation": accumulate_usage(state["investigation"], usage),
            }
        # falls through to Kubernetes routing below on any failure above

    cluster_type = cluster_info.get("cluster_type", "gke")
    selected_mcp = "gke_remote_mcp" if cluster_type == "gke" else "k8s_mcp"
    if selected_mcp not in MCP_REGISTRY:
        selected_mcp = "gke_remote_mcp"

    # Derive allowed tools for selected MCP only
    phase2_allowed = GKE_REMOTE_TOOLS if selected_mcp == "gke_remote_mcp" else CUSTOM_K8S_TOOLS

    # ── Phase 2: pick tool from selected MCP only ─────────────────────
    # Phase 1 above is deterministic (no LLM call, no tokens) — usage below is
    # this node's entire token/cost footprint, not a two-phase combination.
    forbidden_combos = _build_forbidden_list(state["tool_history"])
    action, usage = llm_json(
        MCP_ROUTER_PHASE2_SYSTEM.format(
            mcp_source=selected_mcp,
            allowed_tools=", ".join(sorted(phase2_allowed)),
            tool_descriptions=get_tools_for_source(selected_mcp),
        ),
        MCP_ROUTER_PHASE2_USER.format(
            user_query=user_query or "not provided",
            mcp_source=selected_mcp,
            incident_type=incident_type,
            namespace=namespace,
            pod=pod or "not specified",
            task_plan=task_plan,
            primary_gap=primary_gap,
            tool_call_log=_tool_call_log(state["tool_history"]),
            forbidden_combos=forbidden_combos,
            evidence_digest=_evidence_digest(state),
            evidence_count=evidence_count,
        ),
        max_tokens=300,
    )
    log_node_tokens("mcp_router", state["run_id"], step, usage)

    tool = action.get("tool", "") if action else ""

    # 2026-08-27: these were one branch, all logged as a benign
    # "mcp_router → done" at INFO, and all producing
    # loop_exit_reason="tool_signaled_done". Only ONE of them is the router
    # actually deciding the investigation is complete. The other two are
    # failures -- an unparseable model response, or a response with no `tool`
    # key -- that silently ended the investigation while the final report
    # claimed a normal, complete run.
    #
    # Local import beside its use; the auto-formatter strips a top-level import
    # whose usage lands in a separate edit.
    from agent.llm import llm_json_failed
    router_failure = llm_json_failed(action)
    if router_failure or (action and not tool):
        reason = router_failure or "model response contained no 'tool' key"
        log.error(
            "mcp_router: CANNOT PLAN A TOOL CALL -- %s (mcp_source=%s step=%d "
            "evidence_count=%d). Ending the investigation as FAILED rather than "
            "reporting it as a normal completion.",
            reason, selected_mcp, step, evidence_count,
        )
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
            "errors": [f"mcp_router could not plan a tool call: {reason}"],
            "investigation": {
                "status": "failed",
                "loop_exit_reason": "router_failed",
            },
        }

    if not action or not tool or tool == "done":
        log.info("mcp_router → done (evidence_count=%d)", evidence_count)
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
        }

    mcp_source  = selected_mcp
    raw_args    = action.get("arguments")
    args        = raw_args if isinstance(raw_args, dict) else {}
    skip_reason = action.get("skip_reason", "")

    # ── TOOL ALLOWLIST ENFORCEMENT ────────────────────────────────
    if tool not in phase2_allowed:
        log.warning("mcp_router ALLOWLIST BLOCKED: tool='%s'", tool)
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
            "errors":         [f"Tool '{tool}' blocked — not in allowlist"],
        }

    # ── AUTO-FILL for custom K8s MCP ─────────────────────────────
    if mcp_source == "k8s_mcp":
        # Section 5 redesign: the shared custom MCP now serves multiple clusters
        # from one Cloud Run deployment (see mcp/server.py's resolve_cluster()) --
        # every tool call must carry the exact cluster_id this investigation
        # already resolved (the SAME cluster_name that decided selected_mcp above
        # via the Phase 1 deterministic routing). This is a forced OVERWRITE, not
        # setdefault: the model's own JSON response must never be allowed to name
        # a different cluster_id than the one this investigation was actually
        # resolved to -- evidence text is untrusted input, and letting an
        # LLM-controlled field pick the target cluster would reopen exactly the
        # cross-cluster-evidence risk this redesign closes (issue #86).
        if args.get("cluster_id") not in (None, cluster_name):
            log.warning(
                "mcp_router: overriding model-supplied cluster_id=%r with the "
                "investigation's actual resolved cluster '%s' for tool '%s' -- "
                "a tool call must never target a different cluster than the one "
                "this investigation was resolved to",
                args.get("cluster_id"), cluster_name, tool,
            )
        args["cluster_id"] = cluster_name
        # issue #246: cluster-scoped tools (list_nodes, describe_node) take no
        # namespace param at all -- injecting one made every call fail with
        # the MCP server's own "unexpected_keyword_argument" validation error.
        if tool not in _CUSTOM_TOOLS_WITHOUT_NAMESPACE:
            args.setdefault("namespace", namespace)
        elif "namespace" in args:
            # The auto-fill guard above only stops US from ADDING a namespace --
            # it does nothing if the model's own raw arguments already included
            # one (e.g. it copied "namespace" from a prior namespaced call in the
            # same investigation). That would still reach the MCP server and
            # fail with the identical unexpected_keyword_argument error the
            # auto-fill fix was meant to prevent. Strip it here too, with a
            # clear reason logged, rather than letting an unsupported argument
            # reach the tool contract silently.
            log.warning(
                "mcp_router: stripping unsupported 'namespace' argument the model "
                "supplied directly for cluster-scoped tool '%s' (args=%s) -- this "
                "tool takes no namespace parameter",
                tool, args,
            )
            args.pop("namespace", None)
        if tool in ("describe_pod_detail", "get_current_logs",
                    "get_previous_logs", "list_events") and pod:
            args.setdefault("pod_name", pod)

    # ── REMOVE UNSUPPORTED ARGS ───────────────────────────────────
    args.pop("field_selector",  None)
    args.pop("label_selector",  None)
    args.pop("pod_name_filter", None)

    # ── PARAMETER VALIDATION ──────────────────────────────────────
    ns = args.get("namespace", "")
    if ns and not all(c.isalnum() or c in "-_" for c in ns):
        log.warning("mcp_router PARAM BLOCKED: unsafe namespace='%s'", ns)
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
            "errors":         [f"Unsafe namespace: '{ns}'"],
        }

    # ── DEDUP PREVENTION ─────────────────────────────────────────
    if _is_duplicate(tool, args, state["tool_history"]):
        log.warning("mcp_router DEDUP: %s(%s) already called successfully", tool, args)
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
        }

    log.info(
        "mcp_router → source=%s tool=%s args=%s tokens=%d evidence_count=%d",
        mcp_source, tool, args, usage["total_tokens"], evidence_count,
    )

    skipped = []
    if skip_reason:
        for src in MCP_REGISTRY:
            if src != mcp_source:
                skipped.append(f"{src}: {skip_reason[:60]}")

    from agent.llm.accounting import accumulate_usage

    return {
        "selected_mcp":   mcp_source,
        "current_action": {
            "tool":       tool,
            "arguments":  args,
            "mcp_source": mcp_source,
            "reason":     action.get("reason", ""),
        },
        "sources_skipped": skipped,
        "investigation": accumulate_usage(state["investigation"], usage),
    }
