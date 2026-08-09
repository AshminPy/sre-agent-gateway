# Memory

> **Implementation Status:** IMPLEMENTED (write gate + recall) — human-approval review workflow for memory is PLANNED, not built
> **Last Verified:** 2026-08-08 — `agent/main.py:620-980`
> **Source of Truth:** `agent/main.py:747-838` (`_mb_store`, `_mb_recall`)
> **Owner:** SRE Agent platform team.

## Three different things, don't conflate them

| | What it is | Durable? | Scope |
|---|---|---|---|
| **Investigation/session state** | `AgentState` — the LangGraph state for one investigation | No — exists only for the duration of one `graph.invoke()` call | Single investigation |
| **GCS durable archive** | Raw evidence + full RCA records, written every run | Yes (90/365-day retention — see [Evidence Architecture](evidence-architecture.md)) | Every investigation, regardless of confidence |
| **Long-term AI memory (Memory Bank)** | Compressed one-line summaries of *past high-confidence* investigations, recalled into future prompts | Yes, cross-run | Only `confidence_band == "auto"` investigations |

The GCS archive is complete and unconditional — every investigation's evidence and RCA are saved there, whatever the outcome. Memory Bank is the selective, recalled-into-future-prompts layer, and it's where the interesting design decisions are.

## How memory is written

`_mb_store()` (`agent/main.py:749-789`) writes a fact string (cluster, namespace, pod, incident type, root cause, confidence) to Vertex AI Memory Bank. Dedup key: `pod + incident_type` (stable Kubernetes identifiers — not semantic similarity), checked against existing memories in the same cluster/namespace scope before writing.

## The exact write-gate condition

```python
if confidence_band == "auto":
    cls._mb_store(...)
```
(`agent/main.py:957`) — **only** `auto`-band RCAs get written to persistent memory. Anything else (`review`, `escalate`) only goes into a per-container, non-durable, 20-entry-max fallback list.

Since `confidence_band` only reaches `auto` when `derive_outcome()` returns `CONFIRMED` (strong evidence, independently corroborated, zero unresolved contradictions, zero active competing hypotheses — see [Confidence Scoring](confidence.md)), this is a genuine multi-condition validation gate, not a bare numeric cutoff.

## Why memory cannot automatically be trusted / memory-poisoning risk

The code is explicit and self-critical about this history. A comment block dated 2026-08-04 documents that this gate was **added** because the *previous* behavior wrote to persistent memory for **any** non-failed/non-blocked result, regardless of confidence — despite the storage function's own docstring claiming poisoning prevention when it only deduped by `pod + incident_type`, not confidence. A low-confidence, `escalate`-band RCA could have been written and later recalled as if it were validated fact. The current gate is the fix, but the same comment states plainly what's still missing:

> "a genuine human-approval pipeline (using the existing but currently-unused `sre_feedback`/`validation_status` fields) is a further improvement, **not built here**."

**STATUS: PLANNED** — memory writes today are gated by an automated confidence check, not by any human sign-off. Memories are stored with a `status=pending_review` note in the docstring intent, but no code enforces or surfaces that review workflow to a human today.

## How memory is queried / how it influences planning

`_mb_recall()` (`agent/main.py:801-840`) retrieves up to 3 memories scoped to the current investigation's cluster+namespace, formats them as a short "Past incidents on this cluster/namespace (validate during investigation)" string, and injects it as `payload["memory_context"]`. This flows all the way into the `rca_builder` prompt (with a fallback string "No past investigations on record" if nothing was recalled). A structured `memory_bank_recall` log event is emitted per recalled memory, to make recurrence trackable.

**Important**: the recalled text explicitly says "validate during investigation" — it's presented as a hint to the model, not asserted as ground truth. But the model is still free to weight it however it reasons; there's no code-level mechanism forcing the model to independently re-verify a recalled memory before using it.

## What happens on a memory hit

The recalled summary becomes part of the `rca_builder` node's prompt context — it can influence the model's proposed root cause, but does not bypass the deterministic confidence scoring described in [Confidence Scoring](confidence.md). A recalled memory does not itself grant any evidence-grounding credit; claims still need to cite real evidence IDs from *this* investigation to be scored as grounded.

## Review lifecycle / deletion / retention / ownership

- **Review**: PLANNED, not built (see above) — currently no human-approval step exists between a memory being written and it being recalled into future investigations.
- **Deletion**: not automated in this codebase — an SRE reviewing/deleting a memory would need to do so via the Vertex AI Memory Bank console/API directly, per `agent/.env.example`'s own guidance.
- **Retention**: not explicitly configured in this repo's Terraform (Memory Bank retention is a Vertex AI platform-level setting) — **STATUS: UNKNOWN**, verify directly against the live Memory Bank resource if retention needs to be documented precisely.
- **Ownership**: SRE Agent platform team owns the write-gate logic; the Run/Ops team is the intended reviewer of memory content once that review workflow is built.

## Fallback behavior when Memory Bank is unavailable/unset

If `MEMORY_BANK_RESOURCE` isn't set, or the Memory Bank recall returns nothing, the system falls back to an in-process list (`_recall_memory`/`_save_memory`, max 20 entries, per container process, **not durable across restarts**). This is a graceful-degradation path, not the intended production mode.

---

**Related pages:** [Evidence Architecture](evidence-architecture.md) · [Confidence Scoring](confidence.md) · [AI Governance](../governance/ai-governance.md#can-the-ai-remember-incorrect-information)
