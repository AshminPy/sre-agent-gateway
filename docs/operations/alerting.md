# Alerting

> **Implementation Status:** PARTIALLY IMPLEMENTED — 13 alert policies exist; 3 alert types deliberately not built yet (see below)
> **Last Verified:** 2026-09-06 — `iac/agent/monitoring.tf`
> **Source of Truth:** `iac/agent/monitoring.tf:274-581`
> **Owner:** SRE Agent platform team.

All alerts route to a single email notification channel (`google_monitoring_notification_channel.email_oncall`) — there is no PagerDuty routing or severity tiering today (see [Gaps](#gaps)).

## Every configured alert

| Alert | Condition | Notification | Reason | Operator response |
|---|---|---|---|---|
| SRE Agent — High Error Rate | `errors` SUM > 5 in 5min | email | Catch systemic failures | Check recent `sre-agent-investigations` entries with `status="error"`, find the common cause |
| SRE Agent — High Escalation Rate | `escalations` SUM > 3 in 5min | email | Catch a spike in low-confidence outcomes | Review recent RCAs — new incident type? evidence coverage gap? |
| SRE Agent — Investigation Cost Spike | `investigation_cost_usd` p99 > $0.10 | email | Catch runaway cost | Check for an oscillating/stuck loop burning tokens |
| SRE Agent — Investigation Token Usage Warning | `investigation_tokens_total` p99 > `token_warning_ratio` (default 80%) of `max_tokens_per_run` | email | Early warning before a run hits the hard `token_budget_exceeded` cap; reported after the run finishes, not during it | Check `run_id` in Cloud Logging for the trend; disabled automatically when `max_tokens_per_run=0` |
| SRE Agent — Routing Failures | `routing_failures` SUM > 0 in 5min | email | mcp_router-level safe-stop | Check cluster registry (`clusters.json`) for staleness |
| SRE Agent — Unknown/Ambiguous Cluster Safe-Stop | `unresolved_cluster` SUM > 0 in 5min | email | context_resolver couldn't ID a cluster | Check the incident payload quality / alert-metadata mapping |
| SRE Agent — GKE Remote MCP Failures | `tool_failures{mcp_source="gke_remote_mcp"}` SUM > 3 in 5min | email | Primary MCP source failing | Check GKE Remote MCP status (it's Google Preview/Pre-GA — check for a Google-side incident too) |
| SRE Agent — Custom K8s MCP Failures | `tool_failures{mcp_source="k8s_mcp"}` SUM > 3 in 5min | email | Fallback MCP failing | **Note: the custom MCP isn't deployed live today** — see [MCP Architecture](../architecture/mcp-architecture.md); this alert currently can't fire in practice |
| SRE Agent — Excessive Investigation Latency | `investigation_latency_seconds` p99 > 180s in 5min | email | Slow investigations | Check traces (see [Tracing](tracing.md)) for the slow node |
| SRE Agent — Repeated Tool Failures (any source) | `tool_failures` (unfiltered) SUM > 5 in 10min | email | Aggregate flapping signal, catches cross-source patterns the per-source alerts miss | Investigate broadly — could be an IAM/network issue affecting both sources |
| SRE Agent — Abnormal Loop/Token Termination | `loop_exit_reason` ∈ {timeout, token_budget_exceeded, max_iterations, oscillation_detected, stuck_detected} SUM > 2 in 10min | email | Investigations not reaching a clean stop | Check for a specific incident type or cluster driving this |
| SRE Agent — Evidence Storage Failures | `evidence_storage_failures` SUM > 0 in 5min | email | GCS write failing | Check evidence bucket IAM/existence |
| SRE Agent — Custom MCP Model Armor Fail-Open | `mcp_model_armor_fail_open` SUM > 0 in 5min | email | The custom MCP's application-level response guard (`mcp/response_guard.py`) fell back to fail-open because the Model Armor API call itself errored on a tool response check (not a real detection) | Any occurrence means tool responses went out unsanitized for that window — investigate the Model Armor API error immediately, don't wait for a later audit |

## Alerts that SHOULD exist but are currently missing

Stated explicitly in the Terraform's own comments — not silently absent, but not built:

| Missing alert | Why it's not built |
|---|---|
| PagerDuty webhook failures | No PagerDuty integration code exists at all — there's no webhook to fail |
| Connect Gateway failures | No GKE Fleet/Connect Gateway Terraform exists — see [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md) |
| Agent Gateway failures | The gateway doesn't currently emit an application-observable signal into the agent's own logs — nothing to alert on without first building that instrumentation |

## Gaps (beyond the 3 above)

- No alert on confidence-band *trend* (rising escalate share) — only absolute event-count alerts exist.
- No alert on Memory Bank health (recall silently returning nothing has no signal).
- No dedicated missing-tool-rate metric or alert.
- The "Custom K8s MCP Failures" alert exists but effectively cannot fire, since the custom MCP isn't deployed — don't rely on it as a signal until that's fixed.
- The double-counting issue on several metrics (see [Observability](observability.md)) affects the *thresholds* here too — the "5 errors in 5 minutes" trigger may actually represent ~2-3 real erroring investigations, not 5.

---

**Related pages:** [Observability](observability.md) · [Troubleshooting Runbooks](../runbooks/)
