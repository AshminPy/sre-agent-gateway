# ADR-007: Two separate confidence dimensions, not one blended score

Status: Accepted (scoring code implemented and tested; policy weights explicitly uncalibrated — see [ADR-008](ADR-008-confidence-not-accuracy.md))

## Context

"How confident is the agent?" is actually two different questions that get
conflated if collapsed into one number: *did the investigation look in
enough places* (an evidence-coverage question, answerable mid-investigation,
before any conclusion exists), versus *does the evidence actually support the
specific root cause proposed* (a claim-grounding question, only answerable
once a root cause exists). A single blended score can't distinguish "we
looked everywhere and found nothing conclusive" from "we found one thing and
didn't look further" — both could land at the same number for very different,
operationally important reasons. The redesigned scoring system replaced an
older, single-confidence-score design specifically because of this
ambiguity.

## Decision

Compute two genuinely separate scores, each its own pure function, called
from two different graph nodes at two different points in the investigation:

- **Investigation Completeness** — "did we collect what this incident type
  needs?" Computed every loop iteration by `task_evaluator`, 100%
  deterministically, never consulting the LLM for the number
  (`agent/confidence/scorer.py:32-137`). Weighted components: routing
  confirmed (0.15), identity confirmed (0.10), required-evidence-domain
  coverage (0.40), freshness (0.10), tool success rate (0.15), iteration
  budget (0.10).
- **Root-Cause Confidence** — "does the evidence actually support the
  proposed cause?" Computed once, at the end, by `rca_builder`, given the
  LLM's proposed claims (`agent/confidence/scorer.py:140-285`). Weighted
  components: direct support (0.30), independent corroboration (0.25),
  resource-identity match (0.20), time correlation (0.15, currently a fixed
  placeholder — see [ADR-008](ADR-008-confidence-not-accuracy.md)), claim
  grounding (0.10) — with hard caps applied *after* the weighted score
  (missing critical evidence, an unresolved contradiction, or an unresolved
  competing hypothesis each cap the score, and cannot be bought back by a
  high average elsewhere).

Neither score is blended into the other. `derive_outcome()`
(`agent/confidence/scorer.py:288-316`) consumes both independently, in a
fixed priority order, to produce one of 6 `InvestigationOutcome` values.

## Alternatives Considered

- **One blended confidence score** (the original, superseded design) —
  rejected/replaced because it could not distinguish "thorough investigation,
  genuinely inconclusive evidence" from "thin investigation, one strong-
  looking finding" — two situations that call for different human responses,
  collapsed into indistinguishable numbers.
- **Let the LLM self-report a single confidence percentage** — rejected for
  both scores, not just one: LLMs are known to be overconfident about their
  own correctness, and a self-reported number has nothing to check it
  against. Both axes are instead computed by application code from
  code-checkable facts (does this claim cite real evidence? does the evidence
  actually mention the words in the claim? are there unresolved
  contradictions?) — see [Confidence Scoring](architecture/confidence.md#why-this-exists--the-core-idea).

## Reason

Keeping the axes separate makes each one individually diagnosable: a low
Investigation Completeness score with a high Root-Cause Confidence tells a
reviewer "the agent found strong evidence for what it found, but may have
stopped looking too early" — an actionable, specific signal a single blended
number could never produce. `derive_outcome()`'s priority order (any
contradiction ≥0.5 severity overrides everything;
completeness below threshold forces `INSUFFICIENT_EVIDENCE` regardless of how
confident the root cause looks) further protects against a
high-confidence-but-thin-investigation result masquerading as a solid one.

## Tradeoffs

- Two scores mean two things to explain to a stakeholder instead of one — a
  real usability cost, mitigated by the RCA output surfacing both with their
  full component breakdowns so a reviewer can see *why* each number is what
  it is, not just the number itself (see [RCA
  Generation](architecture/rca-generation.md#investigation-completeness)).
- A legacy 3-value `confidence_band` field (`auto`/`review`/`escalate`) is
  still derived and populated for backward compatibility with existing
  log-based metrics and alert filters — meaning some downstream consumers
  still only ever see a collapsed view, and the code explicitly warns against
  changing the 3-value mapping without also updating `monitoring.tf`'s alert
  filters.
- Both axes are genuinely tested (7+ passing tests for completeness, 9+ for
  root-cause confidence — see [Implemented vs Planned
  Matrix](management/implemented-vs-planned-matrix.md)), but the *weights and
  thresholds* behind both are explicitly uncalibrated — see
  [ADR-008](ADR-008-confidence-not-accuracy.md) for why that is a distinct
  concern from whether the two-axis split itself is correct.

## Related ADRs

- [ADR-006: Evidence before RCA](ADR-006-evidence-before-rca.md)
- [ADR-008: Confidence is explicitly not accuracy](ADR-008-confidence-not-accuracy.md)
