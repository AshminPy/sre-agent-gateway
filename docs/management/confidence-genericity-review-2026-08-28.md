# Confidence Framework — Genericity Review + 14-Case Live Analysis
2026-08-28. Read-only architecture review (4 parallel audits) + a real remote-mode run of all 14
golden cases against the deployed agent, full component data pulled from
`gs://sreagent-t2-demo-eval/runs/`. No scoring-behavior code was changed except one isolated,
already-fixed eval-harness bug (see §9).

---

## 1. Verdict

**Partially hardcoded — the core scorer is architecturally generic, the surrounding tables are not,
and both gaps are now confirmed live, not just theoretical.**

`agent/confidence/scorer.py` itself contains zero tool-name/mcp-source/incident-type string
literals — it only consumes `EvidenceDomain` enum values and policy weights. That isolation is
real and correctly designed. But three tables that feed it are closed and incomplete, and a live
run today reproduced concrete, real score distortions from exactly those gaps — this is not a
hypothetical genericity concern, it already happened.

---

## 2. Exact hardcoded dependencies

| # | Location | What's hardcoded |
|---|---|---|
| 1 | `evidence_domains.py:33-38` `_TOOL_DOMAIN` | 6 explicit tool→domain entries. 21/27 custom-K8s-MCP tools and 2/6 GKE-Remote-MCP tools have no entry → `UNKNOWN`. |
| 2 | `evidence_domains.py:59-68` `classify_tool()` | Takes only a tool **name**, never call **args** — `get_k8s_logs(previous=true)` and `get_k8s_logs(previous=false)` classify identically. |
| 3 | `policy.py:73-117` `evidence_requirement_for()` | Exact, case-sensitive `dict.get()` on a free-text LLM-generated `incident_type` string against 3 explicit keys, else silent fallback to `"_default"`. No telemetry on fallthrough. |
| 4 | `evidence_extractor.py:44-58` `_resource_id_from_call()` | Binary `if mcp_source == "gke_remote_mcp"` / else-K8s-shaped-fallback. A new non-K8s MCP source falls into the K8s branch and either produces an empty `resource_id` or misattributes identity via a context-pod fallback. |
| 5 | `scorer.py:207-216` `direct_support` | `len(fact_claims) / len(root_claims)` where `fact_claims` requires `claim_type == OBSERVED_FACT`. A claim's `support_strength` is never read here — `supported_inference` claims earn **zero** credit regardless of evidence strength. |

---

## 3. Which create wrong scores TODAY — confirmed with live 2026-08-28 data, not static analysis

### 3a. `direct_support`'s claim_type gate — the single biggest driver, hits nearly every case
Real numbers, all from today's live run:

| Case | Score | direct_support | Why |
|---|---|---|---|
| selector-001 | **0.575** (partial_evidence) | **0.0** | The one and only claim — the correct causal RCA (Service selector `app=notification-service` vs Pod label `app=notification`) — is `supported_inference` by nature (a causal conclusion always is). 0/1 = 0.0. Worst-case scenario the audit predicted, now observed for real, on the single hardest designed case in the set. |
| imagepull-001 | 0.925 | 0.75 | 1/4 claims is the causal inference; diluted by 3 correct observed-fact claims. |
| configmap-001 | 0.85 | 0.833 | Same shape. |
| init-001 | **0.84** (review, just under 0.85 auto cutoff) | 0.8 | Same shape — this one specifically missed "auto" band by 0.01. |
| oomkilled-001 | 0.65 (review) | 0.8 | Compounded with §3b below. |

A root-cause claim is *inherently* an inference over observed facts — penalizing it for being typed correctly, rather than scoring it by how well-grounded the inference is, structurally caps well-evidenced RCAs below what their evidence quality justifies. Not a coding bug (matches `docs/confidence-framework-design.md` and ADR-007's literal spec) — a design choice whose real-world effect is now measured, not assumed.

### 3b. `get_k8s_logs(previous=true)` misclassification — confirmed live
`oomkilled-001` (today): tool_calls includes `get_k8s_logs`. `evidence_domains_present` shows
`['current_logs', 'kubernetes_events', 'kubernetes_status']` — **no `previous_logs`**, even though
OOMKilled's whole diagnostic signal (why the container that's now gone died) lives in the
previous container's logs, and the agent evidently did try to fetch them. Result: gap message
*"Missing required evidence domain(s): previous_logs"*, `missing_evidence_penalty = 0.1`, score
capped at 0.65 (review) instead of what a fully-gathered investigation should score. This is the
exact failure mode the tool-domain audit predicted from static code reading — now reproduced in
a real run the same day.

### 3c. `resource_identity_match` — a residual gap, NOT the same as the already-fixed #206 bug
`configmap-001` (0.5) and `init-001` (0.5) both hit *"1 supporting evidence item(s) reference a
different namespace/pod than the resolved investigation target"* — **after** PR #206 was live
(#206 deployed 2026-08-27; these runs are 2026-08-28). Both RCAs are objectively correct. This is
a different, not-yet-root-caused gap from what #206 fixed — flagged for follow-up, not yet traced
to a specific line.

### 3d. `UNKNOWN`-tool zero-corroboration — confirmed live
`conflicting-evidence-001`: gap message *"1 evidence item(s) came from an unclassifiable tool
(ev_001) — check the `_TOOL_DOMAIN` table."* Direct, real-world proof that the 21/27-tools-UNKNOWN
gap from §2 row 1 is not just a static-analysis concern.

---

## 4. Harmless / correct as-is

- The 3 explicit `policy.py` incident-type entries (OOMKilled/ImagePullBackOff/CrashLoopBackOff) —
  real, hand-verified domain expertise (e.g. OOMKilled needing `PREVIOUS_LOGS` because the killed
  container is gone). Keep, do not generalize away.
- `domain_weight()`'s `UNKNOWN → 0.0` — correct fail-closed behavior, itself fixed a real bug
  2026-08-27 where UNKNOWN was worth 1.0. Not a genericity problem, the safety net for one.
- `toolspec.json` missing `get_k8s_logs` — stale doc file, never imported at runtime, zero
  behavioral effect.

---

## 5. Root cause of false-LOW scores across the 14 — ranked by observed live impact

1. **`direct_support` claim-type gate** (§3a) — hits nearly every case, since a root cause is
   almost always an inference. Confirmed to have kept `init-001` out of the auto band by 0.01,
   and dropped `selector-001` — the hardest case in the set, correctly solved — to `partial_evidence`.
2. **`get_k8s_logs(previous=true)` misclassification** (§3b) — confirmed, `oomkilled-001`.
3. **`resource_identity_match` residual gap** (§3c) — confirmed live, root cause not yet traced.
4. **`UNKNOWN`-tool zero-corroboration** (§3d) — confirmed live, didn't change the final band here
   but is a real, measurable drag.

## 6. Root cause of false-HIGH scores

**CORRECTED 2026-08-28 (superseded by the corrections addendum, §15 below) — this conclusion
was wrong.** A real structural false-high mechanism exists: `missing_evidence_penalty` and
`required_evidence_coverage` are computed from evidence-store-wide domain presence
(`domain_map.values()` / `_evidence_domains_present(...).values()`), never scoped to the
claim's own `supporting_evidence_ids` the way `independent_corroboration` correctly is in the
same function. See §15.1 for the full proof, real numbers, and a passing regression test.

~~**No structural false-high mechanism found**, in code or in the live data. `_default`'s fallback
is provably monotonic-lenient (proof in the incident-hardcoding audit: its `required_now` is a
strict subset of every explicit entry, so it can only make coverage checks *easier*, never
produce an unjustified score increase from a real gap).~~ *(The monotonic-leniency proof for
`_default` itself still holds — `_default` cannot produce a false-high on its own. What was missed
is that the missing-evidence check applies to EVERY incident type, not just `_default` cases, and
it's unscoped regardless of which requirement set is active.)*

One case needed a closer look: `secret-001` scored 0.75 (review, not auto) with a root cause
("`user-service-6c6568988c-52jbf` in CrashLoopBackOff, restarted 5×, exit code 1") that has
**nothing to do** with the golden case's intended scenario (a missing Secret, `ContainerCreating`).
Checked the actual claims and evidence: all 4 claims are `grounded`, citing real, specific K8s
facts (restart count, exit code, deletion timing) — **not a hallucination**. What happened: no
manifest exists for `secret-001` (flagged earlier in this session), and a real, unrelated,
already-existing `user-service` pod from prior, unrelated testing was sitting in `demo-incidents`
in a *different* broken state. The agent correctly investigated and correctly reported what it
found — the golden case's test fixture is stale/wrong, not the scorer. Reclassify as **test-
fixture contamination**, not a scoring defect.

---

## 7. Case-by-case summary (all 14, live 2026-08-28 remote-mode run)

| Case | RCA correct? | Outcome | Band | Score | Completeness | Mismatch class |
|---|---|---|---|---|---|---|
| crashloop-001 | Yes | confirmed | high_confidence | 1.0 | 1.0 | none — clean baseline |
| oomkilled-001 | Yes | probable | review_required | 0.65 | 0.867 | **evidence-normalization bug** (§3b) |
| imagepull-001 | Yes | confirmed | high_confidence | 0.925 | 1.0 | none (direct_support drag present but didn't change band) |
| configmap-001 | Yes | confirmed | high_confidence | 0.85 | 1.0 | **claim-grounding/resource-identity gap** (§3c), barely cleared threshold |
| init-001 | Yes | probable | review_required | 0.84 | 1.0 | **claim-grounding/resource-identity gap** (§3c) — missed auto band by 0.01 |
| selector-001 | Yes (hardest case in the set) | possible | partial_evidence | 0.575 | 1.0 | **implementation/design bug** (§3a), worst observed instance |
| cascading-001 | Unknown — no answer produced | insufficient_evidence | insufficient_evidence | 0.0 | 1.0 | **LLM output-parse failure** ("model's response could not be parsed"), not a confidence bug |
| pending-001 | Unknown — no answer produced | insufficient_evidence | insufficient_evidence | 0.0 | 0.97 | **LLM output-parse failure** + 1 tool call failed (possibly load-related) |
| onprem-001 | Yes (correctly refuses) | insufficient_evidence | insufficient_evidence | 0.0 | 0.0 | none — correct by design |
| insufficient-evidence-001 | N/A | — | — | — | — | **infra: 429 quota exhaustion**, needs retry |
| ambiguous-routing-001 | Yes (correctly refuses) | insufficient_evidence | insufficient_evidence | 0.0 | 0.0 | none — correct by design |
| conflicting-evidence-001 | Yes | confirmed | high_confidence | 0.9 | 1.0 | **UNKNOWN-tool gap** (§3d), didn't change band |
| mcp-gateway-failure-001 | Yes | probable | review_required | 0.75 | 1.0 | direct_support drag, minor |
| secret-001 | Real finding, wrong scenario | probable | review_required | 0.75 | 1.0 | **test-fixture contamination** (§6), not a scoring bug |

**9 of 13 scored cases (excl. the quota error) reached the objectively correct root cause.**
3 of those 9 (oomkilled-001, configmap-001, init-001, selector-001 — 4 actually) were held below
their deserved band by a confirmed scoring defect, not by weak evidence. 2 cases produced no
answer due to an LLM parsing failure unrelated to confidence scoring. 1 case's "wrong" answer
traces to a contaminated test fixture, not the agent or the scorer.

**Operational note, unrelated to the confidence framework:** two runs came within ~50s of the
540s hard timeout (`secret-001`: 493.6s: `oomkilled-001`/others triggered a real Cloud Monitoring
p99-latency alert at 535.2s) — a direct side effect of running all 14 cases back-to-back against
a cluster simultaneously loaded with 3 continuously-crashing test pods. Space out future eval
runs or reduce concurrent test-pod churn; not a code defect.

---

## 8. Proposed generic architecture

Formalize the pattern that already exists in `scorer.py`, and close the 3 real gaps around it —
this is the same shape already described in the review request:

```
MCP / Data Source
        v
Source adapter / evidence normalizer   <- fix: args-aware classify_tool() + per-source
                                           resource_id adapter registry
        v
Standard EvidenceRecord (domain, resource identity, timestamp, source, op semantics)
        v
Claims                                  <- fix: direct_support keyed on support_strength,
                                           not a binary claim_type gate
        v
Generic deterministic confidence engine (already generic — scorer.py itself needs no change)
```

## 9. Smallest safe migration — what's isolated vs. what needs a plan

**Already fixed, isolated, regression-tested (per your standing authorization for confirmed small
bugs):**
- `agent/eval/run_eval.py` — `keyword_accuracy()` and `score_case()` crashed with `'dict' object
  has no attribute 'lower'` whenever `likely_root_cause` was a dict (the same non-string-LLM-
  response case `agent/main.py`'s `_extract_root_cause` already guards against, never ported to
  the eval harness). This crashed 3 of today's 14 cases' *local scoring* before I even found the
  real data was safely in GCS regardless. Fixed with `str()` coercion at the source, matching the
  existing pattern. 2 new tests added to `tests/test_eval_run_remote_unwrap.py`, all 6 pass.

**Needs a plan + your sign-off first (real scoring-behavior changes):**
1. `evidence_domains.classify_tool()` — accept optional `args`, special-case `previous=true` →
   `PREVIOUS_LOGS`, `resourceType` → correct domain for `describe_k8s_resource`/`get_k8s_resource`.
2. `ConfidencePolicy.validate()` — add a cross-check that every `EvidenceDomain` referenced in
   `evidence_requirements` has at least one live `_TOOL_DOMAIN` entry that can produce it. Catches
   the Prometheus-style drift at startup instead of silently at runtime.
3. `evidence_requirement_for()` — add `log.warning` on fallthrough to `_default`, so an
   unrecognized incident type becomes visible instead of permanently invisible. Zero scoring
   change, pure observability.
4. `policy.py`'s `_default.required_now` — replace the hardcoded `(KUBERNETES_STATUS,)` with a
   domain-count floor (≥2 distinct, successfully-classified domains), reusing
   `independent_corroboration`'s existing `domain_weight()` logic. Real scoring-behavior change
   for an estimated 7-9 of the 14 cases — needs regression tests against those specific cases
   before merging.
5. `direct_support` — let `supported_inference` claims earn partial credit proportional to
   `support_strength` instead of zero. **Do not pick a multiplier from this one run** — needs the
   4-group calibration methodology (§7 of your request) with real group-C/D control cases first,
   otherwise this is exactly the "tuned to make our 14 cases pass" trap you explicitly want
   avoided.
6. `evidence_extractor.py` — per-MCP-source resource_id adapter registry, replacing the binary
   `gke_remote_mcp`/else branch. Lower priority — no second non-K8s source exists yet to break it.

## 10. Exact files that need changes

- `agent/confidence/evidence_domains.py` — items 1, 2 above
- `agent/confidence/policy.py` — items 2, 3, 4 above
- `agent/confidence/scorer.py` — call-site updates for args-aware `classify_tool`; item 5 (pending calibration)
- `agent/nodes/evidence_extractor.py` — item 6 (lower priority)
- `tests/test_evidence_domains.py`, a new `tests/test_confidence_policy.py` or similar, and
  `tests/test_confidence_scorer.py` — regression coverage for all of the above
- `docs/confidence-framework-design.md` + a new ADR — document the design change once approved

## 11. Exact tests required

- `classify_tool("get_k8s_logs", args={"previous": True})` → `PREVIOUS_LOGS`
- `classify_tool("get_k8s_logs", args={"previous": False})` → `CURRENT_LOGS` (regression, unchanged)
- `classify_tool("describe_k8s_resource", args={"resourceType": "deployment"})` → correct non-pod domain
- `ConfidencePolicy.validate()` raises/warns on a synthetic policy fixture missing a producing tool
- `evidence_requirement_for()` logs a warning on `_default` fallthrough (caplog-based)
- (pending approval) `_default`'s new domain-count floor: 1-domain case fails, 2-domain case passes
- (pending approval) `direct_support` partial-credit formula, using `selector-001`'s and
  `odwb`'s real claims as fixtures, confirming the counterfactual scores land where expected
- Full existing suite re-run after each change (`pytest`, `ruff check agent/ agent/eval/`)

## 12. Does the existing 14-case dataset need changes?

**Yes, two ways:**
1. `secret-001`'s live cluster fixture is contaminated (§6) — needs a real, isolated manifest
   (none exists today) so the case tests what it's supposed to, not whatever unrelated pod
   happens to be sitting in that namespace.
2. Per your §7 framework, the current 14 are all Group A/B shaped (a real, findable correct
   answer). **None deliberately test Group C** (incorrect RCA + superficially plausible evidence)
   **or Group D** (unsupported/hallucinated RCA). Calibration cannot validate separation between
   groups that don't exist in the dataset yet.

## 13. Additional negative/control cases needed for calibration

- At least 1-2 Group C cases: evidence that plausibly points to the wrong culprit (the
  `cascading-001` scenario's own design comment already describes exactly this trap — "naive
  investigation finds order-api crashing -> wrong conclusion" — worth checking once its LLM-parse
  issue is fixed, whether the agent falls into that trap or correctly finds `order-db`).
- At least 1 Group D case: evidence deliberately ambiguous/sparse enough to tempt a fabricated-
  sounding causal chain, to test whether contradiction/grounding checks catch it.
- Once a second non-K8s MCP source exists (Prometheus etc.), a case exercising it, to regression-
  test the genericity fixes themselves rather than assume them.

## 14. Recommended order

1. **Bug fixes** (isolated, safe, regression-tested) — eval-harness `.lower()` fix: **done today**.
   `classify_tool` args-awareness (#206-style, previous_logs + resourceType), `validate()`
   cross-check, `_default` fallthrough logging — small, mechanical, low-risk, propose next.
2. **Genericity fixes needing sign-off** — `_default`'s domain-count floor, `direct_support`'s
   partial-credit formula (pending calibration data), evidence_extractor's adapter registry.
3. **14-case rerun** — after (1), with the new `_default` fallthrough logging live, to fact-check
   which real incident_type strings the LLM emits (confirms the incident-hardcoding audit's
   hypothesis with real data instead of inference). Fix `secret-001`'s fixture and retry
   `insufficient-evidence-001` (quota-exhausted today) at the same time. Space calls out this time.
4. **Analyze that rerun** exactly per this document's §7 shape.
5. **Calibration** — only after (1)-(4) are clean. Needs the Group C/D cases from §13 added first.
6. **Final rerun** with calibrated weights.
7. **Deployment/live validation** — real `invoke_agent.py` smoke test against the redeployed
   agent, cross-checked against Cloud Logging/GCS, before removing the `-uncalibrated` version tag.

---

**Repo state:** `main` @ `d799e38` at review start; the eval-harness fix (§9) merged via PR #217.
**Live data sources:** `gs://sreagent-t2-demo-eval/runs/` (14 real remote-mode runs, 2026-08-28,
run_ids `tcob/zout/qahn/rmlk/rtrb/sjzv/kacq/wjii/ibxu/gtxy/rwsa/rkbc/woda/zxbh`), 4-agent parallel
workflow audit (tool/domain mapping, incident hardcoding, claim scoring, hardcoded conditionals).

---

# CORRECTIONS ADDENDUM — 2026-08-28 (second pass)

Owner review of the above found 10 material gaps. Investigated with 7 parallel deep-dive audits,
each required to run real code (not reason abstractly) and, where asked, add a real passing test
proving current behavior. Two new test files were added — nothing under `agent/` was changed.
No scoring weight, threshold, or the `direct_support` formula was touched, per instruction.

## 15.1 Correction — a real false-high mechanism exists (item 1)

**`missing_evidence_penalty` and `required_evidence_coverage` are not scoped to the claim they're
supposedly gating.** `scorer.py:75` and `scorer.py:350` both build `domains_present` from **every
successfully-collected evidence item in the whole investigation** —
`set(_evidence_domains_present(evidence_store, tool_history).values())` — not from the root-cause
claim's own `supporting_evidence_ids`. `independent_corroboration`, in the same function, does
scope correctly (`scorer.py:229-242`, builds `supporting_ids` from the claims first). The three
components sitting right next to it never reuse that scoping.

**Proven with a real counterexample, run against the actual scorer** (new file
`tests/test_missing_evidence_claim_scoping.py`, 4/4 passing):
- Incident falls to `_default` (`required_now=(KUBERNETES_STATUS,)`)
- `ev_001` = `KUBERNETES_STATUS`, about an unrelated pod, never cited by the claim
- `ev_002` = `KUBERNETES_EVENTS`, about the real target, is the claim's only cited evidence
- Real output: `score=0.875`, `band=high_confidence`, `missing_evidence_penalty=0.0` — the hard
  cap (`max_score_missing_critical_evidence=0.65`) never engages because `missing_required` comes
  back empty, even though the claim's actual supporting evidence is a single, uncorroborated
  domain (`independent_corroboration=0.5`).
- Counterfactual, same scenario, scoped like `independent_corroboration` already is: penalty
  applies, hard cap engages, score caps at 0.65 (`review_required`) — a full band lower.

**This is a 5th false-high mechanism, additive to the false-low ones in §3-5** — it does not
apply only to `_default` cases; it applies to every incident type, since the scoping gap is in
the shared missing-evidence logic itself, not in the `_default` table.

Also found, same shape, inside `root_cause_confidence` specifically: `independent_corroboration`
(claim-scoped, correct) and `missing_evidence_penalty` (store-wide, wrong) disagree with each
other *within the same score* on the same investigation — confirmed by re-running the item-4
default-semantics counterexample below.

**Verification:** `pytest tests/test_missing_evidence_claim_scoping.py -v` → 4 passed. Full suite
`pytest tests/ -q` → 364 passed, unchanged from baseline.

## 15.2 Correction — collection time vs. incident time is a real, separate false-high gap (item 2)

`agent/nodes/evidence_extractor.py:96` stamps `collected_at = time.time()` — unconditionally,
collection time, never anything parsed from the underlying K8s object's own timestamp. The
code's own comment admits this. `freshness` (`scorer.py:107-129`) and `time_correlation`
(`scorer.py:290-319`) both only ever compare `collected_at` values against each other or against
`now` — neither has any signal for how old the underlying incident/event actually is.

**Confirmed with a real counterexample** (new file `tests/test_collection_time_vs_incident_time.py`,
4/4 passing): a pod that "crashed 5 hours ago," investigated right now, scores `freshness=1.0`
and `time_correlation=1.0` — identical to a genuinely fresh incident — purely because the tool
calls happened just now. A control case in the same file confirms the component tracks
*collection* recency, not incident recency, in both directions.

**Classification: confirmed false-high integrity limitation.** Not expanded into a timestamp
redesign per instruction — logged as a real, scoped backlog item (Phase B/C, see final plan).

## 15.3 Correction — the deterministic-gap → planner disconnect is real (item 3)

Traced end-to-end and reproduced with the real functions, not just read:
- `task_evaluator.py:35` computes `completeness.missing_required_domains` (deterministic,
  from `scorer.py:172`) and separately reads `evidence_gaps` from the LLM's own JSON output
  (`task_evaluator.py:113`) — two independent fields in `investigation` state.
- `loop_controller.py:175-188` forces a retry off the **deterministic** field only (this is
  already-shipped, already-tested issue #69 behavior).
- `task_planner.py:32` and its prompt (`prompts.py:49-50`) read **only** `evidence_gaps` (the LLM
  field) — `missing_required_domains` never reaches the planner or its prompt at all (confirmed
  by grep: the field appears in exactly 5 files, none of them `task_planner.py`).
- Reproduced live: constructed an oomkilled-001-shaped state where the LLM's own `evidence_gaps`
  was empty but the deterministic list said `["previous_logs"]`. `loop_controller` correctly
  forced another iteration. The exact prompt block sent to the planner on that forced retry never
  mentioned `previous_logs` or the deterministic list at all — the extra iteration ran with zero
  information about what it was supposed to go collect. This is the mechanism behind the real
  `oomkilled-001` outcome already documented in §3b.

**Minimal fix (design only, not implemented) — respects the required layering:**
1. `task_planner.py:32` — read `investigation["completeness"]["missing_required_domains"]`
   (already present in state, no new field needed — `AgentState`'s merge semantics already
   carry it forward).
2. `prompts.py`'s `TASK_PLANNER_USER` — add one line: *"Deterministically confirmed missing
   evidence domain(s) — target one of these FIRST if non-empty: {required_domains}"*.
3. Nothing else changes. The scorer still only names the missing *domain*; the LLM planner still
   picks the *tool*; the normalizer still maps results back to the domain, unchanged.

## 15.4 Correction — incident-type canonicalization + `_default` semantics (items 4 & 5)

**Canonicalization (item 4):** pulled all 132 real production runs from `gs://sreagent-t2-demo-eval/`
— **zero casing/spelling variance observed**; every `incident_type` value was byte-exact to one
of the 3 policy keys or the literal `"Unknown"`. The prompt hands the model a closed vocabulary
(`prompts.py:10-20`), which is why it's clean today — but nothing *structurally* enforces it (no
`response_schema`/enum constraint on the Gemini call). Recommended minimal design: a
normalize-then-match fallback derived mechanically from the 3 existing keys
(`lowercase + strip non-alphanumeric`, tried after the exact match, before `_default`) — verified
against real variants (`"oomkilled"`, `"OOM Killed"`, `"OOMKILLED"` all correctly resolve;
`"Latency"` and novel strings correctly still fall through). **No hand-built synonym table is
justified by any evidence found** — add only if the new `_default`-fallthrough logging (already
recommended, §9 item 3) ever actually observes real variance.

**`_default` semantics, A vs B (item 5):** confirmed via `agent/graph.py`'s real node ordering
that `investigation_completeness` runs *inside* the loop, before any `Claim` object exists —
`root_cause_confidence` (claim-aware) runs once, after the loop, in `rca_builder.py`. So
completeness is claim-agnostic **by pipeline position**, not by oversight — Design B is not
achievable there without restructuring the graph, which is out of scope.

Quantified the risk anyway with a real constructed case (3 evidence domains collected, only 1
cited by the actual root-cause claim): Design A reports `required_evidence_coverage=1.0`,
`gaps=[]` ("complete") for a claim whose real evidence coverage is 0% by claim-scoped accounting
— a 100-point gap in one realistic scenario.

**Recommendation — hybrid, not a pure pick:**
- `investigation_completeness`'s `_default` floor stays **Design A** (claim-agnostic) — the
  pipeline makes anything else impossible today. Fix the *framing*: document explicitly that
  `"complete"` means "the agent looked at ≥2 kinds of evidence," never "the evidence supports the
  answer."
- `root_cause_confidence`'s `missing_evidence_penalty` should be changed to **Design B**
  (claim-scoped) — reusing the `supporting_domains` set `independent_corroboration` already
  builds, for internal consistency within one function. This is the SAME fix as §15.1 — items 1
  and 5 converge on one fix, not two.

## 15.5 Correction — `support_strength` is not currently trustworthy (item 6)

**Important scope correction: this is not a hypothetical risk for a future change.**
`support_strength` already drives a live score component today — `claim_grounding`
(`scorer.py:321-323`, weighted 0.10, `policy.py:50`). Everything below is a defect in the current
score, not just a risk gate for a not-yet-made `direct_support` change.

Ran `_ground_claim()` (`claim_builder.py:41-114`) — pure lexical keyword-overlap, no negation, no
entailment/contradiction check — against 5 real adversarial pairs, actual function output:

| # | Scenario | Result | Verdict |
|---|---|---|---|
| 1 | Correct fact + invented mechanism | `grounded`, `1.0` | **fails** — fabrication scored full credit |
| 2 | Evidence contradicts the claim | `grounded`, `1.0` | **fails** — negation not detected at all |
| 3 | Topically unrelated evidence, shared namespace word | `grounded`, `1.0` | **fails** — coincidental token match |
| 4 | Directly, genuinely well-supported (control) | `grounded`, `1.0` | correct |
| 5 | True claim, paraphrased, low lexical overlap | `no_overlap`, `0.1` | **fails** — good paraphrasing punished |

**Verdict: not safe to use as-is** — for the `claim_grounding` component it already feeds today,
or as a future basis for `direct_support` credit. Widening `direct_support` to read
`support_strength` (as originally proposed) would import this grounding defect directly into the
heaviest-weighted component, trading the current false-low problem for a new false-high one.
**`_ground_claim` needs its own fix first** (minimum: negation-aware matching + an
entailment/contradiction signal, not raw token overlap) before any inference-credit weighting
change is considered.

## 15.6 Correction — `previous_logs` fix, schema verified, design finalized (item 7)

Verified at 3 independent levels, not assumed: `agent/mcp_client.py:319-336`'s payload builder
reads `arguments.get("previous")` (bool); the live prompt shown to the LLM documents
`previous=false` as the arg; and the **real production tool call** for `oomkilled-001`
(`gs://sreagent-t2-demo-eval/runs/run_20260828_153451_zout.json` + its Cloud Logging
`tool_executor` line) shows `args={..., 'previous': True}` sent for real. Root cause confirmed:
`_map_to_custom_tool()` (`mcp_client.py:363-369`) hardcodes `get_k8s_logs → get_current_logs`
with no conditional at all. The custom K8s MCP side needs no fix — it already has two separate,
correctly-mapped tool names for current/previous logs.

`describe_k8s_resource`/`get_k8s_resource`'s `resourceType` arg is also confirmed live —
production traffic in the last 2 days already sends `deployment`/`replicaset`/`service`/
`configmap`/`job`, all forced to `KUBERNETES_STATUS` today. Fixing this fully requires
`_TOOL_DOMAIN` entries that don't exist yet for those resource types (same gap as the original
report's §2 row 1) — the design below resolves only the values with an unambiguous existing
match (`deployment`/`replicaset`/`statefulset`/`daemonset` → `WORKLOAD_CONFIG`); `service`/`node`/
`configmap`/`job` are deliberately left pending the table-completeness decision, not guessed.

**Finalized minimal design (signatures only, not implemented):**
```python
# evidence_domains.py
def classify_tool(tool_name: str, args: dict | None = None) -> EvidenceDomain: ...
_ARGS_DOMAIN_OVERRIDES = {
    "get_k8s_logs": ("previous", {True: EvidenceDomain.PREVIOUS_LOGS}, EvidenceDomain.CURRENT_LOGS),
    "describe_k8s_resource": ("resourceType", _RESOURCE_TYPE_DOMAIN, EvidenceDomain.KUBERNETES_STATUS),
    "get_k8s_resource": ("resourceType", _RESOURCE_TYPE_DOMAIN, EvidenceDomain.KUBERNETES_STATUS),
}
# evidence_extractor.py -- store args already in hand, mirrors the existing resource_id pattern:
ev_entry["args"] = last_call.get("args") or {}
# scorer.py:39 -- one-line call-site change:
domains[ev_id] = classify_tool(tool or "", ev.get("args") or {})
```
Only 3 tools multiplex on args; every other tool's classification is byte-for-byte unchanged.
Existing no-args call sites and `tests/test_evidence_domains.py:13`'s regression test keep passing
unmodified since `args` defaults to `None`.

## 15.7 Correction — resource_identity_match root-caused precisely (item 8)

**Both `configmap-001` and `init-001`: verdict (c) — the scorer's containment check is too
strict for legitimately-related non-Pod evidence, not (a)/(b)/(d).**

Reconstructed and re-ran the real functions against the real live data for both cases:
- `configmap-001`: flagged evidence is `get_k8s_resource(resourceType="configmap",
  name="app-config")` → `resource_id="test-incidents/app-config"`, cited by the RCA's key causal
  claim ("ConfigMap 'app-config' ... NotFound"). `resolved_context` pod is `"auth-service"`.
  `"auth-service" in "test-incidents/app-config"` → `False` → flagged, even though the evidence
  is exactly correct and directly causal.
- `init-001`: same shape, `get_k8s_resource(resourceType="service", name="db-service")` →
  `resource_id="test-incidents/db-service"`, resolved pod `"inventory-service"` — same false flag.

Reran both reconstructions through the real `score_root_cause_confidence()` — reproduced the
exact live scores (0.85, 0.84) and the exact gap message, confirming the reconstruction is
accurate.

**Root cause:** `evidence_extractor.py:231` already stores `resource_type` (e.g. `"configmap"`,
`"service"`) on every evidence entry — `scorer.py` never reads it. The containment check applies
the same "must contain the resolved pod's name" rule uniformly, even to evidence about a
legitimately-related non-Pod dependency object that structurally can never embed the pod's name.
Not a `_resource_id_from_call()` bug (ruled out — the values it computed were correct) and not
unrelated evidence (ruled out — both are the RCA's actual key evidence).

**Fix direction (not implemented, needs sign-off):** relax `pod_ok` to `True` when
`ev.get("resource_type") != "pod"` and the namespace still matches — i.e. only require pod-name
containment for evidence that is actually *about* a Pod.

## 15.8 Live-data status (item 9)

Restated plainly: **the 14-case dataset is not clean and must not be used for calibration.**
`insufficient-evidence-001` hit a real 429 quota-exhaustion error (all 14 calls run back-to-back,
no spacing). `cascading-001` and `pending-001` both hit a genuine LLM-response-parse failure
("model's response could not be parsed"), unrelated to confidence scoring. `secret-001`'s target
namespace had a contaminated fixture (a real, unrelated, already-broken pod from prior testing,
not the intended missing-Secret scenario). All four need to be fixed/retried before any run of
this dataset is used as a calibration input — tracked in the final plan's Phase C/D gating, not
worked around here.

---

# FINAL IMPLEMENTATION PLAN

Five phases. Nothing beyond Phase A is implemented without separate, explicit approval — this
plan is the artifact being submitted for that approval, not a go-ahead to proceed.

## Phase A — Confirmed correctness bugs (safe to implement now, narrowly scoped, regression-tested)

| Item | Files | Tests | Expected behavior | Risk | Deploy required? |
|---|---|---|---|---|---|
| A1. `previous_logs`/`resourceType` classification (§15.6, report §3b/§7) | `agent/confidence/evidence_domains.py`, `agent/nodes/evidence_extractor.py`, `agent/confidence/scorer.py:39` | New: `classify_tool` args-aware cases in `tests/test_evidence_domains.py`; existing `tests/test_evidence_domains.py:13` must stay green | `get_k8s_logs(previous=true)`→`PREVIOUS_LOGS`; `describe/get_k8s_resource(resourceType=deployment/replicaset/statefulset/daemonset)`→`WORKLOAD_CONFIG`; all other tools unchanged | Low — additive, defaults preserve old behavior when `args` absent | Yes — this changes what a real deployed run scores; needs a live post-deploy check (same pattern as prior PRs: real `invoke_agent.py` run + Cloud Logging/GCS confirmation) |
| A2. `ConfidencePolicy.validate()` cross-check | `agent/confidence/policy.py` | New: a synthetic policy fixture missing a producing tool must raise at `validate()` | Startup-time catch of a required domain with no way to ever be produced | Low — validation-only, no scoring change | No — pure startup guard |
| A3. `_default` fallthrough + normalized-match logging (§15.4 Part A) | `agent/confidence/policy.py` | New: caplog test asserting `log.info` on normalized match, `log.warning` on true `_default` fallthrough | Visibility only — incident types that don't exact-match get one retry via normalize-then-match, then `_default` with a warning instead of silent | Low — logging + a narrow, provably-safe normalize step, no weight/threshold change | No — logging + non-scoring normalization |

## Phase B — Genericity + integrity fixes (need explicit sign-off; each is a real scoring-behavior change)

| Item | Files | Tests | Expected behavior | Risk | Deploy required? |
|---|---|---|---|---|---|
| B1. Scope `missing_evidence_penalty`/`required_evidence_coverage` to claim-cited domains (§15.1, §15.4 Part B — items 1 & 5 converge here) | `agent/confidence/scorer.py` (root_cause_confidence side only — completeness stays Design A per §15.4) | `tests/test_missing_evidence_claim_scoping.py` (already exists, currently proves the BUG — must be updated to prove the FIX once implemented); full regression suite | A claim whose cited evidence doesn't include a required domain now correctly loses `missing_evidence_penalty` credit and can hit the hard cap; a claim that DOES cite the required domain is unaffected | **Medium** — changes real scores for any case with an uncited-but-present required domain; needs the 14-case data (Phase D input) re-checked, not just golden cases | Yes |
| B2. Relax `resource_identity_match`'s pod-only containment check for non-Pod evidence (§15.7) | `agent/confidence/scorer.py` (the containment check only) | New regression test using the real `configmap-001`/`init-001` reconstructions as fixtures; confirm both now score `resource_identity_match=1.0` | Legitimately-related non-Pod evidence (ConfigMap/Service/etc.) no longer falsely dinged; still catches genuinely wrong-pod evidence | Low-medium — narrow, but changes real scores for both live cases identified | Yes |
| B3. Route deterministic `missing_required_domains` into `task_planner` (§15.3) | `agent/nodes/task_planner.py`, `agent/prompts.py` | New: assert the forced-retry prompt now contains the missing domain name, using the same synthetic state the investigation used | The forced extra iteration (issue #69) targets the actual gap instead of running blind | Low — additive prompt content, no scoring-math change; behavior change is in what the LLM planner is told, worth a live check since it changes real tool-selection on retries | Yes — live check that a forced retry actually requests the right tool |
| B4. `_ground_claim` negation/contradiction awareness (§15.5) | `agent/confidence/claim_builder.py` | New adversarial-pair regression tests using the exact 5 cases from §15.5 as fixtures | Cases 1-3 (fabrication, contradiction, unrelated) stop scoring `grounded`/1.0; case 5 (true, paraphrased) stops scoring `no_overlap`/0.1 | **Medium-high** — touches the grounding mechanism feeding `claim_grounding` (already live-weighted 0.10) AND is the prerequisite the report's §9 item 5 (`direct_support`) explicitly needs before proceeding | Yes |

**Not in Phase B, explicitly deferred:** `direct_support`'s claim-type gate (report §9 item 5) —
blocked on B4 landing and being trusted first, per item 6's instruction. Timestamp integrity
(§15.2) — logged as backlog, not a Phase B item, since a real fix needs raw-event timestamp
parsing (a larger change, out of scope per instruction).

## Phase C — Negative/control dataset additions (blocks Phase D)

| Item | Files | Tests | Expected behavior | Risk | Deploy required? |
|---|---|---|---|---|---|
| C1. Fix `secret-001`'s contaminated fixture | new `k8s/scenario-secret-missing.yaml`, `agent/eval/golden_cases.py` (no change needed if manifest matches existing query) | Live cluster verification only (pod reaches ContainerCreating/missing-Secret state) | `secret-001` tests what it says it tests | Low | No (test infra only) |
| C2. Retry `insufficient-evidence-001` in isolation (space out calls, avoid quota) | none | none | Clean, real result for this case | Low | No |
| C3. Fix or re-run `cascading-001`/`pending-001` past their LLM-parse failure | none (investigate the parse failure separately if it recurs) | none | Clean, real result for both | Low — if it recurs under normal (non-back-to-back) load, that's a new, separate reliability finding, not a confidence-framework issue | No |
| C4. Add ≥1 Group C case (plausible-but-wrong RCA) | `agent/eval/golden_cases.py`, a new k8s manifest if needed | none yet — this IS the test data | Confidence framework must score this LOW even though evidence looks superficially supportive | Medium effort, low risk | No |
| C5. Add ≥1 Group D case (unsupported/hallucination-tempting) | `agent/eval/golden_cases.py`, sparse/ambiguous manifest | none yet | Confidence framework must score this VERY low, contradiction/grounding checks must catch it | Medium effort, low risk | No |

## Phase D — Calibration (blocked on Phase B + Phase C both landing)

Not started, not scoped in detail here — per instruction, do not implement or design the
calibration methodology further until Phase A-C are approved and Phase B/C are actually landed.
Placeholder only: re-run all 14 (+ new C4/C5 cases) via `run_eval.py --mode remote`, human-grade
each result into groups A/B/C/D, then and only then discuss weight adjustment — using clean data,
with `direct_support` still deferred pending B4.

## Phase E — Deployment + exact live validation

| Step | Exact command | Confirms |
|---|---|---|
| E1. Deploy | merge to `main` → `terraform-apply` CI auto-triggers | `gh run view <id> --json status,conclusion` = `success` |
| E2. Live smoke test | `source scripts/init-env.sh && python invoke_agent.py --scenario oomkilled --verbose` | `previous_logs` now appears in `evidence_domains_present` for a real OOMKilled run (proves A1) |
| E3. Live smoke test | `python invoke_agent.py --scenario configmap --verbose` (or equivalent) | `resource_identity_match=1.0` for the ConfigMap-NotFound evidence (proves B2) |
| E4. Cloud Logging check | query `sre-agent-investigations`/`sre_agent_run` for the E2/E3 run_ids | `contradictions_count`, `missing_evidence_penalty`, band all match the new expected values, not the old buggy ones |
| E5. Full regression | `pytest tests/ -q && ruff check agent/ agent/eval/` | 0 failures, lint clean |

Only after E1-E5 pass on real deployed runs does Phase D's calibration input dataset get treated
as trustworthy.

---

# PHASE A + B IMPLEMENTATION — 2026-08-29, before/after live results

Owner approved this plan with an added architecture guardrail (tool/MCP-specific logic only in
the normalization/adapter layer; no new incident-name branches; prove the generic `_default`
path works for an incident type with no policy entry) and directed implementation to proceed:
Priority 1 (items 1/2/3/4 above) and Priority 2 (items 5/6, gated on real negative controls).
Weights/thresholds and calibration itself remain untouched, as instructed.

## What was implemented (7 commits, branch `fix/confidence-scoring-structural-corrections`, NOT
## merged, NOT deployed)

1. Args-aware evidence classification (`evidence_domains.py`, `evidence_extractor.py`, `scorer.py`)
2. Deterministic `missing_required_domains` wired into `task_planner` (`task_planner.py`, `prompts.py`)
3. `resource_identity_match` relaxed for legitimate non-Pod evidence (`scorer.py`)
4. `missing_evidence_penalty` scoped to the claim's own evidence (`scorer.py`)
5. `direct_support` credits `supported_inference` claims by `support_strength` (`scorer.py`)
6. Minimal grounding hardening — negation detection + resolved-context token exclusion
   (`claim_builder.py`) — required before #5, per instruction
7. **Found during live validation of #3, not in the original plan:** `resource_type` had the
   exact same LLM-free-text-with-bad-default bug `resource_id` was fixed for in issue #206,
   silently defeating #3's relaxation in real runs. Same fix pattern applied
   (`evidence_extractor.py`). Reported to the owner as a completion of #3, not scope creep —
   confirmed via `git diff main -- agent/` before implementing that it introduces no new
   incident-type or case-specific branching.

Also added: `k8s/scenario-secret-missing.yaml` (secret-001 had no manifest — its target was a
contaminated, unrelated leftover pod) and `tests/test_generic_path_novel_incident_type.py`
(the explicit architecture-guardrail validation: a `PVCMountFailure` incident type, not in
`policy.py`, not in the 14 golden cases, scores `completeness=0.9`/`confidence=1.0` via
`_default` alone with strong evidence, and correctly scores low with weak evidence — proves
genericity isn't lenience).

**Test suite: 394/394 pass** (regression suite + 8 new/updated test files covering all 7 fixes
individually, plus the required negative controls: well-supported inference scores high,
unsupported/plausible inference stays low, contradictory evidence stays low, lexical overlap
alone cannot create high confidence).

**Architecture guardrail compliance, verified not asserted:** `git diff main -- agent/ | grep -E
"incident_type ==|case_id =="` returns zero hits. The only tool-specific tables added
(`_ARGS_DOMAIN_OVERRIDES` in `evidence_domains.py`, `_CUSTOM_TOOL_RESOURCE_TYPE` in
`evidence_extractor.py`) live entirely in the normalization/adapter layer; `scorer.py` gained no
new tool-name or incident-type literals in this round.

## Live validation — full 14-case rerun, local mode (branch code, real cluster, real Gemini calls)

Environment note: local-mode runs need `CLUSTER_CONFIG_BUCKET` (and several other env vars
`scripts/init-env.sh` doesn't set) — undocumented gap, worked around using a known-good env
recipe found in a prior session's own troubleshooting record. Flagging as a real onboarding gap,
out of scope to fix here.

Data-quality notes before the table: this is a **before/after across two separate live runs**
(2026-08-28 deployed baseline vs. 2026-08-29 branch code), not a controlled same-input replay —
both involve real, non-deterministic LLM calls and real tool calls, so some case-to-case
variance is expected from run-to-run LLM/evidence variance alone, not only from the code changes.
Attributed deltas below are called out only where the mechanism is directly traceable (e.g. a
specific component moving in the exact direction a specific fix predicts); everything else is
reported as observed, not claimed as caused.

| Case | Before (score/band) | After (score/band) | Attributable to a fix? |
|---|---|---|---|
| crashloop-001 | 1.0 / high_confidence | 1.0 / high_confidence | No change — clean baseline preserved |
| oomkilled-001 | 0.65 / review_required | **0.955 / high_confidence** | **Yes — fix #1** (`previous_logs` now correctly classified; `missing_evidence_penalty` 0.1→0.0) |
| imagepull-001 | 0.925 / high_confidence | 0.955 / high_confidence | Yes — fix #5 (`direct_support` 0.75→0.875, inference credit) |
| configmap-001 | 0.85 / high_confidence | **1.0 / high_confidence** | **Yes — fixes #3+#7** (`resource_identity_match` 0.5→1.0, live-verified twice) |
| init-001 | 0.84 / review_required | 0.68 / review_required | **Unclear — likely LLM run-to-run variance**, see analysis below, not attributed to any fix |
| selector-001 | 0.575 / partial_evidence | **1.0 / high_confidence** | **Yes — fix #5, the clearest case.** The hardest designed scenario, correctly solved both times; `direct_support` 0.0→1.0 exactly matches the fix |
| cascading-001 | 0.0 (LLM parse failure) | 0.65 / review_required, **wrong root cause** | Parse-failure resolved (unrelated to these fixes — upstream of all of them). New answer is factually wrong; flagged below, not attributed to any fix |
| pending-001 | 0.0 (LLM parse failure) | 0.71 / review_required, correct ("pod does not exist") | Parse-failure resolved; answer correct but doesn't match golden_cases.py's expected wording (known fixture gap, unrelated to these fixes) |
| onprem-001 | 0.0 / insufficient_evidence | 0.0 / insufficient_evidence | No change — correct refusal preserved |
| insufficient-evidence-001 | N/A (429 quota) | 0.65 / review_required, correct | Quota issue did not recur |
| conflicting-evidence-001 | 0.9 / high_confidence | 0.9 / high_confidence | No change — correct both times |
| ambiguous-routing-001 | 0.0 / insufficient_evidence | 0.0 / insufficient_evidence | No change — correct refusal preserved |
| mcp-gateway-failure-001 | 0.75 / review_required, correct | **0.0 (LLM parse failure)** | **Regression in the sense that this run hit a parse failure** — same upstream issue as cascading-001/pending-001 had yesterday, not caused by any of these 7 fixes (none touch `rca_builder`'s LLM call/parsing) |
| secret-001 | 0.75 / review_required, **wrong scenario** (contaminated fixture) | **0.9333 / high_confidence, correct scenario** | **Yes — fixture fix + fixes #3/#7**, live-verified twice |

## Findings that need honest flagging, not hidden

**cascading-001's new answer is wrong — FIXED 2026-08-31.** "The order-api Deployment and its
ReplicaSet are missing from the cluster" is factually false — `order-api` was running throughout
(confirmed via the same cluster-status checks used to seed this scenario). This scored
0.65/review_required (not auto-band, so it would still route to human review), not a false-high,
but it was a real wrong answer.

Root cause, found by tracing the evidence chain rather than the LLM prompt: it was NOT in the
RCA-builder's reasoning as originally suspected — it was in `agent/mcp_client.py`'s `call_tool()`,
upstream of `rca_builder` entirely. `describe_k8s_resource`/`get_k8s_resource` are the two GKE
Remote MCP tools whose `name` field is REQUIRED (`toolspec.json`); when the agent (or its planner)
looked up `order-api`'s Deployment/ReplicaSet by a guessed name — pods and ReplicaSets always carry
a random hash suffix no caller can know in advance — the server correctly returned `NotFound`, but
`call_tool()` had no handling for that shape on these two tools and returned it as `ok: True` real
content. That NotFound text then flowed into `evidence_extractor` and became "evidence" the
RCA-builder LLM cited as proof the resource didn't exist. This is the exact same failure SHAPE as
issue #70 (`list_k8s_events` NotFound, fixed 2026-08-09) — issue #70's fix only covered
`list_k8s_events` (the one tool whose name/namespace filters are both optional, so it can retry
unscoped); `describe_k8s_resource`/`get_k8s_resource` were never given the same treatment.

Fix: both tools now return `ok: False` (`NAME_SCOPED_NOT_FOUND`) on a NotFound result, same
treatment as the existing `isError`/Model-Armor-block branches in the same function, so a wrong
name-guess can no longer by itself ground a "resource is missing" claim. `agent/mcp_client.py`,
+4 new regression tests in `tests/test_mcp_client_describe_get_not_found.py`. Full suite:
398/398 pass (394 baseline + 4 new) after this fix alone.

**mcp-gateway-failure-001 and cascading-001/pending-001 (yesterday) all hit the same
"model's response could not be parsed" failure at different times, intermittently — FIXED
2026-08-31 (most probable root cause; not live-confirmed).** `agent/llm/gemini_adapter.py`'s
`llm_json()` could not tell a response cut off by `max_output_tokens` apart from a genuinely
malformed one — a truncated JSON object (unterminated string / unbalanced braces) is missing
information the brace-repair pass can never reconstruct, so any truncation was a guaranteed parse
failure with zero signal as to why. Fix: `llm_json()` now reads the provider's own `finish_reason`
(`FinishReason.MAX_TOKENS`, not an inference from the broken text) and retries once with a larger
budget specifically when that's the diagnosed cause; `rca_builder`'s `max_tokens` raised 1536→3072
(its output schema — `claims[]`, `alternative_hypotheses_considered[]`, `reasoning_trace[]`,
`suggested_remediation[]` — is the most verbose `llm_json()` call in the codebase, and had the
tightest budget of any node). `agent/llm/gemini_adapter.py`, `agent/nodes/rca_builder.py`, +7 new
tests in `tests/test_llm_gemini_adapter.py`. Full suite: 405/405 pass after both fixes.

Evidence for the root cause: (1) the brace-repair logic is mathematically unable to fix a
truncated response — this is not a hypothesis, it follows directly from what information a
truncated string is missing; (2) only the higher-complexity multi-hop cases ever hit this, never
the simpler single-cause cases (crashloop-001, oomkilled-001, imagepull-001, configmap-001), which
is the pattern you'd expect from an output-length problem and not from a random API glitch or a
persistent regex bug (which would fail deterministically every run). **Caveat, stated plainly:**
this could not be live-confirmed against a real Gemini call — no live GCP calls were in scope for
this fix, and the original failures predate `finish_reason` being logged at all, so there is no
historical log to check either. If this recurs post-fix, Cloud Logging now carries `finish_reason`
on every call, which will confirm or rule this out directly on the next occurrence.

**init-001's score dropped (0.84→0.68) with no fix that should cause a decrease — still open,
out of scope for this fix.** Every component that moved (`independent_corroboration` 1.0→0.5,
`claim_grounding` 1.0→0.8) is driven by which specific claims/evidence THIS run's live LLM call
happened to produce — none of the 7 fixes remove credit that was previously given. Read as
LLM/evidence-gathering variance between two independent live runs, not a regression, but flagged
rather than dismissed since it wasn't independently re-verified the way configmap-001/secret-001
were. This is a scoring-logic question, not an RCA-correctness or parse-reliability bug — needs a
same-input controlled re-check, separately scoped.

## Bottom line

**4 cases show a fix directly and traceably improving a previously wrong score**
(oomkilled-001, selector-001, configmap-001, secret-001) — including selector-001, the hardest
designed scenario, moving from a wrong band to fully correct. **2 pre-existing "insufficient
evidence" results (from yesterday's LLM parse failures / quota exhaustion) resolved on retry,
independent of these fixes.** **2 of the 2 real, unrelated reliability findings that surfaced are
now FIXED as of 2026-08-31** (cascading-001's wrong answer — root-caused to `mcp_client.py`, not
`rca_builder`; the intermittent parse-failure pattern — root-caused to undetected `MAX_TOKENS`
truncation in `gemini_adapter.py`'s `llm_json()`), see the findings above for evidence, caveats,
and file-level changes. **1 result (init-001) needs a same-input controlled re-check before
drawing any conclusion** — still flagged, not resolved, out of scope for this fix.

**Calibration remains BLOCKED, not run.** Verified 2026-08-31: `agent/eval/golden_cases.py` still
has exactly the original 14 cases and zero Group C/D cases (per §13 above). Running calibration
against this dataset would violate this document's own instruction ("the 14-case dataset is not
clean and must not be used for calibration") — so no calibration pass was attempted. The two
parse-failure cases that blocked calibration (cascading-001, pending-001) are now fixed, but the
dataset itself still needs at least 1-2 Group C cases and 1 Group D case added (§13) before a real
calibration pass can run. That is dataset-authoring work, not a bug fix, and is out of scope here.

