"""
tool_executor.py
Executes MCP tool calls.
ADR rule: Raw MCP output NEVER enters graph state.
Only ok/error/duration stored in tool_history.
Raw result passes ONLY to evidence_extractor via latest_tool_result.
latest_tool_result is cleared by evidence_extractor after GCS write.
"""
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

    result = call_tool(
        mcp_source=mcp_source,
        tool_name=tool_name,
        arguments=args,
        run_id=state["run_id"],
        cluster_name=cluster_name,
    )

    # Tool history — COMPACT only, NO raw output stored in state
    # Raw result lives ONLY in latest_tool_result until evidence_extractor clears it
    record = {
        "tool":       tool_name,
        "mcp_source": mcp_source,
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
            tool_name, result.get("blocked", False), result.get("error"),
        )
        try:
            import os

            from google.cloud import logging as cloud_logging
            cloud_logging.Client(project=os.environ.get("PROJECT_ID")).logger("sre-agent-tool-failures").log_struct(
                {
                    "event":      "tool_failure",
                    "run_id":     state["run_id"],
                    "tool":       tool_name,
                    "mcp_source": mcp_source,
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
            "errors":       [f"tool={tool_name} error={result.get('error')}"],
            "latest_tool_result": {
                "ok":         False,
                "tool":       tool_name,
                "mcp_source": mcp_source,
                "error":      result.get("error"),
                "blocked":    result.get("blocked", False),
                # ← No raw result stored
            },
        }

    log.info(
        "tool_executor ok tool=%s duration=%.1fs",
        tool_name, result.get("duration_s", 0),
    )

    return {
        "tool_history": [record],
        # Raw result passes ONLY through latest_tool_result to evidence_extractor
        # evidence_extractor writes it to GCS then sets latest_tool_result=None
        "latest_tool_result": {
            "ok":         True,
            "tool":       tool_name,
            "mcp_source": mcp_source,
            "result":     result.get("result", {}),  # cleared after evidence_extractor
        },
    }
