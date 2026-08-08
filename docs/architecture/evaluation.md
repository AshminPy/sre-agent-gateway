# Evaluation and AI Quality

> **Implementation Status:** IMPLEMENTED (deterministic golden-case evaluation); Vertex AI EvalTask integration — PARTIALLY IMPLEMENTED (present, optional, off by default); LLM-as-judge / rubric grading — DOES NOT EXIST
> **Last Verified:** 2026-08-08 — `agent/eval/golden_cases.py`, `agent/eval/run_eval.py`
> **Source of Truth:** `agent/eval/run_eval.py:73-130` (scoring logic)
> **Owner:** SRE Agent platform team.

## How we know the agent is correct — the honest, current picture

This is **not** an AI-judged evaluation system today. Every correctness check that exists is deterministic string/set matching against hand-written expected outcomes. There is no LLM-as-judge / rubric-graded evaluation anywhere in this codebase — confirmed by direct inspection, not assumed absent. If a stakeholder asks "does a model use another model to grade this one," the answer is no, not currently.

## The golden dataset

**14 curated golden cases** (`agent/eval/golden_cases.py`, `GOLDEN_CASES` list), each with:
- `id`, `payload` (query text, severity, resource hints)
- `expected_trajectory` — the tools the agent should call
- `expected_keywords` — words that should appear in the proposed root cause
- `expected_confidence_min` — a floor on confidence for cases that should be solvable
- Optionally, `expected_outcome` (must match one of the `InvestigationOutcome` values) and `max_confidence` (a **ceiling** — used for cases where a *high* confidence would itself be the failure, e.g. the agent inventing certainty it shouldn't have)

Case IDs cover: `crashloop-001`, `oomkilled-001`, `imagepull-001`, `configmap-001`, `init-001`, `selector-001`, `cascading-001`, `pending-001`, `onprem-001`, `insufficient-evidence-001`, `conflicting-evidence-001`, `ambiguous-routing-001`, `mcp-gateway-failure-001`, `secret-001`. Note the intentional **negative** cases: `insufficient-evidence-001` (a pod that's already been deleted — the agent must NOT invent a cause) and `conflicting-evidence-001` (evidence sources disagree — the agent must flag the conflict, not pick a side).

## Deterministic checks — `score_case()`

Purely code, no LLM (`agent/eval/run_eval.py:73-130`):

- `trajectory_precision` / `trajectory_recall` — did the agent call the expected tools?
- `trajectory_in_order_match` — same tools, in the expected relative order?
- `keyword_accuracy` — do the expected keywords appear (case-insensitive) in the proposed root cause?
- `conf_ok` — is confidence above the expected floor?
- `outcome_ok` — if `expected_outcome` is set, does the actual `outcome` match?
- `max_conf_ok` — if `max_confidence` is set, did the agent stay *below* it (i.e., correctly not overclaim)?

**Pass criteria**: `recall ≥ 0.5 AND keyword_accuracy ≥ 0.5 AND conf_ok AND outcome_ok AND max_conf_ok`.

## How a run is invoked

Two modes:
```bash
python -m agent.eval.run_eval --mode local --cases all
python -m agent.eval.run_eval --mode remote --engine-id <ENGINE_ID>
```
`local` invokes the LangGraph graph directly in-process (fast, no GCP round-trip beyond real Kubernetes access). `remote` calls the actually-deployed Vertex AI Agent Engine — this is the mode that tests the real, live, deployed configuration end-to-end.

## Tool trajectory validation and evidence-grounding validation

Trajectory validation is the `expected_trajectory` matching described above. Evidence-grounding validation happens at the RCA-building layer itself, not the eval layer — see [Confidence Scoring](confidence.md#claims-hypotheses-and-contradictions) for the phantom-citation and keyword-overlap checks that run on every real investigation, not just eval cases.

## Judge-model evaluation — what exists, precisely

`vertexai.preview.evaluation.EvalTask` **is** referenced (`run_eval.py`, imported inside a `run_vertex_eval()` function), gated behind an explicit `--vertex-eval` flag, only usable in `--mode remote`, and requiring an optional pip extra not installed by default. **But the metrics it submits are `trajectory_precision`, `trajectory_recall`, `trajectory_in_order_match`, `trajectory_any_order_match`** — the same structural/deterministic trajectory-matching metrics as the local scoring, not a generative judge-model rubric. The file's own docstring labels this integration **"Public Preview."**

**Conclusion, stated for governance clarity**: there is no semantic-judgment / LLM-as-judge evaluation in this system today. All correctness scoring, local or remote, is deterministic matching against hand-authored expectations.

## Replay testing / shadow-mode comparison against human SRE investigations

**STATUS: PLANNED, not implemented.** No shadow-mode comparison harness or replay-against-real-past-incidents mechanism exists in this codebase today.

## Synthetic variations, edge cases, rejected/incorrect investigations feeding back into tests

Golden cases include deliberately adversarial/edge scenarios (see the negative cases above), but there is currently **no automated pipeline** that takes a real production RCA a human SRE marked as `wrong`/`partial` (via the existing-but-currently-unused `sre_feedback` field) and turns it into a new golden case. **STATUS: PLANNED** — the `sre_feedback`/`validation_status` fields exist on every RCA output specifically so this loop *can* be built, but the loop itself isn't built yet.

## What stops a model or prompt change from silently making the agent worse?

Today: running the golden-case suite (`--mode local` at minimum, `--mode remote` against a real deployed candidate for full confidence) and manually reviewing the pass/fail delta. There is **no automated CI gate that blocks a merge/deploy on an eval regression** — confirmed: `.github/workflows/terraform-apply.yml`'s only automated correctness check is the live `smoke_test.sh` (one scenario, `imagepull`, asserting a well-formed RCA came back — not a pass-rate threshold across the golden set). **STATUS: PARTIALLY IMPLEMENTED** — the eval suite exists and can be run, but it is not wired into the deploy pipeline as an automated quality gate.

## What quality threshold blocks a deployment?

None, formally, beyond the single-scenario smoke test passing. See [CI/CD](../operations/deployment.md#cicd) for the exact pipeline steps.

---

**Related pages:** [Confidence Scoring](confidence.md) · [CI/CD](../operations/deployment.md) · [Updating the Agent](../operations/deployment.md)
