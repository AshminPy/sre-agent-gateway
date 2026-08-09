# ADR-006: Evidence collection happens before any root-cause claim is made

Status: Accepted

## Context

An LLM asked "what's wrong with this incident?" can produce a plausible-
sounding root cause with no supporting data behind it — that's a known LLM
failure mode, not a hypothetical one. For an SRE tool whose output can drive
real triage decisions, a root-cause statement with nothing backing it is
worse than no statement at all, because it looks authoritative. The graph
needed a structural guarantee that a root-cause claim can only be produced
*after* real tool calls have run and their results have been captured as
evidence — not just a prompt instruction telling the model to "check first."

## Decision

Structure the graph so evidence collection is a separate, earlier phase from
RCA generation, with the ordering enforced by graph edges, not model
discretion:

- The investigation loop (`task_planner` → `mcp_router` → `tool_executor` →
  `evidence_extractor` → `task_evaluator` → `loop_controller`) runs first,
  and `evidence_extractor` (`agent/nodes/evidence_extractor.py:25-169`) is
  the only node that writes to `evidence_store`/`evidence_ids`.
- `rca_builder` (`agent/nodes/rca_builder.py:309-500`) is the graph's single
  terminal node before `END` — it always runs last, whether the investigation
  completed normally, hit the loop's step cap, or safe-stopped early.
- If zero evidence was collected by the time `rca_builder` runs, the node
  overrides whatever the LLM proposes with an explicit "no evidence was
  extracted" root cause, and forces `claims`/`hypotheses` empty — the model's
  output is not trusted to self-censor in this case, the code does it.
- Every claim in the final root cause that cites a specific evidence ID is
  checked deterministically against the real, known evidence set
  (`agent/confidence/claim_builder.py`'s `_ground_claim()`): a citation to a
  nonexistent ID is caught (`grounding_status="phantom_evidence"`,
  `support_strength` forced to `0.0`), not silently accepted.

## Alternatives Considered

- **Let the model decide when it has "enough" evidence to conclude, with no
  structural evidence-first ordering** — rejected because it reintroduces the
  exact failure mode this ADR exists to prevent: an LLM under time or context
  pressure can talk itself into concluding early, and there would be no
  code-level check catching that until much later, if at all.
- **Score/validate the RCA only after the fact (post-hoc fact-checking of a
  freely-generated root cause)** — rejected in favor of the current design
  because post-hoc checking can only catch some fabrications (whatever the
  checker happens to test for), whereas making evidence-gathering
  structurally prior means there is no code path where an RCA can be produced
  from zero evidence except the explicit, honest "no evidence" override.

## Reason

This is the same underlying idea as [ADR-003](ADR-003-langgraph-orchestration.md)
applied specifically to the evidence→conclusion boundary: a workflow property
that must always hold ("no root-cause claim without evidence behind it") is
made a fact about the graph's structure and about `rca_builder`'s own code,
not a fact the model is trusted to uphold on its own. Citation-checking
(`_ground_claim()`) closes the remaining gap — even *with* evidence collected,
a claim citing evidence that doesn't actually exist or doesn't actually say
what the claim says is caught, not trusted at face value. See [RCA
Generation](architecture/rca-generation.md) and [Confidence
Scoring](architecture/confidence.md#claims-hypotheses-and-contradictions).

## Tradeoffs

- Every investigation, even ones that safe-stop immediately (missing query,
  unresolved cluster), still produces an RCA — the agent never fails silently
  — but that RCA can be a very short, explicit "why this couldn't proceed"
  statement rather than a real finding. Callers need to check `outcome`, not
  just presence of a response.
- Enforcing this ordering costs one extra required round trip in the common
  case: at least `min_steps = 2` loop iterations must complete before
  `task_evaluator` will even let the LLM be asked whether there's enough
  evidence (`agent/nodes/task_evaluator.py:44-122`) — a deliberate floor, not
  a bug, but it means the graph cannot short-circuit to a fast answer even
  when the first tool call happens to be conclusive.
- The citation-checking mechanism (`_ground_claim()`) is deterministic and
  code-only for *what evidence exists*, but contradiction detection has a
  second, genuinely self-reported half (`contradicting_evidence_ids`, the
  model's own claim about what conflicts with itself) — the code's own
  comments say plainly this half "is a self-report, not an independent
  adversarial check." Evidence-before-RCA guarantees a claim is grounded in
  real evidence; it does not by itself guarantee the model has reported every
  contradiction honestly.

## Related ADRs

- [ADR-003: LangGraph as the orchestration framework](ADR-003-langgraph-orchestration.md)
- [ADR-009: GCS as the durable evidence archive](ADR-009-gcs-durable-evidence-archive.md)
- [ADR-007: Two separate confidence dimensions](ADR-007-two-confidence-dimensions.md)
