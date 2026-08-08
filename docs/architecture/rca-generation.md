# RCA Generation

> **Implementation Status:** IMPLEMENTED
> **Last Verified:** 2026-08-08 — `agent/nodes/rca_builder.py`, `agent/main.py`
> **Source of Truth:** `agent/nodes/rca_builder.py:309-500`
> **Owner:** SRE Agent platform team.

## Where RCA generation happens

The `rca_builder` node, always the graph's final step before `END` — see [LangGraph Workflow](langgraph-workflow.md). It runs whether the investigation completed normally, hit a safety limit, or safe-stopped early — every investigation ends with an RCA, even a very short one that just explains why it couldn't proceed.

## RCA schema — what's in the final output

Returned from `SREAgent.query()` (`agent/main.py:576-596`):

| Field | What it is |
|---|---|
| `status` | Overall pass/fail signal for the run itself (not the incident) |
| `confidence` (marked deprecated) / `confidence_band` | Legacy 3-value field, still populated for backward compat |
| `outcome` | The current, real signal — one of 6 `InvestigationOutcome` values (see [Confidence Scoring](confidence.md)) |
| `investigation_completeness` / `root_cause_confidence` | The full two-axis score breakdowns |
| `summary` | The structured RCA object itself — incident summary, likely root cause, claims, hypotheses, contradictions, evidence chain, suggested remediation |
| `executive_summary` | A plain-English one-paragraph version for non-technical stakeholders |
| `rca_report` | A longer, structured human-readable text report (sections: executive summary, confidence breakdown, impact assessment, remediation, investigation metadata) |
| `working_theory` | The agent's running hypothesis during the investigation |
| `evidence_ids` | Every evidence item cited |
| `tool_calls` | Count of tools called |
| `errors` | Any recorded errors during the run |
| `requires_human_review` | Derived boolean — see [Confidence Scoring](confidence.md) |
| `observability` | The full structured log event, same shape as what's written to Cloud Logging |

## Incident summary

Built from the LLM's proposed `incident_summary` field, included in both the structured `summary` object and the human-readable report.

## Root cause

The LLM proposes `likely_root_cause`; if zero evidence was collected, this is overridden by code with an explicit "no evidence extracted" statement rather than letting the model produce a plausible-sounding but ungrounded guess.

## Evidence

The full `evidence_chain` (list of evidence IDs), cross-referenced against the claims that cite them — see [Evidence Architecture](evidence-architecture.md) and [Confidence Scoring](confidence.md#claims-hypotheses-and-contradictions).

## Confidence / status

`outcome`, `confidence_band`, and the full completeness/confidence score breakdowns — all deterministically computed, not self-reported by the model (see [Confidence Scoring](confidence.md)).

## Tool history

Every tool called during the investigation, success/failure, which MCP source it went through.

## Investigation completeness

The deterministic completeness score and its component breakdown (routing confirmed, evidence coverage, freshness, tool success, iteration budget) — surfaced directly in the RCA so a reviewer can see *why* the score is what it is, not just the number.

## Suggested remediation / next checks

The LLM's proposed remediation text — presented as a suggestion for human action, never auto-executed. See [System Overview](system-overview.md#what-it-intentionally-does-not-do).

## Human review

`requires_human_review` — derived from the confidence band, not self-reported. Every band except `auto` requires it, and `auto` itself requires the full CONFIRMED-outcome gate (see [Confidence Scoring](confidence.md)) to have passed. `sre_feedback` and `validation_status` fields exist on every RCA specifically so a human reviewer's verdict (`correct`/`partial`/`wrong`) can eventually be recorded — but as noted in [Evaluation](evaluation.md), the loop that feeds that feedback back into testing or memory review is not yet built.

## What claims must be evidence-backed

Any claim in `likely_root_cause` (and the broader `claims` list) that cites a specific evidence ID is checked against real, known evidence — a citation to a nonexistent ID is caught (`phantom_evidence`) and the claim's grounding strength is forced to 0. See [Confidence Scoring](confidence.md) for the exact mechanism.

## What happens when no reliable root cause can be determined

The `outcome` lands on `INSUFFICIENT_EVIDENCE`, `UNKNOWN`, or `CONFLICTING_EVIDENCE` (see [Confidence Scoring](confidence.md)), `confidence_band` is forced to `escalate`, and `requires_human_review` is true. The RCA still gets written and returned — the agent never simply fails silently or omits a response when it can't determine a cause; it says so explicitly.

---

**Related pages:** [Confidence Scoring](confidence.md) · [Evidence Architecture](evidence-architecture.md) · [Investigation Loop](investigation-loop.md)
