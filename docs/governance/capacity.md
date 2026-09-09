# Capacity and Quotas

> **Implementation Status:** Reference page. Specific quota numbers are **NOT** stated here — see the "why" below.
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

## Why this page doesn't list specific numbers

GCP quota limits change over time and vary by project/organization negotiated limits. Stating a specific number here (e.g., "Vertex AI Gemini requests per minute: X") risks being wrong the moment Google changes it, and this knowledge base's whole premise is "verify against the real system, don't guess." Instead, this page tells you **exactly where to check**, live, whenever capacity planning is needed.

## Where operators check each quota

| Quota area | Where to check |
|---|---|
| Vertex AI / Gemini model quotas (requests/min, tokens/min) | GCP Console → IAM & Admin → Quotas, filtered to `aiplatform.googleapis.com`, project `sreagent-t2-demo`. Or: `gcloud alpha services quota list --service=aiplatform.googleapis.com --project=sreagent-t2-demo` |
| Agent Engine / Reasoning Engine quotas | Same Quotas page, filtered to the Agent Engine-specific quota metrics (check the current Vertex AI Agent Builder documentation for the exact metric names, since these are still evolving as the product matures) |
| Agent Gateway quotas | Same Quotas page, filtered to `networkservices.googleapis.com` / `networksecurity.googleapis.com` |
| General API quotas (Cloud Logging, Cloud Trace, Cloud Storage) | Same Quotas page, filtered to the relevant service |
| GKE / Connect Gateway quotas | Quotas page for `container.googleapis.com` / `gkehub.googleapis.com`, and separately, standard GKE cluster-level resource quotas (node count, pod count) on the target cluster itself |

## What could actually affect production, based on this system's real usage pattern

- **`min_instances=2` / `max_instances=10`** (Section 9, 2026-09-08 — `iac/agent/agent_engine.tf`'s `deployment_spec`): `max_instances` was previously unset in this repo's Terraform at all (confirmed live via `terraform plan`: the resource's actual live value was `0`, not the ~100 platform default this doc previously assumed — worth knowing if you're diagning a scaling question predating this fix). `10` is a **deliberate, explicit, but NOT load-tested** ceiling — chosen to bound cost/blast-radius for this Phase 1 rollout, not derived from a measured concurrent-throughput test (none has been run against this specific deployment as of this writing). When this ceiling is hit, Vertex AI Agent Engine's own platform layer is the thing returning a retryable saturation response to the caller — this repo's application code does not (and per this section's own guidance, should not) implement a second, custom in-process admission limiter on top of it ("a per-process limiter is not a global quota controller"). Revisit this number once a real concurrent load test has been run.
- **Gemini API rate limits** are the most likely thing to bite under real load — `agent/llm/gemini_adapter.py`'s `_call_model()` retries `429` **and now 500/503/504** (Section 9, 2026-09-08 — previously 5xx got zero retries at all) 3x with linear backoff, but sustained load beyond quota would still degrade investigation success rate. Check the live quota before committing to a specific investigations-per-minute SLA.
- **Concurrent-run accounting isolation** (Section 9, 2026-09-08): confirmed via a real multi-threaded test (`tests/test_gemini_adapter_concurrent_sessions.py`) that two investigations sharing the same warm Agent Engine instance no longer cross-contaminate each other's token/cost accounting — this was a real, demonstrated gap (plain instance attributes on the one process-wide cached LLM adapter) before this fix, not a hypothetical one.
- **Cloud Logging ingestion quotas** are unlikely to be a near-term concern at current volume, but the confirmed double-emission issue (see [Observability](../operations/observability.md)) roughly doubles log volume for several fields — worth fixing before scaling up investigation volume significantly.

## Recommendation

Before any capacity-planning exercise (e.g., "can we handle 1,000 investigations/day"), pull the live numbers from the Quotas console links above rather than relying on any number written in a document — including this one.

---

**Related pages:** [Scaling](scaling.md) · [Cost Management](cost-management.md) · [Agent Engine](../architecture/agent-engine.md)
