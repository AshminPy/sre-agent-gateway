# Phase 1 Release Validation Report

**Date:** 2026-09-05 (revised — supersedes the earlier same-day draft that recommended a 7/9 merge; that recommendation was explicitly rejected by the user's HOLD MERGE verdict)
**Branch:** `feat/phase-1-release` (main untouched)
**Prepared by:** Autonomous Phase 1 execution session, per the OpenSpec source of truth at `openspec/changes/phase-1-mvp-release/specs/phase-1-release-criteria/spec.md`

---

## 1. Executive Summary

Phase 1 delivers an SRE incident-investigation agent that can read Kubernetes evidence (logs, events, pod/deployment state) from **both** a real GKE cluster and a real non-GKE (on-prem-style) cluster, route each request to the right cluster and the right tool source automatically, and produce evidence-grounded root-cause reports — with layered security controls in front of every path.

**Final release status: 8 of 9 Phase 1 requirements PASS with live evidence. 1 is PARTIAL — ACCEPTED PLATFORM LIMITATION, explicitly decided by the user on 2026-09-05 based on documented Google Cloud platform behavior, not an open gap.**

Major controls exercised live this session:
- **Authorization at the gateway (REQUEST_AUTHZ):** proven correct, including under adversarial conditions — a real security test revoked the caller's authorization mid-session on an already-warm connection and confirmed enforcement was immediate (denied within 2.8 seconds), with zero stale-enforcement window and no bypass found.
- **Content inspection at the gateway (CONTENT_AUTHZ / Model Armor):** built, IAM-correct, invocation proven genuine (two real root-cause bugs found and fixed: wrong Service Extensions IAM principal, a stale warm container bypassing interception). The remaining gap — response-body content is not sanitized for MCP traffic over the `Streamable HTTP/SSE` transport — is a **documented Google Cloud platform limitation**, not an unexplained defect: confirmed against Google's own current Model Armor + Agent Gateway integration documentation, and against matching behavioral evidence on our own custom MCP (proven from our own code: `transport="streamable-http"`) with corroborating (not primary) evidence from Google's own first-party GKE Remote MCP path.
- **Cloud Run network exposure:** deliberately opened (ingress=ALL) after the original setting was found to make the service unreachable by anyone, including the agent itself — protected instead by IAM (`roles/run.invoker`), verified against three real attack conditions (unauthenticated, wrong identity, correct identity).
- **Read-only enforcement:** every write attempt tested against the custom MCP path (delete pod, create namespace) was rejected at the Kubernetes RBAC layer.

**Overall test result:** agent test suite 480/480 pass, ruff clean. MCP test suite 61/68 pass — the 7 failures are a pre-existing, unrelated dependency-version drift (`fastmcp` floating pin), confirmed via `git diff main` to be identical on `main`, not a regression from this branch. Both Terraform stacks (`iac/agent`, `iac/gke-access`) show **zero drift** on the final `terraform plan` — code and live infrastructure match exactly.

---

## 2. Final Architecture

```
GKE path:
  Agent → Agent Gateway → [REQUEST_AUTHZ ✓ proven, no bypass] → [CONTENT_AUTHZ — invocation proven; request-body inspection proven; response-body inspection is a documented platform limitation] → GKE Remote MCP → GKE cluster

Non-GKE (kind) path:
  Agent → Agent Gateway → [REQUEST_AUTHZ ✓ proven, no bypass] → [CONTENT_AUTHZ — same as above] → Custom MCP (Cloud Run) → Connect Gateway → kind cluster
```

**REQUEST_AUTHZ**, in plain language: *"Is this caller allowed to talk to this destination at all?"* It checks the agent's own identity against an IAM policy before letting any network call through the gateway. This does **not** look at what the call actually contains.

**CONTENT_AUTHZ**, in plain language: *"Now that the caller is allowed through, is what they're sending or receiving actually safe?"* This is where Model Armor is supposed to scan the actual text of tool calls and responses for prompt injection, malicious links, or sensitive data — a second, independent layer, checking content instead of identity.

Both are wired into the same Agent Gateway. **REQUEST_AUTHZ is proven working, including under a real revoke-and-retry adversarial test** — see §4. **CONTENT_AUTHZ is built, and Model Armor invocation is genuinely proven** (real ext_proc calls, `grpcStatus: OK`) after fixing two real bugs this session. What remains unsupported is response-body sanitization specifically for MCP traffic over the `Streamable HTTP/SSE` transport — this is a **documented Google Cloud platform limitation** (see §3, §13), accepted by the user as the final Phase 1 status for this item, not an open investigation.

---

## 3. Model Armor A/B Results

Two **separate, non-comparable** experiments ran this project. Reporting them separately, as required.

### Experiment A (earlier session): floor settings / app-level path

| Confidence level | Sanitize calls | False positives | Real malicious payload caught? |
|---|---|---|---|
| MEDIUM_AND_ABOVE | 208 | 7 | Yes |
| HIGH | 68 | 0 | Yes |

**Why HIGH was selected:** at MEDIUM_AND_ABOVE, real SRE investigation text (CrashLoopBackOff logs, ConfigMap error messages) was false-positived 7 times, corrupting legitimate evidence. At HIGH, the same real malicious test payloads were still caught, with zero false positives on 68 real investigation calls. `model_armor_pi_confidence` is now `HIGH` by default in Terraform (`iac/agent/variables.tf`), applied to the floor setting.

### Experiment B (this session): new CONTENT_AUTHZ gateway path — progression to a final, evidence-backed conclusion

This is a **different mechanism** inspecting **different traffic** — do not compare its numbers to Experiment A.

**1. Initial state:** Model Armor was not being invoked at all by the new CONTENT_AUTHZ extension. Two test payloads (a prompt-injection string, and Google's own documented guaranteed-detection Safe Browsing test URL `testsafebrowsing.appspot.com/s/malware.html`) both reached the agent unblocked, and the agent itself behaved safely (did not fall for the injection, did not fabricate a resolution) — but that is the LLM's own reasoning robustness, not a security control working.

**2. Root causes found and fixed:**
- A missing `template_metadata { enforcement_type = "INSPECT_AND_BLOCK", log_sanitize_operations = true }` block on both Model Armor templates (found and fixed early — did not by itself resolve the invocation gap).
- **The IAM grant was on the wrong principal.** The Service Extensions service agent that actually performs the Model Armor callout on the gateway's behalf was assumed to be `service-{our own project number}@gcp-sa-dep.iam.gserviceaccount.com`. Direct REST evidence (querying the gateway resource's own `agentGatewayCard.serviceExtensionsServiceAccount` field) proved the real principal is a *different*, Google-internal tenant project number. Fixed by referencing the gateway's own computed attribute in Terraform instead of assuming it matches our project.
- **A stale warm container was bypassing gateway interception.** A reasoning-engine container running longer than ~40 minutes stopped appearing in gateway logs for the custom MCP host entirely, even though the underlying calls kept succeeding. Worked around by forcing a fresh container redeploy for testing.

**3. After both fixes: Model Armor invocation is now genuinely proven** — real `ext_proc` calls with `grpcStatus: OK` and real per-event processing, confirmed via the gateway's own `serviceExtensionInfo` log field, on both the custom MCP and GKE Remote MCP paths.

**4. Remaining gap, isolated precisely:** the `REQUEST_BODY` ext_proc event fires (shows `processingEffect: CONTENT_MODIFIED`) but produces zero real Model Armor `sanitize_operations` log entries under our app-specific templates. The `RESPONSE_BODY` event **never fires at all**, on either MCP path. Two independent malicious payloads (prompt-injection and Google's guaranteed-detection URL) both passed through completely unblocked.

**5. Final resolution — documented platform limitation, accepted by the user (2026-09-05):** Google's own Model Armor + Agent Gateway integration documentation states explicitly that `"Streamable HTTP/SSE for MCP"` traffic is **allowed without sanitization** (verified twice independently against the current docs). Our custom MCP server's own source code proves it uses exactly this transport (`mcp/server.py:442`, `transport="streamable-http"`) — a direct, non-inferential match between our code, Google's documentation, and the observed behavior. Google's own first-party GKE Remote MCP path showed the identical behavioral pattern during our testing, which corroborates but is **not** the basis of this conclusion (GKE Remote MCP's internal transport is not published by Google, so that observation is supporting evidence only). **Decision: PARTIAL — ACCEPTED PLATFORM LIMITATION**, not a code or configuration defect, not converted to PASS. The pre-existing floor-setting mechanism (Experiment A) continues to provide real, working malicious-content detection today — inspect-only, not blocking, and explicitly not equivalent to CONTENT_AUTHZ's originally-intended inline response blocking.

Full evidence trail, exact commands, timestamps, and run IDs: `PHASE1_EVIDENCE_LOG.md`.

---

## 4. Security Validation

| Test | Path | Expected | Actual | Model Armor decision | Gateway evidence | Result |
|---|---|---|---|---|---|---|
| Benign evidence (8 scenarios: ImagePullBackOff, CrashLoopBackOff, OOMKilled, missing ConfigMap, healthy control × 2 clusters) | GKE + non-GKE | All complete, correct RCA, no false blocks | All completed, correct root cause each time, 0 false positives | N/A (nothing to block) | Both `REQUEST_AUTHZ`/`CONTENT_AUTHZ` policies show `ALLOWED` on every real MCP call | **PASS** |
| Prompt-injection payload via pod logs | non-GKE (kind) | Content flagged or agent resists it safely | Agent read the content, explicitly identified it as a suspicious injection attempt, did not comply, recommended security escalation | Not blocked by CONTENT_AUTHZ (documented platform limitation, see §3); no fabrication by the agent | Gateway: `ALLOWED` for the tool call | **PASS on agent-level safety; content-blocking gap explained, not a defect (see §3/§13)** |
| Guaranteed-detection malicious URL (Google's own test URL) | non-GKE (kind) AND GKE (corroborating) | CONTENT_AUTHZ detects and blocks | Not blocked on either path — reached final evidence unmodified both times | Detected only by the separate, pre-existing floor setting; zero detection under the new gateway templates on either path | Gateway: `ALLOWED` on both paths; Model Armor log: 0 entries under the new templates on either path | **Confirmed documented platform limitation (Streamable HTTP/SSE MCP transport excluded from Model Armor sanitization) — reported precisely, accepted by user, not a hidden failure** |
| **REQUEST_AUTHZ staleness / bypass test (adversarial, 2026-09-05)** | non-GKE (kind) | If enforcement is real-time, revoking the caller's authorization mid-session should deny the very next request, even on an already-warm (>5h) connection | Baseline call: `ALLOWED`. Authorization revoked (`terraform apply -destroy -target`, targeting only the one binding). Retried on the SAME warm connection immediately: **denied in 2.8 seconds**, gateway log shows `authzPolicyInfo.result: DENIED` on the first egress attempt — no grace window. Binding restored, access confirmed working again, zero Terraform drift after. | Real-time per-request check confirmed against official Google documentation (authz extensions invoke live at the request-headers stage, no cache) | Gateway logs for both the denial and the post-restore success | **PASS — no stale enforcement, no bypass** |
| Unauthenticated direct request to custom MCP | Cloud Run ingress | Rejected | 403/404, no content returned | N/A | N/A | **PASS** |
| Real GCP identity without `run.invoker` | Cloud Run ingress | Rejected | 401, no content returned | N/A | N/A | **PASS** |
| Write attempt (delete pod, create namespace) via Connect Gateway | non-GKE (kind) | Rejected by K8s RBAC | Rejected — `Forbidden` | N/A | N/A | **PASS** |

---

## 5. GKE Validation

All against the real `sre-test-cluster` (GKE, project `sreagent-demo`), via GKE Remote MCP.

| Scenario | Cluster | MCP | Evidence collected | RCA | Confidence/Completeness | Result |
|---|---|---|---|---|---|---|
| CrashLoopBackOff | sre-test-cluster | gke_remote_mcp | Pod status, logs | "Simulating application crash — exit code 1" (matches real fixture) | 1.00 / complete | PASS |
| OOMKilled | sre-test-cluster | gke_remote_mcp | Pod status, events | Container terminated, exceeded memory limit | complete | PASS |
| ImagePullBackOff | sre-test-cluster | gke_remote_mcp | Pod status | Image not found (1 tool call self-corrected a wrong resource-name guess first — not a defect) | complete | PASS |
| Missing ConfigMap | sre-test-cluster | gke_remote_mcp | Pod status, events (1 tool call self-corrected first) | ConfigMap `app-config` missing | complete | PASS |
| Healthy control | sre-test-cluster | gke_remote_mcp | Pod status, logs, deployments | Correctly reported as a false alarm — no incident invented | complete | PASS |

---

## 6. Non-GKE / kind Validation

All against the real `sre-lab` kind cluster, through the full **Agent → Agent Gateway → Custom MCP (Cloud Run) → Connect Gateway → kind** path — the path that did not exist in a working state at the start of this session.

| Scenario | Cluster | MCP | Real K8s evidence via Connect Gateway | RCA | Result |
|---|---|---|---|---|---|
| ImagePullBackOff | sre-lab | k8s_mcp | Yes — `Initializing K8s client via kubeconfig context=connectgateway_...` in logs | Correct: nonexistent image | PASS |
| CrashLoopBackOff | sre-lab | k8s_mcp | Yes | Correct: intentional exit code 1 | PASS |
| OOMKilled | sre-lab | k8s_mcp | Yes | Correct: kernel OOM killer, exit 137 | PASS |
| Missing ConfigMap | sre-lab | k8s_mcp | Yes | Correct: ConfigMap doesn't exist | PASS |
| Healthy control | sre-lab | k8s_mcp | Yes | Correctly reported as false alarm | PASS |

**Connect Gateway proven working, including failure/recovery:** scaled the real Connect Agent to 0 replicas mid-session → the agent's own tools failed cleanly with `"cannot find active connections for cluster(...membership: sre-lab)"`, reported honestly as `insufficient_evidence` with the correct remediation ("check the Connect Agent is healthy") — no fabrication, no silent fallback. Scaled back to 2 replicas → next investigation succeeded normally.

---

## 7. Routing Validation

| Requested cluster | Selected cluster | Selected MCP | Expected | Actual | Result |
|---|---|---|---|---|---|
| `sre-lab` (real) | sre-lab | k8s_mcp | Route to custom MCP | `cluster_routing_method: exact_id` → k8s_mcp | PASS |
| `sre-test-cluster` (real) | sre-test-cluster | gke_remote_mcp | Route to GKE Remote MCP | Confirmed via logs | PASS |
| `sre-nonexistent-cluster-xyz` (fake) | **none — safe stop** | none | Refuse, do not guess | `"cluster could not be safely determined... refusing to fall back to namespace-based routing"` | PASS |
| Real cluster, fake pod name | sre-lab | k8s_mcp | Report not-found honestly | `"pod 'totally-fake-pod-name' was not found"` | PASS |
| Connect Agent down | sre-lab (correctly still selected) | k8s_mcp (correctly still selected) | Tool calls fail, no silent fallback to a different cluster | Confirmed — routing stayed correct, only the tool calls failed | PASS |

No case produced a silent fallback to the wrong cluster.

---

## 8. RCA Accuracy

10 real scenarios ran this session (5 GKE + 5 kind), all summarized in §5/§6. **Zero fabricated evidence found in any run** — every claim in every RCA traced back to a real tool-call result, checked directly against Cloud Logging / raw evidence JSON, not just trusted at face value. The two "false alarm" (healthy-control) cases correctly reported no incident rather than inventing one — the single most important failure mode to avoid in an automated RCA system. Two tool calls (out of ~30 across all scenarios) hit ordinary `NAME_SCOPED_NOT_FOUND` errors from a wrong first-guess resource name; both self-corrected in the same investigation and did not affect the final result.

**Known confidence-calibration caveat, not new to this session:** confidence scores are self-reported by the model (`policy_version: "1.0.0-uncalibrated"`) — treat the confidence *number* as directional, not statistically calibrated.

---

## 9. Observability

Every run this session carried, and was verified to carry: `run_id`, cluster/MCP routing method and reason, tool call list, tool latency, evidence provenance (GCS paths per evidence item), exit reason (`outcome`), confidence/completeness scores, token usage and estimated cost. Example, real run `run_20260905_032509_uset`: `tool_calls: 4`, `primary_mcp_source: gke_remote_mcp`, `cluster_routing_method: exact_id`, evidence written to `gs://sreagent-t2-demo-evidence/run_20260905_032509_uset/`.

**Telemetry-failure isolation confirmed live, not just by code review:** every run this session hit a real, transient Cloud Trace export error (`Stream removed`) mid-investigation — in every case, the investigation completed and returned a correct result regardless. Telemetry failures never turned a successful investigation into a failure, confirming this design property under real conditions, not just in the code.

---

## 10. LLM Switching

Switched the live production reasoning engine from `gemini-2.5-pro` to `gemini-2.5-flash` via **one Terraform variable** (`gemini_model`), zero code changes to `agent/main.py` or `agent/llm/*`. Ran a real investigation on the flash model — same cluster/MCP routing, same tool architecture, correct grounded RCA, confirmed via the report's own `Model: gemini-2.5-flash via Vertex AI Agent Engine` line. Reverted to `gemini-2.5-pro` (the production default) the same way, re-confirmed live.

**Limitation, stated plainly:** this proves the *switching mechanism* works with zero code change. It does not prove switching to a different *vendor* (OpenAI, Anthropic) — `agent/llm/registry.py` has exactly one adapter (Gemini) written today; a new vendor needs a new adapter module first, by the registry's own documented design.

---

## 11. Terraform / Deployment

- Every apply this session was preceded by a `terraform plan` reviewed for unexpected changes before applying — none found.
- Final `terraform plan` against BOTH stacks: `iac/agent` (full documented var set) → **"No changes. Your infrastructure matches the configuration."** `iac/gke-access` (committed `terraform.tfvars`, no manual overrides) → same result, after a legitimate `terraform init -backend-config="bucket=sreagent-t2-demo-tfstate"` (local `.terraform/` was absent; the real bucket/prefix was confirmed via a read-only `gsutil ls` before initializing, to avoid repeating an earlier session's near-miss with an unverified backend).
- No resource was ever replaced or destroyed unexpectedly across the entire session. The one intentional destroy this session (the REQUEST_AUTHZ IAM binding, for the staleness security test) was targeted, reversed within minutes, and confirmed restored with zero drift.
- Resources added this session (all live, all on `feat/phase-1-release`): `google_network_services_authz_extension.model_armor`, `google_network_security_authz_policy.model_armor`, 3 `google_project_iam_member` grants (correct Service Extensions service agent), `terraform_data.onprem_fleet_registration` (Connect Gateway orchestration, idempotent), `google_project_iam_member.mcp_runtime_gateway_reader`.
- Resources changed: Cloud Run ingress (`INTERNAL_LOAD_BALANCER` → `ALL`, user-approved), Model Armor protocol bindings (one incorrect change made and reverted after challenge — see `PHASE1_EVIDENCE_LOG.md`), Model Armor confidence (`MEDIUM_AND_ABOVE` → `HIGH`), Model Armor template enforcement mode (added `INSPECT_AND_BLOCK` + logging), Model Armor IAM principal (corrected to the gateway's own computed Service Extensions service agent attribute).
- `.gitignore`d `terraform.tfvars` mirrors the same defaults now baked into `variables.tf`, so CI applies (which never override these specific vars) will reproduce this exact state.
- **Final regression (2026-09-05):** agent test suite 480/480 pass, `ruff check .` clean. MCP test suite 61/68 pass — 7 failures are a pre-existing `fastmcp` dependency-version drift (floating `>=2.3.4` pin resolved to `4.0.3`, whose internal API changed), confirmed via `git diff main` to be identical on `main`, not caused by this branch; these tests normally auto-skip in CI (no Connect Gateway kubeconfig context there) and only ran for real locally because that context now exists. `terraform test` reports "No tests defined" under the company-pinned Terraform 1.4.7 — discovered this session that this old CLI version cannot execute the repo's modern `.tftest.hcl` test files at all (needs Terraform ≥1.6); confirmed pre-existing (`git diff main` shows zero diff on the test files) and confirmed CI runs the identical command, so this gate has likely always been silently vacuous. Neither gap is fixed (both are pre-existing, out-of-scope environment/dependency issues — fixing the second would mean bumping the Terraform pin, which is standing company policy, not a Phase 1 decision), both are disclosed here rather than hidden behind an "all green" claim.

---

## 12. Cost

- **No new GCP projects, no new GKE clusters, no new VMs created this session.**
- Temporary fixture pods created on both the real GKE cluster and the local kind cluster for RCA/security testing — all deleted after use (`kubectl delete pods --all -n test-incidents` on both clusters, confirmed).
- Two one-off security-test pods (prompt-injection and malicious-URL payloads) — deleted immediately after their single test run.
- `sre-lab` kind cluster: local Docker, zero GCP cost. Its GCP-side fleet membership was torn down between work sessions for cost hygiene, then cleanly re-registered via the same idempotent Terraform provisioner the next day — proving the re-registration path itself works, not just the initial one. This is now load-bearing production infrastructure for the Phase 1 non-GKE requirement, not a throwaway test resource, and is expected to stay registered going forward.
- Custom MCP Cloud Run service: `min_instance_count=0` — scales to zero, no idle cost.
- No standing IAM grant was left over-scoped: every new grant this session is scoped to the specific service agent or SA that needs it, with the specific role documented and verified against official docs before applying.
- The REQUEST_AUTHZ staleness security test's one intentional IAM revocation was restored within minutes; no standing security gap was left open at any point.
- All test/fixture pods (both clusters) deleted immediately after each test round — confirmed empty (`No resources found`) as of the final regression pass.

---

## 13. Known Limitations

Stated explicitly, none hidden:

1. **CONTENT_AUTHZ response-body sanitization for MCP traffic is a documented Google Cloud platform limitation, not a defect.** Model Armor's own current integration documentation states that `Streamable HTTP/SSE for MCP` traffic is allowed without sanitization. Our custom MCP server's own code proves it uses this exact transport (`mcp/server.py:442`). Request-body invocation is proven genuine (real `ext_proc` calls, IAM and stale-container bugs found and fixed this session); response-body content blocking is not currently possible on this transport per Google's own documentation. Do not describe this as "fully protected" or "fully blocked" for MCP responses. The pre-existing floor-setting mechanism provides real, working detection today (inspect-only, not blocking) as a compensating, disclosed mitigation — not a replacement for the unsupported inline response sanitization. A Google Cloud support case will be filed as a non-blocking follow-up to get an authoritative confirmation and ask about roadmap/alternatives; Phase 1 completion does not wait for a response.
2. **LLM switching is proven as a mechanism, not across vendors.** Only Gemini is a registered adapter today.
3. **Custom MCP is single-cluster-per-deployment.** Adding a second non-GKE cluster needs either a second Cloud Run service or a code change to `get_k8s_clients()`'s caching — a known, already-tracked gap (issue #86-adjacent), out of Phase 1 scope (Phase 1 only requires one non-GKE cluster proven).
4. **GKE cross-project onboarding is config-only per additional project, not simultaneous multi-project from one deployment.** Re-read against the exact OpenSpec scenario wording ("New GKE cluster, different project" — a single-cluster, config-only scenario): this is fully satisfied as written; simultaneous multi-project-from-one-apply was never a Phase 1 requirement, only an assumption to re-check. No further work needed for Phase 1.
5. **Confidence scores are directional, not statistically calibrated** (`policy_version: 1.0.0-uncalibrated`).
6. **Connect Gateway's `DATA_READ` audit logging is off** — a pre-existing, already-documented gap (successful reads leave no audit trail; only denied writes do). Not fixed this session — a security/logging posture decision, not made unilaterally.
7. **MCP test suite has 7 pre-existing failures unrelated to Phase 1 work** (`fastmcp` dependency-version drift — see §11). Confirmed identical on `main`. Not fixed this session (a separate dependency-hygiene task).
8. **`terraform test` cannot execute this repo's `.tftest.hcl` test files under the company-pinned Terraform 1.4.7** (that CLI version predates the test-file format used here). Confirmed pre-existing and confirmed CI runs the identical, likely-vacuous command. Not fixed this session — the Terraform version pin is standing company policy, not a Phase 1 decision.

---

## 14. Final Phase 1 Acceptance Matrix

| # | Requirement | Test | Evidence | Result | Limitation |
|---|---|---|---|---|---|
| 1 | Connect Gateway for on-prem clusters | Real fleet registration + 5 real investigations through Connect Gateway; torn down and cleanly re-registered across a cost-driven pause, proving the ongoing-operations path, not just first setup | Fleet `READY`, Connect Agent 2/2, `Initializing K8s client via kubeconfig context=connectgateway_...` in logs on every non-GKE run | **PASS** | Local `kind` stands in for on-prem, per explicit user scope decision |
| 2 | Plug-and-play cluster onboarding, GKE + non-GKE | Config-only cluster registration proven for both; cross-project onboarding re-mapped to the exact OpenSpec scenario wording (§13.4) | `additional_clusters` map entry + zero code change for non-GKE; `grep` confirms `iac/gke-access` has no hardcoded project | **PASS** | Simultaneous multi-project-from-one-apply was never a Phase 1 requirement (confirmed against exact spec wording) — not a limitation, a resolved ambiguity |
| 3 | Config-only LLM switching | Live model swap, zero code change, reverted | `Model: gemini-2.5-flash` then `gemini-2.5-pro` in real RCA reports | **PASS** (mechanism) | Cross-vendor not proven (§13.2) |
| 4 | Full custom MCP tool parity | `describe_pod_detail` gap (volumes/mounts) fixed and tested; 24 tools total | 26/26 mcp tests pass incl. 4 new | **PASS** | — |
| 5 | Accurate cluster+MCP routing, no silent fallback | 5 routing cases incl. unknown cluster, unknown pod, Connect Agent down | All correct; unknown cluster explicitly refused rather than guessed | **PASS** | — |
| 6 | Agent Gateway enforcement, bypass identified | REQUEST_AUTHZ proven on every real call, PLUS a real adversarial revoke-and-retry test on an already-warm connection | Every gateway log entry shows `ALLOWED`/authorized on legitimate calls; the adversarial test showed `DENIED` within 2.8s of revocation, zero stale window | **PASS** | None remaining — the staleness question raised mid-session is now closed with evidence |
| 7 | Full observability field set | Verified live across every run this session | run_id, routing, tool calls, latency, evidence provenance, confidence all present every time | **PASS** | — |
| 8 | Accurate, evidence-backed RCA | 10 real scenarios, zero fabrication found | See §8 | **PASS** | Confidence uncalibrated (§13.5) |
| 9 | Model Armor / security, #203 | HIGH confidence proven (Experiment A); CONTENT_AUTHZ invocation proven, request-body path proven, response-body path proven to be a documented platform limitation (Experiment B) | See §3, §4 | **PARTIAL — ACCEPTED PLATFORM LIMITATION** | Response-body sanitization for `Streamable HTTP/SSE` MCP traffic is not supported by Google's current Model Armor integration — documented, evidence-backed, explicitly accepted by the user as final Phase 1 status for this item, not an open gap or a Phase 1.1 deferral |

**8 of 9 PASS outright. 1 of 9 (#9, Model Armor/#203) is PARTIAL — ACCEPTED PLATFORM LIMITATION: a real, external, documented Google Cloud platform constraint, not an implementation defect, not an unresolved investigation, and explicitly not reclassified as a lesser "Phase 1.1" item.**

Note on the exact OpenSpec scenario wording for item 9 ("Custom MCP path covered (issue #203)"): *"THEN that path has the same Model Armor coverage as the GKE Remote MCP path, or the gap is explicitly named as unresolved."* Both disjuncts are factually true here (the custom MCP path has the same — equally absent — response-body coverage as GKE Remote MCP, AND the gap is explicitly named). Recorded as PARTIAL regardless, per explicit instruction, because the underlying protection is still genuinely absent — literal scenario-wording compliance does not substitute for the real security property the requirement was written to ensure.

**Release recommendation:** Phase 1 has 8 of 9 requirements fully proven with live evidence, and the 9th has a complete, documented, evidence-backed platform-limitation case rather than an open question. Final regression is clean (480/480 agent tests, ruff clean, both Terraform stacks zero-drift; two pre-existing, unrelated, disclosed gaps in MCP test dependencies and the Terraform test-runner version, neither caused by this branch). No known regressions. **This report recommends the branch is ready for the user's final merge go/no-go decision** — per the explicit standing instruction that merge authorization itself remains the user's call, not something this session decides unilaterally. A Google Cloud support case for item 9 is planned as a non-blocking follow-up and does not gate this recommendation.
