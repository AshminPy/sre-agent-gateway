"""
Writes raw sanitized evidence to GCS and stores compressed facts in state.
"""

import json
import logging

from agent.state import AgentState
from agent.llm import llm_json
from agent.gcs_client import write_evidence, redact
from agent.prompts import EVIDENCE_EXTRACTOR_SYSTEM, EVIDENCE_EXTRACTOR_USER
from agent.otel import trace_node, log_node_tokens

log = logging.getLogger("sre-agent.evidence_extractor")

# Name-field keys the custom Kubernetes MCP's tools use (mcp/server.py's
# @guarded(name_fields=...) decorators — verified exhaustively via grep against
# mcp/server.py, not imported since mcp/ and agent/ are separate deployables).
_K8S_MCP_NAME_FIELDS = (
    "pod_name", "deployment_name", "replicaset_name",
    "service_name", "job_name", "node_name", "name",
)

# Custom K8s MCP tool name -> the resource kind it's about. The tool name itself already
# encodes this deterministically (mcp/server.py's real tool set, verified against
# agent/mcp_client.py's CUSTOM_K8S_TOOLS) -- no need to ask the LLM. Tools not listed here
# (list_pods, describe_pod_detail, get_current_logs, get_previous_logs, list_events, ...) are
# inherently pod-scoped, matching the "pod" default below.
_CUSTOM_TOOL_RESOURCE_TYPE = {
    "get_configmap": "configmap", "list_configmaps": "configmap",
    "describe_deployment": "deployment", "list_deployments": "deployment",
    "describe_service": "service", "list_services": "service",
    "describe_replicaset": "replicaset", "list_replicasets": "replicaset",
    "describe_statefulset": "statefulset", "list_statefulsets": "statefulset",
    "describe_daemonset": "daemonset", "list_daemonsets": "daemonset",
    "describe_node": "node", "list_nodes": "node",
    "describe_job": "job", "list_jobs": "job",
    "describe_hpa": "hpa", "list_hpas": "hpa",
    "describe_pvc": "pvc", "list_pvcs": "pvc",
}


def _resource_type_from_call(args: dict, tool: str, mcp_source: str) -> str:
    """Build resource_type from the REAL tool-call arguments/tool name, never LLM-written
    text -- same fix class as issue #206's resource_id (_resource_id_from_call above).

    2026-08-29, confirmed live during resource_identity_match validation (see
    docs/management/confidence-genericity-review-2026-08-28.md #15.7's fix): the extractor
    LLM's own free-text "resource_type" field defaulted to "pod" whenever the model didn't
    explicitly name the kind, even for evidence that was unambiguously about a ConfigMap or
    Secret (resource_id correctly showed "test-incidents/app-config", but resource_type still
    said "pod"). This silently defeated the resource_identity_match fix that reads
    resource_type to decide whether the pod-name containment check applies -- the relaxation
    never fired because the wrong resource_type was fed into it.

    GKE Remote MCP's describe_k8s_resource/get_k8s_resource carry resourceType directly in
    their call args (toolspec.json). The custom K8s MCP encodes the resource kind in the tool
    NAME itself (_CUSTOM_TOOL_RESOURCE_TYPE). Everything else (logs, events, pod status) is
    inherently pod-scoped.
    """
    if mcp_source == "gke_remote_mcp" and args.get("resourceType"):
        return str(args["resourceType"]).lower()
    return _CUSTOM_TOOL_RESOURCE_TYPE.get(tool, "pod")


def _resource_id_from_call(args: dict, mcp_source: str, ctx: dict) -> str:
    """Build resource_id from the REAL tool-call arguments, never LLM-written text.

    issue #206: the evidence extractor's own LLM call used to write resource_id as
    free text describing what the evidence was about. scorer.py's resource_identity_match
    then did a plain substring check of the resolved namespace/pod against that text --
    so a claim's evidence could be scored "different namespace/pod" purely because the
    extractor phrased it differently, even when the evidence was 100% about the right
    resource. Verified on a real ImagePullBackOff run: all 3 raw evidence items
    (describe_k8s_resource, list_k8s_events, get_k8s_resource) explicitly named
    test-incidents/imagepull-pod in their raw output, yet 2 of 3 were flagged as a
    mismatch, capping root_cause_confidence's resource_identity_match at 0.0 on an
    otherwise unambiguous case.

    namespace/name key names verified against the real declared schema: toolspec.json
    (gke_remote_mcp — all 5 tools use only namespace/name/parent/resourceType) and
    mcp/server.py's @guarded(...) decorators (k8s_mcp — namespace plus one of the 7
    name_fields above).
    """
    namespace = args.get("namespace", "")
    name = ""
    if mcp_source == "gke_remote_mcp":
        name = args.get("name", "")
    else:
        for field in _K8S_MCP_NAME_FIELDS:
            if args.get(field):
                name = args[field]
                break
    # A namespace-wide call (e.g. list_k8s_events with no target resource) carries no
    # explicit name — fall back to the investigation's resolved default, same convention
    # already used by the extraction-failure path below.
    name = name or ctx.get("pod", "")
    namespace = namespace or ctx.get("namespace", "")
    return f"{namespace}/{name}" if namespace or name else ""


def _safe_text(value, limit: int = 500) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + " ...[truncated]"


@trace_node("langgraph.evidence_extractor")
def evidence_extractor(state: AgentState) -> dict:
    log.info(
        "node=evidence_extractor run_id=%s latest=%s",
        state["run_id"],
        "HAS_DATA" if state.get("latest_tool_result") else "NONE",
    )

    latest = state.get("latest_tool_result")
    if not latest:
        return {"latest_tool_result": None}

    tool = latest.get("tool", "unknown")
    mcp_source = latest.get("mcp_source", "unknown")
    run_id = state["run_id"]
    ctx = state.get("resolved_context", {})
    prefer_pod = ctx.get("pod", "")

    existing_count = len(state.get("evidence_ids", []))
    ev_id = f"ev_{existing_count + 1:03d}"

    # issue #68: real wall-clock time this evidence was collected -- scorer.py's freshness
    # and time_correlation components previously had no per-evidence timestamp to work with
    # at all (freshness fell back to a whole-investigation proxy; time_correlation was a
    # flat binary). This is collection time, not the age of the underlying k8s log/event
    # data itself (that would need parsing timestamps out of each tool's raw output, a
    # separate, larger change) -- still a real, usable signal that didn't exist before.
    import time
    collected_at = time.time()

    if not latest.get("ok"):
        error_data = {
            "evidence_id": ev_id,
            "tool": tool,
            "mcp_source": mcp_source,
            "error": latest.get("error"),
            "blocked": latest.get("blocked", False),
            "ok": False,
            "cluster": ctx.get("cluster_name", ""),
        }

        raw_ref = write_evidence(run_id, ev_id, error_data)
        gcs_failed = raw_ref.startswith("gcs_write_failed:")
        if gcs_failed:
            log.error("evidence_extractor: GCS write failed for %s (error record) — audit chain broken", ev_id)

        ev_entry = {
            "ok": False,
            "tool": tool,
            "mcp_source": mcp_source,
            "cluster": ctx.get("cluster_name", ""),
            "region": ctx.get("cluster_region", ""),
            "collected_at": collected_at,
            "summary": _safe_text(f"Tool failed: {latest.get('error', 'unknown')}", 500),
            "key_facts": [],
            "raw_ref": raw_ref,
            "gcs_write_failed": gcs_failed,
        }

        return {
            "evidence_ids": [ev_id],
            "evidence_store": {ev_id: ev_entry},
            "latest_tool_result": None,
            **({"errors": [f"GCS write failed for {ev_id} — audit trail missing"]} if gcs_failed else {}),
        }

    raw = latest.get("result", {})
    sanitized = redact(raw)

    raw_ref = write_evidence(
        run_id,
        ev_id,
        {
            "evidence_id": ev_id,
            "tool": tool,
            "mcp_source": mcp_source,
            "cluster": ctx.get("cluster_name", ""),
            "cluster_region": ctx.get("cluster_region", ""),
            "project_id": ctx.get("project_id", ""),
            "sanitized": sanitized,
        },
    )
    gcs_failed = raw_ref.startswith("gcs_write_failed:")
    if gcs_failed:
        log.error(
            "evidence_extractor: GCS write failed for %s — raw audit copy missing, "
            "compressed facts still captured in state",
            ev_id,
        )

    raw_str = json.dumps(sanitized, indent=2)
    if len(raw_str) > 5000:
        head = raw_str[:2000]
        tail = raw_str[-2500:]
        raw_str = head + "\n...[middle truncated — full output in GCS]...\n" + tail

    extracted, usage = llm_json(
        EVIDENCE_EXTRACTOR_SYSTEM,
        EVIDENCE_EXTRACTOR_USER.format(
            tool=tool,
            mcp_source=mcp_source,
            evidence_id=ev_id,
            preferred_pod=prefer_pod or "any failing pod",
            raw_output=raw_str,
        ),
        max_tokens=700,
    )
    log_node_tokens("evidence_extractor", state["run_id"], state["investigation"].get("current_step", 0), usage)

    # 2026-08-27: extraction can fail while the TOOL CALL succeeded -- llm_json
    # could not parse the model's response, or the model returned no summary.
    # This fallback used to write a plausible placeholder ("Results from
    # describe_k8s_resource") with key_facts=[] and, critically, ok=True.
    #
    # An ok=True entry with no facts is indistinguishable from a successful
    # extraction to everything downstream, and it INFLATES the scores. Measured
    # on a real ImagePullBackOff shape: required_evidence_coverage 0.0 -> 0.5,
    # overall completeness 0.35 -> 0.55, and one genuinely missing evidence
    # domain (kubernetes_status) vanished from the reported gaps. That is the
    # same "high completeness, no real evidence" pattern as the Model Armor
    # incident.
    #
    # It also defeated the rca_builder no_evidence gate, which filters on `ok`.
    #
    # The raw tool output is NOT lost -- it is already written to GCS at
    # raw_ref, so rca_builder's thin-evidence enrichment path can still re-read
    # it. Only the EXTRACTION is degraded, and it is now labelled as such.
    from agent.llm import llm_json_failed
    extraction_failure = llm_json_failed(extracted)
    if not extracted or not extracted.get("summary"):
        extraction_failure = extraction_failure or "extractor returned no summary"
        log.error(
            "evidence_extractor: EXTRACTION FAILED for %s (tool=%s) -- %s. The raw "
            "output is still at %s, but no facts were extracted, so this entry is "
            "marked unusable rather than counted as evidence.",
            ev_id, tool, extraction_failure, raw_ref,
        )
        extracted = {
            "resource_type": "pod",
            "resource_id": f"{ctx.get('namespace', '')}/{prefer_pod}",
            "summary": f"EXTRACTION FAILED for {tool} ({extraction_failure}) — raw output at {raw_ref}",
            "key_facts": [],
        }
    else:
        extraction_failure = ""

    # issue #206: resource_id is deterministic, built from the real tool-call arguments
    # (state["tool_history"][-1]["args"], committed by tool_executor immediately before
    # this node runs), never from the extractor LLM's own free-text description. Same
    # treatment now applies to resource_type -- see _resource_type_from_call's docstring.
    last_call = (state.get("tool_history") or [{}])[-1]
    call_args = last_call.get("args") or {}
    resource_id = _resource_id_from_call(call_args, mcp_source, ctx)
    resource_type = _resource_type_from_call(call_args, tool, mcp_source)

    ev_entry = {
        # ok=False when extraction failed, so every existing `ok` filter treats
        # this correctly: scorer's domain coverage (issue #91), claim grounding,
        # and rca_builder's no-evidence gate.
        "ok": not extraction_failure,
        "extraction_failed": bool(extraction_failure),
        "tool": tool,
        "mcp_source": mcp_source,
        "cluster": ctx.get("cluster_name", ""),
        "region": ctx.get("cluster_region", ""),
        "collected_at": collected_at,
        "resource_type": resource_type,
        "resource_id": resource_id,
        # real tool-call args, same source as resource_id above -- lets scorer.py's
        # classify_tool() distinguish e.g. get_k8s_logs(previous=true) from
        # get_k8s_logs(previous=false), which the tool name alone cannot. See
        # docs/management/confidence-genericity-review-2026-08-28.md #15.6.
        "args": call_args,
        "summary": _safe_text(extracted.get("summary", ""), 500),
        "key_facts": [_safe_text(f, 500) for f in extracted.get("key_facts", [])[:4]],
        "raw_ref": raw_ref,
        "gcs_write_failed": gcs_failed,
    }

    log.info(
        "evidence_extractor ev_id=%s source=%s tool=%s facts=%d raw_ref=%s",
        ev_id,
        mcp_source,
        tool,
        len(ev_entry["key_facts"]),
        raw_ref,
    )

    node_errors: list = []
    if gcs_failed:
        node_errors.append(
            f"GCS write failed for {ev_id} — raw audit trail missing, requires human review"
        )
    if extraction_failure:
        # Surfaced in state so the final report shows the gap. A log line alone
        # is invisible to whoever reads the RCA.
        node_errors.append(
            f"evidence extraction failed for {ev_id} (tool={tool}): {extraction_failure} — "
            f"raw output preserved at {raw_ref}"
        )

    from agent.llm.accounting import accumulate_usage

    return {
        "evidence_ids": [ev_id],
        "evidence_store": {ev_id: ev_entry},
        "latest_tool_result": None,
        "investigation": accumulate_usage(state["investigation"], usage),
        # Both failures can happen in the same call, so they are collected into a
        # single list. Two separate **{"errors": [...]} spreads in one dict
        # literal would silently drop the first -- a later key wins.
        **({"errors": node_errors} if node_errors else {}),
    }
