# Current State — the canonical operational source of truth

**Last updated:** 2026-08-30
**Owner:** SRE Agent platform team
**Role:** This is the single place to answer "where are we now, what's done, what's blocked,
what remains, what's next." It summarizes conclusions and links to the detailed evidence
rather than duplicating it. For task-level detail, use
[`PROJECT_TRACKER.xlsx`](PROJECT_TRACKER.xlsx) (167 rows, 5 status tabs — the authoritative
day-to-day task tracker). For architectural decisions, use the [ADRs](../README.md#status-and-decisions)
(still authoritative, never duplicated here). For the ranked gap list, use
[Risks and Limitations](risks-and-limitations.md).

---

## 1. Current architecture

Two-project split (agent + gateway vs. GKE cluster) behind a Google Cloud Agent Gateway,
LangGraph-orchestrated, read-only by design. Full detail: [System Overview](../architecture/system-overview.md)
and the rest of [`docs/architecture/`](../README.md) (17 pages, current). Do not duplicate here.

## 2. Current production / non-production state

- **Live engine:** `sreagent-t2-demo`, Agent Gateway bound natively via Terraform (`google_vertex_ai_reasoning_engine.sre_agent`'s `agentGatewayConfig` — the gateway binding is Terraform-managed since PR #93, 2026-08-10; no manual re-attach step needed after a normal apply).
- **Model Armor:** both floor settings (`google_mcp_server`, `ai_platform`) are `inspect_only = true` (`iac/agent/model_armor.tf:218-237`). `inspect_and_block` was tried once (2026-08-25) and reverted the next day after two real false positives (one fabricated an RCA, one crashed a run). **Standing precondition gate before re-enabling** (verbatim from the Terraform's own comment, `model_armor.tf:190-195`): a week of `MATCH_FOUND` log entries reviewed with zero false positives on real SRE traffic; `pi_and_jailbreak` block-tested specifically; SDP block-tested at all. None of the three currently hold.
- **App-level Model Armor** (`SREAgent._sanitize()`) — **fixed 2026-08-31, PR pending merge (#203).** `MODEL_ARMOR_TEMPLATE` is now set unconditionally (`iac/agent/agent_engine.tf`), not gated on the Agent Gateway being off — that gate's premise (gateway-side CONTENT_AUTHZ) was confirmed never wired (`agent_gateway.tf`'s own header). #30 (the hostname blocker) is fixed and live-verified for `project_a_id`/`sreagent-t2-demo` (this deployment's target); it remains open/reopened only for the unrelated `project_b_id` Agent Registry, which this repo's CI never touches (`register_endpoints.py` runs against `GCP_PROJECT_A_ID` only). Deployed through the normal CI `terraform-apply` + smoke-test path, not applied locally. **Not yet live-validated end to end** for the custom-MCP path specifically — that requires the custom-MCP Cloud Run service to be network-reachable through the gateway, a separate not-yet-approved networking workstream (see the Custom/fallback MCP line below).
- **Custom/fallback MCP traffic** now has a working app-level Model Armor template (as of the fix above) via `agent/mcp_client.py`'s `_sanitize_custom_mcp_response` (PR #223), gated to run only on the non-GKE-Remote branch so GKE Remote MCP's existing floor-setting coverage isn't double-inspected. Google's floor-setting API still has no custom-Cloud-Run integration type, so this remains the only coverage this path can ever get at the infra layer. **Still not live-proven**: no real tool call has yet been routed through the gateway to the custom-MCP service to confirm inspection actually fires end to end — blocked on the same networking gap. Full detail: `archive/SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md`.

## 3. Completed capabilities

See [Implemented vs Planned Matrix](implemented-vs-planned-matrix.md) — the current capability
truth, every capability ✅/🟡/🔵/❌ with file:line or live-command evidence. Not duplicated here.

Most recent completions (tracker `Completed` tab, 64 rows total): the 16-gap agent-integrity
review (PR #204, 2026-08-27, live-verified — see `archive/RESOLVED_2026-08-27_agent-integrity-review.md`),
7 confidence-scoring structural fixes (PR #219, 2026-08-30), issues #206/#209/#211 (confidence
resource-identity, query-threading, sidecar-logs bugs), and issue #85 (custom Cloud Run MCP
fallback confirmed operational).

## 4. In-progress work

See the tracker's `In Progress` tab (51 rows) for full detail. Notable active threads:
Model Armor controlled validation (tracker row 122, ties #30/#32/#203), supply-chain/CI
hardening (row 123, ties #79), Console Traces telemetry (row 154, ties #130/#136/#164/#165).

## 5. MUST FIX

Materially blocks correctness, security, reliability, production readiness, or an approved
requirement:

- **#30 — Model Armor endpoint hostname mismatch.** Code hardcodes the wrong hostname
  (missing `.rep.`) in the Agent Registry. Small, scoped, unblocked. **Unblocks #203.**
- **#86 — Multi-cluster support is single-cluster in disguise.** Real correctness bug, not
  just a missing feature: for anything other than the default cluster, a single
  `@lru_cache(maxsize=1)` K8s client and single-scalar IAM mean the agent silently
  misbehaves. Secondary concern: overly broad Agent Engine IAM (project-wide
  `principalSet`). Large scope — a genuine redesign, not a quick fix.
- **#35 → downgraded, see NICE TO HAVE.** (Verified: the affected script is emergency-only,
  not auto-invoked in CI — lower blast radius than the title implies. See M017-style
  re-check below.)

## 6. BLOCKED

Valid work that cannot currently proceed:

- **#202 — Block-mode diagnostic for PR #199's fix.** BLOCKED while the Model Armor
  precondition gate (§2 above) remains unmet — flipping `inspect_and_block` live has
  twice already caused a real incident. Do not attempt until the 3 preconditions hold or
  a human explicitly overrides the gate.
- **#203 — MODEL_ARMOR_TEMPLATE dead-code path.** BLOCKED by #30 (explicit, in the issue's
  own text: "Do not enable this before #30 is fixed").
- **#89 — No E2E routing-safety test suite.** BLOCKED — explicitly depends on #86 and #88,
  both open.
- **Tracker `Blocked` tab (4 rows, tracker-internal IDs — NOT GitHub issue numbers, do not
  conflate):** service/user-impact detection (row 35), full compiled-graph E2E test (row 51),
  entrypoint→scorer E2E eval (row 52), deploy custom K8s MCP to cloud (row 86).

## 7. NICE TO HAVE

Improvements that must not block completion:

- **#164** — Console Traces dashboard display (root cause already fixed by PR #165; live
  telemetry itself is intact, only the Console UI is affected).
- **#95** — Real service-status/user-impact check for the RCA report.
- **#88** — PagerDuty integration (tracker treats this as its own future phase, not a
  completion gate).
- **#84** — Unused VPC/NAT Terraform resources (needs a keep-or-delete decision, not urgent).
- **#82** — No full-graph E2E test; human-feedback loop fields unwired (known, disclosed via
  ADR-010, not a silent risk).
- **#81, #80** — Eval-tooling gaps (keyword-matching pass/fail, dataset drift) — affect QA
  confidence, not runtime correctness.
- **#79** — Supply-chain/CI hardening (SHA-pinning partially done per a 2026-08-27 correction;
  lockfile and root-Docker-user sub-items remain, real but not actively exploited).
- **#35** — Error-swallowing in `attach_gateway_to_engine.sh`'s pre-flight checks. Downgraded
  from an earlier draft of this doc: the script is emergency-only (not auto-invoked since
  PR #93), so the blast radius is much smaller than the title suggests.
- **#33** — Agent Registry endpoints have no Terraform representation (needs a documented
  decision: intentional-imperative vs. add drift detection).

## 8. Active security/reliability decisions

- **Model Armor precondition gate** — see §2. The single most important standing decision
  right now; do not re-enable `inspect_and_block` without satisfying all 3 conditions or an
  explicit human override.
- **No local `terraform apply`, ever** (2026-08-25 incident: a local apply against the shared
  GCS backend caused real undocumented drift). Every infra change: branch → PR → CI plan →
  merge → CI apply.
- **Personal-repo auto-merge convention**: PRs are opened, diff-verified, and merged without
  a separate "merge it?" round-trip — this is why PR #219's cautious title didn't block its
  merge (see Known Documentation Notes below).

## 9. Known limitations

Canonical ranked list: [Risks and Limitations](risks-and-limitations.md). Notable additions
from this consolidation pass: custom/fallback MCP traffic is not Model Armor-inspected (§2).

**2026-08-31 update — 2 of PR #219's 3 unresolved loose ends fixed, 1 still open:**
- **`cascading-001`'s wrong RCA — FIXED.** Root cause: `agent/mcp_client.py`'s `call_tool()`
  treated a name-scoped `NotFound` response from `describe_k8s_resource`/`get_k8s_resource`
  as real evidence (`ok: True`) — same failure SHAPE issue #70 already fixed for
  `list_k8s_events`, just on the two GKE Remote MCP tools whose `name` field is required and
  therefore can't be retried unscoped. A guessed/wrong resource name (Deployments' pods and
  ReplicaSets carry a random hash suffix no caller can know in advance) 404'd, and that
  NotFound text became "evidence" the RCA-builder LLM cited as proof `order-api` didn't
  exist — when it was running the whole time. Fix: both tools now return `ok: False` on a
  NotFound result, same treatment as the existing `isError`/Model Armor-block branches, so a
  wrong name-guess can no longer by itself ground a "resource is missing" claim. See
  `docs/management/confidence-genericity-review-2026-08-28.md` for the full writeup.
- **Intermittent LLM-response-parse failure — FIXED (most probable root cause; not
  live-confirmed, see caveat below).** `agent/llm/gemini_adapter.py`'s `llm_json()` could not
  tell a response truncated by `max_output_tokens` apart from a genuinely malformed one — a
  truncated JSON object (unterminated string/unbalanced braces) can never be repaired by the
  brace-matching logic, so any truncation was a guaranteed parse failure with no signal as to
  why. Fix: `llm_json()` now reads the provider's own `finish_reason` and (a) retries once
  with a larger token budget specifically when `finish_reason=MAX_TOKENS`, and (b) raised
  `rca_builder`'s `max_tokens` 1536→3072 (its output schema is the most verbose `llm_json()`
  call in the codebase — the tightest budget on the biggest schema). **Caveat:** this could
  not be live-confirmed against a real Gemini call (no live GCP calls in scope for this fix)
  — the evidence is: (1) the brace-repair logic is mathematically unable to fix a truncated
  response, so if truncation ever happens it is a 100% guaranteed failure; (2) only the
  higher-complexity multi-hop cases (`cascading-001`, `pending-001`, `mcp-gateway-failure-001`)
  ever hit this, never the simpler single-cause cases, consistent with an output-length
  problem rather than a random API glitch. If this recurs post-fix, Cloud Logging now carries
  `finish_reason` on every call, which will confirm or rule this out directly.
- **`init-001`'s unclear score change (0.84→0.68) — still open**, needs a same-input
  controlled re-check per PR #219's own report; out of scope for this fix (scoring-logic
  question, not an RCA-correctness or parse-reliability bug).

**Calibration — BLOCKED, unchanged.** `agent/eval/golden_cases.py` still has all 14 original
cases and zero Group C/D cases (verified 2026-08-31: `grep -c '"id":'` → 14, same as before).
Per `confidence-genericity-review-2026-08-28.md` §13, calibration needs at minimum: 1-2 Group C
cases (evidence that plausibly points to the wrong culprit — e.g. a cascading-failure-shaped
scenario where naive investigation finds the downstream symptom and a correct agent must trace
to the real upstream cause) and 1 Group D case (evidence sparse/ambiguous enough to tempt a
fabricated-sounding causal chain, to test whether contradiction/grounding checks catch it). Not
created here — that is new dataset-authoring work, not a bug fix, and remains a separate,
explicitly scoped follow-up.

## 10. Validation / evidence links

- Agent-integrity review (16 gaps, PASS, live-verified): `archive/RESOLVED_2026-08-27_agent-integrity-review.md`
- Confidence-scoring structural fixes + corrections addendum: `docs/management/confidence-genericity-review-2026-08-28.md`
- Model Armor floor-setting history (superseded snapshots): `archive/SUPERSEDED_2026-08-25_model-armor-management-report.md`, `archive/SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md`
- Terraform 1.4.7 / network-grant removal (status: PARTIAL — CI smoke test still red for an unrelated, pre-existing reason): `docs/management/rca-2026-08-26-tf147-and-network-grant-removal.md`

## 11. Known documentation notes (this consolidation pass)

- `PROJECT_TRACKER.xlsx` row 23 ("Test Model Armor with Agent Identity, IAP, Agent Gateway")
  is marked Completed but its own Notes describe unmerged work — should read In Progress.
  Flagged, not yet corrected in the tracker itself.
- `PRODUCTION-LAUNCH-PLAN.md` and `docs/management/floor-settings-production-plan-2026-08-25.md`
  are cited by 23 and 1 live code/Terraform comments respectively — kept in place rather than
  archived, even though their day-to-day status role has moved to this document and the tracker.
- `README.md`'s troubleshooting section still instructs manually re-running `make attach-gateway`
  after every apply — stale per §2 above (PR #93 made this automatic). Flagged for a follow-up
  fix, not corrected in this pass.

## 12. Next highest-priority deliverable

**#202 is BLOCKED (§6) — not next.** The highest-priority **UNBLOCKED** deliverable is
**#30** (Model Armor endpoint hostname mismatch): small, scoped, no dependencies, and it
unblocks #203. #86 is the highest-priority unblocked item if a larger, security-relevant
redesign is preferred instead.
