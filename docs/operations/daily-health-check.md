# Daily Health Check

> **Implementation Status:** Procedure based on IMPLEMENTED observability — see caveats inline.
> **Last Verified:** 2026-08-08
> **Owner:** Run/Ops team.

Run through this list at the start of a shift, or any time you want a quick read on system health. Every check below uses a real, currently-implemented metric or log — nothing here asks you to check something that doesn't exist.

## 1. Is Agent Engine healthy?

```bash
gcloud ai reasoning-engines describe <ENGINE_ID> \
  --project=sreagent-t2-demo --region=us-central1
```
Confirm it's in a ready state. There's no dedicated "health" metric beyond this — Agent Engine health is inferred from successful invocations (see #4) plus the resource's own state.

## 2. Are investigations succeeding?

Cloud Logging (Log Analytics), query:
```
resource.type="aiplatform.googleapis.com/ReasoningEngine"
jsonPayload.status="error"
```
Compare the count against `jsonPayload.run_id:*` (total invocations) over the same window. **Caveat**: both `status` and `run_id` are emitted on two separate log paths per run (see [Known accuracy issue: several metrics likely double-count](observability.md#known-accuracy-issue-several-metrics-likely-double-count) for why) — treat absolute counts as roughly 2x actual until that's fixed; the *ratio* of errors-to-invocations is still meaningful since both are inflated equally.

## 3. Are investigations taking longer than usual?

Check the `SRE Agent — Excessive Investigation Latency` alert state, or query `sre_agent/investigation_latency_seconds` directly in Cloud Monitoring. Alert fires if p99 exceeds 180s over a 5-minute window (hard timeout is 540s — see [Investigation Loop](../architecture/investigation-loop.md)).

## 4. Are tool failures increasing?

Check `SRE Agent — GKE Remote MCP Failures`, `SRE Agent — Custom K8s MCP Failures`, and `SRE Agent — Repeated Tool Failures` alert states. See [Alerting](alerting.md) for exact thresholds.

## 5. Is confidence degrading?

Query `sre_agent/confidence_band` grouped by label. A rising share of `escalate` relative to `auto` over time is the signal to watch — there's no dedicated alert for this trend today (see [Alerting](alerting.md#gaps) for what's not covered).

## 6. Is cluster routing working?

Check `SRE Agent — Routing Failures` and `SRE Agent — Unknown/Ambiguous Cluster Safe-Stop` alert states. Any nonzero count in a 5-minute window fires these.

## 7. Are evidence writes succeeding?

Check `SRE Agent — Evidence Storage Failures` — any nonzero count in 5 minutes fires this alert.

## 8. Is memory working?

**No dedicated metric or alert exists for Memory Bank health.** Spot-check: query Cloud Logging for `jsonPayload.event_type="memory_bank_recall"` entries — if these stop appearing entirely for clusters that previously had incidents, investigate manually (check `MEMORY_BANK_RESOURCE` env var is still set, check the Memory Bank resource itself in the console). **STATUS: this is a documented observability gap** — flag for [Alerting](alerting.md#gaps).

## 9. Are PagerDuty events reaching the agent?

**N/A today — no PagerDuty integration exists.** See [Reliability](../governance/reliability.md). Skip this check until that integration is built.

## 10. Is the MCP path actually working (not just Agent Engine)?

Run a real live smoke test:
```bash
python3 invoke_agent.py --scenario imagepull
```
This targets the live `sre-test-cluster` (GKE) path end-to-end and asserts a well-formed RCA comes back. This is the same check CI runs after every deploy (`scripts/smoke_test.sh`).

## Quick reference — Console paths

| What | Where |
|---|---|
| Reasoning Engine resource | Vertex AI → Agent Builder → Agent Engine, project `sreagent-t2-demo` |
| Logs | Cloud Logging, project `sreagent-t2-demo` |
| Metrics/Alerts | Cloud Monitoring, project `sreagent-t2-demo` |
| Traces | Cloud Trace, project `sreagent-t2-demo` |
| Evidence/eval buckets | Cloud Storage, project `sreagent-t2-demo` |

---

**Related pages:** [Observability](observability.md) · [Logging](logging.md) · [Alerting](alerting.md)
