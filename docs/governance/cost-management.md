# Cost Management

> **Implementation Status:** Cost tracking per investigation — IMPLEMENTED. Aggregate/monthly cost tooling — PLANNED (manual process today).
> **Last Verified:** 2026-08-08 — `agent/gemini_client.py`, `iac/agent/monitoring.tf`
> **Owner:** SRE Agent platform team.

## Cost drivers, separated

| Driver | Tracked? | Where |
|---|---|---|
| Model input/output tokens | Yes, per investigation | `estimated_cost_usd`, computed from live pricing constants in `agent/gemini_client.py` (`GEMINI_PRICE_INPUT`/`GEMINI_PRICE_OUTPUT` env vars, defaulting to $0.15/$0.60 per 1M tokens — **verify current Gemini pricing directly against Google's published rates before trusting these defaults; they are not automatically kept in sync with Google's price list**) |
| Agent Engine compute | Not itemized in application metrics — standard Vertex AI billing applies, 2 warm min-instances running continuously (`min_instances=2`, `cpu=4`, `memory=8Gi` per `iac/agent/agent_engine.tf`) | GCP Billing console |
| Agent Gateway | Standard GCP networking/gateway billing — not itemized here | GCP Billing console |
| MCP infrastructure | GKE Remote MCP: no separate charge beyond standard GKE API usage. Custom MCP: not currently deployed, so $0 today | GCP Billing console |
| GCS (evidence + eval buckets) | Standard storage pricing, bounded by the 90/365-day lifecycle rules | GCP Billing console |
| Cloud Logging | Standard ingestion/retention pricing — note the confirmed double-emission issue (see [Observability](../operations/observability.md)) roughly doubles log *volume* for several fields, which has a real (if likely small) cost impact | GCP Billing console |
| Cloud Trace | Standard pricing, sampling rate `OTEL_TRACES_SAMPLER_ARG=1.0` (100% sampled — no cost-saving sampling reduction applied today) | GCP Billing console |
| Monitoring | Standard log-based-metric + alert-policy pricing, 10 metrics + 11 alerts | GCP Billing console |
| Networking | Cloud NAT for egress — standard pricing | GCP Billing console |
| Custom MCP service | $0 today (not deployed) — would add Cloud Run compute + any Load Balancer/NEG cost once built | n/a |
| Memory/evaluation services | Memory Bank: standard Vertex AI pricing for the companion reasoning engine. Eval: no incremental infra cost beyond the eval bucket | GCP Billing console |

## How to measure cost per investigation

`jsonPayload.estimated_cost_usd` on the `sre-agent-investigations` log entry, or the `sre_agent/investigation_cost_usd` metric (distribution, buckets $0.01-$1.00). **Caveat**: this is model-token cost only — it does not include the amortized share of Agent Engine's always-on compute, Cloud Logging/Trace ingestion, or storage.

## How to calculate monthly cost

No dedicated dashboard/report exists in this repo today — **STATUS: PLANNED**. The manual process: sum `investigation_cost_usd` over the month (model cost) + pull GCP Billing reports filtered to project `sreagent-t2-demo` for the rest (compute, storage, logging, networking).

## How to identify abnormal token growth

`SRE Agent — Investigation Cost Spike` alert (p99 > $0.10 in a 60s window) plus manual review of `node_token_usage` log events for the specific `run_id` — see [Investigation-Level Failures runbook](../runbooks/investigation-failure.md#22-investigations-suddenly-using-excessive-tokens).

## How loops affect cost

Directly — each of the up-to-5 loop iterations can trigger up to ~4 LLM calls (`task_planner`, `mcp_router` Phase 2, `task_evaluator`, plus `rca_builder` once at the end). A `stuck_detected`/`oscillation_detected` exit still burns tokens for every iteration before it's caught — this is bounded by `max_steps=5` and `MAX_TOKENS_PER_RUN=100,000`, but neither is free.

## How evidence compression affects cost

Directly reduces cost — compressed "key facts" (not raw tool output) are what feed later prompts, keeping per-call token counts bounded regardless of how verbose the underlying tool output was. See [Context and State](../architecture/context-and-state.md#how-is-context-size-controlled).

## How memory affects cost

Small, bounded — up to 3 recalled memories are formatted into a short context string, not a large addition to any prompt.

## How new MCP sources affect cost

No direct token-cost impact from adding a source itself (tool descriptions shown to the model are short) — the cost impact comes from any additional loop iterations a richer tool set might encourage, not the source registration itself.

## How concurrency affects cost

Each investigation's LLM cost is independent — concurrency multiplies total spend linearly with investigation volume, it doesn't create a discount or penalty at the per-investigation level. Agent Engine's `min_instances=2` is a fixed compute cost regardless of investigation volume; `max_instances` being unset (see [Agent Engine](../architecture/agent-engine.md)) means there's no documented ceiling on how far concurrent load could scale compute cost — worth confirming the platform default if you need a cost ceiling.

## Optimization recommendations (based on the actual implementation, not generic advice)

- **The double-log-emission issue** (see [Observability](../operations/observability.md)) is a real, fixable, low-effort cost item — scoping the affected metrics'/logs' `logName` would roughly halve ingestion volume for those specific fields.
- **`OTEL_TRACES_SAMPLER_ARG=1.0`** (100% trace sampling) is a deliberate choice for full observability during this early-operations phase — revisit once investigation volume grows, since Cloud Trace pricing scales with span count.
- **`min_instances=2`** is a fixed cost regardless of usage — if investigation volume is low and cold-start latency is acceptable, this is a lever worth reconsidering; if latency matters, it's already the right tradeoff.

---

**Related pages:** [Observability](../operations/observability.md) · [Scaling](scaling.md) · [Capacity and Quotas](capacity.md)
