# Confidence Framework — Final Verification Report

> PRODUCTION-LAUNCH-PLAN.md priority 2. Implemented 2026-08-04. Status at the bottom is the
> real one — read it before treating this as launch-ready.

## Status

**Implemented as a production-candidate confidence framework. Calibration and validation
against the golden incident dataset are still required before production trust decisions.**

This is not "done and shippable" — it's the deterministic engine built and proven correct
against 55 unit/integration tests, plus one real-incident worked example. It has **not** been
deployed to the live `sre-agent-t2-demo` engine or run against a real incident with this code.
That's a distinct next step requiring your go-ahead, same as the production infra change
earlier today.

---

## 1. Files changed

**Modified (7):**
| File | Change |
|---|---|
| `agent/nodes/task_evaluator.py` | Dropped LLM self-assigned confidence; added deterministic `investigation_completeness` every call |
| `agent/nodes/rca_builder.py` | Builds claims/hypotheses/contradictions from the LLM's proposal, computes `root_cause_confidence` + `outcome` deterministically, sets the final legacy `confidence`/`confidence_band` |
| `agent/main.py` | Fixed root-cause duplication (`_extract_root_cause` shared helper), added `schema_version`, new CONFIDENCE BREAKDOWN report section, added the Memory Bank write gate |
| `agent/nodes/context_resolver.py` | Added `cluster_explicitly_provided` flag (observability only — defaulting behavior unchanged) |
| `agent/prompts.py` | `TASK_EVALUATOR`/`RCA_BUILDER` prompts no longer ask the model to self-assign confidence; `RCA_BUILDER` now asks for structured `claims[]` and `alternative_hypotheses_considered[]` |
| `agent/state.py` | Docstring update only — documents the new `investigation.completeness` field |
| `pyproject.toml` | Added `pytest` dev dependency + `[tool.pytest.ini_options]` |

**New (3 dirs, 1716 lines):**
| Path | Purpose |
|---|---|
| `agent/confidence/` (7 files, 855 lines) | `policy.py`, `evidence_domains.py`, `models.py`, `scorer.py`, `claim_builder.py` + `__init__.py` |
| `tests/` (9 files, 861 lines) | 55 tests — unit tests for every scorer component + integration tests with the LLM call mocked |
| `docs/confidence-framework-design.md` | Step-1 design doc (schema + policy), written before implementation |
| `CONFIDENCE_FRAMEWORK_REPORT.md` | This file |

Not yet committed — sitting locally on `main`, matching how you've wanted to review before I
push. `git diff --stat`: 7 files changed, 276 insertions(+), 183 deletions(-) in modified files,
plus the 1716 new lines above.

---

## 2. Exact scoring formula and weights

See `docs/confidence-framework-design.md` §5–6 for the full derivation. Summary:

**Investigation Completeness** (`agent/confidence/scorer.py:score_investigation_completeness`,
100% deterministic, no LLM call):
```
score = 0.15·routing_confirmed + 0.10·identity_confirmed + 0.40·required_evidence_coverage
      + 0.10·freshness + 0.15·tool_success + 0.10·iteration_budget
```

**Root-Cause Confidence** (`score_root_cause_confidence`, deterministic given LLM-proposed claims):
```
base = 0.30·direct_support + 0.25·independent_corroboration + 0.20·resource_identity_match
     + 0.15·time_correlation + 0.10·claim_grounding

score = base − contradiction_penalty − alternative_hypothesis_penalty − missing_evidence_penalty

then hard-capped (independent of the weighted score):
  if missing required evidence:      score = min(score, 0.65)
  if any contradiction:               score = min(score, 0.65)
  if unresolved competing hypothesis: score = min(score, 0.75)
```

Every weight/penalty/cap lives in `agent/confidence/policy.py`, nowhere else — no number is
inline in `task_evaluator.py` or `rca_builder.py` anymore.

---

## 3. Policy configuration

`agent/confidence/policy.py`, `POLICY_VERSION = "1.0.0-uncalibrated"`. Validated at import time
(`POLICY.validate()` runs at module load — raises immediately on a misconfigured policy, never
silently at request time). Full weights, penalties, evidence requirements per incident type
(OOMKilled/ImagePullBackOff/CrashLoopBackOff + a light `_default`), and band thresholds are in
that file with inline rationale comments. Band thresholds (`auto: 0.85, review: 0.65`) are
unchanged from the old hardcoded values on purpose — this migration changes the *inputs* to the
score, not those specific numbers, so it's not silently making the bar harder or easier to hit.

**The `-uncalibrated` suffix is not decoration.** These are reasoned initial defaults, not
statistically fit weights. See §9.

---

## 4. New schema

Full example in `docs/confidence-framework-design.md` §9. Every `final_summary` (the
`investigate()` API response's `summary` field) now additionally carries: `schema_version`,
`outcome`, `investigation_completeness` (score/band/components/gaps),
`root_cause_confidence` (score/band/components/reasons), `claims[]`, `hypotheses[]`,
`contradictions[]`, `policy_version`, `confidence_deprecated: true`.

---

## 5. Backward-compatibility impact

Full table in the design doc §11. The short version: **nothing that currently reads this
agent's output breaks.**

| Consumer | Verified how |
|---|---|
| `iac/agent/monitoring.tf` — 3 log metrics + 2 alerts filtering `jsonPayload.confidence_band` | Field name + `auto`/`review`/`escalate` vocabulary unchanged; now computed via `confidence_band_from_scores()`, tested in `test_confidence_band_mapping_matches_legacy_three_value_vocabulary` |
| `agent/eval/run_eval.py` — reads `summary["likely_root_cause"/"confidence_score"/"confidence_band"]` | All three preserved, verified in `test_final_summary_has_all_legacy_fields_downstream_consumers_read` |
| `agent/eval/golden_cases.py` — `expected_confidence_min` thresholds (0.5–0.65) | Preserved as literal numbers; **will need recalibration once real runs happen** — expected per §9, not a break |
| `scripts/smoke_test.sh` | Loose check (`"status":"done"` or `rca_report` text) — unaffected |
| `main.py._mb_store` (Memory Bank) | **Behavior change, not a preserved behavior** — see §6, this was a real gap, not a compat concern |

---

## 6. A real gap this work closed (not just preserved)

`main.py`'s `_mb_store()` wrote every non-failed/non-blocked RCA into **persistent** Memory
Bank with **no confidence or validation gate** — despite its own docstring claiming
RCA-poisoning prevention (it only dedups by pod+incident_type). Now gated on
`confidence_band == "auto"`, which only happens for `outcome == "confirmed"`. This is a real
validation gate, not human sign-off — a genuine human-approval pipeline using the existing
but currently-unused `sre_feedback`/`validation_status` fields is a further improvement, not
built here.

---

## 7. Tests added + results

**55 tests, all passing** (`tests/`, run via
`/Users/ashmin/.pyenv/versions/3.12.2/bin/python3 -m pytest tests/ -v`):

| File | Count | Covers |
|---|---|---|
| `test_evidence_domains.py` | 6 | Tool→domain classification, GKE/custom-MCP normalization, related-domain weighting |
| `test_policy.py` | 7 | Startup validation (weight sums, band ordering, hard-cap ranges), unknown-incident-type fallback |
| `test_claim_builder.py` | 11 | Grounding (grounded/phantom/no_overlap/ungrounded), legacy-shape fallback, hypothesis filtering, contradiction detection (deterministic + LLM-flagged) |
| `test_scorer.py` | 24 | Both scoring functions across strong/weak/duplicate/contradicted/missing-evidence scenarios, outcome derivation, legacy band mapping, determinism |
| `test_rca_rendering.py` | 4 | Duplication fix specifically |
| `test_rca_builder_integration.py` | 3 | Full node with LLM mocked — schema compat, strong-vs-weak comparison, no-evidence path |

Also: `ruff check` clean on every changed/new file. `py_compile` clean on the full `agent/` +
`tests/` tree.

**Scenario coverage against your item-14 list** — most covered directly, a few honestly not:
strong OOMKilled ✓, unsupported claim ✓, wrong-cluster evidence ✓, duplicate-domain evidence ✓,
independent corroboration ✓, contradiction (both detection paths) ✓, unresolved hypothesis ✓,
tool failure/MCP timeout ✓ (via `ok=False` tool_history), max iterations ✓, unknown incident
type ✓, LLM cause unsupported/contradicted by evidence ✓, high-completeness-low-confidence and
low-completeness-high-single-evidence ✓ (implicitly, via the independent axis tests), schema
back-compat ✓, dedup rendering ✓, determinism ✓. **Not built:** wrong-pod/wrong-namespace
detection specifically (see §9 — evidence doesn't carry per-item pod/namespace today, only
cluster), a real MCP-timeout integration test (tested via failed tool_history entries, not an
actual timeout exception path), stale-evidence-outside-window as a per-item check (freshness is
investigation-level today, not per-evidence-item — see §9).

---

## 8. Old vs. new — real incident, not synthetic

I didn't spin up a fresh live call for this (that's a production action requiring your
go-ahead, same as this morning's fix). Instead: **this is the actual live RCA from earlier
today's real invoke** (`run_20260803_232356_lepa`, engine `674327732535951360`,
`imagepull-pod` / `ImagePullBackOff`), re-analyzed against the new scorer using the real
evidence from that run.

**What the OLD system produced, verbatim from that real run:**
```
confidence: 0.9, confidence_band: "auto", human_review: false
Root cause: nginx:1.2.3.4.5 image tag not found (ev_003)
Evidence note: "ev_001 and ev_002 are disregarded as they contradict the more detailed and
                conclusive evidence in ev_003"
```

**That evidence note is the whole argument for this redesign, in one real sentence.** The old
system's own LLM output *said, in its own words*, that two of its three evidence items
contradicted the third — and the scoring had no mechanism to notice. It auto-approved at 90%
confidence anyway.

**What the new system does with the same facts:** if that same self-reported contradiction had
flowed into `contradicting_evidence_ids` (which the new `RCA_BUILDER` prompt now explicitly
asks for), `detect_contradictions()` produces a `semantic` contradiction, `root_cause_confidence`
gets capped at `0.65` by `max_score_unresolved_contradiction`, and `derive_outcome()` returns
`conflicting_evidence` or at best `probable` — never `confirmed`/`auto`. Reproduced exactly as
a unit test in `test_scorer.py::test_root_cause_confidence_contradiction_caps_score_even_with_strong_base`
and `test_outcome_conflicting_evidence_when_severe_contradiction_present`.

I want to be precise about what this is and isn't: this is a **worked example proving the
mechanism catches a real case that actually happened**, not a live re-run producing a second
real API response. The live re-run is §11's next step.

---

## 9. Known limitations (not silently omitted)

- **Per-evidence timestamps don't exist yet.** `evidence_store` items don't carry a collection
  timestamp today, so `freshness` (completeness axis) is investigation-level, not per-item, and
  `time_correlation` (confidence axis) is a fixed neutral value when evidence exists, not a real
  computed correlation. Fixing this needs a small addition to `evidence_extractor.py` — not
  built here, flagged as real follow-up work.
- **No wrong-pod/wrong-namespace detection.** Evidence items carry `cluster`/`region` but not
  `pod`/`namespace` today. `resource_identity_match` and contradiction detection currently only
  catch wrong-*cluster* evidence. Same fix path as above.
- **Contradiction detection is single-pass, not adversarial.** The LLM self-reports what it
  thinks contradicts its own claim; the app scores the consequence, but nothing independently
  tries to *find* contradictions the model didn't flag. A dedicated adversarial skeptic pass
  (a second, separate LLM call whose only job is to try to break the first one's conclusion) is
  a real improvement, explicitly not built in this pass — documented in the design doc §3.
- **Evidence-domain classification is tool-name-based, not content-based.** Cheap and reliable,
  but a tool that legitimately returns evidence spanning two domains (unlikely today, possible
  with a future richer MCP tool) would be classified as one domain only.
- **golden_cases.py's `expected_confidence_min` thresholds are untouched** — they were tuned
  against the old single-float scale and will very likely need new values once real runs
  against the new scorer exist. Not a bug, just not done yet (needs real runs, see §11).

## 10. Calibration work still required

Per your item 16 — explicitly not claimed done. `POLICY_VERSION = "1.0.0-uncalibrated"` is a
real marker, not a formality. Before this policy should be trusted for real launch decisions:
run the `agent/eval/golden_cases.py` scenarios (and ideally a larger set) through the new
scorer, compare against human-validated expected outcomes, and adjust
`completeness_weights`/`confidence_weights`/penalty values based on where the initial policy
over- or under-scores relative to what an SRE would actually judge correct. This needs real
Gemini calls against real evidence, not something the pure-function test suite can do — that
suite proves the *mechanism* is correct, not that the *weights* are right.

## 11. Security and observability impact

- **Security:** the Memory Bank gate closure (§6) is a real security-relevant fix — persistent
  memory poisoning risk from unvalidated low-confidence RCAs is reduced, not just documented.
  No new external inputs, no new credentials, no new IAM surface.
- **Observability:** `_write_observability_log` (Cloud Logging) and the stdout `obs_event`
  (parsed by `monitoring.tf`) both gained new fields (`outcome`, `policy_version`,
  `investigation_completeness_score`, `root_cause_confidence_score`, `contradictions_count`) —
  additive only, verified not to collide with or rename anything `monitoring.tf` filters on.
  New Cloud Monitoring dashboards/alerts on these new fields are a natural follow-up (ties into
  PRODUCTION-LAUNCH-PLAN.md priority 4, Logging/metrics/alert validation) but not built here —
  that's explicitly the next item on your reordered list, not this one.

## 12. Rollback instructions

Nothing is committed yet, so rollback today is trivial: the changes are uncommitted local edits
on `main` plus new untracked files. `git checkout -- agent/main.py agent/nodes/ agent/state.py
agent/prompts.py pyproject.toml && rm -rf agent/confidence tests docs/confidence-framework-design.md
CONFIDENCE_FRAMEWORK_REPORT.md` fully reverts. Once committed/merged: revert the merge commit
(single PR, single revert) — this was built as one coherent change specifically so it reverts
as a unit, not a tangle of interdependent commits.

## 13. Explicitly not done in this pass (out of scope for "confidence redesign")

- Deploying this to the live `sre-agent-t2-demo` engine and re-running a real investigation
  against it.
- Recalibrating `golden_cases.py`'s expected-confidence thresholds against real new-scorer runs.
- Building the adversarial contradiction pass, per-evidence timestamps, or pod/namespace-level
  identity matching (§9).
- Anything from PRODUCTION-LAUNCH-PLAN.md priority 3 onward (routing, logging/alerts, on-prem,
  custom MCP, etc.) — this stays scoped to priority 2 only, per "do not make unrelated
  architectural changes."
