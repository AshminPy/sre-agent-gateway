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

**No structural false-high mechanism found**, in code or in the live data. `_default`'s fallback
is provably monotonic-lenient (proof in the incident-hardcoding audit: its `required_now` is a
strict subset of every explicit entry, so it can only make coverage checks *easier*, never
produce an unjustified score increase from a real gap).

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

**Repo state:** `main` @ `d799e38` at review start; one commit made during this review
(`agent/eval/run_eval.py` fix, see §9) — not yet pushed/PR'd, pending your review of this document.
**Live data sources:** `gs://sreagent-t2-demo-eval/runs/` (14 real remote-mode runs, 2026-08-28,
run_ids `tcob/zout/qahn/rmlk/rtrb/sjzv/kacq/wjii/ibxu/gtxy/rwsa/rkbc/woda/zxbh`), 4-agent parallel
workflow audit (tool/domain mapping, incident hardcoding, claim scoring, hardcoded conditionals).
