# Evidence Architecture

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/gcs_client.py`, `agent/nodes/evidence_extractor.py`, `iac/agent/buckets.tf`
> **Source of Truth:** `agent/gcs_client.py:27-153`
> **Owner:** SRE Agent platform team.

## What constitutes evidence

Anything a Kubernetes tool call returns, once it's been sanitized and (usually) compressed into structured facts. There are two forms that matter, and they're deliberately kept separate:

## Raw MCP result

The literal JSON/text a tool call returns. This is **redacted for secrets/PII** (see below) then written to Cloud Storage, and — importantly — **never enters `AgentState` directly**. This is a deliberate design rule stated in the code itself (`agent/state.py:74-78`): raw tool output only ever exists in GCS; only a compressed summary of it becomes part of the graph state or any LLM prompt.

## Structured / normalized evidence

The compressed output of `evidence_extractor`'s LLM call: a handful of "key facts" strings, a short summary, and a `raw_ref` (the GCS path where the full raw data lives, for later re-reading if needed). This is what actually populates `evidence_store` in `AgentState` and what gets shown to the model in later prompts.

## Evidence IDs

Every evidence item gets a sequential ID (`ev_001`, `ev_002`, ...) assigned when `evidence_extractor` writes it. These IDs are what the RCA-building step's citation mechanism checks claims against (see [Confidence Scoring](confidence.md#claims-hypotheses-and-contradictions)) — a claim in the final RCA that cites an evidence ID that doesn't exist is caught and flagged, not silently trusted.

## GCS evidence storage

`write_evidence()` (`agent/gcs_client.py:27-61`, verified) writes each evidence item to `gs://{EVIDENCE_BUCKET}/{run_id}/{evidence_id}.json`. **Retry logic**: up to 2 attempts, 1-second gap between them. If both fail, the function returns a sentinel string (`gcs_write_failed:{path}`) instead of raising — the investigation continues, but the evidence item is marked `gcs_write_failed=True`.

## Evidence references

The `raw_ref` field on each evidence-store entry is the `gs://` URI — this is what `rca_builder`'s "enriched digest" logic uses to re-read full raw data back in when the compressed summary alone wasn't enough (see below).

## How evidence is tied to the RCA

`rca_builder`'s final output includes an `evidence_chain` (the list of evidence IDs actually used) and its own citation-checking logic verifies every root-cause claim actually cites real, known evidence IDs before treating the claim as grounded — see [Confidence Scoring](confidence.md).

## How evidence supports auditability

Every evidence write, tool call, and failure carries the `run_id`, so a reviewer can reconstruct exactly what the agent looked at for any given investigation — see [Logs](../operations/logging.md#how-to-find-one-investigation-across-logs) for the exact query sequence.

## How stale evidence is handled

There is a `freshness` component in the deterministic completeness score (see [Confidence Scoring](confidence.md)) that checks whether evidence was collected within a bounded time window of investigation start — but this is currently a single investigation-level proxy, not a true per-evidence-item timestamp check (a documented, known limitation — there's no per-evidence timestamp field yet).

## How failed tool calls are recorded

A failed tool call still produces a synthetic "error evidence" record (rather than nothing) — no LLM call happens for this case, it's a direct code path in `evidence_extractor`. This means a run's evidence trail includes an honest record of what was *attempted and failed*, not just what succeeded.

## Evidence retention

Set via Terraform lifecycle rules on the storage buckets (`iac/agent/buckets.tf`):

| Bucket | Purpose | Retention | Notes |
|---|---|---|---|
| Evidence bucket | Raw sanitized tool output, per `run_id` | **90 days** | Versioned, `prevent_destroy = true` on the bucket resource |
| Eval bucket | Full RCA records, per `run_id` | **365 days** | Versioned, `prevent_destroy = true` |

Both use uniform bucket-level access and bucket-level (not project-level) IAM.

## Redaction — what gets stripped before storage or the model ever sees it

`redact()` (`agent/gcs_client.py:109-153`) — regex-based, applied at `evidence_extractor` before both the GCS write and any LLM context. Redacts: email addresses, IPv4 addresses, bearer tokens, and any JSON field whose key looks like `password`/`token`/`secret`/`key`/`credential`. Applied consistently across the pipeline — this is not a "sometimes" control.

## A real evidence object (sanitized/illustrative shape)

Exact shape from `ev_entry` in `agent/nodes/evidence_extractor.py:130-142` — note `source` holds
the **tool name** and `mcp_source` holds the **MCP source** (an earlier version of this doc had
these two fields swapped; corrected 2026-08-09). There is no `evidence_id` field inside the
entry — the ID only exists as the outer dict key (`evidence_store[ev_id]`).

```json
{
  "ev_002": {
    "ok": true,
    "source": "list_k8s_events",
    "mcp_source": "gke_remote_mcp",
    "cluster": "sre-test-cluster",
    "region": "us-central1",
    "resource_type": "pod",
    "resource_id": "test-incidents/payment-worker-7f9",
    "summary": "Pod payment-worker-7f9 OOMKilled at 14:32, container payment-worker",
    "key_facts": [
      "OOMKilled, exit code 137",
      "memory limit 512Mi exceeded",
      "restart count 4"
    ],
    "raw_ref": "gs://sreagent-t2-demo-evidence/run_20260808_143012_ab3d/ev_002.json",
    "gcs_write_failed": false
  }
}
```

---

**Related pages:** [Context and State](context-and-state.md) · [Confidence Scoring](confidence.md) · [RCA Generation](rca-generation.md)
