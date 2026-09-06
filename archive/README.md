# Archive — historical investigations and superseded reports

Everything in this folder is **closed, historical work** — kept for reference and
audit trail, not for day-to-day use. If you're looking for the current state of the
project, these are **not** the files to read:

- **Current operational state (start here)** → [`../docs/management/CURRENT-STATE.md`](../docs/management/CURRENT-STATE.md)
- Detailed task-level tracker → [`../docs/management/PROJECT_TRACKER.xlsx`](../docs/management/PROJECT_TRACKER.xlsx) (actively updated; `PRODUCTION-LAUNCH-PLAN.md` below is not)
- Deep per-item research (NOT a status doc) → [`../NEXTSTEPS.md`](../NEXTSTEPS.md)
- Current architecture/operations knowledge base → [`../docs/README.md`](../docs/README.md)
- Current implementation status (what's really done vs planned) → [`../docs/management/implemented-vs-planned-matrix.md`](../docs/management/implemented-vs-planned-matrix.md)
- `../PRODUCTION-LAUNCH-PLAN.md` — kept in place (cited by 15+ live code/test comments as "Priority N"
  provenance) but its day-to-day status role has moved to `CURRENT-STATE.md` and the tracker above;
  1 commit since 2026-08-20 vs. 11 to the tracker in the same window — treat it as historical framing, not current status.

## Naming convention

- **`RESOLVED_<date>_...`** — a real investigation that reached a confirmed fix.
  The finding is done; nothing here should still be "in progress" anywhere else.
- **`SUPERSEDED_<date>_...`** — content that was accurate when written, but has
  since been replaced by newer, more current documentation elsewhere in this repo
  (the newer location is noted below).

## Index

| File | What it was | Status | Superseded/replaced by |
|---|---|---|---|
| `RESOLVED_2026-07-17_AUDIT_REPORT.md` | 49-check audit of the Agent Gateway binding failure | Resolved 2026-07-17 | — |
| `RESOLVED_2026-07-17_CURRENT_STATE.md` | Working-memory scratch file for that same investigation | Resolved 2026-07-17 | — |
| `RESOLVED_2026-07-17_TROUBLESHOOTING_LOG.md` | Full evidence log for that same investigation | Resolved 2026-07-17 | — |
| `RESOLVED_2026-07-17_RCA_REPORT.md` | Management-facing RCA for that same investigation | Resolved 2026-07-17 | — |
| `RESOLVED_2026-07-17_FINAL_RCA.md` | The mTLS certificate-verify root cause (separate issue, same window), plus a 2026-08-07 re-verification addendum | Resolved 2026-07-17, re-confirmed 2026-08-07 | — |
| `RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md` | Live test proving there's no working Terraform path to wire Model Armor CONTENT_AUTHZ to Agent Gateway | Resolved/reverted 2026-08-08 | Conclusion folded into `NEXTSTEPS.md` and `docs/governance/security.md` |
| `SUPERSEDED_2026-08-04_MODEL_ARMOR_GOOGLE_SUPPORT_CASE.md` | Draft Google Cloud support case | Superseded — the case was filed and Google responded | Google responded. A follow-up response draft dated 2026-08-07 exists (`../GOOGLE_SUPPORT_RESPONSE_DRAFT_2026-08-07.md`, still in the repo root); subsequent send/case status is not verifiable from the repository. |
| `SUPERSEDED_2026-08-04_CONFIDENCE_FRAMEWORK_REPORT.md` | Confidence framework verification snapshot | Superseded — predates today's audit | `../docs/architecture/confidence.md`, `../docs/ADR-007-two-confidence-dimensions.md`, `../docs/ADR-008-confidence-not-accuracy.md` |

## Added 2026-08-20 (source-of-truth cleanup)

| File | What it was | Status | Superseded/replaced by |
|---|---|---|---|
| `SUPERSEDED_2026-08-20_architecture-overview.md` | The original single-page architecture doc (2026-07-12) | Superseded — described Model Armor as an active inline filter, which it is not | `../docs/architecture/` (17 pages), entry `system-overview.md` |
| `SUPERSEDED_2026-08-20_architecture-diagram.png` / `.mmd` | Render + Mermaid source for the page above | Superseded — same reason, plus predates the PR #93 gateway migration | Mermaid diagrams inline in `../docs/architecture/system-overview.md` |
| `SUPERSEDED_2026-08-20_sre-agent-architecture.svg` / `.drawio` | Detailed architecture diagram (2026-07-18) | Superseded — predates the 2026-08-10 Agent Gateway Terraform migration and the August tracing rework | `../docs/architecture/` |
| `RESOLVED_2026-08-20_phase2-merge-gate-test.md` | Throwaway PR vehicle used to test `claude-merge-gate.yml` | Resolved — the file says so itself: "Safe to delete after the test" | — |
| `RESOLVED_2026-08-20_google-support-case-reasoning-engine-timeout.md` | Draft Google support case for the undocumented ~300s Reasoning Engine timeout | Resolved — issue #103 was fixed 2026-08-15 via native `stream_query()` and live-validated at 315.3s, so the case is moot. Never filed. | `../docs/management/implemented-vs-planned-matrix.md` (2026-08-20 delta section) |

**Historical conclusions above are still valid as history.** The architecture files
correctly described the system as it was on their own dates — they are archived because
the system changed, not because they were wrong.

Moved here 2026-08-09 as part of a repo-wide documentation currency review — see
`SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md` for that review's full findings.

## Added 2026-08-30 (documentation consolidation)

| File | What it was | Status | Superseded/replaced by |
|---|---|---|---|
| `SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md` | Self-labeled "HISTORICAL SNAPSHOT" doc-currency audit (2026-08-08/09 coverage) | Superseded — was already self-disclaiming, moved here to match the convention its own text describes | `../docs/management/CURRENT-STATE.md` |
| `RESOLVED_2026-08-27_agent-integrity-review.md` | 16-gap agent-integrity RCA (PR #204) | Resolved — PASS, live-verified same session | `../docs/management/CURRENT-STATE.md` §10 |
| `SUPERSEDED_2026-08-25_model-armor-management-report.md` | External-sharing Model Armor management report | Superseded — conclusion predates the 2026-08-26 revert to `inspect_only` | `../docs/management/CURRENT-STATE.md` §2, §8 |
| `SUPERSEDED_2026-08-25_custom-mcp-model-armor-coverage.md` | Real-test finding: custom/fallback MCP traffic transits the gateway but isn't Model Armor-inspected | Superseded as a standalone doc — the finding itself is still ACTIVE and unresolved, now tracked in `CURRENT-STATE.md` §2/§9, not lost | `../docs/management/CURRENT-STATE.md` §2, §9 |

Zero-reference status for all 4 confirmed by a full repo-wide reference sweep before moving
(2026-08-30). All inbound links from other docs updated in the same pass; `PRODUCTION-LAUNCH-PLAN.md`
and `floor-settings-production-plan-2026-08-25.md` were evaluated but deliberately NOT archived —
both are cited by live code/Terraform comments, not just other docs.

## Added 2026-09-06 (documentation accuracy pass)

| File | What it was | Status | Superseded/replaced by |
|---|---|---|---|
| `SUPERSEDED_2026-08-31_agent-registry-terraform-pilot-import.md` | 3-endpoint pilot plan for bringing Agent Registry under Terraform | Superseded — the pilot's conclusion (BLOCKED, 3-endpoint scope only) was fully overtaken 4 days later | `iac/agent/agent_registry.tf`, `iac/agent/agent_registry_mcp.tf` (all registrations, not just 3, brought under Terraform 2026-09-04) |
| `SUPERSEDED_2026-08-31_next-slice-connect-gateway-onboarding.md` | Planned workstream for full Connect Gateway/non-GKE onboarding | Superseded — the planned work was built and shipped 4 days later (Phase 1, PR #242/#244) | `iac/agent/onprem_fleet.tf`, `docs/architecture/gke-vs-nongke.md` |
| `RESOLVED_2026-08-26_rca-tf147-and-network-grant-removal.md` | RCA for a Terraform 1.4.7 pin + network-grant-removal incident | Resolved — PR #198 merged, conclusion proven safe; the doc's own listed CI follow-ups are ~11 days stale (superseded by later clean regression runs) but the RCA itself is closed | `docs/management/CURRENT-STATE.md` |

**Evaluated but deliberately NOT archived** (same reason as `PRODUCTION-LAUNCH-PLAN.md`/`floor-settings-production-plan-2026-08-25.md` above): `docs/management/confidence-genericity-review-2026-08-28.md` and `docs/management/observability-architecture-review-2026-08-23.md` are both cited by path from 15+ and 3+ live `agent/`/`tests/` code comments respectively as provenance for real, current behavior (e.g. `agent/confidence/scorer.py`, `agent/llm/gemini_adapter.py`, `tests/test_main_fail_open.py`) — moving them would have left those comments pointing at a location that no longer exists. Both were briefly archived and moved back in the same pass once this was found via a full-repo reference sweep (not just a docs-only one).

Moved as part of a full documentation accuracy pass (branch `docs/accuracy-pass-2026-09-06`) — see that
PR's description for the complete UPDATED/ARCHIVED/DELETED/MERGED report. Several other pages were
found stale in the same pass (Model Armor/CONTENT_AUTHZ framing, custom-MCP/on-prem reachability
claims) but were corrected in place rather than archived, since their subject matter is still current
and load-bearing — only genuinely closed/superseded investigations were moved here.
