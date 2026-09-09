# Phase 1 MVP — Execution State

> **SUPERSEDED (2026-09-09):** this working log predates the final executed campaign and
> the merge of PR #251. It is kept as historical evidence, not current state. For the
> authoritative final result, see `PHASE1_FINAL_REPORT.md`.

Branch: `feat/phase-1-release`. Main is the clean checkpoint — never touched directly.
Source of truth: `openspec/changes/phase-1-mvp-release/specs/phase-1-release-criteria/spec.md`

## RESUMED 2026-09-05 — REQUEST_AUTHZ staleness test: PASS, no bypass

Fleet membership re-registered (see "resume tomorrow" commands below — already executed). Ran the full bounded REQUEST_AUTHZ security test the user requested before returning to CONTENT_AUTHZ:

- Baseline: real agent call through the full path (Connect Gateway → custom MCP → Agent Gateway), REQUEST_AUTHZ `ALLOWED`, on an already-warm (~5h17m old) reasoning-engine container.
- Revoked ONLY the `roles/iap.egressor` binding (`google_iap_agent_registry_iam_member.agent_egressor`) via targeted `terraform apply -destroy -target=...`.
- Retried on the SAME warm container immediately: denied within 2.8s, gateway log shows `authzPolicyInfo.result: DENIED` on the very first egress attempt. No stale-enforcement window, no bypass.
- Restored the binding, confirmed a post-restore call succeeds again, confirmed `terraform plan` shows "No changes."
- Verdict: **PASS.** REQUEST_AUTHZ is a genuine real-time per-request check (confirmed against official Google docs — authz extensions are invoked live at the request-headers stage, not cached). The earlier CONTENT_AUTHZ finding (gateway logging going quiet after ~40 min for one MCP hostname) does NOT extend to REQUEST_AUTHZ's actual enforcement — proven independently correct even on a container past that same age.
- Full evidence: `PHASE1_EVIDENCE_LOG.md`, section "2026-09-05 — REQUEST_AUTHZ staleness / bypass security test."
- Per the user's own instruction ("If enforcement behaves correctly: record PASS evidence, do not spend more time on it, return immediately to CONTENT_AUTHZ RESPONSE_BODY") — moving to that investigation next.

## SESSION PAUSED 2026-09-05 — cost-driven teardown (historical, already resumed above)

**What was torn down just now (cost hygiene, explicit user instruction):**
- Connect Gateway fleet membership for `sre-lab` — unregistered (`gcloud container fleet memberships list` confirmed 0 items). RBAC on the kind cluster cleaned up too.
- `mcp_runtime_gateway_reader` IAM grant — removed (paired with the fleet membership).
- All test/fixture pods on both clusters (kind `sre-lab` and real GKE `sre-test-cluster`) deleted.

**What was deliberately LEFT UP (not costly, or needed to resume cleanly):**
- The local `kind` cluster (`sre-lab`) itself — free, Docker-only, still running.
- Custom MCP Cloud Run service (`sre-k8s-mcp`) — `min_instance_count=0`, scales to zero.
- Reasoning engine, CONTENT_AUTHZ extension/policy, Model Armor templates, all IAM (except the one gatewayReader grant above) — core Terraform-managed infra, not per-test cost drivers.
- One temporary diagnostic env var still in `agent_engine.tf` (`PHASE1_CONTENT_AUTHZ_FORCE_FRESH_CONTAINER`) — harmless, documented, still needed if CONTENT_AUTHZ investigation continues tomorrow.

**To resume tomorrow — exact commands:**
```bash
cd ~/projects/sre-agent-gateway/iac/agent
terraform apply \
  -var="create_wif=false" \
  -var="enable_custom_mcp=true" \
  -var="custom_mcp_image=$(cat /tmp/phase1_mcp_image.txt 2>/dev/null || echo '<see PHASE1_EVIDENCE_LOG.md for the last known image tag>')" \
  -var="custom_mcp_kube_context=connectgateway_sreagent-t2-demo_global_sre-lab" \
  -var="onprem_fleet_membership=sre-lab" \
  -var="onprem_fleet_kubeconfig_context=kind-sre-lab"
```
This re-registers the fleet membership, re-applies RBAC, and restores everything to today's exact tested state — the `terraform_data` resource's create provisioner is idempotent (checks `READY` state first).

**Where the CONTENT_AUTHZ (#203) investigation stands — NOT resolved, this is the open item:**
- Real bug #1 (wrong protocol_binding) — found, reverted, closed.
- Real bug #2 (wrong Service Extensions service agent — IAM was on the wrong project's SA) — found and fixed. Confirmed via direct REST evidence, not assumption.
- Real bug #3 (stale warm container bypassing gateway interception) — found, worked around via a forced redeploy.
- **After both real fixes, Model Armor is now genuinely being invoked** (`grpcStatus: OK`, real extension processing) for the first time all session — this is real progress, not a dead end.
- **Still open**: `RESPONSE_BODY` never appears in the extension's processed-event list for MCP tool responses, and even the `REQUEST_BODY` event that does fire never produces a Model Armor `sanitize_operations` log entry or blocks anything. Two independent malicious payloads (response-side and request-side) both went through unblocked.
- **User has NOT yet decided** whether to keep investigating this specific gap, treat it as a genuine platform limitation (needs a Google support case), or change the Phase 1 acceptance criterion. Do not assume any of these — ask when resuming.
- Full evidence trail: `PHASE1_EVIDENCE_LOG.md`. Do not re-litigate what's already proven there — read it first before continuing tomorrow.

## Current status (updated every meaningful step)

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | Connect Gateway production wiring | **DONE** — live, CI-proven, RBAC ownership corrected | Real regression found (PR #242's merge silently reverted this via a missing CI variable + a kubeconfig-dependent Terraform resource CI could never legitimately run) — fixed structurally, not patched: Kubernetes RBAC moved to separate repo `AshminPy/sre-k8s-rbac`, Fleet bootstrap is now a manual operator step, CI needs zero kubeconfig access. Fixed in PR #243 (merged), live-verified after a REAL CI apply, and proven with a real post-CI investigation through the full path. See evidence log. |
| 2b | GKE cross-project onboarding | **DONE** | re-read the actual OpenSpec wording — it requires ONE additional cluster via config only, not simultaneous multi-project; already satisfied, no further work needed |
| 3 | LLM config-only switching | **DONE** | live-proven, reverted to production default |
| 4 | Custom MCP tool parity | **DONE** | volumes/mounts fix, 26/26 tests |
| 5a | Routing validation matrix | **DONE** | happy path + 5 failure cases (unknown cluster, unknown pod, Connect Agent down) all proven |
| 5b | Live non-GKE routing proof | **DONE** | |
| 6a | Agent Gateway REQUEST_AUTHZ enforcement | **DONE — PASS, caveat resolved** | 2026-09-05 bounded revoke/retry/restore test proved real-time enforcement, no staleness, no bypass, even on a container past the 40-min mark. See evidence log. |
| 6b/9c | Model Armor CONTENT_AUTHZ on custom MCP path (#203) | **PARTIAL — ACCEPTED PLATFORM LIMITATION** (user decision 2026-09-05) | Exact spec wording (`specs/phase-1-release-criteria/spec.md` scenario "Custom MCP path covered (issue #203)"): "THEN that path has the same Model Armor coverage as the GKE Remote MCP path, **or the gap is explicitly named as unresolved**." Both disjuncts are factually satisfied (custom MCP and GKE Remote MCP show identical coverage gaps; the gap is now explicitly named and documented). Recorded as PARTIAL per explicit user instruction — NOT converted to PASS despite the literal wording match, because the underlying protection (response-body content blocking) is still genuinely absent. See evidence log for full platform-limitation case. |
| — | Final regression | **DONE** | Agent suite 480/480 passed, ruff clean, both Terraform stacks clean plan. 2 pre-existing unrelated gaps found and disclosed (not fixed, not caused by this branch): MCP test suite 7/68 fail on a `fastmcp` floating-version drift; `terraform test` can't run this repo's `.tftest.hcl` files under the company-pinned Terraform 1.4.7. See evidence log. |
| — | Merge | **DONE (2026-09-05, PR #242).** Post-merge regression found, fixed, and live-proven (PR #243). Final status: **Phase 1 COMPLETE — 8 requirements PASS, 1 PARTIAL — ACCEPTED GOOGLE PLATFORM LIMITATION, 0 unresolved implementation release blockers.** | |

## RESOLVED — REQUEST_AUTHZ staleness finding (was open, now closed 2026-09-05)

While chasing #203, found that gateway log entries for the custom MCP stopped appearing entirely after ~40 minutes of container uptime, while other traffic kept logging normally. This raised the question of whether REQUEST_AUTHZ *enforcement* (not just its log entry) also goes stale on a long-lived connection. **Now verified NO** — see the "RESUMED 2026-09-05" section above and the evidence log. The ~40-min logging gap is confirmed isolated to log visibility for that one MCP hostname's traffic; it does not affect the IAP authorization decision itself, which is evaluated live per-request regardless of connection age. This does NOT resolve the original CONTENT_AUTHZ logging/inspection gap — that remains open, see below.

## Cost ledger (updated)

- `sre-lab` kind cluster: local, free, kept running.
- Fleet membership: **torn down** (2026-09-05, cost hygiene) — re-apply command above.
- GKE `sre-test-cluster` fixtures: **torn down**.
- Custom MCP Cloud Run: `min_instance_count=0`, left running (near-zero idle cost).
- No new GCP projects, GKE clusters, or VMs created or left running.

## Full evidence
See `PHASE1_EVIDENCE_LOG.md` for the complete, chronological, command-by-command trail behind every claim above. See `docs/management/PHASE1_RELEASE_VALIDATION_REPORT.md` for the last full management report (written before the CONTENT_AUTHZ deep-dive concluded HOLD MERGE — treat its §9/§14 CONTENT_AUTHZ rows as superseded by this file and the evidence log, not the other way around).
