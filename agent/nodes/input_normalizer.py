"""
Parses alert/query and extracts incident context.
CLI hints (namespace, pod, cluster) always override LLM guesses.
"""
import logging
from agent.state import AgentState
from agent.llm import llm_json
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
        incident_type, usage["total_tokens"], usage["cost_usd"],
    )

    # CLI hints ALWAYS override LLM guesses.
    #
    # Cluster identity is NEVER defaulted here. cluster_hint is the verified signal (came
    # from the caller/alert system via resource_hints, structured data — not free text);
    # cluster_guess is the unverified signal (LLM free-text extraction from the query). Both
    # are passed through as-is, empty if absent — context_resolver.py's deterministic
    # priority chain (agent/mcp_client.py:resolve_cluster_routing) decides what, if anything,
    # they resolve to. This node does not guess a cluster and must not silently invent one.
    #
    # namespace is held to the same rule (issue #73) -- a "test-incidents" fallback here
    # let context_resolver's tier-4 project/environment/namespace routing silently match a
    # real cluster for a request that never specified one. Empty if genuinely absent.
    resolved = {
        "incident_type":     incident_type,
        "namespace":         (hints.get("namespace") or extracted.get("namespace") or "").strip(),
        "pod":               hints.get("pod")        or extracted.get("pod", ""),
        "cluster_hint":      (hints.get("cluster") or "").strip(),
        "cluster_guess":     (extracted.get("cluster_name") or "").strip(),
        "project_hint":      (hints.get("project") or "").strip(),
        "environment_hint":  (hints.get("environment") or "").strip(),
        "deployment":        hints.get("deployment") or extracted.get("deployment", ""),
        "severity":          envelope.get("incident", {}).get("severity", "unknown"),
        # Section 7 (2026-09-08): threaded through unchanged from the incoming payload
        # (agent/main.py's _prepare_investigation_envelope) so rca_builder's verifier
        # has a real incident_time_context to reason against -- see
        # docs/management/confidence-genericity-review-2026-08-28.md #15.2.
        "incident_reported_at":            envelope.get("incident", {}).get("reported_at", ""),
        "incident_reported_at_approximate": envelope.get("incident", {}).get("reported_at_approximate", False),
        "incident_start":                  envelope.get("incident", {}).get("start", ""),
        "incident_end":                    envelope.get("incident", {}).get("end", ""),
    }

    hints_applied = bool(hints.get("cluster") or hints.get("namespace") or hints.get("pod"))
    log.info(
        "input_normalizer cluster_hint=%s cluster_guess=%s namespace=%s pod=%s hints_applied=%s",
        resolved["cluster_hint"] or "(none)", resolved["cluster_guess"] or "(none)",
        resolved["namespace"], resolved["pod"], hints_applied,
    )

    from agent.llm.accounting import accumulate_usage

    return {
        "resolved_context": resolved,
        "investigation": accumulate_usage(state["investigation"], usage),
    }
