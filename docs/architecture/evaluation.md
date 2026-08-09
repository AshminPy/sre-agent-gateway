# Evaluation and AI Quality

> **Implementation Status:** IMPLEMENTED (deterministic golden-case evaluation); Vertex AI EvalTask integration — PARTIALLY IMPLEMENTED (present, optional, off by default); LLM-as-judge / rubric grading — DOES NOT EXIST
> **Last Verified:** 2026-08-09 — `agent/eval/golden_cases.py`, `agent/eval/run_eval.py`
> **Source of Truth:** `agent/eval/run_eval.py:73-133` (scoring logic)
> **Owner:** SRE Agent platform team.

## 2026-08-09 — two real evaluation-harness bugs fixed, corrected baseline established

Two problems in the eval harness itself (not the agent) were found and fixed this day:

1. **Stale expected tool names.** `golden_cases.py`'s `expected_trajectory` fields for 12 of 14
   cases were written against an old tool-naming scheme that no longer exists — this made
   trajectory scoring read as a false 0%. Fixed by rewriting all 12 to the current real
   `GKE_REMOTE_TOOLS`/`CUSTOM_K8S_TOOLS` names (`agent/mcp_client.py`). Guarded going forward by
   `tests/test_golden_cases_tool_names.py`, which checks each case's tool names against the
   *correct* per-case source (not just "valid somewhere in the combined tool set" — that weaker
   check would not have caught this exact bug, since several old names happen to also be valid
   `CUSTOM_K8S_TOOLS` names).
2. **`recursion_limit` mismatch.** Local eval mode (`run_local()`) passed no `recursion_limit`
   config to `graph.invoke()` at all, silently falling back to LangGraph's built-in default of
   25, while the deployed agent uses 60 — this crashed 4/14 cases with "Recursion limit of 25
   reached," unrelated to real agent behavior. Fixed with a single shared constant,
   `GRAPH_RECURSION_LIMIT = 60` (`agent/graph.py:29`), imported by both `agent/main.py` and
   `agent/eval/run_eval.py:140,157`. Guarded by `tests/test_recursion_limit_consistency.py`.
3. A related silent gap closed in the same fix: `_latency_seconds` was already computed by
   `run_local()`/`run_remote()` but was being dropped before it reached the saved score JSON —
   now included as `latency_seconds` in every case's output.

The 14 scenarios were rerun clean after the fix. Result: **14/14 completed execution (0
recursion-limit crashes, was 10/14)**, **12/14 correct tools called (trajectory recall ≥ 0.5,
not measurable before the fix)**, **100% correct MCP source selection**, **0 tool execution
failures**. Full numbers, old-vs-new comparison, and 3 new findings the clean rerun surfaced
(a cross-contamination case-mixup, a silent zero-tool-call anomaly, and confirmation that 3
cases have no live fixture) are in
[`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](../baselines/tool-scaling-baseline-2026-08-09-corrected.md)
— that file is the current official tool-scaling baseline. The original run
(`tool-scaling-baseline-2026-08-09.md`) is kept, unmodified, as historical evidence of the two
bugs above.

**Reading the old "0% tool match" / new "3/14 pass" numbers correctly**: neither number means
what it looks like at first glance. The 0% in the first run was test-data drift, not a real
routing failure. The 3/14 in the corrected run reflects the pass/fail gate's strict AND across
five separate checks (recall, keyword accuracy, confidence, outcome, max-confidence) — the
metric that actually answers "is tool selection working" is trajectory recall ≥ 0.5, which is
12/14. See the corrected baseline doc's "Reading the pass/fail number correctly" section for
the full breakdown.

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

**Known current limitations** (surfaced by the 2026-08-09 corrected rerun, not fixed —
out of scope for that follow-up, listed here for honesty): 3 cases (`pending-001`,
`mcp-gateway-failure-001`, `conflicting-evidence-001`) target pods that have no live k8s
fixture anywhere in this repo, so they can only ever produce a "pod does not exist" result;
`selector-001` was found to sometimes pick the wrong pod to investigate when all 7
`test-incidents` fixtures are live in the same namespace simultaneously with no exact
resource-name hint given; `onprem-001` made zero tool calls with no error logged in the
corrected rerun (unexplained, ties into the still-open question of whether the custom MCP
path is actually reachable). Full detail in the corrected baseline doc above.

## Deterministic checks — `score_case()`

Purely code, no LLM (`agent/eval/run_eval.py:73-133`):

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
