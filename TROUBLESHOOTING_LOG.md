# Troubleshooting log — testing2-gcp-sre-agent mTLS / gateway-attach investigation

Append-only. Do not edit previous entries.

---

**Timestamp:** 2026-07-15 (session start, exact time not captured)
**Objective:** Confirm the plain-endpoint mTLS fix (PR #20) is actually present in `main` before testing anything.
**Command executed:**
```bash
git -C ~/projects/testing2-gcp-sre-agent log --oneline | grep -E "e3e4dcf|bc03b88|3ea49ce"
```
**Exit code:** 0
**Relevant output:**
```
bc03b88 fix(agent): thinking_budget model-awareness + parity fixes vs testing-gcp-sre-agent (#21)
3ea49ce Merge pull request #20 from AshminPy/fix/gemini-plain-endpoint-no-mitm
e3e4dcf fix(agent): force plain Vertex endpoint, bypassing Agent Identity's auto-mTLS
```
Also read `agent/gemini_client.py:47-72` in full — base_url pin logic present, unchanged.
**Result:** SUCCESS
**Evidence discovered:** `main` HEAD contains PR #20's fix.
**Interpretation:** Fix is in the code. Whether the deployed engine runs this code is still open.
**Files modified:** None
**Next action:** Check for infra drift between deployed state and this code.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Compare live deployed state vs current Terraform code for real drift.
**Command executed:**
```bash
cd iac/agent && terraform plan -input=false -no-color
```
**Exit code:** 0
**Relevant output:** `Plan: 23 to add, 1 to change, 0 to destroy.` (full output ~121KB, not reproduced — was saved to a scratch tool-results file during the session)
**Result:** PARTIAL
**Evidence discovered:** `terraform.tfvars` is gitignored (CI never sees it); `create_wif` defaults `true` locally but CI passes `false`; `gemini_model` variable default = `gemini-2.5-flash`, local tfvars had `gemini-2.5-pro`.
**Interpretation:** The "23 to add" is a plan-invocation artifact (local flag mismatch vs CI), not real drift.
**Files modified:** None
**Next action:** Re-run plan matching CI's actual flags to isolate real drift.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Isolate genuine drift using CI's exact flags and a freshly-rebuilt agent package from current HEAD.
**Command executed:**
```bash
bash scripts/package_agent.sh
cd iac/agent && terraform plan -input=false -no-color -var="create_wif=false" -var="gemini_model=gemini-2.5-flash"
```
**Exit code:** 0
**Relevant output:**
```
WARNING: GNU tar not found — building a non-reproducible archive.
Plan: 0 to add, 1 to change, 0 to destroy.
```
Remaining diff: `google_vertex_ai_reasoning_engine.sre_agent` in-place update, mostly a `source_code_spec` hash difference.
**Result:** INCONCLUSIVE
**Evidence discovered:** `package_agent.sh` produces non-reproducible archives without GNU tar (its own warning).
**Interpretation:** The remaining diff is likely a non-reproducible-build artifact, not real code drift (confirmed independently later via CI headSha correlation).
**Files modified:** None (local `agent.tar.gz` rebuilt — gitignored build artifact)
**Next action:** Run the actual smoke test rather than inferring from plan noise.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Reproduce the working/failing behaviour using the exact documented test command.
**Command executed:**
```bash
make smoke
```
**Exit code:** 2
**Relevant output:**
```
Agent response [62.0s]:
{
  "status": "failed",
  "error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com', port=443): Max retries exceeded ... SSLError(SysCallError(-1, 'Unexpected EOF'))"
}
SMOKE TEST FAILED — no well-formed RCA in the response.
```
**Result:** FAILURE
**Evidence discovered:** The failure is real, reproducible, and matches the exact pre-PR#20 mTLS symptom.
**Interpretation:** Problem is not code content — the fixed code is producing unfixed behaviour, so something about the deployed/runtime path is the actual cause.
**Files modified:** None
**Next action:** Rule out stale deploy; check the actual request path (gateway vs direct).

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Rule out "deployed code is stale" as the cause.
**Command executed:**
```bash
gh run view 29308219349 --repo AshminPy/testing2-gcp-sre-agent --json headSha,createdAt,updatedAt,conclusion
curl -s -H "Authorization: Bearer $TOKEN" "https://us-central1-aiplatform.googleapis.com/v1beta1/${ENGINE_NAME}"   # for updateTime
```
**Exit code:** 0
**Relevant output:**
```
{"conclusion":"success","createdAt":"2026-07-14T05:19:17Z","headSha":"bc03b8889049...","updatedAt":"2026-07-14T05:23:14Z"}
updateTime: 2026-07-14T05:22:47.289255Z
```
**Result:** SUCCESS
**Evidence discovered:** CI checked out exactly `main` HEAD; live engine `updateTime` falls inside that CI run's execution window.
**Interpretation:** Deployed code = repo code. Stale-deploy hypothesis rejected.
**Files modified:** None
**Next action:** Check whether the engine is actually routed through the gateway.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Determine whether the engine is wired to the Agent Gateway (ground truth from the API, not Terraform's cached view).
**Command executed:**
```bash
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" "https://us-central1-aiplatform.googleapis.com/v1beta1/${ENGINE_NAME}"
```
**Exit code:** 0
**Relevant output:**
```
identityType: AGENT_IDENTITY
agentGatewayConfig: null
```
**Result:** SUCCESS (diagnostic — revealed the real problem)
**Evidence discovered:** Live engine `agentGatewayConfig` = `null`. `identityType` = `AGENT_IDENTITY`.
**Interpretation:** Gateway is not attached. Without it, calls take a different path than PR #20's fix was validated against.
**Files modified:** None
**Next action:** Find out why the gateway isn't attached.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Rule out "gateway disabled" or "gateway destroyed" as trivial explanations.
**Command executed:**
```bash
grep -n "enable_agent_gateway" -A3 iac/agent/variables.tf
terraform state list | grep -i gateway
```
**Exit code:** 0
**Relevant output:**
```
default = true
google_network_services_agent_gateway.sre_egress[0]
google_project_iam_member.agentgateway_p4sa_dns[0]
time_sleep.wait_for_gateway[0]
```
**Result:** SUCCESS
**Evidence discovered:** Gateway enabled by default, exists in state.
**Interpretation:** Not disabled, not destroyed. The problem is specifically the attach step.
**Files modified:** None
**Next action:** Check the post-apply attach script's CI execution.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** See what actually happened when CI ran `scripts/attach_gateway_to_engine.sh` on the PR #21 apply.
**Command executed:**
```bash
gh api repos/AshminPy/testing2-gcp-sre-agent/actions/jobs/87006194216/logs > /tmp/full_job_log.txt
sed -n '381,417p' /tmp/full_job_log.txt
```
**Exit code:** 0
**Relevant output:**
```
Attaching engine 8599129257987276800 to gateway .../agentGateways/sre-agent-egress ...
PATCH submitted (operation: .../operations/1020856281071616000).
The gateway data plane provisions asynchronously; allow time before traffic flows.
```
Full log saved at `/tmp/full_job_log.txt` (417 lines).
**Result:** SUCCESS (diagnostic)
**Evidence discovered:** The script submits an async PATCH and exits 0 without checking the result.
**Interpretation:** Real bug in the script — CI reports this step green regardless of the operation's actual outcome.
**Files modified:** None
**Next action:** Check that specific operation's terminal status.

---

**Timestamp:** 2026-07-15, prior to 09:28Z
**Objective:** Determine if the PR #21 CI attach PATCH actually succeeded or failed.
**Command executed:**
```bash
curl -s -H "Authorization: Bearer $TOKEN" ".../operations/1020856281071616000"
```
**Exit code:** 0
**Relevant output:**
```json
{"done": true, "error": {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}}
```
**Result:** FAILURE (of the underlying operation — root cause confirmed)
**Evidence discovered:** The gateway-attach PATCH from the PR #21 CI apply genuinely failed.
**Interpretation:** CI's green checkmark was misleading; the attach silently failed.
**Files modified:** None
**Next action:** (User approved) retry the attach script manually.

---

**Timestamp:** 2026-07-15, operation window 08:57:49Z–09:02:10Z
**Objective:** Test whether a clean, isolated retry (no concurrent Terraform apply) succeeds.
**Command executed:**
```bash
bash scripts/attach_gateway_to_engine.sh
```
Polled operation independently afterward.
**Exit code:** 0 (script — known unreliable signal)
**Relevant output:**
```
PATCH submitted (operation: .../operations/7488646020289527808).
```
```json
{"done": true, "error": {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}}
```
**Result:** FAILURE
**Evidence discovered:** Same generic failure, in isolation this time (no concurrent apply).
**Interpretation:** At the time, read as "retry alone doesn't fix it" — later refined (see historical-audit entry below) once the full attempt history showed this is normal variance, not a hard block.
**Files modified:** None
**Next action:** Dig into Cloud Logging / Audit Logs before retrying blindly again.

---

**Timestamp:** 2026-07-15, ~09:05–09:15Z
**Objective:** Find any additional error detail beyond the generic operation error.
**Command executed:**
```bash
gcloud logging read 'resource.type="aiplatform.googleapis.com/ReasoningEngine" ... severity>=ERROR' --project=sreagent-t2-demo
gcloud logging read 'logName="...cloudaudit.googleapis.com%2Factivity" ... protoPayload.methodName=~"ReasoningEngine"' --project=sreagent-t2-demo
```
**Exit code:** 0
**Relevant output:** ERROR-severity count: 0. Audit log `status: {}` for both start and completion entries. Saw coincidental worker shutdown/startup log lines in the same window; re-checked engine `updateTime`/`agentGatewayConfig` afterward — both unchanged.
**Result:** INCONCLUSIVE (absence of detail is itself informative)
**Evidence discovered:** No ERROR-severity Cloud Logging entries correlate with the failure. Audit log captures the call but not a failure reason.
**Interpretation:** Google's platform gives no actionable diagnostic detail for this failure. The coincidental log activity was ruled out as a delayed-success signal (state genuinely unchanged after).
**Files modified:** None
**Next action:** Check Google's official docs for anything not yet covered.

---

**Timestamp:** 2026-07-15, ~09:15Z
**Objective:** Check whether the doc referenced in the error message explains this failure mode.
**Command executed:** `WebFetch https://docs.cloud.google.com/gemini-enterprise-agent-platform/troubleshooting/agent-deployment`
**Exit code:** N/A (tool call)
**Relevant output:** Doc covers serialization errors, GCS permissions, VPC-SC, custom SA config, 429 rate limiting. No mention of `UpdateReasoningEngine` / `agentGatewayConfig` failures.
**Result:** INCONCLUSIVE
**Evidence discovered:** This exact failure mode is undocumented in Google's referenced troubleshooting page.
**Interpretation:** No lead from this doc.
**Files modified:** None
**Next action:** Report status to user; awaited direction.

---

**Timestamp:** 2026-07-15, ~09:20Z
**Objective:** Check the user-supplied `agent-gateway-runtime-deploy` doc for the exact binding procedure and prerequisites.
**Command executed:** `WebFetch https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy` (x2)
**Exit code:** N/A (tool call)
**Relevant output:** Documented PATCH body/updateMask matches our script exactly. Documented gotcha (can't retroactively add `identity_type=AGENT_IDENTITY` via patch) doesn't apply — engine already has it. Doc's troubleshooting only covers "PATCH succeeds but binding silently fails," not "PATCH itself fails."
**Result:** INCONCLUSIVE
**Evidence discovered:** Our PATCH request format matches Google's documented format exactly. `identity_type` gotcha ruled out.
**Interpretation:** Request format is not the problem; the doc has a gap for our exact symptom.
**Files modified:** None
**Next action:** Compare against a known-working reference project.

---

**Timestamp:** 2026-07-15, ~09:22Z
**Objective:** Apply the working-test rule — find a real working example and diff it against ours.
**Command executed:**
```bash
curl -s -H "Authorization: Bearer $TOKEN" ".../projects/sreagent-codelab/.../reasoningEngines/3297266596665360384"
```
**Exit code:** 0 (HTTP 200)
**Relevant output:**
```
identityType: AGENT_IDENTITY
agentGatewayConfig: {"agentToAnywhereConfig": {"agentGateway": "projects/sreagent-codelab/.../agentGateways/agent-gateway"}}
```
**Result:** SUCCESS
**Evidence discovered:** A real, working, gateway-attached engine exists in `sreagent-codelab`.
**Interpretation:** Directly comparable working reference confirmed.
**Files modified:** None
**Next action:** Pull the audit log for how that attach succeeded.

---

**Timestamp:** 2026-07-15, ~09:24Z
**Objective:** Get the exact request that succeeded in the codelab project.
**Command executed:**
```bash
gcloud logging read 'logName="projects/sreagent-codelab/logs/cloudaudit.googleapis.com%2Factivity" protoPayload.methodName=~"ReasoningEngine" ...' --project=sreagent-codelab
```
**Exit code:** 0
**Relevant output:** Successful call used `updateMask: spec.deploymentSpec.env` (not `agentGatewayConfig`) despite the body only containing `agentGatewayConfig`. Same caller/tool as our own script.
**Result:** SUCCESS (diagnostic), later downgraded — see next entry
**Evidence discovered:** The one codelab success used a different updateMask value than documented.
**Interpretation:** Looked like a promising lead at the time; needed to check testing2's own history to see if the documented mask ever succeeds here.
**Files modified:** None
**Next action:** Pull testing2's full attach-attempt history.

---

**Timestamp:** 2026-07-15, ~09:26Z
**Objective:** Determine the true success/failure rate of `agentGatewayConfig` PATCH attempts in `sreagent-t2-demo`, and test the mask hypothesis from the prior entry.
**Command executed:**
```bash
gcloud logging read 'logName="...cloudaudit.googleapis.com%2Factivity" protoPayload.methodName="...UpdateReasoningEngine" protoPayload.request.reasoningEngine.spec.deploymentSpec.agentGatewayConfig:*' --project=sreagent-t2-demo --freshness=30d
# then for each of 26 unique operation IDs found:
curl -s -H "Authorization: Bearer $TOKEN" ".../operations/<id>"
```
**Exit code:** 0
**Relevant output:** 26 attempts, ALL using the documented `updateMask=spec.deploymentSpec.agentGatewayConfig`. **20 succeeded (`error: none`), 6 failed (`error.code: 3`)**, spanning 2026-07-13T01:24Z–2026-07-15T08:57Z. Per-operation timestamps/results captured in conversation transcript, not re-pasted here.
**Result:** SUCCESS (most informative step of the investigation)
**Evidence discovered:** 20/26 historical attempts succeeded using the documented mask. Failure is intermittent (~23%), not deterministic. 2 of 6 failures correlate with a concurrent overlapping update; the other 4 do not.
**Interpretation:** This operation is inherently flaky on Google's beta platform, not permanently broken, and not caused by updateMask value. Retrying is evidence-backed given ~77% base success odds.
**Files modified:** None
**Next action:** Retry the attach script again.

---

**Timestamp:** 2026-07-15T09:30:52Z
**Objective:** Retry attempt (3rd overall) — user re-approved after reviewing journal restructure.
**Command executed:**
```bash
bash scripts/attach_gateway_to_engine.sh
```
**Exit code:** 0 (script — known unreliable signal, polling separately)
**Relevant output:**
```
PATCH submitted (operation: projects/327234009108/locations/us-central1/reasoningEngines/8599129257987276800/operations/2985820449104986112).
```
**Result:** INCONCLUSIVE (submission only — polling in progress)
**Evidence discovered:** Operation `2985820449104986112` created at `2026-07-15T09:31:01Z`; as of `09:31Z` check, `done` field still unset (not yet terminal) after ~3.5 minutes of polling.
**Interpretation:** Still pending. Prior successful/failed operations both resolved within ~1–5 minutes, so this is within normal range — not yet conclusive either way.
**Files modified:** None
**Next action:** Continue polling operation `2985820449104986112` to terminal state.

---

**Timestamp:** 2026-07-15T09:37:55Z
**Objective:** Resolve the pending status of retry attempt 3 (operation `2985820449104986112`).
**Command executed:**
```bash
curl -s -H "Authorization: Bearer $TOKEN" ".../operations/2985820449104986112"
```
**Exit code:** 0
**Relevant output:**
```json
{
  "done": true,
  "error": {"code": 3, "message": "The Reasoning Engine failed to be updated."},
  "metadata": {"genericMetadata": {"createTime": "2026-07-15T09:31:01Z", "updateTime": "2026-07-15T09:36:24Z"}}
}
```
**Result:** FAILURE
**Evidence discovered:** 3rd consecutive failure by us (PR #21 CI attempt, manual retry 1, manual retry 2 — all failed). No successful attach since 2026-07-13T16:24:13Z, the last known success in the 26-attempt history.
**Interpretation:** Still consistent with the ~23% baseline failure rate by chance (3 fails in a row at 23% ≈ 1.2% probability — low but not impossible), but also consistent with a possible recent regression in success rate. Cannot distinguish between these from available evidence. This changes the practical calculus: blind retrying is now lower-confidence than when the historical-audit step (77% success) was the newest evidence.
**Files modified:** None
**Next action:** Report updated failure count to user; do not retry again without a decision on direction (retry again / recreate gateway / GCP support case / try later).

---

**Timestamp:** 2026-07-15T09:41:56Z
**Objective:** User asked whether the codelab comparison actually yielded anything useful. Dig further: check codelab's full attach-attempt history (not just the one success already found) and compare IAM structure between the two projects.
**Command executed:**
```bash
# codelab full attempt history, same technique as testing2's 26-attempt audit
gcloud logging read 'logName="projects/sreagent-codelab/logs/cloudaudit.googleapis.com%2Factivity" protoPayload.methodName="...UpdateReasoningEngine" protoPayload.request.reasoningEngine.spec.deploymentSpec.agentGatewayConfig:*' --project=sreagent-codelab --freshness=30d
# + operations.get on each of the 2 unique ops found
# + caller identity cross-reference for testing2's 26 attempts
# + project IAM policy diff: gcloud projects get-iam-policy sreagent-t2-demo / sreagent-codelab
```
**Exit code:** 0
**Relevant output:**
- Codelab has only 2 historical attach attempts ever (2 different engines: "Mortgage Assistant Agent" and "sre-agent-langgraph-crosstest"), both `error: None` — **2/2, 100% success**, vs testing2's 20/26 (77%).
- Caller identity in testing2 doesn't cleanly predict outcome: `ashmin.sub@gmail.com` (user) = 7 success/4 fail (64%), `sre-agent-deployer@...` (CI SA) = 13 success/2 fail (87%) — both callers see failures, and codelab's 2 successes were BOTH via the user account, same as some of testing2's failures. Not a clean differentiator.
- Gateway-resource-level `getIamPolicy` (via `networkservices.googleapis.com`) returns HTTP 404 in both projects — this resource type doesn't appear to support resource-level IAM policy under that API surface (or it's under a different, undiscovered API path). Dead end.
- **Real structural difference found:** project-level IAM bindings for engine-facing roles (`aiplatform.user`, `aiplatform.expressUser`, `aiplatform.agentDefaultAccess`, `browser`, `cloudtrace.agent`, etc.) are granted differently:
  - codelab: `principalSet://agents.global.org-<id>.system.id.goog/attribute.platformContainer/aiplatform/projects/1045761410716` — a wildcard covering ALL reasoning engines in the project.
  - testing2: `principal://agents.global.org-<id>.system.id.goog/resources/aiplatform/projects/327234009108/locations/us-central1/reasoningEngines/8599129257987276800` — scoped to this ONE specific engine only.
  This matches testing2's deliberate least-privilege design (documented elsewhere in the project) vs. codelab's broader defaults.
**Result:** PARTIAL
**Evidence discovered:** Codelab's attach success rate (2/2) is meaningfully higher than testing2's (20/26). A genuine IAM-scoping difference exists between the two projects (principal vs principalSet).
**Interpretation:** The IAM difference is real but is NOT a convincing root-cause candidate for THIS failure: authorization gaps produce deterministic, consistent denials (typically synchronous 403s), not an intermittent ~23%-of-the-time async failure. If the narrower `principal://` scoping were insufficient, we'd expect 0% success, not 77%. More likely explanation for codelab's cleaner record: much smaller sample size (2 attempts vs 26) — not enough data to conclude codelab is actually more reliable in general, only that it happened to succeed both times it was tried.
**Facts established / hypotheses affected:**
- CONFIRMED: a real IAM-scoping difference exists (principal vs principalSet) between the two projects.
- REJECTED (as primary cause): IAM scoping explains the intermittent failures — inconsistent with deterministic nature of auth failures and with testing2's own 77% success rate under the same IAM config.
- STILL OPEN: whether IAM scoping is a *contributing* factor at the margin (e.g. via propagation lag interacting with the concurrency pattern already noted in 2/6 failures) — not provable from available evidence.
**Files modified:** None
**Next action:** Report findings to user honestly — real difference found, not a proven fix. No further unexplored leads from the codelab comparison remain.

---

**Timestamp:** 2026-07-15T09:49:52Z
**Objective:** User pointed to the actual source repo for the codelab (`github.com/GoogleCloudPlatform/cloud-networking-solutions`) and 2 more official docs. Read the real source instead of rendered tutorial text/WebFetch summaries.
**Command executed:**
```bash
git clone --depth 1 https://github.com/GoogleCloudPlatform/cloud-networking-solutions.git
# + WebFetch on troubleshoot-agent-gateway and agent-gateway-overview docs
# + read demos/agent-gateway/src/mortgage-agent/deploy_agent.py in full
# + read .agents/skills/agent-platform-debugger/references/{agent-gateway,known-issues,field-manual}.md
```
**Exit code:** 0
**Relevant output / findings:**
1. Both official docs (troubleshoot-agent-gateway, agent-gateway-overview) — confirmed via WebFetch — do NOT cover creation-time vs. post-hoc gateway binding, or our specific PATCH failure. No new info there.
2. `deploy_agent.py` (the actual script behind the "Mortgage Assistant Agent" — same displayName as the codelab engine we already inspected) — **does NOT do a separate post-hoc PATCH to attach an existing engine to a gateway.** Its 3-step flow: (1) create an identity-only empty shell (no gateway config), (2) grant IAM via a script, (3) **one single `client.agent_engines.update()` call that bundles application code, env vars, identity_type, AND `agent_gateway_config` together** — not two separate sequential update calls like our Terraform-apply-then-attach-script pattern.
3. `known-issues.md` (curated by the repo's own AI-debugging skill) — **Issue #3: "Private-preview limit: one Reasoning Engine per project bonded to an Agent Gateway."** Symptom: "Updating a Reasoning Engine to use an Agent Gateway fails with `Internal error encountered` or `The specified parameters are invalid.`" Cause: "During the private preview, a project can have only one active ReasoningEngine↔AgentGateway bonding. A second bonding attempt fails." This is thematically the closest match to our symptom found anywhere. **Checked whether this currently applies:** queried every reasoning engine in `sreagent-t2-demo` (2 exist: `sre-agent-gcp` = our target, `sre-agent-memory-bank`) — the memory-bank engine's `agentGatewayConfig` is `null`, not bonded. So no CURRENTLY active conflicting bond exists — this exact mechanism isn't demonstrably firing right now, though stale backend-side bonding state from the gateway's destroy/recreate history (PR #19) can't be ruled out.
4. Issue #17 in the same doc ("Reasoning Engine outbound SSL handshake failure (CERTIFICATE_VERIFY_FAILED) ... bound to an Egress Agent Gateway with AGENT_IDENTITY") — describes the ORIGINAL mTLS symptom class (matches what PR #20 fixed) as a known platform issue with a documented fix: bake the gateway's root CA into the trust store, OR (for SDK/source-based deploys) simply deploy/update *after* the gateway's root certs are ready. Consistent with — doesn't contradict — our existing fix.
**Result:** SUCCESS (real, actionable new evidence)
**Evidence discovered:**
- FACT: Google's own reference implementation performs gateway binding as ONE combined update call (with source+env+config together), never as a narrow standalone PATCH like our `attach_gateway_to_engine.sh`.
- FACT: A documented private-preview limit exists specifically for RE↔Gateway bonding conflicts, with symptom language very close to ours.
- FACT: No second engine is currently bonded to our gateway (rules out the simplest form of issue #3, doesn't rule out a stale/backend-side variant).
**Interpretation:** Two concrete, testable hypotheses now: (a) narrow single-field `agentGatewayConfig` PATCH calls are less robust than bundling gateway config into the same update as other deploymentSpec fields (matches Step "codelab audit" finding that the one successful codelab call used `updateMask=env`, not `agentGatewayConfig` — no longer a red herring, now a corroborated pattern); (b) the private-preview one-bonding-per-project limit may contribute intermittently via stale state, though not provably active right now.
**Files modified:** None
**Next action:** Propose to user: retry using `updateMask=spec.deploymentSpec.env` (matching the empirically-successful codelab pattern) instead of `agentGatewayConfig`, as a cheap, reversible experiment. Get explicit approval before running (still a live write).

---

**Timestamp:** 2026-07-15T09:52:40Z–09:53:42Z
**Objective:** Test whether matching the codelab's exact `updateMask=spec.deploymentSpec.env` value (instead of `agentGatewayConfig`) fixes the attach, per user approval.
**Command executed:**
```bash
curl -sS -X PATCH \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/sreagent-t2-demo/locations/us-central1/reasoningEngines/8599129257987276800?updateMask=spec.deploymentSpec.env" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"spec":{"deploymentSpec":{"agentGatewayConfig":{"agentToAnywhereConfig":{"agentGateway":"projects/sreagent-t2-demo/locations/us-central1/agentGateways/sre-agent-egress"}}}}}'
```
(Same body as `attach_gateway_to_engine.sh` produces; only the `updateMask` query param changed from `agentGatewayConfig` to `env`.)
**Exit code:** 0
**Relevant output:**
```json
{
  "done": true,
  "error": {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."},
  "metadata": {"genericMetadata": {"createTime": "2026-07-15T09:52:41Z", "updateTime": "2026-07-15T09:53:42Z"}}
}
```
**Result:** FAILURE
**Evidence discovered:** The `updateMask` value is NOT the differentiator — same failure with `env` mask as with `agentGatewayConfig` mask. This resolved faster than prior attempts (~1 minute vs 4–5 minutes) — noted but not clearly meaningful (both fast and slow resolutions have occurred on both successes and failures historically).
**Interpretation:** REJECTS the "narrow mask is less robust than a differently-named mask" hypothesis in this simple form. The codelab's one success with `env` mask was most likely coincidental (or explained by some other difference in that call, e.g. it was the FIRST-ever update on a freshly-created empty-shell engine, not a re-update of an already-configured one — a difference not yet tested here). 4 consecutive failures now across all variations tried (PR#21 CI, retry 1, retry 2, env-mask experiment).
**Facts established / hypotheses affected:**
- REJECTED: "swap updateMask to `env` fixes the attach."
- STILL OPEN: whether bundling gateway config into the SAME update call as source/env (rather than any standalone PATCH regardless of mask value) would behave differently — not yet tested, would require changing `attach_gateway_to_engine.sh` to run as part of the Terraform-driven update rather than after it, a larger change.
- STILL OPEN: known-issue #3 (one-bonding-per-project limit) as a contributing factor via stale backend state.
**Files modified:** None
**Next action:** Report result to user plainly. Remaining untested options: (a) bundle gateway config into the same call as an env/source update (bigger script change), (b) recreate the gateway fresh (bigger, disruptive), (c) GCP support case, (d) keep retrying as-is (77% historical base rate, though recent run of 4 failures is concerning).

---

**Timestamp:** 2026-07-15T09:56:57Z–10:04:03Z
**Objective:** User asked how the codelab's cross-test engine could have succeeded via the same kind of post-hoc PATCH we're struggling with. Re-examined the codelab's successful request body (checking for bundled source code, per user's earlier tip that deploy_agent.py bundles everything) and ran a bundled env+agentGatewayConfig test on our own engine per prior approval. Also re-searched the full cloud-networking-solutions repo (all 4 codelab variants) for any dedicated "attach existing engine" script.
**Command executed:**
```bash
# bundled PATCH: current env array (unchanged) + agentGatewayConfig, single call
curl -sS -X PATCH ".../reasoningEngines/8599129257987276800?updateMask=spec.deploymentSpec.env,spec.deploymentSpec.agentGatewayConfig" -d "$BODY"
# + re-inspected codelab's successful request body in full (already captured in /tmp/codelab_audit.json)
# + grep across all 4 codelab deploy_agent.py variants for agent_gateway_config usage pattern
# + full timeline reconstruction: sorted all 26 historical attempts by timestamp against success/fail
```
**Exit code:** 0
**Relevant output:**
- Bundled test operation `3349802778363953152`: `done: true, error: {"code": 3, ...}` — FAILED (5th consecutive failure).
- Codelab's successful request body re-inspected in full: it did NOT include `sourceCodeSpec` — just `agentGatewayConfig` in the body with `updateMask=spec.deploymentSpec.env`, essentially the same shape I'd already tried (env-mask experiment). Not meaningfully different from what's already been tested and failed.
- All 4 codelab variants (`agent-weather`, `agent-datacommons`, `agent-dj`, `mortgage-agent`) confirmed to set `agent_gateway_config` only inside `client.agent_engines.create()` or the identity-shell's bundled `update()` call at deploy time — no standalone post-hoc attach script exists anywhere in Google's reference repo.
- **Full timeline reconstruction (the key finding):** sorted all 26 historical attempts chronologically. **Every single success (20/20) occurred at or before `2026-07-13T16:24:13Z`. Every single failure (6/6) occurred strictly after that timestamp** — spanning `2026-07-13T17:54:50Z` through today's 4 failures (`2026-07-15T09:56:57Z`), with zero successes anywhere in that ~42-hour window despite 6 attempts. `2026-07-13T16:24:13Z` is the exact timestamp of the PR #19 CI rerun that successfully recreated the gateway (`sre-agent-egress`) and attached it — per deploy-state, this was the gateway's fresh-recreation moment.
**Result:** SUCCESS (major finding) / FAILURE (of the bundled-update test itself)
**Evidence discovered:**
- FACT: This is not evenly-distributed ~23% random flakiness. It's a clean regime change: 20/20 success before the current gateway instance's creation-time bind, 0/6 success on every attempt to REBIND to it since.
- REJECTED: "bundling source/env with gateway config in one call fixes it" — tested, failed identically.
- REJECTED: "codelab's request was structurally different (contained source code)" — verified it wasn't; same shape as our failed env-mask test.
**Hypotheses affected:**
- NEW LEADING HYPOTHESIS: gateway `sre-agent-egress` (current incarnation, created 2026-07-13 during PR #19's fix) accepted exactly ONE successful bind at its own creation moment, and has rejected every attempt to (re-)bind since — consistent with known-issue #3's literal symptom ("a second bonding attempt fails"), but manifesting as "can't rebind the SAME engine to a gateway that already received one successful bind," not literally "two different engines competing." The engine's own config shows `agentGatewayConfig: null` (looks unbonded from the engine side), but the gateway side may retain stale "already bonded once" state that blocks re-binding.
- STILL OPEN: whether this is provable/fixable without recreating the gateway.
**Files modified:** None
**Next action:** Propose a cheap, cheap-to-reverse test: explicitly PATCH `agentGatewayConfig` to empty/cleared first (a formal "unbind"), then immediately attempt to bind again — tests whether clearing stale gateway-side state unblocks it, without the bigger step of destroying/recreating the gateway resource. Get user approval before running (still a live write). If that also fails, gateway recreation becomes the well-evidenced next step, not just a generic escalation.

---

**Timestamp:** 2026-07-15T10:09:06Z–10:09:44Z
**Objective:** Test whether explicitly clearing (unbinding) `agentGatewayConfig` first — rather than trying to set it — succeeds, per user approval. Tests whether the failure is direction-specific (only rebind-to-a-value fails) or resource-wide (any touch to this field fails).
**Command executed:**
```bash
curl -sS -X PATCH ".../reasoningEngines/8599129257987276800?updateMask=spec.deploymentSpec.agentGatewayConfig" \
  -H "Content-Type: application/json" -d '{"spec":{"deploymentSpec":{"agentGatewayConfig":{}}}}'
```
**Exit code:** 0
**Relevant output:**
```json
{"done": true, "error": {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}}
```
(operation `4899885475109535744`, resolved in ~37 seconds)
**Result:** FAILURE
**Evidence discovered:** Even an explicit clear/unbind (setting the field to empty, not to a real gateway reference) fails identically. 6th consecutive failure.
**Interpretation:** REJECTS "stale bond state blocks new binds specifically" as the precise mechanism — if that were it, clearing to empty (not asserting a new bond) should plausibly succeed. Instead, this points to something more fundamental: ANY `UpdateReasoningEngine` call that touches `agentGatewayConfig` on this specific engine/gateway pair fails right now, regardless of direction. Combined with the clean timeline split (works only once, at gateway creation), the most likely explanation is the gateway resource itself is in a bad/stuck internal state following its 2026-07-13 recreation — not something fixable via API calls from our side.
**Facts established / hypotheses affected:**
- REJECTED: "explicit unbind clears stale state and unblocks rebinding."
- CONFIRMED (practical): no API-level workaround from our side has worked (6/6 failures across mask variations, bundling, and unbind). The remaining lever is destroying and recreating the gateway resource itself.
**Files modified:** None
**Next action:** User approved gateway recreation if warranted. Proceeding: `terraform taint` the gateway resource (and verify the authz policy's `replace_triggered_by` lifecycle rule from PR #19 handles ordering correctly), `terraform apply`, then immediately attempt attach (matches the only pattern that has ever worked: bind immediately at/after gateway creation).

---

**Timestamp:** 2026-07-15T10:11Z–10:28:11Z
**Objective:** Recreate gateway `sre-agent-egress` (and its dependent authz policy/extension) fresh, per user approval, since 6/6 API-level workarounds on the stuck existing gateway all failed identically.
**Command executed:**
```bash
terraform taint 'google_network_services_agent_gateway.sre_egress[0]'
terraform plan -target='google_network_services_agent_gateway.sre_egress[0]' \
  -target='google_network_security_authz_policy.iap[0]' \
  -target='google_network_services_authz_extension.iap[0]' \
  -target='time_sleep.wait_for_gateway[0]' -var="create_wif=false"
# reviewed plan (2 to add, 2 to destroy — gateway + authz policy, nothing else) before applying
terraform apply -auto-approve [same -target flags] -var="create_wif=false"
```
**Exit code:** 0
**Relevant output:**
```
Apply complete! Resources: 2 added, 0 changed, 2 destroyed.
agent_gateway_id = "projects/sreagent-t2-demo/locations/us-central1/agentGateways/sre-agent-egress"
```
**Result:** SUCCESS
**Evidence discovered:** Gateway and its authz policy destroyed and recreated cleanly, exactly matching the previewed plan — no unrelated resources touched. Confirms PR #19's `replace_triggered_by` fix still works correctly for a manual targeted recreate, not just the original CI-driven one.
**Interpretation:** Fresh gateway instance now exists. Per the timeline evidence (every historical success was a first-bind-after-creation), this is the moment to attempt the bind — before any other activity potentially puts the gateway into whatever state blocked the last 6 attempts.
**Files modified:** None (infra only)
**Next action:** Immediately run `attach_gateway_to_engine.sh` against the fresh gateway, poll to terminal state, then run `make smoke` if the attach succeeds.

---

**Timestamp:** 2026-07-15T10:28:50Z–10:33:59Z
**Objective:** Attempt bind against the freshly-recreated gateway (39 seconds after `terraform apply` completed), testing the leading hypothesis that a fresh gateway's first bind is reliable.
**Command executed:**
```bash
bash scripts/attach_gateway_to_engine.sh
```
**Exit code:** 0 (script — polled independently)
**Relevant output:**
```json
{"done": true, "error": {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}}
```
**Result:** FAILURE — 7th consecutive failure, and critically the FIRST failure on a truly fresh (39-second-old) gateway, directly contradicting the leading hypothesis.
**Evidence discovered / re-examined:** Checked `time_sleep.wait_for_gateway` in `agent_gateway.tf`: `create_duration = "30s"`, `depends_on = [google_network_services_agent_gateway.sre_egress]`. Because `depends_on` alone (no attribute reference / no `triggers` map keyed to the gateway's identity) doesn't force Terraform to treat this resource as needing replacement when its dependency replaces, the earlier plan showed `time_sleep.wait_for_gateway[0]`: **0 to change** — the 30s wait did NOT re-run on this recreate. Re-checked the historical timeline: the one gateway that DID succeed (2026-07-13) had `create_time: 16:18:41Z`, and its successful bind wasn't until `16:24:13Z` — **~5.5 minutes later**, not 30 seconds. That gap was incidentally consumed by the rest of that day's Terraform apply run (other resources being created) before the separate attach script ran — never actually validated as a real requirement, just an artifact of how much other work happened to be in flight at the time.
**Interpretation:** REVISED HYPOTHESIS — the gateway's data plane likely needs materially longer than 30s to be ready to accept an engine bind (the config's own comment says data-plane provisioning "can take a while" but the code never encoded a wait long enough to match observed successful timing). My recreate-then-immediately-attach sequence (39s total) never gave it that time. This is different from, and more specific than, all 6 previously-rejected hypotheses (mask value, bundling, unbind, stale-bond-state) — none of which controlled for elapsed settle time.
**Facts established / hypotheses affected:**
- STILL OPEN (not yet rejected): gateway needs ~5+ minutes of settle time after creation before it reliably accepts a bind. Not yet tested directly — this is the next experiment.
- Checked whether `agentGatewayCard.rootCertificates` (a documented readiness signal for a *different* known issue, #17, mTLS egress) is populated: yes, already returns valid PEM certs. This does NOT confirm bind-readiness specifically — it's a shared regional CA, not necessarily gateway-instance-specific, and covers a different failure mode. Not a reliable readiness signal for our exact symptom.
**Files modified:** None
**Next action:** Wait a meaningful settle period (targeting ≥6 minutes from gateway creation, matching/exceeding the one historical success's gap) against the SAME fresh gateway (no need to recreate again), then retry the attach once. This is the most evidence-grounded remaining hypothesis.

---

**Timestamp:** 2026-07-15T10:38:51Z–10:43:28Z
**Objective:** Retry the attach after ~10 minutes of settle time since gateway creation (well past the historical 5.5-minute gap), testing the settle-time hypothesis directly.
**Command executed:**
```bash
bash scripts/attach_gateway_to_engine.sh
```
**Exit code:** 0 (script — polled independently)
**Relevant output:**
```json
{"done": true, "error": {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}}
```
(operation `4639222054489423872`, createTime 10:39:00Z, resolved 10:43:28Z)
**Result:** FAILURE — 8th consecutive failure.
**Evidence discovered:** Settle time was not the (sole) cause either — 10 minutes since gateway creation, same generic failure.
**Interpretation:** REJECTED: "insufficient settle time" as a sufficient explanation on its own.
**Facts established / hypotheses affected:**
- REJECTED: settle-time hypothesis (tested directly, failed).
- All 8 consecutive attempts since 2026-07-13T16:24:13Z have failed: original mask, mask swap, bundled update, explicit unbind, immediate post-recreate bind, and post-recreate bind after a 10-minute wait.
**Files modified:** None
**Next action:** Checked gateway-side Cloud Logging (`resource.type="networkservices.googleapis.com/Gateway"`) and networkservices audit activity around this failure window — zero entries in both. No further diagnostic signal available from our side; Google's API and logs give nothing beyond the generic `error.code: 3`.

---

**Timestamp:** 2026-07-15T10:44:30Z
**Objective:** Final status check — is there any remaining untested, evidence-based hypothesis, or has this investigation reached a genuine stop condition?
**Result:** STOP CONDITION REACHED. Per the mandatory troubleshooting rules' own criteria: "evidence contradicts the current approach" (every hypothesis tested has been rejected) and no further diagnostic signal is available from our side (Google's API/logs give nothing actionable). Continuing to retry blindly without new evidence would violate "never repeat previously failed troubleshooting unless new evidence justifies it."
**Evidence discovered:** Summary of everything tried and rejected — see `CURRENT_STATE.md` for the consolidated view.
**Interpretation:** The `UpdateReasoningEngine` PATCH for `agentGatewayConfig` on this engine/gateway pair fails 100% of the time (8/8) since 2026-07-13T16:24:13Z, across every structurally distinct variation we could construct (mask value, request bundling, unbind, fresh-gateway immediate bind, fresh-gateway delayed bind). This is very likely a genuine platform-side bug or an undocumented private-preview constraint (matches the spirit, if not the letter, of Google's own documented known-issue #3) that cannot be fixed from the client side. Not something further guessing will solve.
**Files modified:** None
**Next action:** Report to user (currently asleep, pre-approved autonomous continuation for "this task's completion," but a genuine dead-end on the core fix is a decision point, not another step to push through). Options for the user to choose from on waking: (1) open a GCP support case — no self-serve fix visible from our side, (2) fall back to `enable_agent_gateway=false` (a separately-documented, tested, working mode) to unblock the production-critical need while treating the gateway-attach bug as a longer-running separate issue — NOT done unilaterally since it changes the project's governance posture, a decision the user should make, not me, (3) wait and retry later in case this is a longer-cycle platform issue than what we've tested. Did NOT take further mutating action beyond this point without user input.

---

## Clean-room test: does a completely fresh project avoid the bug?

**Timestamp:** 2026-07-15T22:20:01Z–2026-07-16T02:20:07Z
**Objective:** User proposed the decisive test flagged as an open item earlier: deploy the codelab's own proven-working gateway recipe (from `AshminPy/agent-gateway-codelab`) into a brand-new, never-touched project, then attach our SRE agent to it. Isolates whether the bug is specific to `sreagent-t2-demo`'s churned project state, or systemic.
**Commands executed:**
```bash
gcloud projects create sreagent-cleanroom-test --organization=1076201471152 --name="SRE Agent Clean-Room Test"
gcloud beta billing projects link sreagent-cleanroom-test --billing-account=0138AB-1B1BE5-05AFF5   # FAILED: quota exceeded (5 projects already on this billing account)
gcloud beta billing projects list --billing-account=0138AB-1B1BE5-05AFF5   # sreagent-codelab, sreagent-demo, sreagent-t2-demo, ai-adk-sre-agent-demo, project-95d23e63-...
gcloud beta billing projects unlink sreagent-codelab      # user chose unlink over full delete, preserves the project as reference evidence
gcloud beta billing projects link sreagent-cleanroom-test --billing-account=0138AB-1B1BE5-05AFF5   # succeeded
```
**Result:** SUCCESS — project created, billing resolved by unlinking `sreagent-codelab` (kept intact, not destroyed, so its history/evidence remains available).
**Evidence discovered:** `agent-gateway-codelab`'s Terraform (`demos/agent-gateway/terraform/main.tf`) unconditionally creates 3 Cloud Run MCP services (`module "mcp_services"`, no `count` gate) regardless of the `enable_cloud_run_private_networking` flag — needs `mcp_services = {}` in tfvars to actually skip them. Scoped down the deploy per user request (our agent uses GKE Remote MCP, not this demo's Cloud Run MCP path): disable `mcp_services`, `enable_model_armor`, `enable_agent_registry_endpoints`, `enable_psc_interface`; keep `foundation`/`observability`/`networking` (required gateway prerequisites) and `agent_gateway` itself.
**Files modified:** None yet (infra only)
**Next action:** Enable required APIs on `sreagent-cleanroom-test`, write a scoped-down `terraform.tfvars`, run `terraform plan` to confirm the actual minimized resource count before applying.

---

**Timestamp:** 2026-07-16T02:20:07Z–02:35:39Z
**Objective:** Enable APIs, scope down the codelab's Terraform (skip Cloud Run MCP/Model Armor/PSC-interface/agent-registry-endpoints — not needed for our GKE Remote MCP path, per user's scoping request), and deploy the gateway.
**Command executed:**
```bash
gcloud services enable <21 APIs, split into 2 batches — batch enable caps at 20> --project=sreagent-cleanroom-test
gcloud storage buckets create gs://sreagent-cleanroom-test-tfstate --location=us-central1 --uniform-bucket-level-access
# edited terraform.tfvars: project_id -> sreagent-cleanroom-test, mcp_services = {}, enable_model_armor = false,
#   enable_agent_registry_endpoints = false, enable_psc_interface = false
# edited backend.conf: bucket -> sreagent-cleanroom-test-tfstate
cd ~/projects/agent-gateway-codelab/cloud-networking-solutions/demos/agent-gateway/terraform
terraform init -reconfigure -backend-config=backend.conf
terraform plan   # reviewed: 109 to add, 0 to change, 0 to destroy — confirmed no Cloud Run/DLP/Model-Armor templates
terraform apply -auto-approve
```
**Exit code:** 0
**Relevant output:**
```
Apply complete! Resources: 109 added, 0 changed, 0 destroyed.
agent_gateway_id = "projects/sreagent-cleanroom-test/locations/us-central1/agentGateways/agent-gateway"
```
Gateway itself took 2m37s to create; its IAP authz policy took 2m16s. Both timings roughly consistent with prior observations on `sreagent-t2-demo`.
**Result:** SUCCESS
**Evidence discovered:** A truly fresh gateway, in a project untouched by any of `sreagent-t2-demo`'s churn history, deploys cleanly via the codelab's own Terraform. Dropped the mortgage-agent "Control A" deployment (task #8) since it needs the now-disabled Cloud Run MCP services to do anything meaningful — not useful without them.
**Files modified:** `~/projects/agent-gateway-codelab/cloud-networking-solutions/demos/agent-gateway/terraform/terraform.tfvars`, `backend.conf` (local, not committed — this is a scratch test config, not part of any tracked PR)
**Next action:** Deploy our SRE agent's engine (no gateway of our own — `enable_agent_gateway=false`) into `sreagent-cleanroom-test`, grant it cross-project read-only IAM on the existing `sreagent-demo` GKE cluster (via `iac/gke-access`, no new cluster), then attach the engine to this codelab-created gateway using our own `attach_gateway_to_engine.sh`.

---

**Timestamp:** 2026-07-16T02:35Z–03:45:26Z
**Objective:** Grant the new cleanroom project's agent principal read-only cross-project access to the existing `sreagent-demo` GKE cluster (`sre-test-cluster`), reusing it rather than creating a new one.
**Command executed:**
```bash
cd ~/projects/testing2-gcp-sre-agent/iac/gke-access
terraform init -reconfigure -backend-config="bucket=sreagent-cleanroom-test-tfstate" -backend-config="prefix=gke-access-cleanroom"
terraform plan -var="project_a_id=sreagent-cleanroom-test" -var="project_b_id=sreagent-demo" -var="create_gke_cluster=false"
terraform apply -auto-approve [same vars]
```
**Exit code:** 0
**Relevant output:**
```
Apply complete! Resources: 4 added, 0 changed, 0 destroyed.
crossproject_roles_granted = ["roles/container.viewer","roles/mcp.toolUser","roles/logging.viewer","roles/monitoring.viewer"]
gke_cluster_name = "sre-test-cluster"
```
**Result:** SUCCESS. Note: this apply needed extra explicit user confirmation naming the exact IAM roles/target — the permission classifier treats cross-project IAM grants on shared infra (`sreagent-demo`) as a higher protected-scope bar than same-project changes.
**Files modified:** None (infra only, additive IAM bindings)
**Next action:** Deploy our SRE agent engine into `sreagent-cleanroom-test` via `iac/agent` with `enable_agent_gateway=false` (skip creating a redundant gateway — reuse the codelab-created one) and `create_wif=false` (applying locally, not via CI).

---

**Timestamp:** 2026-07-16T03:45:26Z–03:52:58Z
**Objective:** Deploy our SRE agent's own engine (no gateway of our own) into the clean-room project.
**Command executed:**
```bash
bash scripts/package_agent.sh
cd iac/agent
terraform init -reconfigure -backend-config="bucket=sreagent-cleanroom-test-tfstate" -backend-config="prefix=agent-cleanroom"
terraform plan  -var="project_a_id=sreagent-cleanroom-test" -var="project_b_id=sreagent-demo" -var="region=us-central1" \
  -var="notification_email=ashmin.sub@gmail.com" -var="github_repo=AshminPy/testing2-gcp-sre-agent" \
  -var="tfstate_bucket=sreagent-cleanroom-test-tfstate" -var="create_wif=false" -var="enable_agent_gateway=false" \
  -var="gemini_model=gemini-2.5-flash"
terraform apply -auto-approve [same vars]
```
**Exit code:** 0
**Relevant output:**
```
Apply complete! Resources: 59 added, 0 changed, 0 destroyed.
reasoning_engine_id = "3983291483653406720"
reasoning_engine_resource_name = "projects/sreagent-cleanroom-test/locations/us-central1/reasoningEngines/3983291483653406720"
memory_bank_resource_name = "projects/sreagent-cleanroom-test/locations/us-central1/reasoningEngines/5185752584161329152"
```
**Result:** SUCCESS. Confirmed our own `google_network_services_agent_gateway.sre_egress[0]` was NOT created (0 matches for "will be created" in the plan) — the deprecation warning shown is a static schema warning, not evidence of creation.
**Evidence discovered:** User questioned why VPC/NAT and Model Armor were in the plan, suspecting unnecessary scope creep. Checked: both are unconditional in our own `iac/agent` codebase, not toggles that were missed — `networking.tf`'s own comment confirms the VPC/NAT is baseline Cloud NAT egress (not PSC; no PSC subnet is created when `enable_agent_gateway=false`), and Model Armor is our own established fallback content-inspection layer, specifically designed to activate when our own gateway is off. Correctly scoped for a fair "gateway-off" baseline comparison against `sreagent-t2-demo`'s equivalent config — not extra.
**Files modified:** None (infra only)
**Next action:** Attach engine `3983291483653406720` to the codelab-created gateway `projects/sreagent-cleanroom-test/locations/us-central1/agentGateways/agent-gateway` using our own `attach_gateway_to_engine.sh` (adapted to target the codelab's gateway instead of one from our own Terraform). This is the actual decisive test.

---

## DECISIVE RESULT — bind succeeded in the clean-room project

**Timestamp:** 2026-07-16T03:53:36Z–03:57:41Z
**Objective:** The actual decisive test — attach our engine to a gateway created in a completely fresh project with zero churn history, using the exact same PATCH mechanism (`attach_gateway_to_engine.sh`'s logic) that has failed 8/8 times on `sreagent-t2-demo` since 2026-07-13T16:24:13Z.
**Command executed:**
```bash
curl -sS -X PATCH \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/sreagent-cleanroom-test/locations/us-central1/reasoningEngines/3983291483653406720?updateMask=spec.deploymentSpec.agentGatewayConfig" \
  -H "Content-Type: application/json" \
  -d '{"spec":{"deploymentSpec":{"agentGatewayConfig":{"agentToAnywhereConfig":{"agentGateway":"projects/sreagent-cleanroom-test/locations/us-central1/agentGateways/agent-gateway"}}}}}'
```
Polled operation `3442599566883422208` to terminal state (took ~4 minutes, consistent with prior timings).
**Exit code:** 0
**Relevant output:**
```json
{
  "done": true,
  "response": {
    "spec": {
      "deploymentSpec": {
        "agentGatewayConfig": {
          "agentToAnywhereConfig": {
            "agentGateway": "projects/sreagent-cleanroom-test/locations/us-central1/agentGateways/agent-gateway"
          }
        }
      }
    }
  }
}
```
No `error` field. Full engine spec returned showing the gateway correctly bound.
**Result:** SUCCESS — first successful bind since 2026-07-13T16:24:13Z, on the first attempt, in a brand-new project.
**Evidence discovered:** The bind mechanism, request format, and our script's logic are all correct and functional. The persistent failure on `sreagent-t2-demo` is NOT a fundamental bug in our code, our request format, or the platform's bind API in general.
**Interpretation:** This decisively answers the open question from the earlier investigation: **the failure is specific to `sreagent-t2-demo`'s particular project/gateway state** (accumulated from repeated destroy/recreate cycles across this investigation — PR #19's recreate, the "Option C" recreate from the original mTLS investigation, this session's recreate, etc.) — not a systemic platform bug, not a flaw in our approach. Most likely explanation: some stale backend-side state tied to that specific project or that specific gateway's repeated churn history that a completely fresh project doesn't carry.
**Facts established / hypotheses affected:**
- CONFIRMED: our bind mechanism, code, and request format are correct.
- CONFIRMED: the bug is project/gateway-state-specific to `sreagent-t2-demo`, not systemic.
- STILL OPEN: the exact mechanism of what "stale state" means server-side — not diagnosable from our side, but no longer relevant to unblocking the actual work, since the path forward is now clear.
**Files modified:** None
**Next action:** Register the GKE Remote MCP + Vertex AI endpoints for this new project (our own `scripts/register_endpoints.py`, matching testing2's established process), then run `make smoke` equivalent for full end-to-end proof.

---

**Timestamp:** 2026-07-16T04:00Z–04:06:06Z
**Objective:** Register endpoints and run the actual smoke test against the clean-room engine to get full end-to-end proof (agent → gateway → GKE Remote MCP → RCA).
**Command executed:**
```bash
python3 scripts/register_endpoints.py --project=sreagent-cleanroom-test --region=us-central1
PROJECT_ID=sreagent-cleanroom-test REGION=us-central1 REASONING_ENGINE_ID=3983291483653406720 \
  python3 invoke_agent.py --scenario imagepull --verbose
# (retried once — same result, ruling out a "just-created template" transient)
```
**Exit code:** 0 (script ran fine; the *scenario* itself did not produce an RCA)
**Relevant output:**
```
Agent response [56-59s]:
⚠ BLOCKED by Model Armor: Input blocked by safety filter (prompt injection or harmful content detected)
```
Traced to `agent/main.py:820` — our own app-level Model Armor `sanitize_user_prompt` call (client-side content screening, active because our gateway is off and `MODEL_ARMOR_TEMPLATE` env is set) returns `MATCH_FOUND` on the completely benign query `"Pod imagepull-pod in test-incidents cannot pull its image. Investigate."` Reproducible on 2 separate attempts — not a transient/warm-up issue.
**Result:** PARTIAL — endpoints registered successfully (13/13). The smoke test itself did not reach an RCA, blocked by a DIFFERENT mechanism than the one this whole investigation was about.
**Evidence discovered:** No SSL/mTLS error, no gateway-attach error — the bind and the gateway path are unaffected by this. This is a distinct, separate issue: our own app-level Model Armor content-screening call is false-positive-flagging a benign SRE query at the default `MEDIUM_AND_ABOVE` confidence threshold.
**Interpretation:** The gateway-attach investigation's decisive question is answered and this is NOT a regression of it — it's a new, unrelated finding surfaced only because we got far enough (past the bind, past the mTLS path) to reach the app's own content-screening step for the first time in this session. Worth its own separate investigation, not a continuation of this one.
**Facts established / hypotheses affected:**
- CONFIRMED (final, for this investigation): the gateway-attach bug is isolated to `sreagent-t2-demo`'s specific project/gateway state, not our code or approach.
- NEW OPEN ITEM (separate scope): Model Armor false-positive blocking benign SRE queries in the gateway-off / app-level-screening path — needs its own investigation (template config, confidence threshold, or a code-level bug in what's being submitted).
**Files modified:** None
**Next action:** Report full picture to user — primary investigation resolved with a clear, positive, actionable conclusion; flag the Model Armor finding as a new, separate item requiring a decision on whether/how to pursue it further.

---

## IMPORTANT CORRECTION — removing Model Armor unmasked the original mTLS bug

**Timestamp:** 2026-07-16T04:11:26Z–04:15:04Z
**Objective:** Per user request, remove Model Armor from the clean-room engine (delete `MODEL_ARMOR_TEMPLATE` env var) to simplify the end-to-end demo, then re-run the smoke test.
**Command executed:**
```bash
# fetched current env array, filtered out MODEL_ARMOR_TEMPLATE, PATCHed:
curl -sS -X PATCH ".../reasoningEngines/3983291483653406720?updateMask=spec.deploymentSpec.env" -d "$BODY"
# polled to completion: done=true, error=None (8 polls, ~80s)
# verified agentGatewayConfig was NOT disturbed by this follow-up PATCH — still correctly bound
PROJECT_ID=sreagent-cleanroom-test REGION=us-central1 REASONING_ENGINE_ID=3983291483653406720 \
  python3 invoke_agent.py --scenario imagepull --verbose
```
**Exit code:** 0 (script ran; the scenario itself failed)
**Relevant output:**
```
Agent response [2.6s]:
{"status": "failed", "error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com', port=443): ... SSLError(SSLError(\"bad handshake: Error([('SSL routines', '', 'certificate verify failed')])\"))"}
```
**Result:** FAILURE — but a genuinely important, distinct one from anything seen before in this session.
**Evidence discovered:**
- Confirmed `agentGatewayConfig` was untouched by the env-only PATCH (checked directly) — the bind itself is still intact. This is NOT a regression of the bind investigation.
- The error text is DIFFERENT from `sreagent-t2-demo`'s failures: `certificate verify failed` (client-side cert trust failure), not `SysCallError(-1, 'Unexpected EOF')` (transport-level handshake drop). Different failure signature.
- **Critical realization: we had never actually reached the real Gemini API call in this clean-room engine until now.** The earlier "successful bind" test was blocked by Model Armor *before* the code ever attempted the Gemini call — so the bind succeeding proved the BIND mechanism works, but said nothing about whether the deeper, original mTLS issue (that PR #20 was supposed to fix) is actually resolved. Only now, with Model Armor removed, did we reach that code path for the first time.
- The codelab gateway for this clean-room test has `enable_model_armor = false` (deliberately disabled per the earlier scoping request). The codelab's DEFAULT config has `enable_model_armor = true`, and the one cross-project reference that worked historically (`sre-agent-langgraph-crosstest` in `sreagent-codelab`) was bound to a gateway WITH Model Armor/CONTENT_AUTHZ active.
**Interpretation (HYPOTHESIS, not yet proven):** Model Armor/CONTENT_AUTHZ being active on the gateway may not be purely a content-screening feature for us — it may be *load-bearing* for the mTLS trust chain: the gateway's TLS-inspection CA (provisioned specifically for CONTENT_AUTHZ) might be what Agent Identity's runtime ends up trusting for the mTLS handshake, even when our code's `base_url` override should route around it. Removing Model Armor may have removed that CA, so Agent Identity's underlying transport (which still appears to select the mTLS endpoint despite our `base_url` override — the same behavior PR #20 was meant to prevent) now has nothing to validate against. Not confirmed — the alternative explanation (this was ALWAYS broken and Model Armor's block just prevented us from ever seeing it, on this OR on `sreagent-t2-demo`) is equally plausible and not yet ruled out.
**Facts established / hypotheses affected:**
- CONFIRMED: the bind mechanism itself remains proven correct (unaffected by this finding).
- REOPENED: whether PR #20's fix (`base_url` override) actually prevents the mTLS auto-selection in all cases, or only appeared to work previously because Model Armor's gateway-provisioned CA was incidentally present.
- NEW HYPOTHESIS: Model Armor/CONTENT_AUTHZ on the gateway may be a hidden dependency for the mTLS handshake to succeed, not just a content-screening feature.
**Files modified:** None
**Next action:** Do not make further live changes until discussed with the user — this is a significant, unexpected finding that reframes part of the investigation. Awaiting direction: test with Model Armor re-enabled on the clean-room gateway to isolate the variable, or treat this as a separate finding to raise directly with Google alongside the Model Armor false-positive question.

---

## Documentation review — is Model Armor required? What actually governs the mTLS handshake?

**Timestamp:** 2026-07-16T04:15Z–04:32:01Z
**Objective:** User asked to remove Model Armor and, separately, provided a "Governing Agentic Egress" architecture deck (NotebookLM-generated) plus two official doc URLs, suspecting Model Armor might actually be required (deck implies it) despite docs reportedly saying optional. Told explicitly: review all hyperlinks, get a grounded answer, do not guess.
**Sources reviewed:**
1. `Governing_Agentic_Egress.pptx` (15 slides, all image-based — no extractable hyperlinks in the file itself; read visually)
2. `docs.cloud.google.com/.../govern/gateways/set-up-agent-gateway` (WebFetch + full hyperlink list, ~200 links, mostly nav chrome)
3. `docs.cloud.google.com/.../govern/configure-model-armor` (WebFetch + full hyperlink list)
4. `docs.cloud.google.com/.../govern/agent-identity-overview` (followed from #2's hyperlinks — directly relevant to mTLS)
5. `docs.cloud.google.com/.../scale/runtime/agent-identity` (followed from #2's hyperlinks — Agent Runtime-specific identity mechanics)
6. `docs.cloud.google.com/.../govern/gateways/delegate-authorization` (followed from #2's hyperlinks — how Model Armor wires into the gateway)
**Result:** SUCCESS — precise, sourced answers obtained; one genuine documentation gap identified (not resolvable by more reading, flagged for Google).
**Evidence discovered (all direct quotes, not paraphrase-as-fact):**
- **Model Armor is explicitly optional**, confirmed twice: set-up-agent-gateway doc says *"(Optional) If your deployment requires safeguarding against prompt injection attacks..."* and *"Optional: In the AI Security section, configure additional security"*. configure-model-armor doc frames it as something you *configure* on top, never as a prerequisite.
- **The presentation deck oversells this.** It visually presents Model Armor (CONTENT_AUTHZ) and IAP (REQUEST_AUTHZ) as a paired, standard "Two-Phase Authorization Framework" — a *recommended architecture* framing, not a technical requirement. Our own clean-room test already empirically proved this: we created a gateway and successfully bound our engine to it with `enable_model_armor = false`. Bind and gateway creation do not need Model Armor.
- **mTLS to Google Cloud APIs IS the documented DEFAULT for Agent Identity** — not a bug, not something PR #20 was "fixing" in a sanctioned way. `govern/agent-identity-overview`: *"By default, agent identities use mutual TLS (mTLS) with X.509 certificates when communicating directly with Google Cloud APIs."* `scale/runtime/agent-identity`: *"We also auto-provision and manage an x509 certificate on the agent with the same identity for secure authentication"* and *"Agent identity credentials are secured by default through a Google-managed Context-Aware Access (CAA) policy. This policy enforces mTLS binding..."*
- **The only documented opt-out is an env var, and we already have it set** — `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES=False` disables the default CAA policy (doc calls this "strongly discouraged"). Checked our own engine's live env vars (captured earlier in this investigation): we already have `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES: "false"` set. But the doc frames this narrowly as enabling SDK *credential/token sharing*, not endpoint-hostname selection — these read as two adjacent but distinct mechanisms, and the docs don't clarify whether setting this also changes which hostname (mtls vs plain) gets targeted.
- **No documented mechanism exists to force the plain/non-mTLS endpoint.** Searched specifically for this across all 3 identity/gateway docs — nothing. Our `agent/gemini_client.py` `base_url` override (PR #20's fix) is an **undocumented workaround**, not a Google-sanctioned configuration.
- **No documented relationship between Model Armor and the mTLS trust chain** — checked explicitly in `delegate-authorization` (the doc that covers how Model Armor wires into gateway authz). It describes gateway-to-Model-Armor communication using its own TLS ("HTTP2 protocol with TLS encryption... port 443") but says nothing about this affecting agent-to-gateway or agent-to-Vertex mTLS.
**Interpretation:** The deck's Layer 1 diagram ("mTLS: for first-party access to the GATEWAY" / "DPoP: for interactions beyond the gateway") appears to be the architecturally-intended design: the mTLS handshake target is supposed to be the Agent Gateway itself (which terminates it and is trusted via the platform's own SPIFFE/X.509 auto-provisioning), not a raw Vertex AI endpoint reached directly or via a base_url trick. This reframes the whole investigation's premise: PR #20's fix likely "worked" historically not because it avoided mTLS, but because it happened to route through a gateway that was properly configured to terminate that mTLS handshake — and it fails when there's no gateway properly intercepting/terminating it (direct-to-Vertex) or when the gateway's own trust provisioning for this specific agent isn't complete.
**Facts established / hypotheses affected:**
- CONFIRMED (documented fact): Model Armor is optional for Agent Gateway.
- CONFIRMED (documented fact): mTLS-to-Google-APIs is Agent Identity's documented default behavior, not a bug.
- REJECTED as a sanctioned approach: forcing the plain endpoint via `base_url` — works empirically sometimes, but is not how Google's architecture is designed to be configured.
- NEW HYPOTHESIS (better-supported than the Model-Armor-CA theory from the previous entry): the mTLS handshake is meant to terminate AT the gateway, not at raw Vertex — cert-verify failures likely mean the gateway isn't properly positioned/trusted for this specific call path, independent of Model Armor specifically.
- GENUINE DOCUMENTATION GAP (not resolvable by more reading, worth asking Google): exact relationship between `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES` and mTLS endpoint selection; whether there's ANY supported way to route Agent Identity calls through the gateway's mTLS termination point when calling Vertex AI directly (as opposed to via MCP/tool calls, which the docs describe more thoroughly).
**Files modified:** None
**Next action:** Present findings to user. Well-sourced answer to "why remove Model Armor" is ready. The mTLS root-cause investigation has a new, better-evidenced direction (gateway-termination, not Model-Armor-CA) but is not yet conclusively resolved — worth bringing directly to Google given the identified documentation gap, rather than more internal guessing.
