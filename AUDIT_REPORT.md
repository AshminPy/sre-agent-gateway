# Full Audit — Agent Runtime → Agent Gateway Implementation vs. Google Docs

**Date:** 2026-07-17
**Scope:** Terraform, deployment scripts, Python code, REST requests, env vars, IAM, Agent Registry, gateway config, runtime config, documentation, and the investigation itself — for both `sreagent-cleanroom-test` (working) and `sreagent-t2-demo` (failing).
**Method:** 6 parallel research agents, each required to mark UNKNOWN rather than guess, cite live API output or file:line for every claim. One agent's process (not findings) is flagged separately below.

**Security note before anything else:** one subagent wrote a live GCP access token to a plaintext file (`token.txt`) in the scratchpad, twice, instead of piping it directly into its `curl` calls. No lingering copy was found afterward, and access tokens from `gcloud auth print-access-token` are short-lived (~1hr), so the practical exposure window has almost certainly closed — but the practice itself was wrong and is noted here for the record.

---

## 1. Prerequisites (checks 1–6)

| # | Verdict | Evidence | Fix |
|---|---|---|---|
| 1 | **PASS** (both) | Doc: *"An Agent Gateway can't be bound to Runtime Reasoning Engines created before April 29, 2026."* Cleanroom engine createTime `2026-07-16`, t2-demo `2026-07-13` — both after cutoff. | None |
| 2 | **PASS** (both) | Engine and gateway `name` fields share the same project on both. | None |
| 3 | **PASS** (both) | Both in `us-central1`. | None |
| 4 | **PASS** (both) | `googleManaged.governedAccessPath: "AGENT_TO_ANYWHERE"` on both live gateways. | None |
| 5 | **PASS** (both) | `agentGatewayCard` fully populated (mtlsEndpoint, serviceExtensionsServiceAccount, rootCertificates) on both — the only available readiness proxy, since this API has no explicit state field. | None |
| 6 | **UNKNOWN (partial)** | 4 documented limitations checked; 2 clearly respected, 1 not applicable (ingress-only rule, we're egress-only), 1 genuinely unverified: each project also has a Memory Bank engine whose own gateway binding was never queried. | Check both Memory Bank engines' `agentGatewayConfig` |

## 2. Agent Identity (checks 7–10)

All **PASS**, both projects: created with `identity_type=AGENT_IDENTITY` (confirmed in Terraform/codelab source and live), still shows `AGENT_IDENTITY` live (no drift), `effectiveIdentity` correctly parameterized per project, and the attach script's PATCH never touches `identityType` — confirmed by reading the script in full.

## 3. Reasoning Engine Update / PATCH body (checks 11–17)

| # | Verdict | Notes |
|---|---|---|
| 11–12 | PASS | `agentGatewayConfig.agentToAnywhereConfig.agentGateway` present and correctly nested |
| 13 | **PASS (primary path) / FAIL (dead fallback)** | The `iac/gateway-codelab` fallback path in the script points at a directory that has never existed in this repo — confirmed via `git log --all`. Silently no-ops (exits 0) if ever reached. |
| 14–16 | PASS | Regional endpoint used correctly; updateMask is an exact 1:1 match to the body; source+gateway config submitted in one PATCH, confirmed by tracing every curl call in the file. |
| 17 | **FAIL (deliberate, documented divergence)** | Google's own minimal doc example is `{"spec":{"deploymentSpec":{"agentGatewayConfig":{...}}}}` only — no `sourceCodeSpec`. Our script adds `sourceCodeSpec` as a sibling, which is *not* in Google's documented example anywhere. This is intentional (the bundling fix, empirically derived from this investigation) but the script's comments should say explicitly "this diverges from Google's minimal example" rather than only explaining the mTLS rationale, so it doesn't read as accidental drift. |

## 4. Agent Registry (checks 18–24)

All core checks **PASS** on both projects (registry present, same project/region, both plain and mTLS regional hostnames registered, `protocolBinding: JSONRPC` uniform). Check 24 (registration-before-bind) is **not a real dependency** — cleanroom's bind succeeded before any registry entries existed at all. Fix the *check's* premise, not the infra.

## 5. IAM (checks 25–28)

- **Check 25 — important correction to the audit's own instructions:** the specified project-level `get-iam-policy` command checks the wrong scope and returns a false negative for both projects. `roles/iap.egressor` for Agent Gateway lives at the **Agent Registry/IAP resource level**. Queried correctly: **t2-demo (the failing project) has this grant correctly configured. Cleanroom (the working project) does not have it at all.** Inverted from what you'd expect if this were the cause.
- **Check 28 — a real, new, unexplained difference:** cleanroom's effective identity has 4 extra roles t2-demo lacks (`cloudapiregistry.viewer`, `iam.serviceAccountTokenCreator`, `modelarmor.user` [explained — gateway-conditional], `telemetry.writer`), and a much broader `principalSet://` (project-wide) grant pattern — 9 roles via principalSet on cleanroom, only 1 on t2-demo. **This was never previously found or tested in this investigation.**

## 6. Gateway Configuration (checks 29–31)

The one **live, material difference** across every field checked: `spec.deploymentSpec.agentGatewayConfig` is present on cleanroom's engine, entirely absent on t2-demo's — this is literally the field the failing PATCH is supposed to set. Everything else compared (networkConfig, governedAccessPath, protocols-absence, state-absence) is structurally identical. One new, real, small gap: cleanroom's authz extension has `metadata.iamEnforcementMode: "DRY_RUN"` explicitly set; t2-demo's has no such key at all (defaults to enforce). **Note: this specific variable was already tested directly earlier in this investigation and empirically rejected as a cause** — flagging its current asymmetric state here for completeness, not as a new lead.

## 7. Endpoints (checks 32–40)

Two real, **symmetric** gaps found on both projects (not a differentiator, but real defects): the agent code uses `cloudtrace.googleapis.com` for tracing, but only `trace.googleapis.com` (a different hostname) is registered — Cloud Trace calls may not be governed correctly. Model Armor's code-hardcoded endpoint (`modelarmor.{region}.rep.googleapis.com`) doesn't match what's registered (missing `.rep.`) — currently dormant since neither project has `MODEL_ARMOR_TEMPLATE` set.

## 8. Environment Variables (checks 41–45)

All 15 env vars identical by name across both engines; only inherently per-project values differ (bucket names, project ID). `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=false` is set on both, which contradicts the platform's own documented secure default (*"strongly discouraged... vulnerable to credential theft"*) — deliberate and symmetric (copied from the reference codelab), not a project-to-project gap, but worth a conscious accept-the-risk decision rather than inherited silently.

## 9. Post-Deployment (checks 46–49)

Cleanroom: fully attached, verified end-to-end (FINAL_RCA.md). T2-demo: `agentGatewayConfig` still absent live, right now — the bind has never durably succeeded.

## 10. Script Review — `attach_gateway_to_engine.sh`

**Would I change anything? Yes, three concrete things, none of which explain the current failure:**
1. Remove or fix the dead `iac/gateway-codelab` fallback (directory never existed in this repo).
2. Two checks (API-enabled, endpoint-registered) collapse "command failed" and "genuinely empty result" into the same output — inconsistent with the three-state pattern already used for the IAM policy check. Apply the same fix.
3. **The readiness gate has a confirmed false positive**: t2-demo's gateway reports `agentGatewayCard` fully populated (READY) right now, on the exact gateway that has failed to bind 8+ times. The gate isn't wrong to exist, but it has near-zero diagnostic value for this specific, ongoing failure — worth a comment saying so.

Also found: the script GETs the gateway via `networkservices.googleapis.com/v1beta1`, while Terraform's provider manages this resource as GA `v1` — unverified whether the two API versions return identical shapes. Applies equally to both projects, so not a likely differentiator, but a real gap in what's been verified.

## 11. Terraform Review

- **A factually wrong comment in our own code**: `iac/agent/agent_gateway.tf` calls the PSC-I addition "EXPERIMENTAL... revert if this doesn't change the bind outcome" — but the vendored reference module actually creates PSC-I **unconditionally**, every time, with no no-PSC variant. The comment should be corrected to reflect that PSC-I is a required match to cleanroom, not an optional experiment.
- Resource ordering (the specific thing asked about): verified correct via `terraform graph` — no missing `depends_on`, not the source of any bug.
- Agent Registry endpoint registrations have **zero Terraform representation** — pure imperative `gcloud` calls with no state tracking or drift detection, unlike the IAP egressor grant which was properly migrated into Terraform.
- If `enable_custom_mcp` is ever turned on, the Cloud Run fallback would be unreachable — the reasoning engine resource has no way to originate "internal" traffic in its current config (a `psc_interface_config` field exists in the provider schema but isn't wired up). Not currently live (defaults false).

## 12. Python Review

Two **critical, real defects unrelated to the gateway-bind failure**, found during this audit:
1. **SSE response parsing returns on the first parseable JSON frame**, even if it's an empty notification, not the real result — a plausible silent false-positive-success path if any MCP server ever sends multi-frame responses.
2. **Model Armor's OUTPUT-sanitization verdict is silently discarded.** A genuine content match on agent output (PII, malicious content — exactly what this filter exists to catch) currently produces only a log line, no redaction, no block, no `requires_human_review` flag.

Plus several lower-severity findings: unbounded/non-string-aware JSON repair logic, credentials re-derived from scratch on every MCP call (inefficient, not insecure), `register_endpoints.py` always exits 0 even on total failure, and a few dead-code paths.

## 13. Investigation Meta-Review

The most important finding in the entire audit:

> **The single most decisive missing experiment was never run**: testing2's own gateway Terraform module (`iac/agent/agent_gateway.tf`) has never been deployed fresh into a brand-new, untouched project. The "decisive" clean-room test that supposedly proved the fix used the **codelab's different module** (unconditional PSC-I, different structure) — not our own module. This conflates two variables (project freshness vs. which module built the gateway) that were never independently isolated. The clean-room result proves "the codelab's module works fine in a fresh project" — it does not prove "our own module would too."

**This matters right now**: the new AGENT-Works project, as currently planned, uses `iac/gateway-codelab` (the same codelab module cleanroom uses) — meaning it will replicate cleanroom's success/failure pattern but **still won't close this specific gap**. If we want a genuinely clean answer to "is it our module or is it project state," AGENT-Works would need to deploy `iac/agent/agent_gateway.tf` (our own module) instead, at least once.

Other findings: the "ROOT CAUSE CONFIRMED" language for cleanroom rests on a single successful trial, never repeated. "Every candidate closed" was declared three separate times and reopened each time by a new finding. The 274-vs-8 SSL-noise frequency difference was folded into "it's noise" without explaining the 34x disparity. VPC-SC membership remains unresolved because the one path to resolve it (enabling Access Context Manager API, or org-admin access) was never put to you as an explicit yes/no. GCP Support escalation has been recommended four separate times and never filed.

---

## Final Verdict

**1. Are we following Google's deployment guide correctly?**
Structurally yes for the parts that matter — regional endpoint, `updateMask`, field nesting all match. We deliberately add `sourceCodeSpec` bundling beyond Google's minimal documented example, which is evidence-based (not guessed) but not itself Google's documented pattern. Two real, symmetric endpoint-registration gaps exist (Cloud Trace hostname mismatch, Model Armor hostname mismatch, the latter currently dormant).

**2. Is `attach_gateway_to_engine.sh` production-ready?**
Close, not quite. Well-engineered overall (full pre-flight snapshots, hard gates, SHA-256 hashing, proper HTTP status checks on every call). Three fixable issues: dead fallback path, two checks that conflate "failed to check" with "checked, negative," and a readiness gate with a confirmed false-positive for this exact ongoing failure.

**3. Anything missing from the implementation?**
Cloud Trace endpoint registration, Model Armor hostname correction (low urgency, dormant), Terraform representation for Agent Registry entries, and a decision on the 4 unexplained IAM role gaps between the two projects' effective identities.

**4. Anything missing from the investigation?**
Yes — the module-isolated fresh-project test (see §13) is the single biggest gap. Also: VPC-SC resolution was never actually attempted (only discussed), engine recreation was proposed twice and never run, the bundled-fix wasn't retested for reproducibility, and GCP Support was never actually contacted despite being recommended repeatedly.

**5. What would a Google Cloud Staff Engineer likely check next, before accepting "this is a platform/backend issue"?**
In order: (a) demand the module-isolated test — deploy your own Terraform, not a reference implementation, into a fresh project, before accepting any "it's not your code" conclusion; (b) ask for confirmed VPC-SC perimeter membership from someone with org-admin access, since that's the one structural unknown that's never actually been resolved either way; (c) ask whether the specific reasoning ENGINE resource (not just the gateway) could carry server-side state from its 8+ prior failed bind attempts, and request a cheap, reversible test — recreate the engine, not just the gateway; (d) check whether `error.details` is truly always empty for this failure class across many retries, or whether it's ever populated (already checked here — confirmed empty every time, so this closes fast); (e) at that point, file the actual support case with this full reproduction, rather than more self-directed debugging.

**Items (a) and (c) above have since been directly tested — see the follow-up section below.**

---

## Follow-up: Two Controlled Experiments (2026-07-17)

Per direct instruction, the module-isolation gap (§13/final verdict item 4) was closed with two controlled experiments, kept explicitly separate from the general production-readiness fixes filed as GitHub issues #29–#36.

**IAM roles — researched, not blindly copied.** Before either experiment, checked the documented purpose of every role cleanroom's effective identity has that t2-demo's lacks (`cloudapiregistry.viewer`, `iam.serviceAccountTokenCreator`, `telemetry.writer`). None map to admin-plane PATCH, and t2-demo's failure has never shown a permission denial (`code: 7`) — only `code: 3` (INVALID_ARGUMENT-class). **None granted.**

| Test | Result | Evidence | Interpretation | Next action |
|---|---|---|---|---|
| **1. Module-isolated fresh-project test** — `iac/agent/agent_gateway.tf` (our own module, not the codelab's) deployed fresh into `agent-works-502620`, every other value held identical to t2-demo's real config | **SUCCESS** | 76 resources applied cleanly. Bundled PATCH: HTTP 200, operation completed, zero errors, `agentGatewayConfig` durably set. Full `invoke_agent.py` test: `status: "done"`, `confidence_score: 1.0`, correct RCA, cross-project GKE MCP confirmed working. | **Rules out: our own Terraform module.** Binds and works correctly, first try, in a fresh project. | None — module cleared. Project subsequently torn down (its purpose was fulfilled); proof preserved in the `sreagent-gateway-verified` repo. |
| **2. New-engine-in-t2-demo test** — a temporary second engine, created via standalone REST call (not Terraform), bound to t2-demo's real, existing, always-failing gateway (`sre-agent-egress`) | **SUCCESS (bind)** | CREATE: HTTP 200. Bundled PATCH: HTTP 200, operation completed after 270s, zero errors (`has("error")` confirmed `false`), `agentGatewayConfig` correctly set. | **Rules out: the project. Rules out: the gateway.** A brand-new engine, same project, same gateway that has failed to bind the original engine 8+ times, binds immediately. | Temp engine deleted after the test. Original engine never touched. |
| **Verification — Memory Bank "same gateway" rule** | **PASS** | All three projects' Memory Bank engines checked live: `identityType: null`, `agentGatewayConfig: null` on all three — symmetric, no violation. Memory Bank engines aren't `AGENT_IDENTITY` resources, so the documented same-gateway rule doesn't apply to them. | Confirms Experiment 2's result is unaffected — nothing for the new engine's binding to have conflicted with. | None. |

**Combined conclusion:** module, project, and gateway are now all directly, empirically cleared — not by elimination of a list, but by two independent, controlled, successful reproductions. **The only remaining candidate is the original engine resource (`8599129257987276800`) itself carrying accumulated backend state from 8+ historical failed bind attempts.** This is not yet claimed as a confirmed Google backend defect, per explicit instruction — the one test that would confirm it (recreating the original engine) is disruptive, real, and not yet approved or run.

**Cleanup performed:** temp engine deleted; `agent-works-502620`'s cross-project GKE grants on `sreagent-demo` destroyed cleanly via `terraform destroy`; `agent-works-502620` project itself deleted (`DELETE_REQUESTED`, standard 30-day recovery window). The `sreagent-gateway-verified` repo remains as the durable, reviewable record of the successful module-isolation proof.

### RESOLUTION (2026-07-17, same day) — investigation closed

The one remaining candidate was tested with explicit approval: the original engine (`8599129257987276800`) was recreated via `terraform apply -replace=`. Result: **bound on the first attempt, full end-to-end functional test passed.** This confirms the final root cause — the original engine resource had accumulated backend state from its own failed bind history that blocked every subsequent attempt, independent of module, project, gateway, or code, all four of which were independently cleared by direct experiment. Full details in `FINAL_RCA.md`'s "t2-demo addendum" and `TROUBLESHOOTING_LOG.md`'s final entry. **`sreagent-t2-demo` is now fully working, end to end, using its own real project, gateway, and code.**
