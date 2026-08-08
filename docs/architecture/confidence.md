# Confidence Scoring

> **Implementation Status:** IMPLEMENTED (deterministic scoring code) — but the scoring **policy** is explicitly self-flagged as uncalibrated: `POLICY_VERSION = "1.0.0-uncalibrated"` (`agent/confidence/policy.py:20`)
> **Last Verified:** 2026-08-08 — `agent/confidence/*.py`
> **Source of Truth:** `agent/confidence/scorer.py`, `agent/confidence/policy.py`
> **Owner:** SRE Agent platform team.

This is the redesigned, current confidence system — a deterministic two-axis model, not the older single confidence-score approach. If you see documentation elsewhere describing a single "confidence score" with no further breakdown, that's describing the *legacy, superseded* design — mentioned here only for history.

## Why this exists — the core idea

**The LLM proposes; code decides the score.** The model is asked to identify claims, hypotheses, and a likely root cause — but the actual numeric confidence and the pass/fail band are computed by application code from those proposals, not self-reported by the model. This matters because LLMs are known to be overconfident about their own correctness; grounding the score in code-checkable facts (does this claim cite real evidence? does the evidence actually mention the words in the claim? are there unresolved contradictions?) is what makes the score trustworthy enough to gate anything on.

## Two axes, computed separately

### Investigation Completeness — "did we collect what this incident type needs?"

Computed every loop iteration by `task_evaluator`, 100% deterministically (`agent/confidence/scorer.py:32-137`) — the LLM is never consulted for this number.

| Component | Weight | What it measures |
|---|---|---|
| `routing_confirmed` | 0.15 | Was the cluster explicitly identified (not defaulted)? |
| `identity_confirmed` | 0.10 | Did MCP source selection actually happen and get used? |
| `required_evidence_coverage` | 0.40 | Fraction of required evidence *domains* (not raw count) collected for this incident type |
| `freshness` | 0.10 | Was evidence collected within a bounded time window? (currently an investigation-level proxy, not per-item — a known limitation) |
| `tool_success` | 0.15 | Fraction of tool calls that succeeded |
| `iteration_budget` | 0.10 | Did the loop hit `max_steps` before required domains were covered? |

Bands: `≥0.85` complete, `≥0.60` complete_with_gaps, else incomplete.

### Root-Cause Confidence — "does the evidence actually support the proposed cause?"

Computed once, at the end, by `rca_builder` (`agent/confidence/scorer.py:140-285`), given the LLM's proposed claims.

| Component | Weight | What it measures |
|---|---|---|
| `direct_support` | 0.30 | Fraction of root claims that are observed fact vs. inference/hypothesis |
| `independent_corroboration` | 0.25 | Domain-weighted count of distinct evidence *domains* behind the claim (related domains count 0.5 each, so two related domains ≠ two independent sources) |
| `resource_identity_match` | 0.20 | Does supporting evidence match the resolved cluster? |
| `time_correlation` | 0.15 | **Currently a fixed placeholder** — 1.0 if any supporting evidence exists, 0.0 otherwise. No per-evidence timestamp exists yet, so this isn't a real time-correlation check. **STATUS: PARTIALLY IMPLEMENTED.** |
| `claim_grounding` | 0.10 | Mean grounding strength across root claims (see "Claims" below) |

Penalties subtracted after the weighted base: 0.15 per contradiction (capped 0.60), 0.15 flat for any active unresolved competing hypothesis with its own supporting evidence, 0.10 per missing required evidence domain (capped 0.40).

**Hard caps** applied after the weighted score — these cannot be bought back by a high average:
- Missing critical evidence → capped at 0.65
- Any unresolved contradiction → capped at 0.65
- Any unresolved competing hypothesis → capped at 0.75

Bands: `≥0.85` high_confidence, `≥0.65` review_required, `≥0.35` partial_evidence, else insufficient_evidence.

## How the two axes combine into an outcome

`derive_outcome()` (`agent/confidence/scorer.py:288-316`), in this exact priority order:

1. Any contradiction with severity ≥0.5 → `CONFLICTING_EVIDENCE` (overrides everything else)
2. Completeness < 0.35, or confidence = 0.0 → `INSUFFICIENT_EVIDENCE`
3. Completeness < 0.60 → `INSUFFICIENT_EVIDENCE`
4. Confidence ≥0.85 AND no active alternative hypothesis AND no contradictions → `CONFIRMED`
5. Confidence ≥0.65 → `PROBABLE`
6. Confidence ≥0.35 → `POSSIBLE`
7. else → `UNKNOWN`

All 6 `InvestigationOutcome` values: `CONFIRMED`, `PROBABLE`, `POSSIBLE`, `INSUFFICIENT_EVIDENCE`, `UNKNOWN`, `CONFLICTING_EVIDENCE`.

## The legacy `confidence_band` field — still present, now derived

For backward compatibility with existing log-based metrics and alerts (which filter on this exact 3-value field), `outcome` is mapped back down:

| `outcome` | `confidence_band` |
|---|---|
| `CONFIRMED` | `auto` |
| `PROBABLE` | `review` |
| anything else | `escalate` |

**Do not change these 3 mapped values without also updating `monitoring.tf`'s alert filters** — this is called out explicitly in the code itself.

## Is the policy calibrated?

**No — explicitly not.** `agent/confidence/policy.py:20`: `POLICY_VERSION = "1.0.0-uncalibrated"`. The docstring states plainly: these are "reasoned initial defaults... not statistically calibrated against real incident outcomes," and calibration against the golden dataset plus real SRE-validated RCAs "is required before these numbers should be trusted for a real launch decision." Treat every threshold and weight above as a considered starting point, not a proven-correct number.

## Claims, hypotheses, and contradictions — real grounding, not pure self-report

This is the mechanism that keeps the LLM honest. `agent/confidence/claim_builder.py` turns the model's free-text proposal into structured, code-checked objects:

- **Claim** grounding (`_ground_claim()`): every claim's `supporting_evidence_ids` are checked against the real, known evidence ID set. Cite an evidence ID that doesn't exist → `grounding_status="phantom_evidence"`, `support_strength=0.0`. Cite real evidence but with no keyword overlap between the claim text and that evidence's key facts → `grounding_status="no_overlap"`, `support_strength=0.1`. Otherwise → `"grounded"`, `support_strength=1.0`. **This is deterministic, code-only checking — not trusting the model's self-report.**
- **Contradiction detection** has two sources: (1) a deterministic structural check — supporting evidence tagged with a different cluster than the resolved one → automatic `wrong_resource` contradiction, code-only; (2) **the model's own self-reported** `contradicting_evidence_ids` per claim — this one genuinely is a self-report, and the code says so explicitly in its own comments ("the model proposes which evidence conflicts with its own claim... this is a self-report, not an independent adversarial check").
- **Not implemented**: `wrong_time_window` contradiction detection — there's no per-evidence timestamp yet, so this kind of contradiction is never produced. **STATUS: PLANNED.**

## What the user/reviewer sees

Every RCA includes: the `outcome`, `confidence_band`, the completeness and root-cause-confidence scores with their component breakdowns, any contradictions or unresolved alternative hypotheses, and `requires_human_review` (derived, not self-reported — see below).

## What happens when confidence is low

`requires_human_review` is set whenever the band isn't `auto` — and even `auto` only happens when `derive_outcome()` returns `CONFIRMED`, which itself requires zero unresolved contradictions and zero active competing hypotheses. So "auto" genuinely means "strong, corroborated, uncontested evidence," not just "a number above a threshold."

## What happens when tools/evidence are missing entirely

If zero evidence was collected, `rca_builder` doesn't even let the LLM's proposed root cause stand — it's overridden with an explicit "no evidence was extracted" statement, and claims/hypotheses are forced empty. The investigation is never allowed to present a fabricated-sounding root cause with no evidence behind it.

---

**Related pages:** [RCA Generation](rca-generation.md) · [Evaluation](evaluation.md) · [Investigation Loop](investigation-loop.md)
