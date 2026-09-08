# Final Report — "Claude Code — SRE Agent Gateway correction and expansion instructions"

Branch: `phase1-final-readiness-review` · Base main: `dd84660fdd177256be0c2af29514599199b55bca`
Current HEAD: `be1664325aafaffd13c0cc22ad0b3227360618de`
**Not merged to main. Not deployed to production. Live-tested only against the personal test project `sreagent-t2-demo`.**

## Section-by-section findings

| Section | Status | Evidence |
|---|---|---|
| 1. Objective/scope, re-establish truth | DONE | Verified via `gh pr list`/`gh issue view` and direct code reads throughout — see individual section entries below. |
| 2. Re-establish current truth | DONE | Confirmed live: #86 was NOT yet fixed at the start of this work despite an earlier "no code fix" verdict; the real, already-live-validated `sre-lab` connection at session start used the OLD single-global-context mechanism, not the registry Section 5 assumed was already live. |
| 3. Loop safety fix | FIXED | `agent/nodes/loop_controller.py` — hard time/token budgets now always win over "enough evidence." Commit `bc06135`/earlier. 8 new tests. |
| 4. Finish #246 | FIXED (already substantially done, confirmed) | Cluster-scoped tools no longer receive namespace. Live-tested. |
| 5. Multi-cluster isolation redesign | FIXED, LIVE-VERIFIED | Per-cluster `@lru_cache(maxsize=32)`, dynamic Connect Gateway (no static kubeconfig), generic plug-and-play dispatch. Commits `14eb6b1`, `1ad31d2`, `3cd8688`, `7b72f9c`, `7c730ea`, `4b4c2ae`, `bc35959`. Real pod created on `sre-lab`, retrieved correctly through the deployed service end to end. |
| 6. Capability-based MCP selection | FIXED (Prometheus scaffold; not live) | `agent/source_catalog.py` + `agent/sources/prometheus_adapter.py`. Zero behavior change while disabled (proven by test). Prometheus itself: catalog + adapter + fixture tests only — **NOT VERIFIED live** (no real instance exists here), exactly as the section's own instruction permits. |
| 7. Causal verification/remediation | FIXED | Temporal-relevance dead code closed (CONFIRMED outcome was structurally unreachable before this — real, not hypothetical). Wording bug fixed. Remediation restructured with deterministic cause-tying. Full differential-hypothesis reasoning explicitly deferred (disclosed, not hidden — judged too large/risky for this pass). |
| 8. Content-safety/memory lifecycle | FIXED | Response-guard init failure now alertable. Inspection status propagates to evidence and gates memory promotion. Real memory review-status lifecycle built (`scripts/review_memory.py`) — ADR-010's own "NOT YET IMPLEMENTED" gap closed. |
| 9. Model portability/capacity | FIXED | Concurrent-run accounting isolation (real bug, proven via live multi-threaded test). 5xx retry added. `max_instances=10` set (was unset; live value was `0`). Fake-provider adapter contract already existed — verified, not rebuilt. Second real vendor: correctly NOT invented (no target/credentials given). |
| 10. Evaluation/CI | FIXED | FastMCP drift root-caused and fixed, live-verified against the real cluster (6/7 pass; 7th is an identity-scope mismatch, not a defect). Dependencies exact-pinned. pip-audit run for real, scoped correctly; all 9 real findings confirmed NOT runtime-exercised via grep evidence, not assumption. Terraform test gate's silent no-op made loud, not silently accepted. 2 required scenario families addressed (1 real case added, 1 explicitly documented as already-covered-elsewhere rather than faked). Dataset-drift docstring corrected. |
| 11. Docs/ops | FIXED | 6 stale "current state" claims corrected with evidence. 2 new alerts (1 added, 1 researched-but-not-fabricated). 2 new runbooks. Dead link + stale Arc A status row fixed. OTel span-naming gap disclosed, not fixed (scoped lower priority). |
| 12. This report | DONE | This file. |

## What was ALREADY FIXED before this work started (not re-done, verified first)

- The `LLMClient` adapter abstraction and fake-provider contract tests (Section 9).
- The deterministic-gap → planner disconnect (`task_planner.py` reading `missing_required_domains`) — confirmed already implemented from an earlier session, per a prior internal review doc.
- REQUEST_AUTHZ fail-closed and the custom-MCP response guard (PR #249) — confirmed intact, not reopened.

## Accepted limitations (real, permanent, or explicitly deferred — not hidden)

- GKE Remote MCP has no response-body inspection equivalent to `response_guard.py` — a real, permanent Google platform limitation, already well-documented across multiple docs.
- Model Armor floor settings remain `inspect_only` (not block mode) — correct, unchanged posture; the 3-condition precondition for ever changing this still doesn't hold.
- A second real LLM vendor: not implemented — no target/credentials specified, per the section's own explicit instruction not to invent either. Concrete 7-step checklist documented instead (`docs/architecture/llm-adapter.md`).
- A genuine concurrent-load capacity report — not done; the section explicitly warns against inferring this from a sequential campaign. Real load-testing work for later.
- Connect Gateway `DATA_READ` audit logging — off, extensively pre-documented as an accepted, owner-level decision, not an oversight.
- Full differential-diagnosis (multi-hypothesis) evidence targeting in the planner — the narrower, already-scoped fix works; the broader version judged too large a change to force into this pass without a dedicated design review.
- OTel span naming for the new Connect Gateway/capability-dispatch code paths — falls into existing generic spans; `mcp/server.py` has zero OTel instrumentation at all.

## Blocked (with concrete reason)

- None outstanding at time of writing. The one live-verification gap encountered mid-session (my personal identity lacking Connect Gateway RBAC) was resolved by testing through the real deployed service's own identity instead, not left blocked.

## Unverified (explicitly, not silently assumed)

- Prometheus catalog/adapter — built, unit-tested, **not run against a live instance**.
- A second on-prem cluster's dynamic Connect Gateway path — the mechanism is live-verified for `sre-lab`; a genuinely different second cluster hasn't been onboarded to prove the "zero code change" claim against a truly new cluster.
- The Cloud Run/Agent Engine `max_instances=10` ceiling — set deliberately, not derived from a real load test.

## Cluster / source / model matrix

| | Status |
|---|---|
| GKE clusters (via GKE Remote MCP) | Fully plug-and-play — new project + registry entry, zero code, verified via `iac/gke-access`'s generic module design. |
| On-prem/custom clusters (via custom MCP + Connect Gateway) | Fully plug-and-play, dynamic connection (no static kubeconfig) — live-verified for `sre-lab`. |
| Additional data sources (Prometheus, Elastic, Grafana, git MCP) | Generic catalog + adapter mechanism proven (fake second source in tests); Prometheus is the only scaffolded real adapter, disabled, not live. |
| LLM models | Gemini `2.5-flash`/`2.5-pro`, config-only switch. No second vendor. |

## Verification summary (this work, all commits on this branch)

- Full agent test suite: 571 passed, 0 failed (final count after Section 11).
- `mcp/tests/` (mocked): 85 passed, 0 failed. Live cluster tests: 6/7 pass for real (7th is a documented identity-scope mismatch).
- `terraform validate`: clean throughout. Live `terraform plan` after every infra change: 0 destroyed, every diff explained.
- ruff: clean throughout.
- Real live end-to-end proof for the multi-cluster migration (Section 5): a real pod created on the real `sre-lab` cluster, correctly retrieved through the actually-deployed Cloud Run service.

## Required remaining decisions (stated once, exact impact)

1. **Deploy this branch's code to the live Agent Engine.** All Terraform changes are plan-verified (0 destroys) but the Agent Engine's own source code has not been repackaged/redeployed with today's Sections 7-11 fixes — only the MCP image and Terraform config changes from earlier in the session are live. Impact if not done: the live agent keeps running older code; none of Sections 7-11's fixes take effect in production until this happens.
2. **Merge to main.** Explicitly out of scope for this task per its own instructions — no impact from leaving this undone; a deliberate choice, not a gap.
3. **Real second on-prem cluster or LLM vendor.** No target/credentials given — correctly not started. Impact if never provided: those two "expansion deliverable" claims stay unverified beyond the mechanism-level proof already done.
