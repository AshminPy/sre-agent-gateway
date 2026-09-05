## Validation Result (current `main` @ 2b59ef8, validated 2026-09-04)

Status legend: DONE / PARTIAL / NOT STARTED / RELEASE BLOCKER / NOT REQUIRED FOR PHASE 1

### 1. Connect Gateway for on-prem clusters — RELEASE BLOCKER (corrected 2026-09-04)
- **Correction:** this was NOT "never built." A real prototype was deployed and live-tested against a local `kind` cluster (`sre-lab`) standing in for on-prem, per `docs/connect-gateway-onprem.md` (2026-08-06/07): fleet membership registered, workload identity federation configured, read-only RBAC applied and verified (`get pods/deployments/events` succeeded, `create`/`delete`/`get secrets` correctly denied), an outage was simulated and auto-recovery confirmed, latency measured. `mcp/server.py`'s `get_k8s_clients()` has a real `K8S_MCP_KUBE_CONTEXT` mode for Connect Gateway — proven live per `PRODUCTION-LAUNCH-PLAN.md:587` (18/19 tools returned real data, output shape verified identical to GKE Remote MCP).
- After proving the concept, the GCP-side fleet membership was deliberately unregistered to avoid ongoing footprint — verified live 2026-09-04: `gcloud container fleet memberships list --project=sreagent-t2-demo` → 0 items. The local `kind` cluster itself was never deleted (`docker ps -a` shows `sre-lab-control-plane`/`sre-lab-worker`/`sre-lab-worker2` still running, 5 months old).
- What's still actually missing for release: the proven path was never wired into `agent/mcp_client.py`'s automatic cluster registry, never made Terraform-managed (no `google_gke_hub_membership` anywhere in `iac/`), and its integration test (`mcp/tests/test_live_connect_gateway.py`) is auto-skipped in CI (`docs/architecture/gke-vs-nongke.md:35`) — confirmed working once, by hand, never continuously verified. `docs/management/next-slice-connect-gateway-onboarding-2026-08-31.md` (a separate, later planning doc) lays out the follow-up work to take this from proven prototype to production: re-register the fleet membership via Terraform, bake the auth plugin into `mcp/Dockerfile`, extend the cluster registry schema, wire the Connect Gateway branch into `mcp/server.py`'s live routing path, un-skip the CI test, run one live external-cluster E2E test.
- Smallest fix: re-register `sre-lab` (or a real on-prem-representative cluster) as a Terraform-managed fleet membership, then execute the 6-step plan already written in the next-slice doc. Meaningfully less work than "build from scratch" — the hard credential/RBAC/audit questions are already answered with evidence in `docs/connect-gateway-onprem.md`. Still not realistic as a Phase 1 week-1 item given it needs new Terraform, a Dockerfile change, and a live E2E test.

### 2a. Plug-and-play GKE onboarding, same project — DONE
- Evidence: `iac/agent/main.tf:39-68` (`for`/`merge()` over `var.additional_clusters`), `iac/agent/variables.tf:41-66`, collision guard `iac/agent/buckets.tf:66-85` (`lifecycle.precondition`), no `ignore_changes` in `iac/agent/`. PR #52 (merged 2026-08-09, commit 7316aa3), live-verified against `sre-test-cluster`, Terraform test suite `iac/agent/tests/clusters_json.tftest.hcl` 4/4 passing.

### 2b. Plug-and-play GKE onboarding, different project — PARTIAL
- Evidence: `iac/gke-access/crossproject_iam.tf:27,37,56` grants IAM to a single scalar `var.project_b_id`. `mcp/server.py:78-79` `get_k8s_clients()` is `@lru_cache(maxsize=1)` — one client per process. Tracked as GitHub issue #86 (filed 2026-08-10, self-admitted in PR #52's "Known gap").
- Smallest fix: change `crossproject_iam.tf` to loop over a list of `(cluster, project)` pairs; change the MCP client cache key from `maxsize=1` to per-cluster.

### 2c. Plug-and-play non-GKE onboarding — NOT STARTED
- Same evidence as item 1 (Connect Gateway). No real registration mechanism exists; `onprem-dc1-cluster` is a fixture only.

### 3. Config-only LLM switching — PARTIAL
- Evidence: `agent/llm/base.py`, `registry.py`, `gemini_adapter.py` — a real provider-agnostic abstraction exists, but only one provider (Gemini) is registered today. Switching is architecturally config-only but has never been exercised against a second real provider.
- Smallest fix: register and live-test one additional provider adapter to prove the switch path works, not just that the interface exists.

### 4. Full read-only custom MCP tool parity — PARTIAL
- Evidence: 24 read-only tools exist in `mcp/tools/*.py`; concrete gap identified in `mcp/tools/pods.py`'s `describe_pod_detail` — no `volumes`/`volumeMounts` field, needed for the config/secret-error incident class.
- Smallest fix: add `volumes`/`volumeMounts` to `describe_pod_detail`'s return payload in `mcp/tools/pods.py`.

### 5a. Cluster/MCP routing logic — DONE
- Evidence: `resolve_cluster_routing()` in `agent/mcp_client.py` — deterministic 5-tier routing with explicit fail-closed safe-stop tier, `_build_cluster_registry()` returns `{}` (not a silent fallback cluster) on failure. 29/29 real tests passing (`test_multi_cluster_registry.py`, `test_cluster_unresolved_safe_stop.py`, etc.).

### 5b. Cluster/MCP routing, live non-GKE proof — RELEASE BLOCKER
- Evidence: no live run of this routing logic against a real non-GKE cluster exists (depends on item 1/2c being unbuilt). Only unit-test coverage today.

### 6a. Agent Gateway enforcement, no bypass — DONE
- Evidence: `docs/architecture/agent-identity.md:35` — Agent Gateway's IAP `REQUEST_AUTHZ` extension checks the caller against the target's IAM policy before allowing the request through; registry-level grant at `iac/agent/iap_egressor.tf:13-21`. Confirmed no direct unmediated Cloud Run invocation path for the custom MCP. The one out-of-Terraform path found (`attach_gateway_to_engine.sh`) is engine-to-gateway attachment, unrelated to MCP traffic routing.

### 6b. Agent Gateway content inspection for custom MCP — PARTIAL (latent)
- Evidence: routing/authz is enforced; Model Armor content inspection of custom-MCP traffic is not (same underlying gap as item 9's #203). Currently latent because custom MCP isn't live as the primary path yet — becomes a live gap the moment #203 work starts.

### 7. Full observability field set — DONE
- Evidence: `agent/main.py` lines ~556-670 — confirmed fields `run_id`, `cluster_routing_method`, `primary_mcp_source`, `evidence_count`, `evidence_storage_ok`, `mcp_latency_s`, `model_latency_s`, `total_latency_s`, `tool_calls`. Telemetry-failure guard at 550-618 confirmed: a telemetry failure degrades the event, not the investigation result.

### 8. Accurate, evidence-backed RCA — DONE
- Evidence: across all 3 Model Armor A/B/C test rounds (Phase A/B/C, PRs #238-241), zero fabricated RCA content found on manual review; RCA conclusions traced to real `evidence_count`/tool-output data in each run. One confidence-score anomaly (oomkilled 0.94→0.65) was checked against real Cloud Logging and attributed to normal LLM run-to-run variance, not a defect — this cross-check process is itself the evidence the RCA pipeline is being held to a real standard.

### 9a. Model Armor positive block test — DONE
- Evidence: block-mode A/B test (PR #240, HIGH confidence `pi_and_jailbreak`) blocked known malicious payloads, confirmed via `modelarmor.googleapis.com%2Fsanitize_operations` Cloud Logging entries showing `filterMatchState` matches.

### 9b. Model Armor false-positive cost — DONE (at HIGH confidence)
- Evidence: quantified result across test phases — 7/208 false positives at MEDIUM_AND_ABOVE confidence, 0/68 at HIGH confidence. Recommendation: run floor settings at HIGH confidence for `pi_and_jailbreak`.

### 9c. Model Armor coverage for custom MCP path (#203) — NOT STARTED
- Evidence: `agent/main.py`'s `_sanitize()` app-level client exists but is dead code (`MODEL_ARMOR_TEMPLATE` env var unset). No live test of custom-MCP-as-primary with Model Armor enabled has been run. Issue #203 open.
- Smallest fix: set `MODEL_ARMOR_TEMPLATE`, wire the app-level `_sanitize()` call into the custom MCP request path, then re-run the same 6 K8s scenarios with custom MCP as primary.

## Final Verdict

**PHASE 1 CANNOT SAFELY RELEASE NEXT WEEK.**

Two items are RELEASE BLOCKERs, not PARTIALs: (1) Connect Gateway / non-GKE clusters was proven once as a real prototype, then its GCP-side fleet membership was torn down and it was never wired into automatic routing or made Terraform-managed — so nothing is live today, even though the hard technical questions are already answered with evidence. (5b) cluster/MCP routing has never been proven live against a non-GKE cluster because there is currently no registered non-GKE cluster to prove it against. If "Phase 1" is scoped to GKE-only clusters, both blockers disappear and the remaining PARTIAL items (2b cross-project IAM, 3 LLM switching, 4 tool parity, 6b/9c Model Armor custom-MCP coverage) are real but non-blocking gaps that can be fixed or explicitly deferred with a documented risk note.
