# Current State — the canonical operational source of truth

**Last updated:** 2026-09-06
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
- **Model Armor CONTENT_AUTHZ (corrected 2026-09-06 — the finding below this bullet through 2026-08-30 was wrong, root cause was the wrong service hostname, not a platform block):** real and live. `google_network_services_authz_extension.model_armor` + `google_network_security_authz_policy.model_armor` (`iac/agent/model_armor.tf`), using the regional hostname `modelarmor.us-central1.rep.googleapis.com`, both templates `enforcement_type = "INSPECT_AND_BLOCK"`. Live-verified: genuine REQUEST-body inspection and blocking. **Confirmed platform limitation**: RESPONSE-body inspection never fires for MCP `tools/call` responses on either MCP source — Google's own docs list "Streamable HTTP/SSE for MCP" as excluded from gateway sanitization, and this is the MCP spec's own current transport with no viable non-streaming remote alternative. Tried and reverted a `json_response=True` fix on the custom MCP — no measurable change, confirming this is transport-level, not fixable in our Terraform/code.
- **Model Armor floor settings** (`google_mcp_server`, `ai_platform`) remain `inspect_only = true` (`iac/agent/model_armor.tf`) — a SEPARATE mechanism from CONTENT_AUTHZ, detect-only, never blocking. Fires on GKE Remote MCP requests and the agent's own Gemini calls; does NOT and cannot protect arbitrary custom-MCP response content. `inspect_and_block` was tried once (2026-08-25) and reverted the next day after two real false positives — the precondition gate for re-enabling it (a week of zero-false-positive `MATCH_FOUND` review) still doesn't hold.
- **App-level Model Armor** (`SREAgent._sanitize()`) is still dead code in every real deployment — gated on the Agent Gateway being off, and the gateway defaults on. This is a DIFFERENT, unrelated mechanism from CONTENT_AUTHZ above (it only ever covered the agent's own query/summary text, never MCP tool traffic).
- **NEW, unmerged POC (2026-09-06)**: `mcp/response_guard.py` (branch `poc/mcp-response-guard-model-armor`) calls Model Armor directly from the custom MCP server, closing the CONTENT_AUTHZ response-side gap for that source specifically. Live-proven to genuinely block a real malicious response with real log evidence. Fail-open-on-Model-Armor-error is an unresolved, unapproved production decision. Not merged.
- **REQUEST_AUTHZ/IAP fail-open**: `authz_fail_open = true` default remains on `main`. A validated fail-closed fix exists, unmerged (`fix/content-authz-json-response-and-fail-closed`) — live-tested revoke/retry showed correct denial, no bypass.
- **Custom/fallback MCP is deployed and reachable** (fixed 2026-09-04 — ingress `INGRESS_TRAFFIC_ALL`, connectivity env vars set). It is also the production path for non-GKE/on-prem clusters via Connect Gateway, proven end-to-end against `sre-lab`. Full detail: [MCP Architecture](../architecture/mcp-architecture.md), [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md). The original 2026-08-25 finding that this traffic isn't Model Armor-inspected is superseded by the more precise CONTENT_AUTHZ response-side finding above — see `archive/SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md` for the original, now-superseded evidence.

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

- **#30 — Model Armor endpoint hostname mismatch, PARTIALLY fixed.** `sreagent-t2-demo`
  fixed and live-verified 2026-08-31 (§10). `sreagent-demo` still has the bug — the same
  fix fails live there with a real "URL already in use by another service" error, a
  previously-unknown platform constraint. Small in scope, but now blocked on
  understanding that constraint before a second attempt — see §10 for the exact error.
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
- **#203 — MODEL_ARMOR_TEMPLATE dead-code path.** #30 is only partially fixed
  (`sreagent-t2-demo` yes, `sreagent-demo` no — see §5/§10) — its precondition does not
  yet fully hold. Even once it does, enabling `MODEL_ARMOR_TEMPLATE` remains a separate,
  deliberate decision gated on the same Model Armor precondition gate as #202 (§2 above).
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
  `archive/RESOLVED_2026-08-28_confidence-genericity-review.md` for the full writeup (archived
  2026-09-06 — the fixes described are merged and live, only the standalone report is archived).
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

**Calibration — UNBLOCKED as of the dataset, not yet independently re-verified as a full calibration pass.** `agent/eval/golden_cases.py` now has 16 cases (verified 2026-09-06: `grep -c '"id":'` → 16, up from 14) — Group C/D cases have been added since the 2026-08-31 check below was written. Whether a full calibration run against these new cases has been performed is not confirmed by this pass; check `agent/eval/` output directly before relying on this. Per `archive/RESOLVED_2026-08-28_confidence-genericity-review.md` §13, calibration needs at minimum: 1-2 Group C
cases (evidence that plausibly points to the wrong culprit — e.g. a cascading-failure-shaped
scenario where naive investigation finds the downstream symptom and a correct agent must trace
to the real upstream cause) and 1 Group D case (evidence sparse/ambiguous enough to tempt a
fabricated-sounding causal chain, to test whether contradiction/grounding checks catch it). Not
created here — that is new dataset-authoring work, not a bug fix, and remains a separate,
explicitly scoped follow-up.

## 10. Validation / evidence links

- **#30 — Model Armor endpoint hostname fixed for `sreagent-t2-demo` ONLY (2026-08-31,
  live-verified). `sreagent-demo` (`project_b_id`) has the identical bug, unresolved —
  do not read this as fixed on both engines.** Root cause: `scripts/register_endpoints.py`'s
  generic `regional_only` pattern didn't know Model Armor needs a `.rep.` segment. Fix:
  `_REGIONAL_INTERFACE_HOSTNAME_OVERRIDES` corrects the registered interface URL while
  leaving resource-name derivation (and therefore the live resource ID,
  `us-central1-modelarmor-us-central1`) unchanged — an in-place `services update`, not a
  delete+recreate. Live `describe` on `sreagent-t2-demo` after the fix:
  `url: https://modelarmor.us-central1.rep.googleapis.com`, same resource name, same
  `registryResource` id as before.
  **`sreagent-demo` blocked by new evidence, not yet resolved:** the identical
  `services update` call against `sreagent-demo` fails live —
  `"Interface URL 'https://modelarmor.us-central1.rep.googleapis.com' is already in use
  by another service"` — a real, previously-unknown Agent Registry constraint (apparent
  cross-project interface-URL uniqueness). This was not anticipated by the original fix
  and needs its own investigation before a second attempt (see issue #30's follow-up
  comment). The `-mtls` variant is explicitly untouched on both projects (no verified
  correct hostname shape for it yet). `MODEL_ARMOR_TEMPLATE` remains unset on both
  engines — this fix is registration-only, does not enable Model Armor.
- Agent-integrity review (16 gaps, PASS, live-verified): `archive/RESOLVED_2026-08-27_agent-integrity-review.md`
- Confidence-scoring structural fixes + corrections addendum: `archive/RESOLVED_2026-08-28_confidence-genericity-review.md`
- Model Armor floor-setting history (superseded snapshots): `archive/SUPERSEDED_2026-08-25_model-armor-management-report.md`, `archive/SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md`
- CONTENT_AUTHZ/REQUEST_AUTHZ real-traffic investigation (2026-09-05/06 — the finding that supersedes the two entries above): `PHASE1_EVIDENCE_LOG.md`
- Terraform 1.4.7 / network-grant removal (status at archival: PARTIAL — CI smoke test was red for an unrelated, pre-existing reason; not re-verified since): `archive/RESOLVED_2026-08-26_rca-tf147-and-network-grant-removal.md`

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

**#202 is BLOCKED (§6) — not next.** #30 is unblocked but only half-done — finishing
`sreagent-demo` needs the cross-project URL-uniqueness constraint understood first (§10).
**#86** (multi-cluster support is single-cluster in disguise) is the highest-priority
UNBLOCKED item with no open questions blocking it.
