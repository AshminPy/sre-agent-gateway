# ADR-008: Confidence is explicitly NOT treated as accuracy

Status: Accepted design intent; calibration (the mechanism that would let confidence be read as accuracy) is NOT YET IMPLEMENTED — see Tradeoffs

## Context

It's tempting to read a "0.85 root-cause confidence" score as "this is right
85% of the time." That reading is only valid if the score has been
*calibrated* — checked against real outcomes to confirm that, say, claims
scored around 0.85 really do turn out correct about 85% of the time. Nothing
in this system's design does that check today, and conflating "the score is
high" with "the score is accurate" would be actively misleading for a system
whose output can shape real incident response.

## Decision

Keep confidence (what the deterministic scoring functions compute from
code-checkable facts — evidence coverage, claim grounding, contradictions)
and accuracy/calibration (whether those scores actually track real-world
correctness) as two explicitly distinct, separately-measured concerns, and
be honest in the code and docs about which one currently exists:

- `agent/confidence/policy.py`'s own docstring states plainly that its
  weights and thresholds are "reasoned initial defaults... not statistically
  calibrated against real incident outcomes," and that calibration "is
  required before these numbers should be trusted for a real launch
  decision." The version string makes this machine-checkable, not just
  documented in prose: `POLICY_VERSION = "1.0.0-uncalibrated"`
  (`agent/confidence/policy.py:20`).
- `sre_feedback`/`validation_status` fields exist on every RCA output
  (`agent/nodes/rca_builder.py:284-285,434,436`) specifically to eventually
  hold a human reviewer's verdict (`correct`/`partial`/`wrong`) against the
  agent's own score — the raw material calibration would need. No code reads
  these fields anywhere today (grep-confirmed) — they are populated but
  unconsumed.
- The evaluation harness (`agent/eval/run_eval.py`) measures a different
  thing entirely: deterministic trajectory/keyword matching against
  hand-written expected outcomes on 14 golden cases — a check on whether the
  agent *behaves* as expected on known scenarios, not a check on whether its
  *confidence scores* track real-world correctness. There is no LLM-as-judge
  or rubric-graded evaluation anywhere in the codebase — confirmed absent by
  direct code search, not assumed.

## Alternatives Considered

- **Present the confidence score as a probability of correctness today**
  (skip the "uncalibrated" framing) — rejected because it would overstate
  what the number means to anyone consuming it (an SRE, a dashboard, an
  alert threshold), and the code's own honesty about being uncalibrated would
  become a lie by omission the moment the label wasn't carried forward into
  how the number is presented.
- **Wait to ship any confidence scoring until calibration data exists** —
  rejected because the two-axis deterministic scoring (grounded in
  code-checkable facts, not model self-report) is independently useful for
  gating behavior (what routes to `auto` vs `escalate`, what requires human
  review) even before it's been proven statistically accurate — see
  [ADR-007](ADR-007-two-confidence-dimensions.md). The uncalibrated score
  still enforces real structural guarantees (grounding, contradiction
  detection) that don't depend on calibration to be meaningful.

## Reason

Confidence, as computed today, answers "does this conclusion satisfy the
structural checks we know how to run" (real evidence cited, no unresolved
contradiction, corroborated by multiple independent domains). That is a
genuinely useful, code-enforced property — but it is a different claim from
"this conclusion is statistically likely to be correct," which requires
comparing scores against ground-truth outcomes over time. Keeping the two
concepts named and measured separately (`policy.py`'s uncalibrated label,
`sre_feedback` sitting unused, the eval harness measuring behavior not
calibration) means nobody can accidentally cite a confidence number as an
accuracy guarantee without that being visibly, provably false against the
code.

## Tradeoffs

- The honest byproduct of this design intent: **no calibration measurement
  exists anywhere in the codebase today** — confirmed absent by direct code
  search, not assumed. `requires_human_review` and the `auto`/`review`/
  `escalate` routing are real, tested gates, but they gate on structural
  checks, not on any proven accuracy rate.
- The human-approval loop that would produce calibration data
  (`sre_feedback`/`validation_status` being read back and compared against
  actual outcomes) is designed for but not built — see
  [ADR-010](ADR-010-human-approval-before-trusted-memory.md), which covers
  the closely related but distinct memory-write-trust gap this same missing
  loop also affects.
- One component of Root-Cause Confidence — `time_correlation` (weight 0.15)
  — is currently a fixed placeholder (1.0 if any supporting evidence exists,
  0.0 otherwise), because no per-evidence timestamp exists yet. This is a
  real gap in the score's own inputs, separate from calibration, but worth
  knowing when reasoning about how trustworthy any individual score component
  is today.
- Until calibration exists, every threshold and weight in `policy.py` should
  be treated as "a considered starting point," per the code's own docstring —
  not a proven-correct number, for any launch decision that depends on it.

## Related ADRs

- [ADR-007: Two separate confidence dimensions](ADR-007-two-confidence-dimensions.md)
- [ADR-010: Human approval before a memory write becomes trusted](ADR-010-human-approval-before-trusted-memory.md)
