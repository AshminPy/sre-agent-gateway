# Runbook: Investigation-Level Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 1. Agent Engine invocation failure

**Symptom**: `SREAgent.query()` returns `{"error": ..., "status": "failed"}` or the caller gets a hard error.

**How to verify**: check the container's stdout/stderr logs for a Python traceback around the failure time — an unhandled exception anywhere in `investigate()` is caught at the top level and surfaces this way.

**Resolution**: depends entirely on the traceback — this is a catch-all, not a specific failure mode. Common root causes seen historically: a malformed LLM JSON response that exhausted the repair fallback, an unexpected exception in a node not covered by that node's own error handling.

**Escalation**: platform team, with the traceback.

## 18. Model call failure

**Symptom**: an LLM call raises an exception that isn't a `429`.

**How to verify**: `agent/gemini_client.py`'s `llm()` retries only `429` errors (up to 3x with backoff); any other exception propagates immediately.

**Resolution**: check the exception type — auth issues (Agent Identity/IAM to Vertex AI), quota issues (see #19), or a genuine API-side problem. Live model config: `gemini-2.5-pro`, temperature 0.0 — see [Agent Engine](../architecture/agent-engine.md#how-it-is-invoked).

## 19. Model quota / rate-limit failure

**Symptom**: `429` errors from Gemini.

**Verify/Resolution**: the client already retries these automatically (3 attempts, `30 * (attempt+1)` second backoff) — a `429` that still fails after 3 retries means sustained quota pressure, not a transient blip. Check Vertex AI quota for the project/region. See [Capacity and Quotas](../governance/capacity.md).

## 20. LangGraph loop reaching maximum iterations

**Symptom**: `loop_exit_reason="max_iterations"`.

**Verify**: this fires at exactly `current_step >= 5` (`max_steps`, hardcoded — see [Investigation Loop](../architecture/investigation-loop.md)).

**Resolution**: not itself a bug — it's a designed hard cap. If this is happening frequently for a specific incident type, that's a signal the investigation needs more than 5 tool calls to resolve reliably; consider whether `task_planner`'s prompt or the evidence-domain coverage for that incident type needs improvement (a code/prompt change, not a config knob — `max_steps` is not currently configurable).

## 21. Very low confidence investigations

**Symptom**: repeated `outcome=INSUFFICIENT_EVIDENCE`/`UNKNOWN` for a specific cluster or incident type.

**Verify**: check `investigation_completeness`'s component breakdown in the RCA — which factor is low (evidence coverage? tool success rate?).

**Resolution**: usually points to either a real gap in what tools can observe for that incident type, or a cluster-specific access problem (check `tool_success` component specifically — low tool success drags completeness down directly).

## 22. Investigations suddenly using excessive tokens

**Symptom**: `investigation_cost_usd` spikes; `SRE Agent — Investigation Cost Spike` alert fires.

**Verify**: check `node_token_usage` log events for the affected `run_id`s — which node is consuming unusually many tokens.

**Resolution**: check for an oscillating/stuck loop running more iterations than typical before hitting a stop condition; check if evidence digests grew unusually large (many tool calls with big raw output triggering the "enriched digest" GCS re-read path in `rca_builder`).

## 23. Latency spike

**Symptom**: `SRE Agent — Excessive Investigation Latency` fires.

**Verify**: use `trace_id` (see [Tracing](../operations/tracing.md)) to see exactly which node/span took the time.

**Resolution**: depends on findings — a slow tool call (upstream MCP/K8s API latency) vs. a slow LLM call (Gemini-side) require different follow-up. Not something application config can directly fix beyond investigating the root cause.

---

**Related pages:** [Investigation Loop](../architecture/investigation-loop.md) · [Tracing](../operations/tracing.md) · [Alerting](../operations/alerting.md)
