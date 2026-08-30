# Report — Agent Integrity Review, 16 Gaps Fixed, Merged, Deployed, Live-Tested

**Date:** 2026-08-27
**Repo:** `AshminPy/sre-agent-gateway`
**PR:** [#204](https://github.com/AshminPy/sre-agent-gateway/pull/204) — merged `2026-08-27T16:38:50Z`
**Deploy:** [run 33094304739](https://github.com/AshminPy/sre-agent-gateway/actions/runs/33094304739) — success
**Status:** **PASS** — merged, deployed, live-tested against the real agent, evidence and Model Armor logs checked directly.

---

## 1. Why this review happened

On 2026-08-26 the agent fabricated a root cause. A Model Armor block was silently treated as a successful tool call, the block notice became "evidence", and the LLM invented an image name and error string that appeared in no real data. That was fixed in PR #199. This review asked the follow-up question directly: **is that the only place this can happen?**

It was not. Four review passes over `agent/` and `agent/eval/` found **16 separate gaps**, all the same bug class — something that is not real data or not a real success gets treated as one, and that false signal reaches either the confidence score, the loop's exit decision, or the model's reasoning.

## 2. The 16 gaps, by pass

**Pass 1 — tool output becoming evidence**
1. MCP `isError: true` was never read anywhere in the codebase. A tool execution error's message sits in `content`, exactly where real data sits — `call_tool` returned `ok=True` and the error text became evidence.
2. `rca_builder`'s no-evidence safety gate counted evidence **slots**, not usable evidence. A failed call still gets a slot, so an all-failed run did not trip the gate.
3. `llm_json()` returned a bare `{}` when the model's response could not be parsed — indistinguishable from a legitimately empty result.
4. `_ground_claim` gave **full credit** to a claim citing evidence with no content, contradicting its own comment.
5. JSON-RPC protocol errors were discarded and replaced with a generic "Empty response", losing the server's real diagnostic message.

**Pass 2 — the rest of the workflow**
6. Memory Bank recall returned `""` for three different situations (not configured / genuinely nothing found / recall threw) — the report claimed "No prior similar incidents found" even when recall never ran, in **two** separate prompt sites (`rca_builder` and `task_planner`).
7. `mcp_router` collapsed three outcomes — genuine completion, an unparseable response, a response missing its `tool` key — into one benign `mcp_router → done` log line, all producing the same "normal completion" exit reason.
8. `task_planner` silently invented a generic plan on a parse failure and logged it as though planning had worked.
9. `evidence_extractor`'s fallback entry was written `ok=True`. Measured on a real ImagePullBackOff shape: `required_evidence_coverage` inflated 0.0 → 0.5, overall completeness 0.35 → 0.55, and a genuinely missing evidence domain disappeared from the reported gaps.
10. The outer crash boundary returned only `{"error", "status"}` — no `run_id`, so a crashed run could only be found by hunting Cloud Logging by timestamp.

**Pass 3 — confidence scoring and loop control**
11. `domain_weight()` gave evidence from an unclassifiable tool full corroboration credit (1.0), worth half the `independent_corroboration` component on its own — directly contradicting `classify_tool`'s own docstring.
12. **The most serious finding.** `loop_controller` unconditionally wrote `status: "done"`, silently **overwriting** a `"failed"` status set earlier in the same iteration by `mcp_router`. `investigation` merges with `operator.or_` and `loop_controller` runs last, so it always won. This made the Gap 7 fix — from earlier in the same review — completely inert: a router failure still surfaced as a normal completion.
13. `task_evaluator`'s zero-evidence gate had the same slot-counting bug as Gap 2.
14. Same bug in `mcp_router`'s prompt `evidence_count` and the report's impact-assessment gate. Consolidated into one shared definition, `agent.state.usable_evidence_ids`, so it cannot be gotten wrong at a new call site.
15. `rca_builder` offered the model the full evidence ID list to cite, including failed items, while its own prompt demands every claim cite a real evidence ID.

**Pass 4 — otel.py and eval/**
16. `run_remote()` in the eval harness double-wrapped the Agent Engine response. Proved directly, through the real function with only the network client mocked: a **perfect** agent response — correct tools, the exact expected root cause, confidence 0.8 — scored `predicted_tools=[]`, `confidence=0.0`, `passed=False`. Remote-mode eval failed every golden case regardless of real agent quality, with nothing to say the harness itself was broken.

**Reviewed and found clean, no change needed:** `context_resolver` (refuses to guess, logs at ERROR, records the error), `input_normalizer` (never invents a cluster/namespace), `state.py` reducers, the cluster-registry loader, `policy.py` (validates its own weights sum to 1.0 at import), `derive_outcome`'s hard score caps, `models.py` defaults (fail closed), `gcs_client.write_evidence` (retries, logs "audit chain broken", returns a distinguishable failure marker), `otel.py` (every span write degrades silently by design; one low-severity `get_tracer()` init race noted but not fixed — not reachable given how it's actually called), `golden_cases.py` (pure data).

## 3. What actually changed

- **Code:** `agent/mcp_client.py`, `agent/llm/{base,gemini_adapter,__init__}.py`, `agent/nodes/{rca_builder,mcp_router,task_planner,task_evaluator,evidence_extractor,loop_controller}.py`, `agent/main.py`, `agent/state.py`, `agent/confidence/{claim_builder,evidence_domains,scorer,models}.py`, `agent/eval/run_eval.py`
- **New tests:** 61, across `test_failed_results_never_become_evidence.py`, `test_degraded_steps_are_reported_not_hidden.py`, `test_confidence_and_loop_gaps.py`, `test_eval_run_remote_unwrap.py`
- **Full suite:** 346 passed
- **CI:** `pytest`, `mcp-pytest`, `plan` — all green on the second push (first push failed `ruff`, see §5)

## 4. Merge and deploy

- PR #204 merged to `main` at `2026-08-27T16:38:50Z`
- `terraform-apply.yml` triggered automatically (the workflow's own `paths` filter includes `agent/**`)
- Deploy run [33094304739](https://github.com/AshminPy/sre-agent-gateway/actions/runs/33094304739): `conclusion=success`
- CI's own smoke test: `SMOKE TEST PASSED — agent returned a structured RCA.`

## 5. A failure along the way, shown not hidden

The first push to PR #204 failed CI's `pytest` job. **The tests themselves passed 346/346, in CI and locally.** The failure was `ruff`, the linter, which I had not run locally before pushing:

- `E402` — a new function (`llm_json_failed`) landed between two `import` statements in `agent/llm/__init__.py`, pushing the second import below non-import code.
- `F401` — an unused `import pytest` left over from an edit to a new test file.

Both are real, both were fixed, both verified with the actual `ruff` binary before pushing again — not assumed fixed. Second push: all three CI checks green.

## 6. Live test — evidence, not just a green checkmark

Ran the exact `imagepull` scenario that fabricated a wrong answer on 2026-08-26, against the freshly redeployed agent.

**Command**
```
PROJECT_ID=sreagent-t2-demo REGION=us-central1 REASONING_ENGINE_ID=7801582006105538560 \
python invoke_agent.py --scenario imagepull --verbose
```

**Result** — `run_20260827_164529_nazx`, 2026-08-27T16:47:52Z:

| Signal | Value |
|---|---|
| `working_theory` | *"Pod is in ImagePullBackOff because the specified image 'gcr.io/google-containers/nonexistent-image:v99.9.9' does not exist."* — the **real** image on the pod |
| `tool_success` | 1.0 |
| `investigation_completeness_score` | 1.0 |
| `error_count` | 0 |
| `root_cause_confidence` | **0.825**, band `review_required` — honest, not the false 1.0 the fabricated run reported |
| `evidence_ids` | `ev_001`, `ev_002` |
| `outcome` | `probable` |

**Independently verified, not just trusted from the summary:**

- **The real pod image** — `kubectl -o jsonpath='{.spec.containers[0].image}'` on `imagepull-pod` confirmed `gcr.io/google-containers/nonexistent-image:v99.9.9`, matching the agent's answer exactly.
- **The evidence is real data, not a placeholder.** Fetched both stored evidence files (`ev_001`, `ev_002`) directly from GCS and checked each for the Model Armor block-notice string (`"model armor"` + `"violates content security"`). Neither present. `ev_002` (`describe_k8s_resource`) is 2,381 bytes of real pod description.
- **Model Armor was live and active during this exact run.** `pi_and_jailbreak` matched twice at `16:47:20` and `16:47:30`, both `SANITIZE_USER_PROMPT`. Because floor settings run `inspect_only`, nothing was blocked — but this proves the filter is genuinely engaged, not disabled, and that the agent still produced an honest, evidence-grounded answer with it active.

## 7. Conclusion

| Claim | Status |
|---|---|
| 16 real gaps found, all same bug class as the original incident | **Proven**, each with a before/after reproduction using real code, not description |
| All 16 fixed with tests | **Proven** — 61 new tests, 346 total, all passing |
| Merged to `main` | **Proven** — PR #204 |
| Deployed | **Proven** — apply run succeeded, CI's own smoke test passed |
| Works correctly on the real agent | **Proven** — live run reproduces the real image, real evidence confirmed in GCS, honest non-inflated confidence |
| Model Armor still engaged post-deploy | **Proven** — two real filter matches during the test run, logged, not blocking |

## 8. What is still open, on purpose

- **Issue [#202](https://github.com/AshminPy/sre-agent-gateway/issues/202)** — the deliberate block-mode diagnostic (flip `inspect_and_block` back on temporarily, confirm a real block now fails honestly instead of fabricating) has still not been run. Everything in this report proves the fixes work when Model Armor does not block. It does not yet prove the exact failure path — `isError`/block detection — under a real block, live.
- **`get_tracer()`'s init race** (§2, "reviewed and found clean") — documented, not fixed, because it is not reachable given the current call pattern. Worth a real fix only if that call pattern ever changes.
- This review covered `agent/` and `agent/eval/`. `scripts/`, `iac/`, and the custom MCP server's own code were not in scope.
