# PHASE 1 FINAL READINESS — execution state

Review branch: `phase1-final-readiness-review`, base main `dd84660fdd177256be0c2af29514599199b55bca`.
Do NOT merge to main. Do not close issues. Do not mark tracker rows Completed while work is
branch-only. Full evidence: `PHASE1_EVIDENCE_LOG.md` (append-only, this task's entries are under
the `2026-09-06 — PHASE 1 FINAL READINESS review branch` headers). This file is the live summary —
update after every section, don't let it drift.

## Section status (17 sections, per the user's plan)

| # | Section | Status | Notes |
|---|---|---|---|
| 1 | Baseline (main HEAD, 1 GKE + 1 kind investigation) | **DONE** | Kind: `run_20260906_234504_hwck`/`_234710_tdzo`/`_234738_ynxp` (sre-lab). GKE: `run_20260907_000827_lqdb` (sre-test-cluster, project `sreagent-demo`), root_cause_confidence 1.0, investigation_completeness complete/1.0, zero errors. Fixture applied+deleted, cost hygiene preserved. Sequencing note: baseline ran on review-branch code (post-#246-fix), not pre-fix main — see evidence log. |
| 2 | Fix #86 (multi-cluster correctness) | **DETERMINED — NO CODE FIX** | Confirmed real (5-finding breakdown from 2026-08-09 audit still accurate) but NOT currently exploitable — today's deployment is exactly 1 cluster per MCP type. A real fix is a major architecture redesign (per-cluster `mcp_url`, keyed client cache, per-project IAM) — triggers this task's own stop condition. Not fixed. Issue stays open, not touched further this task. |
| 3 | Fix #246 (cluster-scoped tool args) | **DONE — live-proven** | See `PHASE1_EVIDENCE_LOG.md` 2026-09-06 entry. Committed `6315ed3`. Unit tests 11/11 pass, full suite 491/491 pass, ruff clean, deployed to test env, live-tested (list_nodes, describe_node x4, 5 normal namespaced tools) — zero errors, namespace present/absent exactly as designed. |
| 4 | Verify #203 (CONTENT_AUTHZ / response guard) | **DONE** | Verified live, not re-implemented. Response guard deployed + actively blocking (real BLOCKED event 2026-09-06T19:28Z). Both `iap` and `model_armor` authz extensions confirmed `fail_open=false` in live state. Floor settings confirmed HIGH+INSPECT_ONLY (`inspect_and_block=false`). RESPONSE_BODY gap re-confirmed present (3-day log spot-check, zero events) — unchanged platform limitation. Fail-open alert present, never fired. Google Support case confirmed still draft/unfiled. See evidence log. |
| 5 | Verify #202 (floor-setting block mode) | **DONE — determined NOT PERFORMED, left OPEN** | The diagnostic itself requires temporarily flipping global floor settings to block mode, which this task's own Section 5 rule forbids. Precondition unmet → correctly left open, not forced closed, not treated as a 50-run blocker (live config confirmed safe: inspect-only, no fabrication behavior observed in any run this session). PR #199's code fix (`_is_model_armor_blocked_result`) confirmed still present/wired at `agent/mcp_client.py:405,787`. See evidence log. |
| 6 | Review: calibration count, Connect Gateway DATA_READ audit logging, FastMCP test failures, eval-quality gate (defer to Phase 2) | **DONE** | (A) 16 golden cases confirmed (code, not assumed). Last run 2026-09-05: strict pass 2/16, but outcome_ok 12/16, confidence_ok 14/16 — headline number inflated by rigid trajectory-matching + 3 missing-fixture cases. 4 real outcome mismatches flagged as genuinely unvalidated, not fixed. (B) DATA_READ audit logging gap confirmed real; NOT implemented — cost can't be soundly bounded, treated as owner decision, recommendation only. (C) FastMCP 7/73 failures re-confirmed unchanged, pre-existing, unrelated to #246 (spot-checked). (D) eval-gate deferred to Phase 2, untouched. See evidence log. |
| 7 | Hard gate before 50-run campaign | **PASS — all 15 items** | PR #251 opened (NOT for merge, CI-trigger only) — python-tests + terraform-plan both success. Both Terraform stacks: no drift. Alternating GKE→kind→GKE: 2 transient Gemini 500 errors on first GKE attempts (known pre-documented pattern, root-caused, not a defect), both succeeded clean on retry with correct cluster-scoped evidence (`source=gke_remote_mcp`/`k8s_mcp` matched requested cluster every time, zero cross-contamination). See evidence log for full 15-item checklist. |
| 8 | Build 50-case catalog (25 GKE / 25 kind) | NOT STARTED | Gate passed — cleared to proceed. |
| 9 | Run 50 investigations | NOT STARTED | Real cost — GCP LLM calls x50. |
| 10 | Looker Studio readiness data | NOT STARTED | |
| 11 | Cleanup fixtures + final Terraform plan | NOT STARTED | |
| 12 | Memory Bank design audit | NOT STARTED | |
| 13 | MCP extensibility design (design only) | NOT STARTED | |
| 14 | Documentation cleanup/sync | NOT STARTED | |
| 15 | GitHub + tracker sync (In Progress language only) | NOT STARTED | |
| 16 | Final regression vs Section 1 baseline | NOT STARTED | |
| 17 | Final consolidated report + READY/CORRECTIONS/NOT READY verdict | NOT STARTED | STOP after this, wait for user review, no merge. |

## Known live-environment facts (verified this session, 2026-09-06)

- gcloud binary: NOT on default shell PATH, but installed at `~/Downloads/google-cloud-sdk/bin/gcloud` — use `export PATH="$HOME/Downloads/google-cloud-sdk/bin:$PATH"` per command.
- Active gcloud account: `ashmin.sub@gmail.com`, project `sreagent-t2-demo` (personal test env — correct scope).
- Fleet membership `sre-lab`: LIVE (confirmed via `gcloud container fleet memberships list`) — the PHASE1_EXECUTION_STATE.md "Cost ledger" section saying "torn down" is STALE, do not trust it; the file's own "RESUMED 2026-09-05" section (re-registered) is the accurate one, now independently re-confirmed live today.
- kind cluster `sre-lab`: running (Docker containers up 4+ weeks).
- Custom MCP Cloud Run image currently live: `us-central1-docker.pkg.dev/sreagent-t2-demo/sre-agent-repo/sre-k8s-mcp:7624897239d597c3b6e26bd1e38ba32fe55be3ac` (do NOT use the stale tag in `/tmp/phase1_mcp_image.txt` — it does not match live).
- Reasoning engine `sre_agent` currently deployed with the review branch's `agent/` code (as of the #246 fix apply, 2026-09-06) — NOT main's agent code. Remember this when doing Section 4/5 verification (behavior reflects review branch, not main) and when doing Section 16 final regression (compare against Section 1 baseline, not against main).
- `imagepull-pod` fixture does NOT currently exist on `sre-lab` (404 on describe_pod_detail during #246 live test) — will need recreating for the 50-run campaign if that scenario is reused.
- Terraform apply var set for `iac/agent` (personal test env): `-var="create_wif=false" -var="enable_custom_mcp=true" -var="custom_mcp_image=<live tag above>" -var="custom_mcp_kube_context=connectgateway_sreagent-t2-demo_global_sre-lab" -var="onprem_fleet_membership=sre-lab"`.
- Agent invocation pattern: `invoke_agent.py` (fixed scenarios) or a custom script using `aiplatform_v1beta1.ReasoningEngineExecutionServiceClient` + `query_reasoning_engine(request={"name": f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/{ENGINE_ID}", "input": {...}})`. Output dict has key `tool_calls` (NOT `tool_history` — that name is only used internally in agent state, not in the API output). For real tool-by-tool evidence, query Cloud Logging instead: `resource.type="aiplatform.googleapis.com/ReasoningEngine" textPayload:"mcp_router →"` (agent-side routing decisions) and `resource.type="cloud_run_revision" resource.labels.service_name="sre-k8s-mcp" textPayload:"sre-mcp.audit"` (MCP-server-side tool execution results) — bound with explicit `timestamp>=...` / `timestamp<=...`, NOT `--freshness`, which returned weeks-stale results when combined with `--order=asc` in this environment (confirmed bug in this gcloud invocation pattern — always use explicit timestamp bounds).
- Known benign trailing atexit tracebacks in both pytest runs and Cloud Trace exports (`NotFound: 404 projects/your-gcp-project-id`, or `PermissionDenied: cloudtrace.traces.patch ... test-project`) — always appear AFTER the real result line, unrelated to it. Never pipe these commands through `tail -N` in the original command (it can truncate the real result behind a longer trailing traceback) — always redirect fully to a file first, then grep/inspect.

- `sre-test-cluster` (GKE) lives in project **`sreagent-demo`** (Project B), region `us-central1` — NOT `sreagent-t2-demo` (Project A, where the agent/gateway/custom-MCP infra lives). Confirmed via `gsutil cat gs://sreagent-t2-demo-cluster-config/clusters.json`. Use `gcloud container clusters get-credentials sre-test-cluster --region us-central1 --project sreagent-demo`. It runs with 0 idle nodes (autoscales 0→1 on first pod schedule) — applying any fixture pod triggers a real (small, short-lived) node scale-up; delete the fixture immediately after each test.
- `test-incidents` namespace on both clusters currently has NO pod fixtures (both `crashloop-pod` on GKE and `imagepull-pod` on sre-lab were torn down in the 2026-09-05 cost-hygiene pass and never recreated) — any scenario needing a specific fixture must (re)apply it first. Existing fixture manifests are in `k8s/*.yaml` (confirmed `k8s/crashloop-pod.yaml` exists and works).

## Immediate next step (if resuming)

Section 1 is DONE. Proceed to Section 4 — verify (do not re-implement) issue #203's current
state: custom MCP response guard still deployed/working, REQUEST_AUTHZ still fail-closed,
Gateway CONTENT_AUTHZ behavior, Model Armor floor settings (HIGH+INSPECT_ONLY), the GKE Remote
MCP RESPONSE_BODY platform-limitation documentation, and whether the Google Support case is
still outstanding. Then Section 5 (#202 verify-only), then Section 6 (calibration count,
Connect Gateway audit logging, FastMCP drift, eval-gate deferral).
