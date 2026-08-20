# Archive — historical investigations and superseded reports

Everything in this folder is **closed, historical work** — kept for reference and
audit trail, not for day-to-day use. If you're looking for the current state of the
project, these are **not** the files to read:

- Current ordered launch work → [`../PRODUCTION-LAUNCH-PLAN.md`](../PRODUCTION-LAUNCH-PLAN.md)
- Deep per-item research (NOT a status doc) → [`../NEXTSTEPS.md`](../NEXTSTEPS.md)
- Current architecture/operations knowledge base → [`../docs/README.md`](../docs/README.md)
- Current implementation status (what's really done vs planned) → [`../docs/management/implemented-vs-planned-matrix.md`](../docs/management/implemented-vs-planned-matrix.md)

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
`../docs/DOCUMENTATION-VALIDATION-REPORT.md` for that review's full findings.
