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
        "resource_type": extracted.get("resource_type", "pod"),
        "resource_id": extracted.get("resource_id", ""),
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
