"""
Parses alert/query and extracts incident context.
CLI hints (namespace, pod, cluster) always override LLM guesses.
"""
import logging
from agent.state import AgentState
from agent.gemini_client import llm_json
from agent.prompts import INPUT_NORMALIZER_SYSTEM, INPUT_NORMALIZER_USER
from agent.otel import trace_node, log_node_tokens

log = logging.getLogger("sre-agent.input_normalizer")


@trace_node("langgraph.input_normalizer")
def input_normalizer(state: AgentState) -> dict:
    log.info("node=input_normalizer run_id=%s", state["run_id"])
    envelope = state.get("incident_envelope", {})
    query    = envelope.get("user_query", "")
    hints    = envelope.get("resource_hints", {})

    if not query:
        return {
            "errors":        ["input_normalizer: user_query is required"],
            "investigation": {"status": "failed"},
        }

    extracted, usage = llm_json(
        INPUT_NORMALIZER_SYSTEM,
        INPUT_NORMALIZER_USER.format(query=query),
        max_tokens=512,
    )
    log_node_tokens("input_normalizer", state["run_id"], state["investigation"].get("current_step", 0), usage)

    incident_type = extracted.get("incident_type", "Unknown")
    log.info(
        "input_normalizer incident_type=%s tokens=%d cost=$%.6f",
        incident_type, usage["tokens_total"], usage["cost_usd"],
    )

    # CLI hints ALWAYS override LLM guesses
    resolved = {
        "incident_type": incident_type,
        "namespace":     hints.get("namespace") or extracted.get("namespace", "test-incidents"),
        "pod":           hints.get("pod")        or extracted.get("pod", ""),
        "cluster_name":  hints.get("cluster")    or extracted.get("cluster_name", "sre-test-cluster"),
        "deployment":    hints.get("deployment") or extracted.get("deployment", ""),
        "severity":      envelope.get("incident", {}).get("severity", "unknown"),
    }

    hints_applied = bool(hints.get("cluster") or hints.get("namespace") or hints.get("pod"))
    log.info(
        "input_normalizer cluster=%s namespace=%s pod=%s hints_applied=%s",
        resolved["cluster_name"], resolved["namespace"], resolved["pod"], hints_applied,
    )

    return {
        "resolved_context": resolved,
        "investigation": {
            "tokens_input":       usage["tokens_input"],
            "tokens_output":      usage["tokens_output"],
            "tokens_total":       usage["tokens_total"],
            "estimated_cost_usd": usage["cost_usd"],
        },
    }
