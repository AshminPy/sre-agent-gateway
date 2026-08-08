# Logs

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — full grep of `agent/` for every `client.logger(...)` and stdout JSON emission
> **Owner:** SRE Agent platform team.

## Where do I see one complete investigation?

Given a `run_id`, query in this order:

1. **Default/stdout log** — `jsonPayload.run_id="<run_id>"`. Finds the `sre_agent_run` summary event and any `node_token_usage` events (per-LLM-call token tracking).
2. **`sre-agent-investigations`** — `logName="projects/sreagent-t2-demo/logs/sre-agent-investigations" AND jsonPayload.run_id="<run_id>"`. The full structured audit event: `trace_id` (for Cloud Trace correlation), failed-tools detail, the complete confidence/outcome breakdown, token/cost/latency.
3. **`sre-agent-tool-failures`** (if any tool failed) — same `run_id` filter.
4. **`sre-agent-routing-failures`** (if a routing safe-stop occurred) — same filter.
5. **`sre-agent-evidence-storage-failures`** (if a GCS write failed) — same filter.
6. **Cloud Trace** — use the `trace_id` from step 2 to pull the matching spans (see [Tracing](tracing.md)).

**Caveat**: steps 1 and 2 carry substantially overlapping fields for the same run (see the double-counting note in [Observability](observability.md)) — you will see the same logical event represented twice if you query by field value without also constraining `logName`.

## Complete logging inventory

### Named Cloud Logging loggers (queryable by `logName`)

| `logName` | Emitted by | Trigger | Severity |
|---|---|---|---|
| `sre-agent-investigations` | `agent/nodes/rca_builder.py` | Every completed investigation (end of `rca_builder`) | INFO |
| `sre-agent-tool-failures` | `agent/nodes/tool_executor.py` | Every failed MCP tool call | WARNING |
| `sre-agent-routing-failures` | `agent/nodes/mcp_router.py` | mcp_router-level routing safe-stop | ERROR |
| `sre-agent-evidence-storage-failures` | `agent/gcs_client.py` | GCS evidence write fails after 2 retries | ERROR |

### Stdout JSON events (default log, parsed as `jsonPayload` automatically)

| `event_type` | Emitted by | Trigger |
|---|---|---|
| `sre_agent_run` | `agent/main.py` | Once per investigation, after the graph completes |
| `node_token_usage` | `agent/otel.py` (`log_node_tokens`) | Once per LLM-consuming node call |
| `memory_bank_recall` | `agent/main.py` | Once per recalled Memory Bank memory (0-3 per run) |

## Agent Identity / authentication errors

Standard Cloud Audit Logs capture IAM-relevant calls made using the Agent Identity principal, same as any other GCP principal — query Cloud Logging's Audit Logs, not the application loggers above, for auth-layer failures.

## GCS evidence errors

`sre-agent-evidence-storage-failures` (application-level, only fires after both retries are exhausted) — for lower-level GCS API errors, check standard Cloud Audit Logs for the bucket.

## Model errors

Not a dedicated named logger — a Gemini API failure that isn't retried away (see [Investigation Loop](../architecture/investigation-loop.md#retry-logic)) propagates as an unhandled exception, caught by `investigate()`'s outer try/except, and surfaces as `status: "error"` in the `sre_agent_run` event plus a Python traceback in the container's stdout/stderr logs.

## LangGraph node errors

Same path — an unhandled exception anywhere in a node function is caught at the top level of `investigate()`, not per-node. Individual node-level *expected* failures (a failed tool call, a routing safe-stop) are handled gracefully and logged via the dedicated loggers above, not as exceptions.

## Sample query — Cloud Logging (Log Analytics)

```sql
SELECT jsonPayload.run_id, jsonPayload.cluster, jsonPayload.confidence_band,
       jsonPayload.total_latency_s, jsonPayload.estimated_cost_usd
FROM `sreagent-t2-demo.global._Default._Default`
WHERE jsonPayload.event_type = "sre_agent_run"
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
ORDER BY timestamp DESC
```

---

**Related pages:** [Tracing](tracing.md) · [Observability](observability.md) · [Alerting](alerting.md)
