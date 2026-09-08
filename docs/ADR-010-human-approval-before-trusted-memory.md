# ADR-010: Human approval required before a memory write becomes "trusted"

Status: IMPLEMENTED 2026-09-08 (Section 8 of the new-assignment expansion work). `agent/main.py`'s `_mb_store()` now writes `status=pending_review` (plus `run_id`/`policy_version`) into every memory's fact string; `_mb_recall()` only returns `status=approved` memories to future investigations. The review operation is `scripts/review_memory.py` (list/approve/reject/revoke — a CLI, not a new UI, using the Memory Bank SDK's public `get()`/`delete()`/`create()`, since no public `update()` exists). See `PHASE1_EVIDENCE_LOG.md`'s Section 8 entry for verification evidence. The rest of this document (context/decision/alternatives/reasoning) describes the design that was actually built — kept as-is since it's still accurate, not just historical.

## Context

Vertex AI Memory Bank lets the agent recall summaries of past investigations
into future prompts, which can meaningfully speed up recognizing recurring
incidents — but it also creates a memory-poisoning risk: if a low-confidence
or wrong RCA gets written to durable memory, it can later be recalled and
treated as if it were validated fact, compounding the original mistake into
future investigations. The codebase's own history documents this concretely:
a comment dated 2026-08-04 in `agent/main.py` records that memory writes were
originally gated on "any non-failed/non-blocked result, regardless of
confidence," despite the storage function's own docstring claiming
poisoning prevention when it actually only deduped by `pod + incident_type`,
not confidence.

## Decision

Two things are true at once here, and both need to be stated plainly:

**What is built today** — an automated confidence gate, not human approval:
```python
if confidence_band == "auto":
    cls._mb_store(...)
```
(`agent/main.py:957`) — only `auto`-band RCAs are written to persistent
Memory Bank. `confidence_band` only reaches `auto` when `derive_outcome()`
returns `CONFIRMED` (strong evidence, independently corroborated, zero
unresolved contradictions, zero active competing hypotheses — see
[ADR-007](ADR-007-two-confidence-dimensions.md)), so this is a genuine
multi-condition validation gate, not a bare numeric cutoff. Anything else
only goes into a per-container, non-durable, 20-entry-max fallback list.

**What is decided but not built** — the intended next layer. The same
2026-08-04 comment states plainly: "a genuine human-approval pipeline (using
the existing but currently-unused `sre_feedback`/`validation_status` fields)
is a further improvement, **not built here**." Those fields already exist on
every RCA output (`agent/nodes/rca_builder.py:284-285,434,436`,
`validation_status` defaulting to `"pending"`, `sre_feedback` to `None`,
intended values `correct`/`partial`/`wrong`) — but no code anywhere reads
them (grep-confirmed). A memory write today happens purely on the automated
confidence gate passing; no human ever signs off before a memory becomes
recallable.

## Alternatives Considered

- **Ship only the automated confidence gate and call the memory-poisoning
  risk closed** — rejected as the final design, even though it's what's
  built today: the code's own comment explicitly flags that a human-approval
  layer is a "further improvement" still needed, and `derive_outcome()`
  being `CONFIRMED` is a structural-evidence check, not a human judgment
  call — a case can pass every structural check and still be substantively
  wrong in a way only a human reviewer with incident context would catch.
- **Require human approval on every memory write, with no automated
  pre-filter** — rejected (implicitly, by the current design) as the
  starting point, because it would put a human in the loop for every
  investigation regardless of confidence, defeating the point of an
  automated triage tool for the large majority of clearly-low-confidence
  cases where a human wouldn't need to look anyway. The intended design
  layers human review only on top of what already passed the automated gate.
- **Recall memories without ever writing anything durable (keep it
  per-process/in-memory only)** — rejected because it forgets everything on
  every restart, losing the actual value of recognizing recurring incidents
  across time that Memory Bank is meant to provide.

## Reason

The confidence gate alone answers "did this investigation's evidence satisfy
our structural checks" — a code-checkable, deterministic question. It cannot
answer "did a human with real incident context agree this is actually
correct," which is a fundamentally different kind of check and the reason the
`sre_feedback`/`validation_status` fields exist at all. Layering human
approval on top (rather than instead of) the automated gate keeps the
automated triage value while adding the one check the code itself admits it
cannot do alone. See [Memory](architecture/memory.md#why-memory-cannot-automatically-be-trusted--memory-poisoning-risk).

## Tradeoffs

- **This is the honest, current gap, not a hedge**: recalled memories today
  are exactly as trustworthy as the automated confidence gate that wrote
  them — no better. The recalled text is presented to the model with an
  explicit "validate during investigation" hint, but there's no code-level
  mechanism forcing the model to independently re-verify a recalled memory
  before using it; a wrong `auto`-band RCA (structurally clean but
  substantively wrong) could still be written, recalled, and shape a later
  investigation's prompt with no human ever having checked it.
- Building the human-approval loop requires more than just reading
  `sre_feedback` — it needs a surface for a human to actually submit that
  feedback in the first place (e.g. against a specific `run_id`), which does
  not exist today either.
- This same missing loop is also what blocks confidence-score calibration
  (see [ADR-008](ADR-008-confidence-not-accuracy.md)) — `sre_feedback`
  compared against the score that was assigned is the raw material
  calibration would need, so this gap has two consequences, not one.
- Deletion of a bad memory, once written, is not automated in this codebase
  — an SRE would need to remove it via the Vertex AI Memory Bank
  console/API directly.

## Related ADRs

- [ADR-008: Confidence is explicitly not accuracy](ADR-008-confidence-not-accuracy.md)
- [ADR-006: Evidence before RCA](ADR-006-evidence-before-rca.md)
