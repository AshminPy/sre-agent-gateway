# Archive — historical investigations and superseded reports

Everything in this folder is **closed, historical work** — kept for reference and
audit trail, not for day-to-day use. If you're looking for the current state of the
project, these are **not** the files to read:

- Current roadmap → [`../NEXTSTEPS.md`](../NEXTSTEPS.md)
- Current launch tracking → [`../PRODUCTION-LAUNCH-PLAN.md`](../PRODUCTION-LAUNCH-PLAN.md)
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
| `SUPERSEDED_2026-08-04_MODEL_ARMOR_GOOGLE_SUPPORT_CASE.md` | Draft Google Cloud support case | Superseded — the case was actually filed and answered | See `../GOOGLE_SUPPORT_RESPONSE_DRAFT_2026-08-07.md` (still active — a reply is pending) |
| `SUPERSEDED_2026-08-04_CONFIDENCE_FRAMEWORK_REPORT.md` | Confidence framework verification snapshot | Superseded — predates today's audit | `../docs/architecture/confidence.md`, `../docs/ADR-007-two-confidence-dimensions.md`, `../docs/ADR-008-confidence-not-accuracy.md` |

Moved here 2026-08-09 as part of a repo-wide documentation currency review — see
`../docs/DOCUMENTATION-VALIDATION-REPORT.md` for that review's full findings.
