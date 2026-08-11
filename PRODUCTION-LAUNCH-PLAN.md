# SRE Agent — Production Launch Plan

> **Personal-repo tracking only.** This file lives in `sre-agent-gateway` for planning/tracking
> purposes and is intentionally **not** ported to `sre-agent-app-infra` (the company repo) — that
> repo tracks its own rollout separately. Only the *logic* of finished work gets ported there
> (per the personal-repo-first workflow), never this planning doc itself.

_Created 2026-07-28. Status is evidence-based: marked ✅/🟡 only where backed by real code/config (file:line). Scanned the working repo (agent/, mcp/, iac/agent/) before writing. Supersedes the ordering in NEXTSTEPS.md for production-launch work; NEXTSTEPS.md is retained for the deeper per-item research._

## 2026-08-11 update — Phase 1/Phase 2 restructure, read this first (supersedes the 2026-08-09 section below as the current status source)

Reframed around **Phase 1 (production MVP)** vs **Phase 2 (post-MVP)**, tracked by GitHub
issue number rather than the Priority-1-12 scheme below. The Priority write-ups are kept
as historical detail — this section is the current plan.

### Phase 1 — Production MVP

**Keep (already real, verified live this session):**
- Gemini 2.5 Pro as the deployed model
- The `agent/llm/` adapter design (issue #63 PR 1 — provider-neutral interface,
  Gemini-only adapter, `LLM_PROFILE`-driven selection)
- Accurate token tracking (input/cached/candidates/reasoning/tool-use/total, all captured
  and aggregated correctly — verified live, 20/20 per-call log entries summed exactly to
  the final reported totals)
- Run ID, provider, and model telemetry
- Read-only investigation (no write/exec/port-forward anywhere in `mcp/` — enforced by
  `mcp/tests/test_no_mutation.py`)
- Evidence-backed RCA (every claim cites a real evidence ID)
- Human approval required before any remediation (no auto-remediation exists)

**#63 scope change:** the per-request estimated dollar cost is being **removed**, not
made dynamic. It was always computed from manually configured Terraform pricing variables
— real spend visibility belongs to Google Cloud Billing, not a hand-maintained rate table
in this repo. Token counting stays exactly as accurate as PR 1 left it; only the dollar
conversion goes away. (This replaces the earlier "PR 2: dynamic per-investigation
pricing" sketch floated right after PR 1 merged — that approach is deferred to Phase 2's
"Billing-export cost attribution," not built now.)

**Phase 1 blockers, in order:**
1. **#63** — cost cleanup (see below)
2. **#74** — cross-investigation token/state leakage (`_session_tokens_*` module-level
   globals in the Gemini adapter can mix data between separate Agent Engine invocations
   sharing a warm process)
3. **#95** — reliable service-status and user-impact detection (the real fix #61's
   Option A fallback text deferred)
4. Final security, rollback, and production end-to-end validation

**Deferred, not blocking Phase 1:** #32 and #92 (Model Armor output-block verification and
its dependencies) — Model Armor is intentionally disabled at both the gateway and app
layers right now (see the Model Armor status note further down); no path to close these
until that's resolved, and that resolution is out of scope for the Phase 1 MVP gate.

**Do not begin Phase 2 work** until every Phase 1 blocker above is closed.

### Phase 2 — post-MVP

- Dynamic per-investigation pricing (fetched, cached, versioned rate source — the
  approach originally sketched for #63's "PR 2," now correctly scoped here instead)
- Additional LLM provider adapters (the `agent/llm/` interface was built for this; only
  Gemini is implemented today, deliberately)
- True one-variable (`LLM_PROFILE`) switching between multiple configured providers —
  only meaningful once a second adapter actually exists
- Billing-export cost attribution by investigation (join Cloud Billing export data back
  to `run_id`, replacing the removed per-request dollar estimate with something backed by
  real billing data instead of a manual rate table)

### Working rule for this phase

Handle one Phase 1 blocker at a time — implementation, regression tests, and a diff review
before merging each one. No parallel starts across blockers.

---

## 2026-08-09 update — read this first

Real progress since the 2026-08-05 status below, verified today via 6 parallel live-code
audits (not just a doc pass — includes real `gcloud iam roles describe`, `terraform test`,
and pytest runs):

- **Priority 1 (baseline documentation): now ✅ done.** A full 51+ page knowledge base
  exists under `docs/`, re-audited and corrected today, plus a new master
  [`docs/management/implemented-vs-planned-matrix.md`](docs/management/implemented-vs-planned-matrix.md)
  — this is now the **live, ongoing source of truth for every capability's real status**,
  not the table below. Update that file when a capability's status changes; treat this
  doc's per-priority write-ups below as the historical research they were written from,
  refreshed only where explicitly noted.
- **Priority 5 (deterministic cluster & MCP routing): registry gap closed.** The "thin
  registry" and "missing fields" problems described below are fixed — `var.additional_clusters`
  (Terraform) now carries the full field set (aliases, environment, allowed_namespaces,
  owner, enabled), multi-cluster support works, verified live. Two specific claims below
  are now confirmed **stale, not current bugs**: `context_resolver.py` does **not**
  default to `sre-test-cluster` (re-confirmed via direct code read, twice — 2026-08-08
  and today), and this was likely already wrong when originally written. What's still a
  real, open gap: adding a cluster in a *different* GCP project gets no IAM grant
  (`iac/gke-access` is hardwired to one project).
- **Priority 8 (RCA accuracy validation): baseline established and corrected.** 14 golden
  cases now run clean (0 crashes, was 4/14 failing on an eval-harness bug), 12/14 correct
  tool selection, 100% correct MCP source routing — see
  [`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`](docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md).
  Still open: no LLM-judge for root-cause correctness, no confidence calibration
  measurement (both confirmed absent by direct code search, not assumed).
- **Priority 10 (logging/metrics): the "7 log metrics + 3 alerts" count below is stale.**
  Real count today: **11 log-based metrics, 11 alert policies** (`iac/agent/monitoring.tf`,
  counted directly). The specific missing-alert list further down is now shorter than
  written — re-verify against the matrix before treating any single row as still open.
- **Model Armor: status changed since the "MVP blocker, not yet filed" note below.** The
  Google support case referenced there **was filed and answered** (2026-08-07) — see
  `GOOGLE_SUPPORT_RESPONSE_DRAFT_2026-08-07.md` (a reply from us is still pending) and
  `archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md` (confirmed: no working
  Terraform path exists to wire `CONTENT_AUTHZ` to Agent Gateway — an API-level rejection,
  not a config mistake). **Correction to an earlier draft of this note**: this is not fully
  "resolved to a deliberate tradeoff" — the gateway-level gap is a real, confirmed platform
  limitation (documented, not a mistake), but the app-level fallback (`agent/main.py`'s
  `_sanitize()`) is ALSO currently inactive in the live config, since it only activates
  when the gateway is *off* (`enable_agent_gateway=false`) and the live deployment has the
  gateway *on* — meaning **Model Armor filters nothing today, at either layer**. That's a
  real, open gap, not something to treat as settled. What's directly actionable and not yet
  applied: Google's own tuning guidance (confidence threshold to `HIGH`, check org floor
  settings, split templates) — but applying it doesn't by itself close the "nothing is
  active right now" gap. See [Security Operations](docs/governance/security.md) for the
  full picture and current compensating controls.
- **Not re-verified today, status below still assumed current**: PagerDuty integration
  (still nothing built), Connect Gateway on-prem connectivity, cost validation specifics.
  Don't treat their unchanged 🟡/⬜ marks as freshly confirmed — they're carried forward,
  not re-audited in this pass.

**Objective:** controlled production rollout as a **read-only** Kubernetes investigation assistant, triggered by PagerDuty, covering **GKE and on-prem/non-GKE** clusters, with deterministic cluster→MCP routing. Do not rebuild working components without a confirmed issue.

Legend: ✅ done · 🟡 partial · ⬜ not started

## Status at a glance (table below dated 2026-08-05 — see the "2026-08-09 update" section above for what changed since; going forward, [`docs/management/implemented-vs-planned-matrix.md`](docs/management/implemented-vs-planned-matrix.md) is the live status source, not this table)
| # | Priority | Status (2026-08-05) | 2026-08-09 change |
|---|---|---|---|
| — | **Model Armor resolution** | 🟥 blocked — case not yet filed | 🟡 case filed + answered; actionable tuning plan pending; gateway-level CONTENT_AUTHZ confirmed no Terraform path exists |
| 1 | Document & protect working baseline | 🟡 partial | ✅ **done** — full knowledge base + status matrix built and audited |
| 2 | PagerDuty incident integration | ⬜ not started | unchanged |
| 3 | On-prem connectivity (Connect Gateway) | ⬜ not started | unchanged |
| 4 | Custom read-only Kubernetes MCP server | 🟡 partial | unchanged (built, not deployed) |
| 5 | Deterministic cluster & MCP routing | 🟡 partial | ✅ registry/multi-cluster gap **closed**; cross-project IAM for a different GCP project still open |
| 6 | End-to-end routing tests | ⬜ not started | unchanged |
| 7 | Confidence score redesign | 🟡 built, not ported to company repo | unchanged |
| 8 | Minimum RCA accuracy validation | 🟡 partial (eval harness exists) | 🟡 corrected baseline established (14/14 clean run); LLM-judge + calibration still absent |
| 9 | Production security validation | 🟡 partial (strong baseline) | unchanged |
| 10 | Logging/metrics/alert validation | 🟡 partial (7 metrics + 3 alerts claimed) | 🟡 real count is **11 metrics + 11 alerts** (was already higher than this table said) |
| 11 | Cost validation & optimization | 🟡 partial | unchanged |
| 12 | Controlled production launch | ⬜ gated on all of the above | unchanged |

---

## Priority 1 — Document & protect the working baseline  🟡
**Have:** `archive/RESOLVED_2026-07-17_CURRENT_STATE.md`, `archive/RESOLVED_2026-07-17_RCA_REPORT.md`, `archive/RESOLVED_2026-07-17_FINAL_RCA.md`, `archive/RESOLVED_2026-07-17_AUDIT_REPORT.md`, `README.md`; git clean on `main@897e48b`; backup made (`~/projects/_backups/testing2-...-BACKUP-2026-07-28.tar.gz`). Confidence + routing logic now documented (this scan).
**Missing:** one consolidated production-baseline doc (architecture + Agent Engine/Gateway/MCP/identity config + cross-project flow + rollback path in one place); explicit rollback procedure; separate branches/envs for Connect-Gateway / custom-MCP / PagerDuty testing.
**Acceptance:** a single BASELINE.md a new engineer can read to understand + safely roll back; rollback tested once.
**Depends on:** none (do first).

## Priority 2 — PagerDuty incident integration  ⬜
**Have:** nothing — repo-wide grep finds **zero** PagerDuty code/IaC (only prose in NEXTSTEPS.md item #4 and a docstring in `invoke_agent.py:240`).
**Build:** authenticated webhook receiver (Cloud Run) → validate PagerDuty signature → normalize fields → extract routing hints (cluster, env, project, namespace, workload, service, region, alert source) → dedup + idempotency + correlation ID → trigger **read-only** investigation → return RCA **draft** for human review. Define behavior when routing info is insufficient.
**Acceptance:** a signed PD webhook starts one investigation; replayed/duplicate events are ignored; an event with no cluster info does **not** guess — it stops or asks. Remediation stays out of the flow.
**Depends on:** 5 (routing) for cluster resolution; can start the receiver independently.

## Priority 3 — On-prem connectivity via GKE Fleet Connect Gateway  ⬜
**Have:** nothing — no Fleet/`gkehub`/Connect-Gateway code or IaC. Only routing comments anticipate `cluster_type != gke`.
**Build:** register a test non-GKE cluster with GKE Fleet → validate Connect Gateway access with **short-lived creds** (no stored kubeconfig) → validate IAM + K8s RBAC (read-only) → confirm audit logging → measure latency/reliability → document onboarding/removal + failure behavior. Confirm the intended architecture with Google where docs are unclear.
**Acceptance:** agent reaches a registered non-GKE cluster read-only via Connect Gateway using short-lived creds; onboarding + outage behavior documented.
**Depends on:** none technically; must exist before 4 is useful for on-prem.

## Priority 4 — Custom read-only Kubernetes MCP server  🟢
**Have:** `mcp/server.py` (FastMCP) with **27 read-only tools** — full parity with `agent/mcp_client.py`'s `CUSTOM_K8S_TOOLS` allowlist (Pod, Deployment/ReplicaSet, StatefulSet/DaemonSet, Service/Endpoints, Node, ConfigMap, HPA, PVC, Job, namespace-wide events). New tool logic in `mcp/tools/{workloads,services,nodes,configmaps,scaling,storage,jobs}.py` + extended `deployments.py`/`events.py`. Hardening centralized in `mcp/security.py` (validation, namespace scope limits, response trimming, secret redaction, timeouts, response-size limits, rate limiting, audit logging) applied to every tool via a `@guarded()` decorator; health checks at `/healthz` (liveness) and `/readyz` (real connectivity check). `get_k8s_clients()` now also supports a `K8S_MCP_KUBE_CONTEXT` kubeconfig-context mode for GKE Fleet Connect Gateway (P3) — proven live: all 27 tools invoked through `server.mcp._call_tool_mcp` against context `connectgateway_sreagent-t2-demo_global_sre-lab` reaching the real `sre-lab` cluster; 18/19 spot-checked tools returned real data, `list_nodes` correctly surfaced the documented `view`-role 403 as a structured `{"error": ...}` instead of crashing. Output envelope proven identical to GKE Remote MCP via a real side-by-side call through `agent/mcp_client.call_tool()` against both sources (`sre-test-cluster` on GKE, `sre-lab` via Connect Gateway) — both return the exact same 5-key wrapper (`ok`, `result`, `tool`, `mcp_source`, `duration_s`); internal `result` payload shape differs (GKE Remote returns kubectl-describe text, custom MCP returns structured JSON) but that's opaque to the reasoning layer, which LLM-summarizes `result` regardless of its internal shape (`agent/nodes/evidence_extractor.py`). 91 tests passing (`mcp/tests/`, incl. 7 live-cluster integration tests) — see `docs/custom-k8s-mcp.md` for full evidence.
**Not yet done:** the Cloud Run deployment itself (`iac/agent/cloudrun_mcp.tf`) is not wired to reach clusters via Connect Gateway in production — that needs `gke-gcloud-auth-plugin`/`gcloud` baked into `mcp/Dockerfile`'s slim image plus a `roles/gkehub.gatewayReader` binding on the runtime SA (`docs/connect-gateway-onprem.md`'s "Open items"); today's proof is the code path + a live local run against Connect Gateway, not a redeployed Cloud Run service. Rate limiting is in-process only (no distributed limiter across Cloud Run instances — acceptable for a single-tenant internal tool per `mcp/security.py`'s design note).
**Acceptance:** same logical capabilities as GKE Remote MCP — met (27/27 tool names match the allowlist). No write/exec/port-forward — met, verified by a permanent regression test (`mcp/tests/test_no_mutation.py`) plus manual grep, zero mutating K8s API calls anywhere in `mcp/`. Validated + rate-limited + audited + tested — met. Output shape identical to GKE-MCP — met at the envelope level (the level the reasoning layer actually consumes), proven with a real side-by-side call, not code review alone.
**Depends on:** 3 (for on-prem reach) — done.

## Priority 5 — Deterministic cluster & MCP routing  ✅ (registry gap closed 2026-08-09; original write-up below kept for history)
**2026-08-09: the registry and safety-default problems described below are fixed.** `var.additional_clusters` (`iac/agent/variables.tf`) is now `map(object(...))` with the full field set (aliases, project, region, type, environment, allowed_namespaces, owner, enabled), rendered into `clusters.json` by Terraform (`iac/agent/main.tf`) — no manual GCS edits, no thin schema. Real collision-guard `validation` block, Terraform-native tests (`iac/agent/tests/clusters_json.tftest.hcl`, 4/4 passing live) and Python routing tests (`tests/test_multi_cluster_registry.py` + `test_mcp_router.py`, 13/13 passing live). Separately confirmed via direct code re-read (twice: 2026-08-08 and 2026-08-09) that `context_resolver.py` does **not** default a missing cluster to `sre-test-cluster` — that specific claim below was already stale/wrong when checked, not a bug introduced since. Still genuinely open: adding a cluster in a **different** GCP project gets no IAM grant (`iac/gke-access` is hardwired to one `project_b_id`) — see `docs/runbooks/add-gke-cluster.md`.

**Original write-up (2026-08-04, kept for history):**
**Have (good):** MCP **source** selection is **deterministic** — `mcp_router.py:81-88`: `cluster_type=="gke" → gke_remote_mcp else k8s_mcp`; tool pick within a source is model-based but constrained by a hard allowlist (`mcp_router.py:140-145`). Selected cluster/MCP recorded in the structured `obs_event` (`main.py:455-461`).
**Registry (thin):** `gs://…/clusters.json` (`mcp_client.py:134-169`), seeded by `clusters.json.tftpl`. Fields today: `name, project, region, type(+optional mcp_url)`. **Missing registry fields:** aliases, environment, allowed namespaces, ownership, **enabled/disabled**, canonical ID, per-cluster MCP destination.
**Safety gaps:** unknown/ambiguous handling is inconsistent — `context_resolver.py:27-37` **defaults a missing cluster to `sre-test-cluster`**; `mcp_router.py:84-88` **silently falls back to gke** if the registry is empty. No recorded rationale for the source decision.
**Build:** authoritative registry with the full field set + routing priority (exact ID → verified PD metadata → approved alias → project/env/namespace → human); reject disabled/unknown; **stop safely on ambiguity (no defaulting, no silent gke fallback)**; record why a cluster+MCP were chosen; put it in RCA metadata; prevent the model from picking arbitrary MCP endpoints.
**Acceptance:** the safety matrix in P6 passes; never defaults or guesses a cluster.
**Depends on:** registry redesign; feeds 2 and 6.

## Priority 6 — End-to-end routing tests  ⬜
**Have:** an RCA eval harness (`agent/eval/golden_cases.py`, `run_eval.py`) but **no** routing-safety scenarios.
**Build the scenarios:** GKE→remote MCP; non-GKE→custom MCP; cross-project correct selection; similar-named clusters not confused; unknown rejected; missing metadata → no guessed access; conflicting metadata → explicit error; disabled cluster blocked; MCP/Connect-Gateway outage handled; evidence proven to come from the selected cluster; RCA names exact cluster/ns/workload; no cross-cluster evidence bleed.
**Acceptance (the one that matters):** the agent **never silently investigates the wrong cluster**.
**Depends on:** 2, 4, 5.

## Priority 7 — Confidence score redesign  🟡 (exists, but has the exact flaws you flagged)
**Current logic (evidence):** single `confidence` float, **both** model + app. Deterministic cap is driven by **raw evidence count** — `coverage_score = min(1, evidence_count/4)`, `cap = coverage+0.25` (`task_evaluator.py:92-100`). Bands hard-coded 0.85/0.65 (`task_evaluator.py:104-110`, **not configurable**). Completeness and root-cause certainty are **conflated** into one number. **Not penalized:** failed tools (only indirect), contradicting evidence (no detection at all), ambiguous cluster identity. RCA text **duplicates root cause 3×** (`main.py:231/238/501`). **No fields** for contradicting evidence or alternative explanations; `evidence_gaps` gives partial "missing evidence".
**Build:** split into **Investigation Completeness** (did we collect expected evidence — cluster confirmed, route confirmed, required tools done, logs/events/status/config collected, freshness, failed tools) and **Root-Cause Confidence** (direct evidence, multiple independent sources, tied to exact resource, in time window, contradictions, alternatives, missing evidence, inference-vs-observation). Model proposes cause + reasoning; **app computes final confidence deterministically**; penalize failed/missing/stale/contradictory evidence + ambiguous cluster + unresolved alternatives; cap when logs-only / resource uncertain / dependency unchecked; support insufficient/unknown/multiple-cause. Make bands configurable. Fix the 3× RCA duplication; add contradicting-evidence + alternatives fields.
**Acceptance:** two separate scores in the RCA; confidence drops on missing/failed/stale/contradictory evidence; agent returns "unknown" rather than inventing a cause.
**Depends on:** 1 (document current — done here); informs 8, 10.

## Priority 8 — Minimum RCA accuracy validation  🟡
**Have:** `agent/eval/` golden-cases + runner.
**Build:** scenario matrix — CrashLoopBackOff, ImagePullBackOff, OOMKilled, Pending, one non-GKE via custom MCP, one insufficient-evidence, one conflicting-evidence, one ambiguous-routing, one MCP/Connect-Gateway failure. Deterministic checks: correct cluster/MCP/ns/workload, claims cite collected evidence, unsupported claims excluded, confidence drops when evidence missing, returns "unknown" vs inventing, remediation stays advisory. LLM-judge optional secondary.
**Acceptance:** all scenarios pass the deterministic checks.
**Depends on:** 7, 5.

## Priority 9 — Production security validation  🟡 (strong baseline)
**Confirmed good:** `identity_type = AGENT_IDENTITY` (`agent_engine.tf:98`, no static SA); IAP **REQUEST_AUTHZ** on the gateway (`agent_gateway.tf:88/107`); read-only cross-project GKE roles (`gke-access/crossproject_iam.tf`).
**Gaps to validate/document:** Model Armor is **NOT** wired at the gateway (comments claim it but no CONTENT_AUTHZ resource) — document as a **known control gap** with compensating controls (read-only tools, structured responses, input validation, output trimming, evidence grounding, bounded loops, human review). Log redaction is weak (raw tool errors/facts logged). Add: PagerDuty webhook auth, custom-MCP authz, Connect-Gateway IAM+RBAC, "MCP endpoints not arbitrarily selectable", secrets-not-in-prompts/logs/RCA, evidence isolation per investigation/cluster, human approval for prod deploys. **Do not** describe IAP/Gateway as content-inspection controls.
**Acceptance:** each item confirmed against code/live config; accepted risks + open Google questions documented.
**Depends on:** 2, 4, 5 to exist first.

## Priority 10 — Logging, metrics & alert validation  🟡
**Correction (2026-08-09):** the "7 log metrics + 3 alerts" count below is stale — counted
directly against `iac/agent/monitoring.tf` today: **11 log-based metrics, 11 alert
policies**. Re-check the "Missing alerts" list below against the real resource list before
treating any specific row as still open; several may already exist.

**Have (2026-08-04 write-up, count superseded above):** `monitoring.tf` — 7 log metrics (`invocations, errors, escalations, investigation_cost_usd, confidence_band, loop_exit_reason, tool_failures`) + 3 alerts (error-rate, escalation-rate, cost-spike) + email channel. Rich per-investigation structured log (`rca_builder` `sre-agent-investigations`).
**Missing visibility:** trace_id correlation, cluster-routing reason, MCP/model/total **latency** (per-tool `duration_s` is captured then discarded), agent-gateway + connect-gateway failures, investigation completeness, evidence-storage success/failure, a top-level `status` field (so the `errors` metric may never fire from RCA-path failures), PagerDuty incident id.
**Missing alerts (all 11 required):** PD webhook failures, routing failures, unknown/ambiguous clusters, GKE-MCP failures, custom-MCP failures, connect-gateway failures, agent-gateway failures, excessive latency, repeated tool failures, loop/token termination, evidence-storage failures. (Note: `tool_failures`/`loop_exit_reason` metrics exist but have **no** alert.)
**Acceptance:** required fields emitted; alerts exist AND are **proven** by triggering controlled failures (not "Terraform resource exists").
**Depends on:** 5, 7 (needs routing reason + completeness/confidence fields).

## Priority 11 — Cost validation & optimization  🟡
**Have:** per-investigation token + cost logged; cost-spike alert; per-node token JSON to stdout.
**Missing:** cost per successful vs failed investigation, model/token usage **by node** aggregated, repeated/unnecessary tool-call detection, oversized-response trimming, evidence dedup, configurable per-investigation token+cost limits, a realistic monthly estimate. Measure before optimizing; don't weaken evidence/security/confidence.
**Acceptance:** cost-per-investigation measured + bounded by config limits; monthly estimate produced.
**Depends on:** 1; before 12.

## Priority 12 — Controlled production launch  ⬜
Read-only, human-reviewed, no auto-remediation, limited clusters + engineers, auditable, tested rollback. **Launch gate = all criteria in P1–P11 met.**
**Depends on:** 1–11.

---

## Required execution order — REVISED 2026-08-04, supersedes the order below

Business-priority reorder, verified against every Priority's own "Depends on" line so nothing
executes ahead of what it needs (see [[app-infra-gateway-parity-build]] memory / session
2026-08-04 for the full dependency check):

1. **Model Armor resolution** — root-cause + fix. Carved out of P9; now an MVP/business-critical
   blocker per management + security team decision (see Cross-cutting notes). Independent of the
   rest — no dependency on PagerDuty/routing/Custom-MCP.
2. Confidence score redesign (P7) — self-satisfied dependency (P1 "done here").
3. Deterministic cluster & MCP routing (P5) — no blocking dependency, moved up specifically to
   unblock 4, 5, and 7 below.
4. Minimum RCA accuracy validation (P8) — needs 2 + 3, now satisfied.
5. Logging/metrics/alert validation (P10) — needs 3 + 2, now satisfied.
6. On-prem connectivity — Connect Gateway (P3) — no dependency, moved up to unblock 7.
7. Custom read-only Kubernetes MCP server (P4) — needs 6, now satisfied.
8. End-to-end routing tests, **pass 1** (P6) — everything not requiring PagerDuty; needs 5(old
   4)+7, now satisfied. PagerDuty-triggered scenarios deferred to pass 2.
9. PagerDuty incident integration (P2) — needs 3 (cluster resolution via routing), satisfied.
10. End-to-end routing tests, **pass 2** (P6, complete) — re-run now that PagerDuty exists;
    closes the one dependency pass 1 couldn't cover.
11. Remaining: Document & protect working baseline (P1), rest of Production security validation
    (P9, minus Model Armor which is done at step 1), Cost validation & optimization (P11).
    **Do P1 early within this group** — P11 depends on it, and it's the rollback-safety doc;
    don't leave it for last.
12. Controlled production launch (P12) — gate, needs everything above done.

## Original execution order (superseded 2026-08-04, kept since Priority write-ups above use these numbers)
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12.
**Do NOT** start broad MCP expansion (Prometheus/Grafana/Elastic/Confluence) or the ADK migration until the controlled rollout is stable (Post-Launch).

## Post-launch (do not block rollout)
More clusters; Prometheus/Grafana/Elastic/Cloud-Logging MCP; runbook/Confluence sources; expand golden dataset; confidence calibration from engineer feedback; automated PD updates; richer dashboards; revalidate Model Armor; **LangGraph → Google ADK migration (last)**.

## Cross-cutting notes
- **Model Armor — status as of 2026-08-09 (updates the 2026-08-04 note below).** The Google
  support case referenced below **was filed and answered** (2026-08-07 — see
  `GOOGLE_SUPPORT_RESPONSE_DRAFT_2026-08-07.md`, our reply is still pending). Google's
  proposed mTLS root cause was checked against this repo's own evidence and rejected (see
  `archive/RESOLVED_2026-07-17_FINAL_RCA.md`'s 2026-08-07 addendum — re-tested live, no
  difference). Separately confirmed (2026-08-08 live test,
  `archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md`): there is **no working
  Terraform path** to wire `CONTENT_AUTHZ` to Agent Gateway — an API-level rejection
  (`Error 400: unsupported Google API for AuthzExtension`), not a config mistake. What
  *is* directly actionable, from Google's own guidance, and not yet applied: set
  prompt-injection/jailbreak confidence to `HIGH` on the operational-data template, check
  org-level floor settings first (they can override), split input/output and
  log-ingestion/chat templates. **Important correction**: the gateway-level gap is
  documented and understood, but the app-level fallback is *also* currently inactive in
  the live config (it only runs when the gateway is off) — Model Armor filters nothing
  today at either layer. Applying Google's tuning guidance alone does not close this; see
  [Security Operations](docs/governance/security.md) for the full picture.

- **Original 2026-08-04 note (superseded above, kept for history): Model Armor is now an MVP blocker.** Originally scoped as a documented
  control gap with compensating controls, not a to-fix-now blocker (P9). **Reversed per
  management + security team decision (meeting, week of 2026-07-28): security requires it before
  any production rollout, to avoid attack risk at launch.** Root cause not yet identified — it's
  currently disabled because enabling it broke mTLS and produced false positives on K8s logs
  (open Google ticket, ticket ref not on file). Must be root-caused, not just re-enabled blindly.
- The custom MCP and GKE Remote MCP must emit a **common evidence format** (P4) so the reasoning layer never depends on the MCP implementation.
- Routing is deterministic for **source**; keep the model out of final cluster/endpoint selection (P5).
