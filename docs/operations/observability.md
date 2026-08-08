# Observability

> **Implementation Status:** IMPLEMENTED (all metrics below exist and are wired) — with one confirmed accuracy issue, see below
> **Last Verified:** 2026-08-08 — `iac/agent/monitoring.tf`
> **Source of Truth:** `iac/agent/monitoring.tf`
> **Owner:** SRE Agent platform team / Run-Ops (once handed off).

## ⚠️ Known accuracy issue: several metrics likely double-count

`errors`, `confidence_band`, `escalations`, `invocations`, `investigation_cost_usd`, and `investigation_latency_seconds` all filter on `jsonPayload.<field>` values with **no `logName` restriction**. The same field values (`run_id`, `status`, `confidence_band`, `estimated_cost_usd`, `total_latency_s`) are emitted **twice per investigation run** — once via the stdout `sre_agent_run` event (`agent/main.py`) and once via the dedicated `sre-agent-investigations` Cloud Logging entry (`agent/nodes/rca_builder.py`). This is a structural finding from code inspection (both emission paths definitely exist and definitely carry the same field names); it has not yet been confirmed against live Cloud Logging with a direct count comparison. **Until fixed or confirmed otherwise, treat absolute counts on these 6 metrics as approximately 2x the real number of investigations** — ratios between two equally-inflated metrics (e.g. error rate) remain meaningful. The metrics that are correctly scoped by `logName` (`tool_failures`, `routing_failures`, `evidence_storage_failures`) don't have this issue.

## Infrastructure metrics

Standard GCP platform metrics for Agent Engine / Cloud Run / networking are available through normal Cloud Monitoring, not enumerated here since they're not specific to this application.

## AI / Agent behavioral metrics — the full inventory

All are Cloud Logging log-based metrics defined in `iac/agent/monitoring.tf`.

| Metric | Filter | What it means | Normal | Abnormal | Action |
|---|---|---|---|---|---|
| `sre_agent/invocations` | `jsonPayload.run_id:*` | Investigation count | Matches expected incident volume | Sudden drop to zero | Check Agent Engine health (§1 of [Daily Health Check](daily-health-check.md)) |
| `sre_agent/errors` | `jsonPayload.status="error"` | System/tooling failures (not just low confidence) | Occasional, isolated | >5 in 5 min | `SRE Agent — High Error Rate` alert fires; check recent logs for the failing `run_id`s |
| `sre_agent/escalations` | `jsonPayload.confidence_band="escalate"` | Low-confidence investigations | Some fraction expected | >3 in 5 min | `SRE Agent — High Escalation Rate` alert fires; review recent RCAs for a pattern (bad evidence coverage? new incident type?) |
| `sre_agent/investigation_cost_usd` | `jsonPayload.estimated_cost_usd > 0` | Per-run Gemini cost | Sub-cent to a few cents typically | p99 > $0.10 | `SRE Agent — Investigation Cost Spike` alert; check for a runaway loop or unusually large evidence digest |
| `sre_agent/confidence_band` | `jsonPayload.confidence_band != ""` | Distribution across auto/review/escalate | Mostly auto/review | Escalate share rising | No dedicated alert — manual trend-watch, see [Daily Health Check](daily-health-check.md#5-is-confidence-degrading) |
| `sre_agent/loop_exit_reason` | `jsonPayload.loop_exit_reason != ""` | Why investigations stopped | Mostly `confidence_sufficient` | Rising `timeout`/`stuck_detected`/`oscillation_detected` | `SRE Agent — Abnormal Loop/Token Termination` alert covers the abnormal subset |
| `sre_agent/tool_failures` | `logName=".../sre-agent-tool-failures"` | Failed MCP tool calls, labeled tool/cluster/mcp_source | Occasional | >3/5min per source, or >5/10min aggregate | Two alerts cover this — see [Alerting](alerting.md) |
| `sre_agent/routing_failures` | `logName=".../sre-agent-routing-failures"` | mcp_router-level safe-stops (cluster resolved but no usable MCP entry) | Zero | Any occurrence | `SRE Agent — Routing Failures` fires on >0 |
| `sre_agent/unresolved_cluster` | `jsonPayload.cluster_routing_method="unresolved"` | context_resolver couldn't identify any cluster | Zero | Any occurrence | `SRE Agent — Unknown/Ambiguous Cluster Safe-Stop` fires on >0 — check the incident payload/alert metadata quality |
| `sre_agent/evidence_storage_failures` | `logName=".../sre-agent-evidence-storage-failures"` | Permanent GCS write failures | Zero | Any occurrence | `SRE Agent — Evidence Storage Failures` fires on >0 — check evidence bucket IAM/existence |
| `sre_agent/investigation_latency_seconds` | `jsonPayload.total_latency_s > 0` | Total wall-clock per investigation | Well under 180s | p99 > 180s | `SRE Agent — Excessive Investigation Latency` alert |

## Missing-tool rate / memory hit rate

**Not currently tracked as dedicated metrics.** Missing-tool events are folded into `tool_failures`/`routing_failures`; memory hits are only visible via the `memory_bank_recall` log event (no metric). See [Alerting](alerting.md#gaps) for the gap list.

---

**Related pages:** [Alerting](alerting.md) · [Logging](logging.md) · [Daily Health Check](daily-health-check.md)
