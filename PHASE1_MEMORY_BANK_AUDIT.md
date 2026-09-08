# Phase 1 — Memory Bank Design Audit

Built 2026-09-08, per the original "PHASE 1 FINAL READINESS" plan's Step 12. Reviews
the CURRENT code/Terraform, not the design as originally imagined — cites real
file:line evidence throughout, most of it produced during today's Section 8 work
(content-safety/memory lifecycle) on this same branch.

## Checklist

| Dimension | Finding |
|---|---|
| What is written to memory | `agent/main.py::_mb_store()` — cluster, namespace, pod, incident_type, root_cause (truncated 300 chars), confidence, **and, as of today, `status=pending_review`, `run_id`, `policy_version`** (previously absent — ADR-010's own "NOT YET IMPLEMENTED" gap, closed today). |
| When it is written | Only when `confidence_band == "auto"` (i.e. the `CONFIRMED` outcome, a multi-condition structural gate — not a bare numeric cutoff) AND (new today) the primary claim's cited evidence was not `inspection_status == "fail_open"` (Model Armor bypass). Deduped by `pod + incident_type`. |
| What is read | `agent/main.py::_mb_recall()` — scoped by `cluster + namespace`, up to 3 memories, **now filtered to `status == "approved"` only** (previously: every memory the instant it existed, regardless of review state). |
| When previous memory influences a new investigation | Injected as prompt text into `task_planner`/`rca_builder` calls, explicitly labeled "hint only... do NOT cite memory as evidence" (`agent/prompts.py`). Verified via grep: memory context never reaches `agent/confidence/*.py`'s deterministic scoring functions at all — it cannot affect the confidence score or gating, only the model's own narrative reasoning. |
| Cluster isolation | Enforced by the `scope={"cluster": ..., "namespace": ...}` parameter on every `retrieve()`/`create()` call — Memory Bank's own native scoping, not something this codebase layers on top. |
| Incident isolation | Dedup key is `pod + incident_type` (stable K8s identifiers, not LLM-generated text) — the same scenario is never double-written regardless of how the RCA is phrased. |
| User/tenant isolation | Not applicable — this deployment has no multi-tenant concept; every investigation runs as the same service identity. |
| Stale evidence contamination risk | Addressed today (Section 7): `incident_time_context` is now actually populated (previously always empty, so `temporal_relevance` could never be anything but `"unknown"` — a real, structural gap, now closed) — but full "wrong time window" detection at the evidence-content level (not just the incident-anchor level) is still not implemented (`agent/confidence/claim_builder.py`'s own docstring: "wrong_time_window detection is NOT implemented — evidence_store does not currently carry a per-item timestamp"). Real, disclosed, partial gap. |
| Risk of previous unsupported RCA influencing new reasoning | Substantially reduced today: only `status=approved` memories are recalled, and nothing was ever approved through a real process before today (no pre-existing memory can silently claim trusted status). Residual risk: the model is not FORCED to independently re-verify a recalled memory before using it as narrative context — the "hint only" instruction is a prompt convention, not a code-enforced check. Disclosed in ADR-010, unchanged today. |
| Verified facts vs. model-generated conclusions | The stored `root_cause` text is the model's own conclusion, not independently re-verified before storage beyond the `CONFIRMED`-outcome gate itself (which does include the separate adversarial verifier step, `agent/confidence/verifier.py`, before a claim can even become the primary cause). |
| Retention/TTL | Not explicitly configured — **STATUS: UNKNOWN**, no `ttl`/`expire_time` set on `memories.create()` calls (confirmed via grep: `agent/main.py::_mb_store()` passes no `config=` argument at all). Memory Bank's own platform-level default applies, unverified in this pass. Real, disclosed gap. |
| Sensitive-data/Secrets handling | `root_cause` text derives from evidence that already passed through `agent/gcs_client.py::redact()` at extraction time — confirmed no unredacted secret-shaped value reaches the stored fact string (Section 8's audit checked this specifically for the memory-write path and found no violation). |
| Behavior when Memory Bank is unavailable | Explicitly handled and tested: `_mb_recall()` returns the `MEMORY_RECALL_UNAVAILABLE` sentinel (distinct from "genuinely zero memories"), never silently reported as "no prior incidents found." `_mb_store()` catches its own exceptions and logs a warning rather than crashing the investigation. |
| Read/write observability | `_mb_recall()` emits a structured `memory_bank_recall` JSON log line per recalled memory (cluster, namespace, pod, incident_type, confidence) for recurrence tracking. `_mb_store()` logs a plain INFO line on success/skip, a WARNING on failure — no dedicated Cloud Monitoring alert exists specifically for memory-write/recall failures (a real, minor observability gap; not part of today's 14-alert set). |
| Governance/auditability | Real, as of today: `run_id`/`policy_version` in every new memory's fact string let a reviewer trace back to the exact originating investigation and its full GCS evidence trail. `scripts/review_memory.py`'s `reject`/`revoke` commands record `reviewed_by`/`reason`. |
| Human approval appropriateness | Now built (was the single largest gap before today): `scripts/review_memory.py` (list/approve/reject/revoke), gating what `_mb_recall()` actually returns. This is the smallest supported operation (a CLI using the Memory Bank SDK's own public `get()`/`delete()`/`create()`), not a new UI. |

## Verdict

**SAFE WITH REQUIRED CHANGES** — specifically, changes already made today, not
hypothetical future work:

- Before today's Section 8 work, this design was **NOT PRODUCTION-READY**: any
  `CONFIRMED`-band RCA was written and immediately, permanently recallable with zero
  human review — exactly the gap ADR-010 itself flagged as "decided but not built."
- After today's fixes (real `status` field + enforced `approved`-only recall +
  `scripts/review_memory.py` + the uninspected-evidence write gate), the design is
  **SAFE, conditional on the review workflow actually being used** — the mechanism
  now exists and is tested, but its safety in practice depends on an SRE actually
  running `scripts/review_memory.py approve` on legitimate memories; an unreviewed
  backlog simply means memory recall stays empty (fails safe, not fails open), so
  the worst case of neglecting the review step is "the agent doesn't benefit from
  memory yet," never "an unreviewed RCA gets treated as fact."

**Remaining smallest-required items, not blocking the verdict above, but real:**
1. Retention/TTL is unset (platform default, unverified) — recommend confirming
   the actual Memory Bank default retention behavior directly, not assuming.
2. No dedicated alert for memory read/write failures — recommend adding one small
   log-based alert matching the existing pattern, when convenient.
3. Full per-evidence-item time-window detection (beyond the incident-anchor level
   fixed today) — a real, larger piece of work, correctly not attempted as part of
   this smallest-required-changes pass.

Do not redesign further beyond these three items — the core design (three-tier
separation, deterministic scoring untouched by memory content, real human-approval
gate) is sound as verified.
