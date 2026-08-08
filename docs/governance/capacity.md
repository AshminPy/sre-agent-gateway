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

- **`min_instances=2`** is a fixed floor — this alone won't hit a compute quota under normal load, but combined with an unset `max_instances` (see [Agent Engine](../architecture/agent-engine.md)), a sudden large spike in concurrent investigations has no documented ceiling from this repo's Terraform — the platform default is what would actually apply, and that's worth confirming directly if you're planning for a specific peak volume.
- **Gemini API rate limits** are the most likely thing to bite under real load — `agent/gemini_client.py` already retries `429`s 3x with backoff, but sustained load beyond quota would still degrade investigation success rate. Check the live quota before committing to a specific investigations-per-minute SLA.
- **Cloud Logging ingestion quotas** are unlikely to be a near-term concern at current volume, but the confirmed double-emission issue (see [Observability](../operations/observability.md)) roughly doubles log volume for several fields — worth fixing before scaling up investigation volume significantly.

## Recommendation

Before any capacity-planning exercise (e.g., "can we handle 1,000 investigations/day"), pull the live numbers from the Quotas console links above rather than relying on any number written in a document — including this one.

---

**Related pages:** [Scaling](scaling.md) · [Cost Management](cost-management.md) · [Agent Engine](../architecture/agent-engine.md)
