"""
Selects ONE MCP source AND ONE tool per iteration.
Enforces tool allowlist.
Passes evidence_count to prompt so router knows when it must keep collecting.
"""
import logging
from agent.state import AgentState
from agent.gemini_client import llm_json
from agent.mcp_client import (
    MCP_REGISTRY, _get_cluster_registry,
    GKE_REMOTE_TOOLS, CUSTOM_K8S_TOOLS,
    get_tools_for_source,
)
from agent.prompts import MCP_ROUTER_PHASE2_SYSTEM, MCP_ROUTER_PHASE2_USER
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
    sources_skipped = state.get("sources_skipped", [])
    evidence_count  = len(state.get("evidence_ids", []))
    min_steps       = state["investigation"].get("min_steps", 2)

    # ── Phase 1: deterministic MCP source selection (no LLM tokens) ─
    # GKE clusters → gke_remote_mcp first. On-prem → k8s_mcp first.
    cluster_name = ctx.get("cluster_name", "")
    cluster_info = _get_cluster_registry().get(cluster_name, {})
    cluster_type = cluster_info.get("cluster_type", "gke")
    selected_mcp = "gke_remote_mcp" if cluster_type == "gke" else "k8s_mcp"
    if selected_mcp not in MCP_REGISTRY:
        selected_mcp = "gke_remote_mcp"
    usage1: dict = {"tokens_input": 0, "tokens_output": 0, "tokens_total": 0, "cost_usd": 0.0}

    # Derive allowed tools for selected MCP only
    phase2_allowed = GKE_REMOTE_TOOLS if selected_mcp == "gke_remote_mcp" else CUSTOM_K8S_TOOLS

    # ── Phase 2: pick tool from selected MCP only ─────────────────────
    forbidden_combos = _build_forbidden_list(state["tool_history"])
    action, usage2 = llm_json(
        MCP_ROUTER_PHASE2_SYSTEM.format(
            mcp_source=selected_mcp,
            allowed_tools=", ".join(sorted(phase2_allowed)),
            tool_descriptions=get_tools_for_source(selected_mcp),
        ),
        MCP_ROUTER_PHASE2_USER.format(
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

    # Combine token usage from both phases
    usage = {
        "tokens_input":  usage1.get("tokens_input",  0) + usage2.get("tokens_input",  0),
        "tokens_output": usage1.get("tokens_output", 0) + usage2.get("tokens_output", 0),
        "tokens_total":  usage1.get("tokens_total",  0) + usage2.get("tokens_total",  0),
        "cost_usd":      usage1.get("cost_usd", 0.0)   + usage2.get("cost_usd", 0.0),
    }
    log_node_tokens("mcp_router", state["run_id"], step, usage)

    tool = action.get("tool", "") if action else ""

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
        args.setdefault("namespace", namespace)
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
        mcp_source, tool, args, usage["tokens_total"], evidence_count,
    )

    skipped = []
    if skip_reason:
        for src in MCP_REGISTRY:
            if src != mcp_source:
                skipped.append(f"{src}: {skip_reason[:60]}")

    current_tokens = state["investigation"].get("tokens_total", 0)
    current_cost   = state["investigation"].get("estimated_cost_usd", 0.0)

    return {
        "selected_mcp":   mcp_source,
        "current_action": {
            "tool":       tool,
            "arguments":  args,
            "mcp_source": mcp_source,
            "reason":     action.get("reason", ""),
        },
        "sources_skipped": skipped,
        "investigation": {
            "tokens_input":       state["investigation"].get("tokens_input", 0)  + usage["tokens_input"],
            "tokens_output":      state["investigation"].get("tokens_output", 0) + usage["tokens_output"],
            "tokens_total":       current_tokens + usage["tokens_total"],
            "estimated_cost_usd": round(current_cost + usage["cost_usd"], 6),
        },
    }
