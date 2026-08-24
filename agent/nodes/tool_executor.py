"""
tool_executor.py
Executes MCP tool calls.
ADR rule: Raw MCP output NEVER enters graph state.
Only ok/error/duration stored in tool_history.
Raw result passes ONLY to evidence_extractor via latest_tool_result.
latest_tool_result is cleared by evidence_extractor after GCS write.
"""
import contextlib
import logging
from agent.state import AgentState
from agent.mcp_client import call_tool
from agent.otel import trace_node

log = logging.getLogger("sre-agent.tool_executor")


@trace_node("langgraph.tool_executor")
def tool_executor(state: AgentState) -> dict:
    log.info("node=tool_executor run_id=%s", state["run_id"])
    action = state.get("current_action")

    if not action or action.get("tool") == "done":
        return {}

    tool_name    = action["tool"]
    args         = action.get("arguments", {})
    mcp_source   = action.get("mcp_source", state.get("selected_mcp", "k8s_mcp"))
    ctx          = state.get("resolved_context", {})
    cluster_name = ctx.get("cluster_name", "")
    step         = state["investigation"]["current_step"]

    log.info(
        "tool_executor source=%s tool=%s cluster=%s args=%s",
        mcp_source, tool_name, cluster_name, args,
    )

    from agent.otel import get_tracer, set_span_attributes

    tracer = get_tracer()
    span_cm = (
        tracer.start_as_current_span(f"mcp.{tool_name}")
        if tracer is not None
        else contextlib.nullcontext()
    )
    with span_cm as span:
        result = call_tool(
            mcp_source=mcp_source,
            tool_name=tool_name,
            arguments=args,
            run_id=state["run_id"],
            cluster_name=cluster_name,
        )
        # set_span_attributes() no-ops on span=None and never raises (agent/otel.py) --
        # matches this codebase's existing pattern instead of raw span.set_attribute().
        set_span_attributes(span, {
            "mcp.tool":          tool_name,
            "mcp.server":        mcp_source,
            "tool.duration_ms":  round(result.get("duration_s", 0) * 1000),
            "tool.status":       "ok" if result.get("ok") else "error",
        })

    # issue #71: call_tool() can auto-fall back to a different tool/mcp_source than
    # requested (GKE Remote MCP failure -> custom MCP, or issue #70's broadened retry).
    # Its return value's own "tool"/"mcp_source" reflect what ACTUALLY executed --
    # everything below (tool_history, latest_tool_result, the failure-logging block)
    # must record THAT, not the pre-call request, or evidence/observability silently
    # claim the wrong source ran.
    executed_tool   = result.get("tool", tool_name)
    executed_source = result.get("mcp_source", mcp_source)

    # Tool history — COMPACT only, NO raw output stored in state
    # Raw result lives ONLY in latest_tool_result until evidence_extractor clears it
    record = {
        "tool":       executed_tool,
        "mcp_source": executed_source,
        "args":       args,
        "ok":         result["ok"],
        "duration_s": result.get("duration_s", 0),
        "error":      result.get("error") if not result["ok"] else None,
        "blocked":    result.get("blocked", False),
        "step":       step,
        "cluster":    cluster_name,
        # ← Raw result intentionally NOT stored here
    }

    if not result["ok"]:
        log.warning(
            "tool_executor FAILED tool=%s blocked=%s error=%s",
            executed_tool, result.get("blocked", False), result.get("error"),
        )
        try:
            import os

            from google.cloud import logging as cloud_logging
            # issue #139 fix, confirmed live 2026-08-23: this Cloud Logging write 403'd
            # ("unregistered in the Agent Registry") on 144 of 145 real attempts over 180
            # days when using the default gRPC transport -- Agent Gateway never resolved a
            # registry match for it (unlike every other working destination). Switching to
            # HTTP_JSON (matched by the us-central1-logging registry entry's protocolBinding)
            # fixed it: 3/3 clean live runs after the change, same fix already confirmed for
            # rca_builder.py's identical call. See docs/least-privilege-iam.md and issue #139.
            cloud_logging.Client(project=os.environ.get("PROJECT_ID"), _use_grpc=False).logger("sre-agent-tool-failures").log_struct(
                {
                    "event":      "tool_failure",
                    "run_id":     state["run_id"],
                    "tool":       executed_tool,
                    "mcp_source": executed_source,
                    "error":      result.get("error", ""),
                    "blocked":    result.get("blocked", False),
                    "step":       step,
                    "cluster":    cluster_name,
                },
                severity="WARNING",
            )
        except Exception:
            pass
        return {
            "tool_history": [record],
            "errors":       [f"tool={executed_tool} error={result.get('error')}"],
            "latest_tool_result": {
                "ok":         False,
                "tool":       executed_tool,
                "mcp_source": executed_source,
                "error":      result.get("error"),
                "blocked":    result.get("blocked", False),
                # ← No raw result stored
            },
        }

    log.info(
        "tool_executor ok tool=%s duration=%.1fs",
        executed_tool, result.get("duration_s", 0),
    )

    return {
        "tool_history": [record],
        # Raw result passes ONLY through latest_tool_result to evidence_extractor
        # evidence_extractor writes it to GCS then sets latest_tool_result=None
        "latest_tool_result": {
            "ok":         True,
            "tool":       executed_tool,
            "mcp_source": executed_source,
            "result":     result.get("result", {}),  # cleared after evidence_extractor
        },
    }
