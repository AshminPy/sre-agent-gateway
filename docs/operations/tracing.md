# Tracing

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/otel.py`
> **Source of Truth:** `agent/otel.py`
> **Owner:** SRE Agent platform team.

## What's traced

`agent/otel.py` sets up a Cloud Trace exporter. `trace_node(span_name)` is a decorator wrapping LangGraph node functions — every node that matters (`mcp_router`, `tool_executor`, `rca_builder`, etc.) is wrapped, creating a span named e.g. `langgraph.rca_builder`. Each span carries rich attributes pulled from state before and after the node runs, and records exceptions with an ERROR status if the node fails. `investigate()` also wraps the *entire* run in a top-level `sre_agent.investigation` span.

## Trace ID / run ID / parent span / child span

- **`trace_id`**: the 32-hex-char OTel trace identifier for one investigation — this is the join key between Cloud Logging and Cloud Trace.
- **`run_id`**: the application-level identifier (see [Context and State](../architecture/context-and-state.md)) — distinct from `trace_id`, but both are stamped on the same log entries so you can pivot between "logs for this run" and "trace for this run."
- **Parent span**: `sre_agent.investigation` (the whole run).
- **Child spans**: one per traced LangGraph node (`langgraph.<node_name>`).

`get_trace_id_hex()` never raises — if the tracer is disabled/unavailable, it returns an empty string, and the whole tracing mechanism is fail-open by design (a tracing failure never breaks an investigation).

## Where traces go

Google Cloud Trace, project `sreagent-t2-demo`. There's no hardcoded console URL in the code — the standard pattern is:
```
https://console.cloud.google.com/traces/list?tid=<trace_id>&project=sreagent-t2-demo
```
(This URL pattern follows standard GCP conventions; it isn't itself something the codebase constructs — verify it works for your account/org before relying on it in a runbook.)

## How an operator answers "why did investigation X take 60 seconds?"

1. Get the `run_id` (from the caller, or by searching Cloud Logging for the incident details).
2. Query `sre-agent-investigations` for that `run_id` to get the `trace_id` (see [Logging](logging.md)).
3. Open that `trace_id` in Cloud Trace — the span durations for each `langgraph.<node>` child span show exactly where the time went (which node's LLM call was slow, whether a tool call hung, etc.).
4. Cross-reference with `node_token_usage` log events for the same `run_id` if the slowness looks token/LLM-related rather than tool-call-related.

## Flush behavior

`flush_traces(timeout_millis=5000)` is called at the very end of `investigate()`, before the response returns — this exists specifically because Agent Engine containers are short-lived per-request, and spans need to be exported before the process could be torn down.

---

**Related pages:** [Logging](logging.md) · [Observability](observability.md)
