# SRE Agent — Production Launch Plan

> **Personal-repo tracking only.** This file lives in `sre-agent-gateway` for planning/tracking
> purposes and is intentionally **not** ported to `sre-agent-app-infra` (the company repo) — that
> repo tracks its own rollout separately. Only the *logic* of finished work gets ported there
> (per the personal-repo-first workflow), never this planning doc itself.

_Created 2026-07-28. Status is evidence-based: marked ✅/🟡 only where backed by real code/config (file:line). Scanned the working repo (agent/, mcp/, iac/agent/) before writing. Supersedes the ordering in NEXTSTEPS.md for production-launch work; NEXTSTEPS.md is retained for the deeper per-item research._

**Objective:** controlled production rollout as a **read-only** Kubernetes investigation assistant, triggered by PagerDuty, covering **GKE and on-prem/non-GKE** clusters, with deterministic cluster→MCP routing. Do not rebuild working components without a confirmed issue.

Legend: ✅ done · 🟡 partial · ⬜ not started

## Status at a glance (updated 2026-08-05 — see "Revised execution order" below for the real sequencing)
| # | Priority | Status |
|---|---|---|
| — | **Model Armor resolution (MVP blocker, new 2026-08-04)** | 🟥 **blocked — root cause not found, support case drafted, not yet filed with Google** |
| 1 | Document & protect working baseline | 🟡 partial |
| 2 | PagerDuty incident integration | ⬜ not started |
| 3 | On-prem connectivity (Connect Gateway) | ⬜ not started |
| 4 | Custom read-only Kubernetes MCP server | 🟡 partial |
| 5 | Deterministic cluster & MCP routing | 🟡 partial |
| 6 | End-to-end routing tests | ⬜ not started |
| 7 | Confidence score redesign | 🟡 **built + live-verified in personal repo/GCP 2026-08-04 — NOT complete: not yet ported to `sre-agent-app-infra-main`, not merged/tested in company environment. "Done" = merged + tested on work laptop, per 2026-08-05 clarification.** |
| 8 | Minimum RCA accuracy validation | 🟡 partial (eval harness exists) |
| 9 | Production security validation | 🟡 partial (strong baseline) |
| 10 | Logging/metrics/alert validation | 🟡 partial (monitoring.tf exists, under-covers) |
| 11 | Cost validation & optimization | 🟡 partial |
| 12 | Controlled production launch | ⬜ gated on all of the above |

---

## Priority 1 — Document & protect the working baseline  🟡
**Have:** `CURRENT_STATE.md`, `RCA_REPORT.md`, `FINAL_RCA.md`, `AUDIT_REPORT.md`, `README.md`; git clean on `main@897e48b`; backup made (`~/projects/_backups/testing2-...-BACKUP-2026-07-28.tar.gz`). Confidence + routing logic now documented (this scan).
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

## Priority 4 — Custom read-only Kubernetes MCP server  🟡
**Have:** `mcp/server.py` (FastMCP) with **6 read-only tools** — `list_pods`, `describe_pod_detail`, `get_current_logs`, `get_previous_logs`, `list_events`, `list_deployments`; containerized (`mcp/Dockerfile`); deployed via `iac/agent/cloudrun_mcp.tf` (gated on `enable_custom_mcp`, internal LB). Agent-side hard block on write verbs (`mcp_client.py:92-95`, `:211-218`). Reaches clusters via direct GKE endpoint + WI bearer token — **not** Connect Gateway yet.
**Missing ops:** StatefulSet/DaemonSet status, Service/endpoints, resource requests/limits, config metadata, rollout/change info.
**Missing hardening:** request validation, namespace/cluster scope limits, response trimming, secret redaction, timeouts + response-size limits, rate limiting, audit logging, health checks, unit+integration tests, output **normalization** to a common evidence format, and routing via Connect Gateway (from P3).
**Acceptance:** same logical capabilities as GKE Remote MCP; no write/exec/port-forward; validated + rate-limited + audited + tested; output shape identical to GKE-MCP so the reasoning layer is vendor-agnostic.
**Depends on:** 3 (for on-prem reach).

## Priority 5 — Deterministic cluster & MCP routing  🟡
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
**Have:** `monitoring.tf` — 7 log metrics (`invocations, errors, escalations, investigation_cost_usd, confidence_band, loop_exit_reason, tool_failures`) + 3 alerts (error-rate, escalation-rate, cost-spike) + email channel. Rich per-investigation structured log (`rca_builder` `sre-agent-investigations`).
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
- **Model Armor is now an MVP blocker (updated 2026-08-04).** Originally scoped as a documented
  control gap with compensating controls, not a to-fix-now blocker (P9). **Reversed per
  management + security team decision (meeting, week of 2026-07-28): security requires it before
  any production rollout, to avoid attack risk at launch.** Root cause not yet identified — it's
  currently disabled because enabling it broke mTLS and produced false positives on K8s logs
  (open Google ticket, ticket ref not on file). Must be root-caused, not just re-enabled blindly.
- The custom MCP and GKE Remote MCP must emit a **common evidence format** (P4) so the reasoning layer never depends on the MCP implementation.
- Routing is deterministic for **source**; keep the model out of final cluster/endpoint selection (P5).
