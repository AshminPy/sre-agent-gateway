# Investigation Dashboard — schema and source → normalized field mapping

Backing Terraform: `iac/observability/`. Real data verified 2026-09-05 against
project `sreagent-t2-demo`, dataset `sre_agent_investigations`.

## Raw tables (Cloud Logging sink destinations — names NOT chosen by us)

Cloud Logging derives each raw table's name from the sink's log ID (dots,
slashes, hyphens sanitized to underscores). Confirmed empirically, not
assumed — see `iac/observability/views.tf`'s header comment.

| Log source | Raw BigQuery table |
|---|---|
| `sre-agent-investigations` (agent/nodes/rca_builder.py + agent/main.py) | `sre_agent_investigations` |
| `modelarmor.googleapis.com/sanitize_operations` | `modelarmor_googleapis_com_sanitize_operations` |
| `networkservices.googleapis.com/gateway_requests` | `networkservices_googleapis_com_gateway_requests` |

All three are `use_partitioned_tables = true` (one stable table, day-partitioned
internally by BigQuery on `timestamp` — NOT date-sharded `_YYYYMMDD` tables),
with a 90-day partition expiration (`var.partition_expiration_days`).

## Dashboard field → source mapping

| Dashboard field | View | Raw source field | Notes |
|---|---|---|---|
| run_id | `v_investigations` | `jsonPayload.run_id` | Format `run_YYYYMMDD_HHMMSS_xxxx` |
| event_timestamp | all three views | `DATETIME(timestamp, var.dashboard_timezone)` | Cloud Logging ingestion time as LOCAL wall-clock (default America/New_York) so Looker Studio day buckets and "include today" follow the readers' calendar |
| event_timestamp_utc | all three views | `timestamp` | The same instant as a UTC TIMESTAMP, for correlating with Cloud Logging / traces |
| event_date_local | all three views | `DATE(timestamp, var.dashboard_timezone)` | Local calendar date (default America/New_York) for Looker Studio relative date ranges; `timestamp` itself stays UTC |
| status | `v_investigations` | `jsonPayload.status` | `"success"` \| `"error"` (rca_builder's own enum — Log A's differently-typed `status` field is NOT sunk here) |
| environment | `v_investigations` | `jsonPayload.environment` | Added 2026-09-05 — resolved once in `agent/nodes/context_resolver.py`, threaded through, never re-derived |
| cluster / cluster_region | `v_investigations` | `jsonPayload.cluster` / `cluster_region` | |
| cluster_type | `v_investigations` | derived | `CASE mcp_source WHEN 'gke_remote_mcp' THEN 'GKE' WHEN 'k8s_mcp' THEN 'non-GKE'` — deterministic by construction (verified against `agent/nodes/mcp_router.py`, not a runtime field) |
| mcp_source | `v_investigations` | `jsonPayload.mcp_source` | |
| model | `v_investigations` | `jsonPayload.model_name` | Static deployment fact (`agent.llm.MODEL`), not per-run resolved |
| tokens_input/output/total | `v_investigations` | `jsonPayload.tokens_*` | |
| estimated_llm_cost_usd | `v_investigations` | `jsonPayload.estimated_cost_usd` | Canonical cost field — see "Cost calculation" below. No second cost calculation exists anywhere in this stack. |
| tool_call_count | `v_investigations` | `ARRAY_LENGTH(jsonPayload.tools_called)` | `tools_called` is `STRING REPEATED` (a list of successful tool names) |
| evidence_count | `v_investigations` | `ARRAY_LENGTH(jsonPayload.evidence_ids)` | |
| investigation_completeness_score / root_cause_confidence_score | `v_investigations` | `jsonPayload.*_score` | Scores only — band/gaps text lives in GCS `runs/{run_id}.json`, not in this log |
| incident_type_raw | `v_investigations` | `jsonPayload.incident_type` | Raw, unmodified LLM free text — never discarded |
| rca_category_normalized | `v_investigations` | derived (regex over `incident_type_raw`) | **Not an authoritative Agent classification** — best-effort bucketing for charts only. Falls to `"Other"` for anything that doesn't match a known pattern. Deliberately does NOT map `outcome="insufficient_evidence"` to "Healthy / False Alarm" — that outcome can also mean tool failures prevented evidence collection, not that the pod was healthy (a real run hit exactly this case, `status="error"` with `outcome="insufficient_evidence"`). |
| loop_exit_reason | `v_investigations` | `jsonPayload.loop_exit_reason` | Includes `"runtime_exception"` for crash-terminal events (see below) |
| trace_id | `v_investigations` | `jsonPayload.trace_id` | The one reliable, always-present correlation key — matches Cloud Trace's own trace ID format directly |
| agent_log_link | `v_investigations` | derived | Cloud Logging console URL pre-filtered to this exact `run_id` |
| Model Armor mechanism / state | `v_model_armor_activity` | `resource.labels.template_id`, `jsonpayload_v1_sanitizeoperationlogentry.sanitizationresult.*` | Aggregate/time-series ONLY — no run_id exists in this log, no per-investigation join is possible or attempted |
| Agent Gateway ALLOW/DENY | `v_gateway_activity` | `jsonpayload_type_loadbalancerlogentry.authzpolicyinfo.*` | Aggregate/time-series ONLY — same reason as above |

## Two terminal-event kinds feeding `sre_agent_investigations`, one discriminator

- `terminal_kind = "completion"` — written by `agent/nodes/rca_builder.py` on every normal investigation completion (success or a handled error).
- `terminal_kind = "crash"` — written by `agent/main.py`'s `_write_crash_investigation_event()` ONLY when `investigate()`/`investigate_stream()` itself raises before the graph ever reached `rca_builder` (added 2026-09-05 — see `PHASE1_EVIDENCE_LOG.md`-style commit message on that change). Without this, a crashed run was previously invisible to any structured log, undercounting total investigations and overstating success rate.
- Both share `event_type = "sre_agent_run_terminal"` and `terminal_event_schema_version` as the discriminator/version marker, so `v_investigations` can treat them as one logical event stream (`WHERE jsonPayload.event_type = "sre_agent_run_terminal"`).

## Known current gap — `error_type`/`error` not yet in the live BigQuery schema

Both write paths declare `error_type`/`error` (always `null` on the completion
path, populated on the crash path). BigQuery's schema auto-detection only
materializes a column once at least one log entry has a **non-null** value for
it — as of 2026-09-05, no crash has occurred since the fix was deployed, so
these two columns do not exist in the raw table yet, and `v_investigations`
does not reference them (referencing a non-existent field would fail `CREATE
VIEW` today).

**How to add them once a real crash occurs** (this doubles as the general "how
to add another field" answer):
1. Confirm the columns exist: `bq show --format=prettyjson PROJECT:sre_agent_investigations.sre_agent_investigations` and look for `error_type`/`error` under `jsonPayload`.
2. Add `jsonPayload.error_type AS error_type, jsonPayload.error AS error_message` to `v_investigations`'s `SELECT` list in `iac/observability/views.tf`.
3. `terraform apply` — this is a `google_bigquery_table` in-place update (a view's own query text), never a table replace.

The crash-event code path itself is proven correct independent of this gap —
see `tests/test_crash_investigation_telemetry.py` (4 tests: successful
investigation still produces the normal event, a crash produces an error
terminal event, a telemetry write failure never masks the original exception,
no duplicate terminal event for one run).

## Cost calculation

`estimated_llm_cost_usd` is a pass-through of `agent/nodes/rca_builder.py`'s
own `estimated_cost_usd` (computed in `agent/llm/gemini_adapter.py` from real
per-call token counts × `var.gemini_price_input_per_1m` /
`var.gemini_price_output_per_1m`, both defined in `iac/agent/variables.tf`).
Verified 2026-09-05 against Google's current, live, official Gemini 2.5 Pro
pricing (ai.google.dev/gemini-api/docs/pricing) — exact match for the ≤200K-
token tier the agent actually uses. **No second, independently-computed cost
exists anywhere in this dashboard stack** — by explicit design decision, to
avoid two sources of truth. If the deployed model or its pricing ever
changes, update `iac/agent/variables.tf`'s pricing variables (versioned via
normal Terraform/git history) — this dashboard automatically reflects
whatever the agent itself computed, nothing more.

Labeled precisely as **"Estimated LLM Cost"**, never "actual investigation
cost" — the real GCP bill also includes Agent Engine, Cloud Run, Logging, and
BigQuery costs this number does not capture.

## Model Armor — the three mechanisms, never conflated

See `PHASE1_EVIDENCE_LOG.md` for the full investigation. Summary for this
dashboard's purposes:

| Mechanism | `template_id` | Status |
|---|---|---|
| Floor setting | `FLOOR_SETTING-*` | The only mechanism with confirmed, live, working detections today. `inspect_only` — never blocks. |
| App-level `_sanitize()` | `sre-agent-request-guard` / `sre-agent-response-guard` | Currently **disabled** by configuration (dead code in the default deployment). |
| Gateway CONTENT_AUTHZ extension | same two templates as above | Invocation proven live; `RESPONSE_BODY` inspection is a documented, accepted Google platform limitation (Streamable HTTP/SSE MCP transport) — not a bug, not fixed, disclosed. |

`v_model_armor_activity`'s `mechanism` column cannot fully distinguish the
app-level path from the gateway CONTENT_AUTHZ path by `template_id` alone
(they share templates) — in the current deployment this is moot since
app-level is disabled, but the view does not hard-code that assumption as
permanent. **Never present a floor-setting detection as an inline block —
it is inspect-only by design.**
