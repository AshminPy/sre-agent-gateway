"""
GCP LangGraph graph — deterministic sequential node execution.
Node order is structurally enforced — cannot skip or run out of sequence.
"""
from __future__ import annotations
import logging
from langgraph.graph import END, StateGraph
from agent.state import AgentState, get_initial_state  # noqa
from agent.nodes.input_normalizer   import input_normalizer
from agent.nodes.context_resolver   import context_resolver
from agent.nodes.task_planner       import task_planner
from agent.nodes.mcp_router         import mcp_router
from agent.nodes.tool_executor      import tool_executor
from agent.nodes.evidence_extractor import evidence_extractor
from agent.nodes.task_evaluator     import task_evaluator
from agent.nodes.loop_controller    import loop_controller
from agent.nodes.rca_builder        import rca_builder

log = logging.getLogger("sre-agent.graph")

# LangGraph's own step-count safety net (distinct from investigation.max_steps=5
# in agent/state.py, which governs loop_controller's own exit decision — this is
# a much larger ceiling, since each loop iteration touches ~4-5 graph nodes).
# LangGraph's built-in default is 25, which is too low for this graph's normal
# shape and was silently being used by agent/eval/run_eval.py's local mode
# (only agent/main.py's investigate() passed a recursion_limit config,
# independently hardcoded — the two drifted apart with no test catching it).
# Both callers must import this constant, not hardcode their own number.
GRAPH_RECURSION_LIMIT = 60


def _after_input(state: AgentState) -> str:
    return "failed" if state["investigation"].get("status") == "failed" else "ok"


def _after_context(state: AgentState) -> str:
    """Route straight to rca_builder if context_resolver safe-stopped (e.g. cluster could
    not be deterministically resolved) — mirrors _after_input. Without this, a "failed"
    status set by context_resolver would be silently overwritten by loop_controller on the
    next iteration and the graph would proceed to call tools anyway."""
    return "failed" if state["investigation"].get("status") == "failed" else "ok"


def _after_tool(state: AgentState) -> str:
    """Route after tool_executor — skip evidence if no result."""
    action = state.get("current_action", {})
    if action.get("tool") == "done":
        return "skip"
    latest = state.get("latest_tool_result")
    if latest is None:
        return "skip"
    return "extract"


def _after_loop(state: AgentState) -> str:
    return "continue" if state["investigation"]["status"] == "running" else "finish"


def compile_graph():
    g = StateGraph(AgentState)

    g.add_node("input_normalizer",   input_normalizer)
    g.add_node("context_resolver",   context_resolver)
    g.add_node("task_planner",       task_planner)
    g.add_node("mcp_router",         mcp_router)
    g.add_node("tool_executor",      tool_executor)
    g.add_node("evidence_extractor", evidence_extractor)
    g.add_node("task_evaluator",     task_evaluator)
    g.add_node("loop_controller",    loop_controller)
    g.add_node("rca_builder",        rca_builder)

    g.set_entry_point("input_normalizer")

    g.add_conditional_edges(
        "input_normalizer",
        _after_input,
        {"ok": "context_resolver", "failed": "rca_builder"},
    )

    g.add_conditional_edges(
        "context_resolver",
        _after_context,
        {"ok": "task_planner", "failed": "rca_builder"},
    )
    g.add_edge("task_planner",     "mcp_router")
    g.add_edge("mcp_router",       "tool_executor")

    # Conditional after tool_executor — ensures evidence_extractor
    # only runs AFTER tool_executor state is committed
    g.add_conditional_edges(
        "tool_executor",
        _after_tool,
        {
            "extract": "evidence_extractor",
            "skip":    "task_evaluator",
        },
    )

    g.add_edge("evidence_extractor", "task_evaluator")
    g.add_edge("task_evaluator",     "loop_controller")

    g.add_conditional_edges(
        "loop_controller",
        _after_loop,
        {"continue": "task_planner", "finish": "rca_builder"},
    )

    g.add_edge("rca_builder", END)

    return g.compile()
