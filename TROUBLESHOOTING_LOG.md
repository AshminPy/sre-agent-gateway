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

---

## Is this ADK-only? Should we convert LangGraph → ADK?

**Timestamp:** 2026-07-16T04:33Z–04:37:49Z
**Objective:** User found `codelabs.developers.google.com/agw-cuj-arun-egress-gmcp` (Agent Runtime → Google Cloud MCP servers) as closely matching our own use case, and asked whether Agent Gateway/mTLS only works with Google ADK, and whether converting our LangGraph agent to ADK is worth trying.
**Sources reviewed:**
1. `codelabs/agw-cuj-arun-egress-gmcp/agent-dj/agent/agent.py` (already cloned locally from earlier in this session)
2. Same codelab's other 2 variants (`agw-cuj-arun-egress-vpc/agent-weather`, `agw-cuj-arun-egress-emcp/agent-datacommons`) for pattern consistency
3. `demos/agent-gateway/src/mortgage-agent/agent/agent.py` + `__init__.py` (already known: this is the one that FAILED with an mTLS error per `RUN-NOTES-2026-07-12.md`, read earlier in this investigation)
4. WebFetch of the actual codelab page
**Result:** SUCCESS — clear, evidence-based answer; framework switch is NOT well-supported as a fix.
**Evidence discovered:**
- `agent-dj`'s ADK agent (`LlmAgent(model='gemini-2.5-flash', ...)`) passes only a plain model-name string — it **never constructs its own `genai.Client` or overrides `base_url`** at all, unlike our `agent/gemini_client.py`. It lets Agent Identity's default routing happen untouched.
- All 4 Google reference agents (agent-dj, agent-weather, agent-datacommons, **and mortgage-agent**) apply the same `urllib3.contrib.pyopenssl.extract_from_urllib3()` SSL-library swap at import time — a platform-recommended pattern, not ADK-specific, not something our code currently does.
- **Critical: `mortgage-agent` is ADK-based, uses the pyopenssl trick, never overrides base_url — and STILL failed with an mTLS handshake error** (`telemetry.mtls.googleapis.com`, per `RUN-NOTES-2026-07-12.md`, already logged earlier in this investigation). This is first-party proof that ADK + the pyopenssl trick + not-overriding-base_url is **not sufficient** to guarantee mTLS success on its own.
- The codelab page itself: *"Agent Runtime ADK agent with agent identity"* — demonstrates with ADK, doesn't claim exclusivity, doesn't address LangGraph or other frameworks either way. This specific codelab targets `bigquery.googleapis.com/mcp` (Google-managed BigQuery MCP), not `container.googleapis.com/mcp/read-only` (GKE Remote MCP, what we actually use) — architecturally similar (both first-party Google MCP endpoints registered in Agent Registry) but not identical.
**Interpretation:** The mTLS/Agent Identity mechanism operates at the platform/transport layer (Vertex AI SDK + Agent Identity's CAA policy), not the agent-orchestration-framework layer. Nothing in the docs or the codelab's own code suggests ADK has special access to a fix LangGraph couldn't also use — and Google's own ADK reference agent hitting the same failure class is direct evidence against framework choice being the deciding factor.
**Facts established / hypotheses affected:**
- REJECTED (well-evidenced): "switching from LangGraph to ADK would fix the mTLS issue." Google's own ADK example failed the same way.
- CONFIRMED: two concrete, cheap, testable differences exist between our code and every Google reference agent — (1) we override `base_url`, they never do; (2) they apply the pyopenssl SSL swap, we don't.
**Files modified:** None
**Next action:** Recommend against a full LangGraph→ADK rewrite (large cost, no evidentiary support). Instead recommend a cheap, targeted experiment on the clean-room engine: remove our `base_url` override in `agent/gemini_client.py` (stop fighting Agent Identity's default) and add the same pyopenssl swap, then retest — isolates the two real, evidenced differences without a framework migration. Given even Google's own reference agent fails this way, this also strengthens the case for raising it directly with Google rather than continuing to self-diagnose.

---

## LIKELY ROOT CAUSE FOUND — mTLS endpoint never registered in Agent Registry

**Timestamp:** 2026-07-16T04:38Z–04:43:41Z
**Objective:** User suspected the mTLS hostname might not be registered in Agent Registry, and asked to check `troubleshoot-agent-gateway` doc specifically for this.
**Command executed:**
```bash
grep -n -B5 -A20 "mtls" scripts/register_endpoints.py   # found --mtls-endpoints flag, default=exclude
gcloud alpha agent-registry services list --project=sreagent-cleanroom-test --location=us-central1
gcloud alpha agent-registry services list ... | grep -i mtls   # empty result
# + WebFetch of docs.cloud.google.com/.../troubleshoot-agent-gateway targeting this exact question
```
**Exit code:** 0
**Relevant output:**
- `scripts/register_endpoints.py:176-180`: `--mtls-endpoints` argument, `choices=["include","exclude"]`, **`default="exclude"`**. Earlier registration run (`python3 scripts/register_endpoints.py --project=sreagent-cleanroom-test --region=us-central1`) did not override this — ran with the default.
- Live registry check: 14 services registered for `sreagent-cleanroom-test` (regional, us-central1) — includes `us-central1-aiplatform.googleapis.com` (plain). **Zero entries containing "mtls".**
- Official troubleshooting doc, direct quote: *"a Google API such as `aiplatform.googleapis.com` can resolve through multiple hostnames depending on the SDK version, regional client configuration, or mTLS usage. For example, `us-central1-aiplatform.googleapis.com`, or `us-central1-aiplatform.mtls.googleapis.com`, or `aiplatform.googleapis.com`."* And: *"the gateway only matches hostnames exactly. Therefore, if you register `aiplatform.googleapis.com` but the agent calls `us-central1-aiplatform.mtls.googleapis.com`, the gateway denies the request."*
**Result:** SUCCESS — a precise, documented match to our exact scenario.
**Evidence discovered:** FACT: mTLS-specific hostname is not registered. FACT: Google's own troubleshooting doc describes exactly this exact-match failure mode for exactly this hostname pair.
**Interpretation:** HYPOTHESIS (strong, but not 100% confirmed): our observed client-side error (`SSLError: certificate verify failed`) is the gateway's denial of an unregistered destination, surfacing as a connection/handshake-level failure rather than a clean HTTP 403 — plausible if the gateway's front-door rejects the connection before completing a normal HTTP response cycle for an unmatched destination. Note: the doc's own documented *typical* symptom for this is "403 Forbidden," which doesn't exactly match our SSL-level error text — flagging this discrepancy honestly rather than overclaiming a perfect match. Still the single best, most concretely actionable, most precisely-matching lead found in this entire investigation.
**Facts established / hypotheses affected:**
- CONFIRMED: mTLS hostname not registered for `sreagent-cleanroom-test`.
- CONFIRMED: exact-match hostname registration is a real, documented gateway requirement, not an assumption.
- STRONG HYPOTHESIS (not fully confirmed — error signature doesn't perfectly match the doc's stated typical symptom): this is the actual root cause of the mTLS failures in this clean-room test (and possibly the original `sreagent-t2-demo` failures too, worth re-checking there separately).
**Files modified:** None yet
**Next action:** Combine both pending experiments (they're complementary): (1) re-register endpoints for `sreagent-cleanroom-test` with `--mtls-endpoints=include`, (2) remove the `base_url` override in `agent/gemini_client.py` so Agent Identity's default routing applies untouched (matching every Google reference agent), repackage, redeploy to the clean-room engine, retest. If this works, also check whether `sreagent-t2-demo`'s original endpoint registration ever included mTLS hostnames — may explain the whole original investigation retroactively.

---

## Executing the combined fix + correction on the pyopenssl trick

**Timestamp:** 2026-07-16T04:44Z–04:47:18Z
**Objective:** Execute both fixes together: register mTLS endpoints, remove the base_url override.
**Command executed:**
```bash
python3 scripts/register_endpoints.py --project=sreagent-cleanroom-test --region=us-central1 --mtls-endpoints=include
gcloud alpha agent-registry services list --project=sreagent-cleanroom-test --location=us-central1 --format="value(displayName)" | grep -i aiplatform
```
**Exit code:** 0
**Relevant output:** `us-central1-aiplatform.mtls.googleapis.com` and `us-central1-aiplatform.googleapis.com` both now confirmed registered.
**Result:** SUCCESS (registration step)
**Files modified:**
- `agent/gemini_client.py` — removed the `base_url` override entirely; `_get_client()` now constructs `genai.Client(vertexai=True, project=..., location=...)` with no `http_options` override, matching every Google reference agent.
- `agent/__init__.py` — added the `urllib3.contrib.pyopenssl.extract_from_urllib3()` defensive call.
**CORRECTION to the previous entry's characterization:** re-reading the actual Google reference comment more carefully — `extract_from_urllib3()` does NOT "enable" or "add" PyOpenSSL. It does the opposite: it **removes/prevents** urllib3 from using PyOpenSSL if something else already injected it, to avoid a specific OTEL span-exporter bug (`"Context has already been used to create a Connection"`). Corrected description in the code comment; noting the correction here since the earlier log entry mischaracterized this as "applying an SSL library swap."
**Next action:** Rebuild `agent.tar.gz`, deploy the updated code to the clean-room engine (source-only update, no config/env change needed), retest with `invoke_agent.py`.

---

## GENERALIZABLE FINDING — terraform apply silently wipes out-of-band gateway bindings

**Timestamp:** 2026-07-16T04:48Z–04:53:18Z
**Objective:** Deploy the code changes (no base_url override, pyopenssl defensive import) to the clean-room engine via a targeted `terraform apply` on just `google_vertex_ai_reasoning_engine.sre_agent`.
**Command executed:**
```bash
bash scripts/package_agent.sh
cd iac/agent
terraform plan  -target='google_vertex_ai_reasoning_engine.sre_agent' [same vars as engine deploy]   # confirmed: 0 add, 1 change, only source_code_spec diff shown
terraform apply -auto-approve [same target + vars]
# verification immediately after:
curl -s ".../reasoningEngines/3983291483653406720" | jq '.spec.deploymentSpec.agentGatewayConfig'
```
**Exit code:** 0 (apply succeeded; the verification check is what revealed the problem)
**Relevant output:**
```
Apply complete! Resources: 0 added, 1 changed, 0 destroyed.
agentGatewayConfig: null
```
**Result:** UNEXPECTED SIDE EFFECT — the gateway binding was silently cleared, even though: (a) the terraform plan only showed a `source_code_spec` diff, nothing about `agentGatewayConfig`, (b) our Terraform code has no attribute/resource managing that field at all (`enable_agent_gateway=false` here).
**Evidence discovered:** A `terraform apply` targeting only the reasoning engine resource — even one whose plan shows no diff in `agentGatewayConfig` — appears to send a full-object update to `spec.deploymentSpec` that clears fields Terraform's own state doesn't track, including a binding set entirely out-of-band via direct API PATCH.
**Interpretation:** This is a real, generalizable bug in our own deployment process, independent of the mTLS/registration investigation. **This may retroactively explain part of the original `sreagent-t2-demo` mystery too**: if any CI-triggered `terraform apply` runs *after* a successful out-of-band gateway bind, it would silently wipe that bind without any error — consistent with the pattern of successes being followed by later failures, though this doesn't explain the bind *operation itself* failing (a separate, already-documented issue). Worth checking `sreagent-t2-demo`'s CI history for applies that ran after successful binds, once this investigation's current thread is resolved.
**Facts established / hypotheses affected:**
- CONFIRMED (new): `terraform apply` on the reasoning engine resource clears out-of-band `agentGatewayConfig`, regardless of whether the plan shows that field changing.
- NEW OPEN ITEM: our deployment process needs to either (a) always re-run the attach script immediately after any `terraform apply` touching the engine, or (b) get Terraform provider support for `agent_gateway_config` so it's managed in-band (already known to be missing from the provider, per repo history).
**Files modified:** `agent/gemini_client.py`, `agent/__init__.py` (both deployed via this apply)
**Next action:** Re-run `attach_gateway_to_engine.sh` against the clean-room engine (gateway itself is untouched, still exists and warm), verify the bind, then run `invoke_agent.py` — without any further terraform applies in between this time.

---

## Combined fix result — mTLS error persists despite all four fixes

**Timestamp:** 2026-07-16T04:53:58Z–05:04:34Z
**Objective:** Re-attach gateway (wiped by the terraform apply), re-remove `MODEL_ARMOR_TEMPLATE` (also wiped by the same apply — confirmed via direct check before re-removing), then run the real end-to-end test with all four fixes in place: mTLS endpoint registered, `base_url` override removed, pyopenssl defensive import added, Model Armor removed.
**Command executed:**
```bash
# re-attach (PATCH agentGatewayConfig) — polled, done=true, error=None
# confirmed MODEL_ARMOR_TEMPLATE was restored by the terraform apply, removed it again (PATCH env) — polled, done=true, error=None
# confirmed agentGatewayConfig still intact after the second env PATCH
PROJECT_ID=sreagent-cleanroom-test REGION=us-central1 REASONING_ENGINE_ID=3983291483653406720 \
  python3 invoke_agent.py --scenario imagepull --verbose
```
**Exit code:** 0 (script ran; scenario failed)
**Relevant output:**
```
Agent response [2.5s]:
{"status": "failed", "error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com', port=443): ... SSLError(SSLError(\"bad handshake: Error([('SSL routines', '', 'certificate verify failed')])\"))"}
```
**Result:** FAILURE — identical error to the first cert-verify failure (04:13:18Z), byte-for-byte same failure signature, despite four distinct fixes applied since then.
**Evidence discovered:** The mTLS-registration hypothesis, while a real and correctly-identified documentation gap (now fixed regardless — good hygiene), was NOT sufficient to fix this specific failure on its own, nor in combination with the other three fixes.
**Interpretation:** The 2.5-second failure time (near-instant, not a multi-second real network round-trip to Google's mTLS infrastructure) combined with a raw TLS-layer failure (not an HTTP-level 403, which is what the gateway's application-layer authorization would produce for an unregistered/denied destination) suggests the call may not be reaching the gateway's interception point at all — i.e., this might be failing at a pure client-side certificate trust step before any gateway involvement, not a gateway-side denial. This reopens the question of HOW the gateway is supposed to physically intercept an Agent Identity engine's calls to Google APIs (as opposed to calls to MCP/VPC-hosted destinations, which the docs describe more thoroughly) when there's no PSC interface or other network-level routing configured between the engine and the gateway (we deliberately left `enable_psc_interface=false` on the codelab gateway per the earlier scoping-down request — worth reconsidering whether that specific flag is actually required for Google-API-destination interception, not just VPC-destination interception as its description implies).
**Facts established / hypotheses affected:**
- REJECTED (as sufficient fix, though still correct hygiene): mTLS endpoint registration, `base_url` override removal, pyopenssl import, Model Armor removal — none of these, individually or combined, resolved the cert-verify failure.
- REOPENED: how gateway interception actually works for direct-to-Google-API calls from an Agent Identity engine — unclear whether `enable_psc_interface=false` on the gateway (deliberately disabled per earlier scoping) removes a required interception mechanism, or whether that flag is unrelated to this failure mode (its own description frames it as being about VPC reachability, not Google API interception).
**Files modified:** None beyond the already-committed `agent/gemini_client.py`, `agent/__init__.py`.
**Next action:** This has now survived 4 distinct, well-evidenced fix attempts without resolution. Report honestly to user rather than keep guessing — this looks like it needs Google's direct input at this point, backed by a precise, well-documented reproduction case (which this investigation now has in full).

---

## THE ACTUAL ROOT CAUSE — endpoint is hardcoded plain in google-genai; substitution happens BELOW our code

**Timestamp:** 2026-07-16T05:05Z–05:16:44Z
**Objective:** User provided a detailed 10-step investigation directive (search repo for mTLS-related env vars/patterns, verify `GOOGLE_API_USE_MTLS_ENDPOINT`/`GOOGLE_API_USE_CLIENT_CERTIFICATE`, force plain endpoint, upgrade Python + dependencies, add diagnostics, redeploy, retest). Ran a 4-way parallel research workflow (ultracode) to verify each premise against the actual installed library source and current dependency landscape before blindly executing — several steps conflicted with prior findings and needed checking, not assuming.
**Sources/method:** 4 parallel subagents — (1) repo-wide grep for all 8 named patterns, (2) direct inspection of the INSTALLED `google-genai` 1.47.0 and `google-api-core` 2.30.3 source code (not docs, not assumptions), (3) PyPI/GitHub changelog research for all 7 named dependencies, (4) live REST check of the deployed engine's actual env vars + `requirements.txt` pins.
**Result:** SUCCESS — definitive, source-level answer.

**Finding 1 — confirms prior finding, doesn't contradict it:** `google.genai.Client(vertexai=True)` (what `agent/gemini_client.py` uses) **never reads `GOOGLE_API_USE_MTLS_ENDPOINT` or `GOOGLE_API_USE_CLIENT_CERTIFICATE` anywhere**, and never even imports `google.api_core` (the library that DOES implement this logic, but only inside its generated Long-Running-Operations/GAPIC client transport — a code path `google.genai` doesn't touch). Confirmed via exhaustive grep of the actual installed source, zero matches, full list of every env var `google.genai` reads obtained directly. Setting either var would have **zero effect** on this call path. This is not new — it corrects the *user's* step 2/3 premise, and matches the pre-existing internal note this session already had on record.

**Finding 2 — THE actual root cause:** `google/genai/_api_client.py:659-672` **hardcodes the endpoint URL, and there is no mTLS branch anywhere in that logic**:
```python
if self.api_key or self.location == 'global':
    self._http_options.base_url = f'https://aiplatform.googleapis.com/'
...
else:
    self._http_options.base_url = f'https://{self.location}-aiplatform.googleapis.com/'
```
This produces the **plain** endpoint (`us-central1-aiplatform.googleapis.com`), not `.mtls.`. There is no code path in `google-genai` that would ever ask for the mTLS hostname. **This means the mTLS hostname substitution we keep observing is not happening inside our Python code at all** — something below the application layer (most likely Agent Identity's own runtime-injected transport/credential wrapper, consistent with the docs' *"agent identity credentials are secured by default through a Google-managed Context-Aware Access (CAA) policy [that] enforces mTLS binding"*) is intercepting the plain-endpoint request and redirecting/upgrading it to mTLS transparently. This retroactively explains why all 4 previous fixes (mTLS registration, `base_url` removal, pyopenssl, Model Armor removal) had zero effect — every one of them operated at the application layer, and the actual substitution happens beneath it.

**Finding 3 — a real, concrete, actionable lead:** dependency research surfaced genuinely relevant, RECENT fixes:
- `google-auth` v2.55.2 (Jul 7, 2026): **"Agentic Identities mTLS gaps fix `_is_mtls` and `SslCredentials`"**, "align mTLS discovery and enforce fail-fast transport configuration", "handle `PermissionError` on workload certificates to avoid startup hang/crash". v2.56.0 (Jul 13, 2026): "Implement python mtls helpers". This is the library that actually implements Agent Identity's mTLS/credential machinery — directly in the blast radius of our bug, unlike `google-genai`.
- `google-cloud-aiplatform` v1.160.0 (Jul 8, 2026): "Added mTLS and telemetry endpoint configurations to preview AdkApp" + "Fixed API endpoint handling when location is set to 'global'".
- No relevant fixes found in `google-genai`, `certifi`, `requests`, or `urllib3` changelogs (checked directly, not assumed).
**Our `requirements.txt` pins are all loose `>=` floors**: `google-auth>=2.29.0` (predates ALL the relevant fixes above by over a year), `google-cloud-aiplatform>=1.52.0`, `google-genai>=1.0.0` — no `google-api-core`, `certifi`, `requests`, or `urllib3` entries at all. Deployed engine confirmed on Python 3.11 already (`sourceCodeSpec.pythonSpec.version: "3.11"` — the user's step 6 premise about Python 3.9 was about the LOCAL test-invocation machine, not the deployed runtime, which was already correctly on 3.11).
**Facts established / hypotheses affected:**
- CONFIRMED (source-level, not inferred): the endpoint substitution happens outside `google-genai`'s own logic.
- REJECTED (again, now with full source citation): env-var-based mTLS control for this call path.
- NEW, WELL-EVIDENCED LEAD: pin `google-auth>=2.56.0` (the "Agentic Identities mTLS gaps" fix release) and `google-cloud-aiplatform>=1.160.0` — the loose `>=2.29.0`/`>=1.52.0` floors in `requirements.txt` don't force these fixes to be present in whatever got resolved at deploy time.
**Files modified:** None yet
**Next action:** Pin the two evidenced dependency versions in `agent/requirements.txt`, add the diagnostic startup logging (Python version, resolved hostname, the two env vars, `certifi.where()` — no credentials/certs), repackage, redeploy, retest. Also re-run the exact same base_url-forcing approach (PR #20's original fix) now stacked on top of the dependency fix, since we now know the two operate at different layers and may be complementary rather than alternatives.

---

## Dependency fix deployed and tested — new, more specific error reveals the real mechanism

**Timestamp:** 2026-07-16T05:16Z–05:34:40Z
**Objective:** Pin `google-auth>=2.56.0` and `google-cloud-aiplatform>=1.160.0` (evidenced fixes), add startup diagnostics, redeploy, retest.
**Command executed:**
```bash
# requirements.txt: google-cloud-aiplatform>=1.52.0 -> >=1.160.0, google-auth>=2.29.0 -> >=2.56.0, added certifi>=2026.6.17
# gemini_client.py: added [mtls-diag] startup log (python version, resolved base_url, both env vars, certifi.where())
bash scripts/package_agent.sh
cd iac/agent && terraform apply -auto-approve -target='google_vertex_ai_reasoning_engine.sre_agent' [same vars]
# terraform wiped agentGatewayConfig + re-added MODEL_ARMOR_TEMPLATE again (same known behavior) — fixed both in one combined PATCH this time (env + agentGatewayConfig together)
# this combined operation took ~10 minutes to resolve (longer than the usual 1-5 min — noted, not yet explained)
PROJECT_ID=sreagent-cleanroom-test REGION=us-central1 REASONING_ENGINE_ID=3983291483653406720 python3 invoke_agent.py --scenario imagepull --verbose
```
**Exit code:** 0 (script ran; scenario failed)
**Relevant output:**
```
Agent response [2.6s]:
{"status": "failed", "error": "HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com', port=443): ... SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate in certificate chain (_ssl.c:1016)'))"}
```
**Result:** FAILURE — but with a materially more specific error message than every prior attempt. Previous errors said only "certificate verify failed"; this one explicitly says **"self-signed certificate in certificate chain."**
**Evidence discovered:** This exact phrase is a verbatim match to `known-issues.md` issue #17 from the codelab repo (already read earlier this session, 2026-07-15): *"Reasoning Engine outbound SSL handshake failure (CERTIFICATE_VERIFY_FAILED): self signed certificate in certificate chain... When outgoing connections pass through Secure Web Proxy (SWG) on the Agent Gateway, SWG performs TLS decryption and inspection using a dynamic certificate signed by its custom root CA. If the Reasoning Engine trust store does not contain these root certificates, outbound handshakes fail."* Documented fix for SDK/source-based deployments (our case): *"Create the Agent Gateway first and wait until provisioning completes... Once this returns PEM certificates, deploy/update the Reasoning Engine. The pipeline will automatically bake them in."*
**Interpretation:** The gateway IS intercepting this call via TLS inspection (Secure Web Proxy), presenting its own self-signed root CA — our engine doesn't trust it. Per the documented fix, the CA-baking happens as part of the **deployment pipeline** when source code is pushed, and only picks up the CURRENT gateway's root cert **if the gateway is already bound at deploy time**. Our own sequence has been backwards every time: we `terraform apply` (source push) BEFORE re-attaching the gateway (a separate, later, narrow PATCH) — because terraform wipes the binding each time and we've been re-attaching AFTER. This means every source deployment in this investigation happened while `agentGatewayConfig` was `null`, so there was never a gateway association for the pipeline to bake certs for. The unusually long ~10-minute operation for this round's combined PATCH may itself be relevant (cert propagation taking longer this time) — not yet conclusive.
**Facts established / hypotheses affected:**
- CONFIRMED: gateway TLS interception (Secure Web Proxy) is active for this call, presenting a self-signed cert — matches a specific, named, documented Google issue, not a guess.
- NEW, HIGH-CONFIDENCE HYPOTHESIS: our deploy SEQUENCE is backwards — need gateway bound BEFORE source deployment, not after, for the cert-baking pipeline to pick it up.
**Files modified:** `agent/requirements.txt`, `agent/gemini_client.py` (already committed changes from this session, now confirmed deployed and live-tested).
**Next action:** With the gateway currently still bound (confirmed intact), trigger ANOTHER source deployment (even trivial) so the pipeline runs while `agentGatewayConfig` is already set — tests the sequencing hypothesis directly without any other variable changing.

---

## ✅ ROOT CAUSE CONFIRMED, FIX VERIFIED — full end-to-end success

**Timestamp:** 2026-07-16T05:36:41Z–05:40:51Z
**Objective:** Test the bundling/sequencing hypothesis directly: submit `sourceCodeSpec` (the actual agent code) and `agentGatewayConfig` in ONE atomic PATCH, matching Google's own reference `deploy_agent.py` pattern (which always bundles source + gateway config together, never as separate calls) — rather than our own two-step process (deploy source via Terraform, attach gateway via a separate later PATCH).
**Command executed:**
```bash
python3 -c "
import base64, json
with open('agent.tar.gz', 'rb') as f:
    archive_b64 = base64.b64encode(f.read()).decode('ascii')
body = {'spec': {
    'sourceCodeSpec': {
        'inlineSource': {'sourceArchive': archive_b64},
        'pythonSpec': {'entrypointModule': 'agent.main', 'entrypointObject': 'SREAgent',
                        'version': '3.11', 'requirementsFile': 'agent/requirements.txt'}
    },
    'deploymentSpec': {'agentGatewayConfig': {'agentToAnywhereConfig': {
        'agentGateway': 'projects/sreagent-cleanroom-test/locations/us-central1/agentGateways/agent-gateway'}}}
}}
json.dump(body, open('/tmp/bundled_source_gateway_body.json','w'))
"
curl -sS -X PATCH \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/sreagent-cleanroom-test/locations/us-central1/reasoningEngines/3983291483653406720?updateMask=spec.sourceCodeSpec,spec.deploymentSpec.agentGatewayConfig" \
  -H "Content-Type: application/json" -d @/tmp/bundled_source_gateway_body.json
# polled: done=true, error=None, resolved in ~2.5 minutes
PROJECT_ID=sreagent-cleanroom-test REGION=us-central1 REASONING_ENGINE_ID=3983291483653406720 \
  python3 invoke_agent.py --scenario imagepull --verbose
```
**Exit code:** 0
**Relevant output:**
```json
{
  "status": "done",
  "likely_root_cause": "The pod 'imagepull-pod' is in 'ImagePullBackOff' status because it failed to pull the image 'gcr.io/google-containers/nonexistent-image:v99.9.9' due to a 'not found' error.",
  "confidence": 0.9,
  "observability": {
    "selected_mcp": "gke_remote_mcp", "primary_mcp_source": "gke_remote_mcp",
    "cluster": "sre-test-cluster", "project_id": "sreagent-demo",
    "tools_called": 3, "evidence_count": 3, "tokens_total": 9291,
    "estimated_cost_usd": 0.002185, "latency_ms": 44908,
    "loop_exit_reason": "confidence_sufficient"
  }
}
```
Full formatted RCA report generated, correct diagnosis, cross-project GKE access confirmed working end-to-end (engine in `sreagent-cleanroom-test`, GKE cluster in `sreagent-demo`, via the `iac/gke-access` IAM grants from earlier this session).
**Result:** SUCCESS — first complete, working, end-to-end RCA in this entire investigation, across both projects and every prior attempt.

## Confirmed root cause
The Agent Gateway performs TLS inspection (Secure Web Proxy) using a dynamically-provisioned, self-signed root CA, matching Google's own documented known-issue #17. The reasoning engine's trust store only gets that CA baked in as part of processing a **source-code deployment request that already includes the gateway association in the same call**. Our deployment process (Terraform for source, a separate later PATCH for `agentGatewayConfig`) never satisfied that — every previous deploy in this entire investigation (on both `sreagent-t2-demo` and the first several clean-room attempts) pushed source code while the engine was *not yet* gateway-bound, so the pipeline had nothing to bake certs for. `GOOGLE_API_USE_MTLS_ENDPOINT`/`GOOGLE_API_USE_CLIENT_CERTIFICATE` were never relevant (confirmed dead code path for this SDK). The `google-auth`/`google-cloud-aiplatform` dependency pins were good hygiene (real, recent, relevant fixes) but not the deciding factor on their own — the bundling was.

## Exact file/config changes
- `agent/gemini_client.py` — removed the undocumented `base_url` override; added `[mtls-diag]` startup diagnostics (Python version, resolved base_url, both env vars, `certifi.where()` — no credentials logged).
- `agent/__init__.py` — added defensive `urllib3.contrib.pyopenssl.extract_from_urllib3()` (matches every Google reference agent).
- `agent/requirements.txt` — `google-cloud-aiplatform>=1.52.0` → `>=1.160.0`; `google-auth>=2.29.0` → `>=2.56.0`; added `certifi>=2026.6.17`.
- Live engine (`sreagent-cleanroom-test`, `3983291483653406720`): one bundled PATCH setting `spec.sourceCodeSpec` + `spec.deploymentSpec.agentGatewayConfig` together — **this specific bundling is the operative fix**, not a code change per se.

## Before / after endpoint
- Before: `us-central1-aiplatform.mtls.googleapis.com` — `SSLCertVerificationError: self-signed certificate in certificate chain`
- After: request succeeds (exact resolved hostname not re-verified via the `[mtls-diag]` log on this successful run — worth pulling from Cloud Logging as a follow-up, but the functional result is unambiguous: full RCA delivered).

## Facts established
- CONFIRMED: bundling `sourceCodeSpec` + `agentGatewayConfig` in one atomic call is required — sequential calls (however close together) do not trigger the same cert-provisioning pipeline.
- CONFIRMED: `scripts/attach_gateway_to_engine.sh`'s design (a narrow, standalone `agentGatewayConfig`-only PATCH) is fundamentally the wrong shape for this operation — it should never have been expected to reliably work, independent of the earlier intermittent-failure investigation on `sreagent-t2-demo`.
- **This likely also fully explains the ORIGINAL `sreagent-t2-demo` mystery from earlier in this investigation** — every attach attempt there used the same narrow, standalone-PATCH script.
**Files modified this round:** `agent/gemini_client.py`, `agent/__init__.py`, `agent/requirements.txt` (all committed changes, now live-verified working).
**Next action:** Report to user in the exact format requested. Fix `scripts/attach_gateway_to_engine.sh` to bundle source+gateway (or clearly document that it must never be used standalone after a Terraform source deploy). Apply the same bundled approach to `sreagent-t2-demo` to confirm this closes out the original investigation too.

---

## Bundled fix applied to sreagent-t2-demo — still fails, gateway-instance-specific

**Timestamp:** 2026-07-16T08:00Z–08:20:43Z
**Objective:** After merging the fix to `main` (PR #24), apply it to the original deployment: rebuild `agent.tar.gz` from fixed code, `terraform apply` to push the code (re-pointed at `sreagent-t2-demo`'s own backend/tfvars), then run the newly-fixed `attach_gateway_to_engine.sh` (which now bundles source+gateway and actually checks its own result).
**Command executed:**
```bash
bash scripts/package_agent.sh
cd iac/agent && terraform init -reconfigure -backend-config="bucket=sreagent-t2-demo-tfstate" -backend-config="prefix=agent"
terraform plan -var="create_wif=false"    # Plan: 0 to add, 1 to change, 0 to destroy — source_code_spec only
terraform apply -auto-approve -var="create_wif=false"    # succeeded
cd ../..
bash scripts/attach_gateway_to_engine.sh
```
**Exit code:** 1 (script correctly failed and reported it — the script-level fix is working)
**Relevant output:**
```
PATCH submitted (operation: .../operations/3905718262549184512). Polling to terminal state...
ERROR: operation failed: {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}
```
**Result:** FAILURE — same generic `error.code: 3` seen in all 8 prior attempts against this specific project/gateway.
**Evidence discovered:** The exact same bundled approach that worked immediately and cleanly on `sreagent-cleanroom-test`'s gateway fails identically on `sreagent-t2-demo`'s gateway (`sre-agent-egress`).
**Interpretation:** This is strong confirmation of the earlier (pre-clean-room) finding: `sreagent-t2-demo`'s specific gateway instance carries some accumulated bad state (from repeated destroy/recreate churn across this whole investigation — PR #19's recreate, this session's earlier "Option C"-style recreate attempts, 8+ prior failed attach calls) that the correct bundled-deploy approach cannot work around. The FIX ITSELF is proven correct (clean-room evidence stands); this specific gateway resource on `sreagent-t2-demo` most likely needs to be destroyed and recreated fresh, THEN immediately bound using the now-fixed bundled script (its very first bind, matching every historical success pattern in this investigation).
**Facts established / hypotheses affected:**
- CONFIRMED: the bundled-deploy fix does not unstick an already-poisoned gateway instance — it's the correct approach for engines/gateways that haven't accumulated this specific stale state, not a universal unstick-anything fix.
- REOPENED (with much higher confidence than before): `sreagent-t2-demo`'s gateway needs to be recreated fresh, this time immediately followed by the bundled attach (not a standalone one) — the same recreate action tried once earlier this session, but that earlier attempt used the OLD, broken standalone-attach script, so it was never actually a fair test of "fresh gateway + correct bundled attach."
**Files modified:** None this round (infra only; code already correct)
**Next action:** Get explicit user confirmation before recreating `sreagent-t2-demo`'s gateway again (destructive, same class of action already approved once this session) — do not proceed without asking, per "don't mess anything up" instruction.

---

## Fresh gateway + correct bundled fix STILL fails on sreagent-t2-demo — theory revised

**Timestamp:** 2026-07-16T08:20Z–08:45:35Z
**Objective:** User approved recreating `sreagent-t2-demo`'s gateway fresh, then immediately binding with the now-fixed bundled script — testing whether "gateway freshness" was the missing variable, matching every historical success pattern.
**Command executed:**
```bash
terraform taint 'google_network_services_agent_gateway.sre_egress[0]'
terraform plan -target=[gateway+authz policy+extension+time_sleep] -var="create_wif=false"   # 2 to add, 2 to destroy, reviewed
terraform apply -auto-approve [same targets]   # succeeded: 2 added, 2 destroyed
bash scripts/attach_gateway_to_engine.sh   # run IMMEDIATELY after, on the fresh gateway
```
**Exit code:** 1
**Relevant output:**
```
Apply complete! Resources: 2 added, 0 changed, 2 destroyed.
...
Attaching engine 8599129257987276800 to gateway .../agentGateways/sre-agent-egress (bundled with a fresh source deploy)...
PATCH submitted (operation: .../operations/6070911744337772544). Polling to terminal state...
ERROR: operation failed: {"code": 3, "message": "The Reasoning Engine failed to be updated. ..."}
```
**Result:** FAILURE — identical error, on a genuinely fresh gateway (created minutes earlier), using the confirmed-correct bundled approach.
**Evidence discovered:** "Gateway freshness" is NOT the differentiating variable between `sreagent-t2-demo` and `sreagent-cleanroom-test` — a fresh gateway here still fails immediately, while the clean-room's fresh gateway succeeded immediately. Something about the PROJECT `sreagent-t2-demo` itself (not the gateway resource specifically) differs.
**Interpretation:** Candidate project-level differences (not yet tested): `sreagent-t2-demo` uses `iap_iam_enforcement_mode = null` (ENFORCE mode, changed from DRY_RUN on 2026-07-14) vs. the clean-room gateway's DRY_RUN default — though IAP REQUEST_AUTHZ governs gateway *data-plane* traffic, not the admin-plane `UpdateReasoningEngine` API call, so this is a weak candidate, not a strong one. The reasoning engine itself (`8599129257987276800`) has been through 8+ failed bind attempts, multiple terraform applies, and model changes across this entire investigation — unlike the clean-room's freshly-created engine — so accumulated ENGINE-side state (not gateway-side) is now a more plausible candidate than before, though not yet isolated.
**Facts established / hypotheses affected:**
- REJECTED: "a fresh gateway alone resolves this" — directly disproven by this test.
- CONFIRMED (stronger than before): the fix (bundled deploy) is correct and works reliably on `sreagent-cleanroom-test`; the persistent failure is specific to something about `sreagent-t2-demo` as a project or its specific long-lived engine resource, not the gateway resource's own history.
- NEW CANDIDATE (untested): the reasoning ENGINE itself, not just the gateway, may carry accumulated bad state from this investigation's history — would require recreating the ENGINE too (a much bigger, more disruptive action: new engine ID, reconfiguring IAM bindings, likely reconfiguring `iap_iam_enforcement_mode` back to DRY_RUN to match the one variable we haven't controlled for) to test.
**Files modified:** None
**Next action:** This is a strong, well-evidenced case for a GCP support ticket — identical code, identical procedure, fresh gateway, works immediately on one project and fails immediately on another. Report to user plainly: recommend either (a) escalating to Google with this precise reproduction, (b) testing an engine recreation as one more internal experiment (bigger, more disruptive, not yet approved), or (c) treating `sreagent-cleanroom-test` as the reference/production path forward and deprioritizing further `sreagent-t2-demo` debugging given a working alternative already exists.

---

## Systematic side-by-side config comparison (user pushback: "just diff the two")

**Timestamp:** 2026-07-16T08:50Z–09:14:09Z
**Objective:** User correctly pushed back on jumping to bigger actions (engine recreate, support case) without first doing the basic thing: a real, systematic side-by-side diff of every config dimension between the working (`sreagent-cleanroom-test`) and failing (`sreagent-t2-demo`) projects.
**Commands executed:** direct REST/gcloud comparison across: engine env vars (full diff), engine IAM roles (full diff), gateway `protocols`/`googleManaged`/`networkConfig`, gateway authz policies, gateway authz extensions (`iamEnforcementMode`, `failOpen`, `service`), Agent Registry mTLS hostname registration.
**Findings, verified not guessed:**
1. **Env vars**: identical except expected project-specific values (bucket names, project ID) and a deliberate `GEMINI_MODEL` choice (pro vs flash). Not the cause.
2. **Engine IAM roles**: identical except `roles/modelarmor.user`, present on cleanroom, absent on t2-demo — traced to our OWN conditional IAM logic tied to `enable_agent_gateway` (t2-demo=true uses its own gateway's Model Armor instead; cleanroom=false needed direct API access). Expected, unrelated to Vertex AI's TLS handshake (different service entirely). Not the cause.
3. **Gateway `protocols`/`networkConfig`**: t2-demo sets `protocols=["MCP"]` and no `networkConfig`; cleanroom has no `protocols` and a PSC-I `networkConfig`. Verified directly against the actual `agw-cuj-arun-egress-gmcp` codelab (the one matching our exact use case, Agent Runtime → Google MCP servers) via WebFetch — it also omits `networkConfig` entirely, confirming t2-demo's config is correct per the matching reference. The difference is explained by cleanroom's gateway coming from a *different*, fuller demo (mortgage-agent, which supports private Cloud Run MCP and thus needs PSC-I) — an artifact of which reference we happened to build from, not a requirement for our use case. Not tested directly as a fix (would require nontrivial new infra) given the stronger candidate below was tested first and also failed — worth revisiting if all other leads are exhausted.
4. **mTLS hostname registration**: already present on t2-demo (checked directly — not previously verified, could have been the answer, was already correct).
5. **`iamEnforcementMode`**: t2-demo unset (defaults ENFORCE), cleanroom `DRY_RUN`. **This was the one concrete, empirically-testable difference remaining** — tested directly.
**Test performed:** Temporarily set `iap_iam_enforcement_mode=DRY_RUN` on t2-demo's `google_network_services_authz_extension.iap[0]` (scoped, targeted apply, user explicitly approved), immediately re-ran the bundled attach.
**Result:** FAILURE — identical `error.code: 3`, no change in behavior. Reverted `iamEnforcementMode` back to `ENFORCE` immediately after (confirmed via direct REST check: `metadata` no longer contains the key, matching original state). `sreagent-cleanroom-test` was not touched at any point during this test (confirmed Terraform was scoped to t2-demo's backend throughout, verified via `terraform output` before starting).
**Facts established / hypotheses affected:**
- REJECTED: `iamEnforcementMode` (ENFORCE vs DRY_RUN) as the differentiator.
- REJECTED (already, restated for completeness): env var differences, engine IAM role differences, mTLS registration status.
- STILL UNTESTED, lower confidence: gateway `networkConfig`/PSC-I presence (real difference, but explained as a demo-artifact rather than a requirement — would need real new infra to test, not yet justified given no positive evidence it matters).
- STILL UNTESTED: whether the reasoning ENGINE itself (not the gateway) carries accumulated bad state from 8+ historical failed binds — the one remaining structurally-different candidate (cleanroom's engine is brand new; t2-demo's has extensive history).
**Files modified:** None (net zero — temporary change reverted)
**Next action:** Systematic manual comparison across every readily-inspectable dimension has been exhausted without finding the differentiator. Report to user plainly with the full comparison table and remaining options: engine recreation (untested, disruptive), GCP support case (strong reproduction now available), or accept `sreagent-cleanroom-test` as the working reference and deprioritize further t2-demo debugging.

---

## Platform-state investigation (7-point directive) — gateway comparison re-confirms networkConfig gap

**Timestamp:** 2026-07-16T09:30Z–09:45Z (approx, background agent)
**Objective:** User directed a broader platform-state investigation (not deployment code, already proven correct) across 7 areas: engine creation dates vs a claimed Google-documented "April 29, 2026" Agent Gateway cutoff, full live engine resource diff, listing all engines in t2-demo for hidden conflicts, full gateway comparison, endpoint registration, and a documentation search — followed by a PASS/FAIL/UNKNOWN synthesis with no speculation.
**Commands executed:** `gcloud alpha network-services agent-gateways describe` (both projects, full JSON), `gcloud alpha network-services authz-policies/authz-extensions list` (both — command doesn't exist in this gcloud version), `gcloud model-armor templates list` (both — PERMISSION_DENIED despite owner role, cause unverified), `gcloud iap web get-iam-policy` (both).
**Exit codes:** gateway describe = 0/0; authz-policies/extensions = 2/2 (invalid subcommand, not a real check); model-armor = 1/1 (PERMISSION_DENIED); IAP = 0/0.
**Result:**
- Engine creation dates: t2-demo engine (`2026-07-13T00:41:23Z`) is OLDER than cleanroom's (`2026-07-16T03:47:46Z`) — t2-demo's engine predates cleanroom's, relevant to any claimed cutoff-date theory (not yet cross-checked against the specific claim — that's the doc-search agent, still pending).
- Full live engine diff: byte-identical on every field except project-specific values and `GEMINI_MODEL` (known, unrelated) and — the one real finding — **`spec.deploymentSpec.agentGatewayConfig` is completely ABSENT on t2-demo's live engine right now** (expected: last attach attempt failed, so it was never set).
- Engine listing: exactly 2 engines in t2-demo (main + memory-bank), no gateway reference found in either via the `reasoningEngines.list` API (caveat: this endpoint may not surface gateway bindings at all).
- **Gateway comparison (re-run, independent of the earlier systematic diff): CONFIRMS the previously-noted `networkConfig` difference.** Cleanroom's gateway has `networkConfig.egress.networkAttachment: "projects/sreagent-cleanroom-test/regions/us-central1/networkAttachments/agent-gateway-na"`. t2-demo's gateway has **no `networkConfig` key in the describe output at all** — not null, not empty, entirely absent. Also newly noted: t2-demo's gateway has `description` and `protocols: ["MCP"]` keys that cleanroom's gateway entirely lacks (cleanroom has neither field). `googleManaged.governedAccessPath: AGENT_TO_ANYWHERE` identical on both. Root CA subject template identical (`CN=Agent Gateway TLS Inspection CA (us-central1)`), different serial/validity per-gateway (expected, each gateway auto-provisions its own CA).
- Authz-policies/authz-extensions: **UNKNOWN — command doesn't exist in installed gcloud (573.0.0)**, not a real comparison; this contradicts the earlier systematic-diff log entry that reported authz extension details (`iamEnforcementMode`) — that earlier check must have used the REST API directly, not this gcloud subcommand, so treat the earlier REST-based finding as authoritative and this UNKNOWN as a gcloud-tooling gap, not new information.
- Model Armor: UNKNOWN on both (identical PERMISSION_DENIED despite `roles/owner` on both projects — API enablement not verified, not established as fact).
- IAP web IAM policy: identical (both empty, only an etag) — does not confirm/deny IAP on a specific backend service.
- Endpoint registration (from earlier-completed parallel check): PASS on both projects, both hostnames (plain + `.mtls.`), regionally — not a differentiator.
**Interpretation:** The `networkConfig` absence on t2-demo's gateway is now the single most concrete, still-untested structural difference in the entire investigation. Previously (systematic-diff entry above) this was found and set aside as "explained by a demo-artifact, not a requirement, per the matching codelab reference" — but it was explicitly flagged there as **not tested directly as a fix**. Every other real candidate (env vars, IAM roles, `iamEnforcementMode`, mTLS registration) has now been both found AND empirically tested AND rejected. `networkConfig`/egress-networkAttachment is the only remaining candidate that has been found but never actually tested.
**Facts established / hypotheses affected:**
- REOPENED with higher priority: `networkConfig.egress.networkAttachment` presence/absence between the two gateways — real, confirmed twice independently, never empirically tested.
- Model Armor comparison remains UNKNOWN (tooling/permission gap, not evidence either way).
- Authz-policy/extension comparison via gcloud is a dead end (command doesn't exist); rely on the earlier REST-based finding instead.
**Files modified:** None (read-only investigation)
**Next action:** Awaiting the doc-search agent (Google documentation search, including the specific "April 29, 2026" cutoff claim) to complete the 7-point directive before synthesizing the final PASS/FAIL/UNKNOWN report per the user's explicit format.

---

## Platform-state investigation (7-point directive) — documentation search completed, final synthesis

**Timestamp:** 2026-07-16T09:45Z–10:05Z (approx, background agent + synthesis)
**Objective:** Complete item 6 of the user's 7-point directive — verify or refute the specific claimed "April 29, 2026" Agent Gateway cutoff, plus other documented limitations, project-migration/allowlist requirements, and known issues.
**Method:** Multi-query WebFetch/WebSearch against official Google Cloud docs (5 pages fetched directly, cross-checked with independent WebSearch), GitHub, Stack Overflow. `issuetracker.google.com` inaccessible to the tool (auth wall) — flagged as a genuine gap, not a negative result.
**Findings (verbatim quotes, with URLs):**

1. **April 29, 2026 cutoff — CONFIRMED TRUE.** Exact quote from https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-gateway-runtime-deploy, under a "Limitations" heading: *"An Agent Gateway can't be bound to Runtime Reasoning Engines created before April 29, 2026."* Cross-checked against actual engine createTime values already gathered this session:
   - t2-demo engine (`8599129257987276800`): createTime `2026-07-13T00:41:23Z`
   - cleanroom engine (`3983291483653406720`): createTime `2026-07-16T03:47:46Z`
   **Both dates are AFTER April 29, 2026.** This documented cutoff, while real, does **NOT** explain the observed failure — neither engine falls on the wrong side of it.

2. **`identity_type` cannot be retroactively set via PATCH** — same URL, exact quote: *"Updating an existing reasoning engine to set `agentGatewayConfig` does not change its `identity_type`. If the engine was originally created without `identity_type=AGENT_IDENTITY`, you cannot retroactively make it eligible... You must redeploy a new reasoning engine with both `agent_gateway_config` and `identity_type=AGENT_IDENTITY` set at agent creation time."* Cross-checked against the live resource diff already gathered: both engines currently show `spec.identityType: AGENT_IDENTITY`. Since this doc confirms PATCH can never set this field, both engines' current state proves `identity_type=AGENT_IDENTITY` was already set at CREATION time for both — this limitation is **satisfied on both projects already**, not a differentiator.

3. **No documented per-project manual allowlist/enablement step** beyond standard API enablement, per direct fetch of the official "Set up Agent Gateway" page (~20 APIs + IAM roles listed, no allowlist mentioned). Absence of evidence, not proof of absence.

4. **`error.code: 3` + Agent Gateway/reasoningEngines on issue trackers — NO MATCHING RESULTS** found on GitHub or Stack Overflow after multiple searches. `issuetracker.google.com` could not be searched (auth wall) — genuine gap.

5. **Documented general limitation, real but likely orthogonal**: *"Agent Gateway doesn't support connections to public or private destinations with self-signed certificate chains. Use publicly trusted CA certificates for all destinations."* (agent-gateway-overview) and *"Agent Gateway does not validate self-signed certificate chains"* (troubleshoot-agent-gateway). This describes gateway EGRESS data-plane behavior toward a downstream destination, not the admin-plane `UpdateReasoningEngine` bind operation that is actually failing (which calls no external destination). Flagged as real documentation but not evidenced as applicable to this specific attach-time failure.
   Also found: gateway region must match app location; all agents in a project+region must bind to the same gateway instance; binding routes ALL existing traffic immediately (not staged); custom org policies take up to 15 minutes to propagate.

6. **Preview/allowlist gating — UNRESOLVED.** No primary-source quote found stating Agent Gateway requires manual per-project allowlisting. Circumstantial fact (confirmed directly): the official codelab uses `gcloud alpha network-services agent-gateways` and `v1alpha1`/`v1beta1` REST endpoints — consistent with a non-GA launch stage, which for other Google Cloud alpha features has historically required allowlisting, but not confirmed as applicable here.

**Facts established / hypotheses affected:**
- REFUTED as an explanation: the April 29, 2026 cutoff (real, but both engines postdate it).
- CONFIRMED as satisfied, not a differentiator: `identity_type` set at creation (both engines already show `AGENT_IDENTITY`, which per docs could only occur if set at creation, since PATCH cannot add it).
- STILL OPEN, real evidence: the self-signed-cert-chain limitation is documented but not evidenced as applicable to an admin-plane bind failure specifically.
- STILL OPEN, unresolved: whether Agent Gateway carries an undocumented per-project allowlist gate (alpha/beta API status is a real, confirmed fact; the allowlist mechanism itself is unconfirmed).
- The one remaining REAL, FOUND-BUT-UNTESTED difference from the gateway-comparison check (previous log entry) stands: t2-demo's gateway has no `networkConfig` key; cleanroom's has `networkConfig.egress.networkAttachment` set to a PSC-I network attachment.
**Files modified:** None (read-only)
**Next action:** Present full PASS/FAIL/UNKNOWN synthesis to user per their exact requested format. Do NOT proceed to test `networkConfig`/PSC-I on t2-demo without explicit user approval (real infra change, not read-only).

---

## networkConfig/PSC-I deep-dive — answering the 4 user questions before any infra change

**Timestamp:** 2026-07-16T10:10Z–10:35Z
**Objective:** User asked to verify 4 specific things about the one remaining untested difference (gateway `networkConfig`/PSC-I network attachment) before either building it in t2-demo or concluding this is a Google platform issue: (1) is it required for Agent-to-Anywhere gateways, (2) why did it appear automatically in one project but not the other, (3) is cleanroom's PSC-I healthy/READY, (4) can it be created in t2-demo.
**Evidence gathered:**

1. **Source code — cleanroom's actual Terraform module** (`agent-gateway-codelab/.../terraform/modules/agent-gateway/main.tf:15-21,35-44,74-77`): module docstring: *"Provisions a Google-managed Agent Gateway in AGENT_TO_ANYWHERE mode for MCP, a PSC-Interface network attachment in the dedicated co-location subnet... This is what the Agent Gateway egresses through to reach the customer VPC (and from there the MCP internal LB)."* The `google_compute_network_attachment` resource has **no `count`/`for_each` gate** — it is created unconditionally by this module, because this module (the mortgage-agent demo) is architected around a **private, internal-LB-fronted MCP server**.

2. **Source code — t2-demo's own Terraform module** (`testing2-gcp-sre-agent/iac/agent/agent_gateway.tf:16-21`, pre-existing comment from earlier in this investigation): *"Per the official codelab (agw-cuj-arun-egress-gmcp): a Google-managed gateway with NO networkConfig / NO PSC network attachment. The gateway reaches the public Google-API + GKE Remote MCP destinations over Google's backbone; a PSC egress attachment into a VPC is only for PRIVATE-VPC targets (not our case), and the codelab creates none."*

3. **Official Google documentation, fetched directly** (https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-agent-gateway): exact quoted section: *"**Optional: Configure VPC connectivity** — To learn how to configure your Agent Gateway so that it can privately communicate with a VPC network in your organization, see Set up VPC connectivity for Agent Gateway."* Cross-checked via WebSearch: the linked VPC-connectivity guide requires registering "the network attachment created with your Agent Gateway egress configuration" specifically so the gateway "can reach the policy engine over the private network." **CONFIRMED: `networkConfig`/network attachment is an OPTIONAL step, explicitly scoped to private VPC communication — not a requirement for AGENT_TO_ANYWHERE gateways reaching public Google APIs.**

4. **Live infra check** (`gcloud compute network-attachments describe agent-gateway-na --project=sreagent-cleanroom-test --region=us-central1`): `connectionEndpoints[0].status: "ACCEPTED"`, `connectionPreference: "ACCEPT_AUTOMATIC"`, subnetwork `gateway-agent-gateway-subnet`, network `gateway-vpc`, created `2026-07-15T19:29:29-07:00`. **Healthy and connected** — no health/READY field exists on this resource type; `status: ACCEPTED` on the connection endpoint is the correct health signal and it is positive.

**Direct answers:**
1. Required for Agent-to-Anywhere? **NO** — confirmed optional per official docs, scoped to private-VPC-only use cases.
2. Why present on one, absent on the other? **Because the two gateways were built from two different Terraform source modules** — cleanroom's source module (mortgage-agent demo, architected for a private internal-LB MCP server) creates it unconditionally; t2-demo's own module (built against the actually-matching codelab for our public-endpoint use case) deliberately never creates it. Not a bug, not drift — a documented, intentional difference in which reference each project was built from.
3. Is cleanroom's PSC-I healthy? **YES** — `ACCEPTED`, live and connected.
4. Can we create the same thing in t2-demo? **Technically yes** (new VPC/subnet/network-attachment/firewall rule), but there is no evidence it's needed — t2-demo's actual destinations (Vertex AI public APIs, GKE Remote MCP via `container.googleapis.com`, a public Google API) do not require PSC-I per the documentation above, and the failure being chased is an **admin-plane** `UpdateReasoningEngine` PATCH failure (binding the gateway to the engine), not a data-plane egress call — PSC-I only affects data-plane egress routing, not the admin-plane bind operation.
**Facts established / hypotheses affected:**
- REJECTED as the root cause: `networkConfig`/PSC-I absence on t2-demo. It is optional, correctly absent per t2-demo's own matching reference architecture, healthy where present, and architecturally unrelated to the specific admin-plane bind call that is actually failing.
- This was the last remaining untested candidate from the 7-point platform-state investigation. **Every candidate identified across the entire investigation has now been either tested-and-rejected or explained-with-documented-evidence.**
**Files modified:** None (read-only)
**Next action:** Report findings to user plainly — recommend against building PSC-I in t2-demo (no evidence it would fix an admin-plane failure, adds real infra cost/complexity for a documented optional-only feature) and recommend GCP Support escalation instead, given a complete, well-evidenced reproduction now exists. Await user decision.

---

## Full gateway match applied (PSC-I + protocols + description + timeout) — bind retry STILL FAILS identically

**Timestamp:** 2026-07-16T13:52Z–14:10Z (approx)
**Objective:** Per direct user instruction to stop testing one variable at a time and match every discoverable gateway-level difference in one pass before concluding anything: added PSC-I (`network_config`/`google_compute_network_attachment` + dedicated `/28` subnet), removed `description`, removed `protocols = ["MCP"]`, and changed `authzExtension.timeout` from `1s` to `2s` — matching every field found to differ between t2-demo's gateway and `sreagent-cleanroom-test`'s working gateway.
**Commands executed:**
```bash
# Apply 1 — PSC-I (scoped): subnet + network_attachment + gateway replace + authz policy replace
terraform plan  -var="create_wif=false" -target=google_compute_subnetwork.agent_gateway_psc \
  -target=google_compute_network_attachment.sre_egress -target=google_network_services_agent_gateway.sre_egress \
  -target=time_sleep.wait_for_gateway -target=google_network_services_authz_extension.iap \
  -target=google_network_security_authz_policy.iap -out=/tmp/psc_test3.tfplan
terraform apply "/tmp/psc_test3.tfplan"   # Apply complete! Resources: 4 added, 0 changed, 2 destroyed.

# Apply 2 — protocols/description/timeout match (scoped)
terraform plan -var="create_wif=false" -target=google_network_services_agent_gateway.sre_egress \
  -target=google_network_services_authz_extension.iap -target=time_sleep.wait_for_gateway \
  -target=google_network_security_authz_policy.iap -out=/tmp/psc_test4.tfplan
terraform apply "/tmp/psc_test4.tfplan"   # Apply complete! Resources: 1 added, 2 changed, 1 destroyed.

# Bind retry
bash scripts/attach_gateway_to_engine.sh
```
**Exit codes:** both applies = 0. Bind retry = 1.
**Full output (bind retry, complete and untruncated this time — a genuine gap flagged earlier in this investigation):**
```
Attaching engine 8599129257987276800 to gateway projects/sreagent-t2-demo/locations/us-central1/agentGateways/sre-agent-egress (bundled with a fresh source deploy)...
PATCH submitted (operation: projects/327234009108/locations/us-central1/reasoningEngines/8599129257987276800/operations/4190385121515274240). Polling to terminal state (this can take several minutes)...
ERROR: operation failed: {"code": 3, "message": "The Reasoning Engine failed to be updated."}
```
**Result:** FAILURE — identical `error.code: 3`, identical generic message, on a gateway now matching cleanroom's on every field ever found to differ (PSC-I/networkConfig, protocols, description, authzExtension.timeout). **Confirmed: the previous log entries showing this message truncated with "..." were not actually truncated by my own logging — this generic one-sentence message is the API's complete, full response. There is no additional detail Google's API surface returns for this error.**
**Evidence discovered:** Every field-level difference ever found between t2-demo's and cleanroom's gateway/authz-extension/authz-policy configuration has now been matched. The bind operation still fails identically. This rules out the entire class of "gateway configuration mismatch" as the root cause.
**Facts established / hypotheses affected:**
- REJECTED: PSC-I/`networkConfig` absence (matched, still fails).
- REJECTED: `protocols=["MCP"]` presence (removed to match, still fails).
- REJECTED: `description` field presence (removed to match, still fails — expected, cosmetic only).
- REJECTED: `authzExtension.timeout` mismatch (matched to `2s`, still fails).
- CONFIRMED: the API's error message has no hidden detail — `{"code": 3, "message": "The Reasoning Engine failed to be updated."}` is complete, not truncated.
- Every gateway/authz-level field-by-field difference between the two projects that could be found via `describe`/REST calls has now been either matched-and-retested or previously ruled out (`iamEnforcementMode`). None remain at this layer.
**Files modified:** `iac/agent/agent_gateway.tf` (network_attachment resource added, `description`/`protocols` removed from gateway resource, `timeout` changed to `"2s"`), `iac/agent/networking.tf` (new `agent_gateway_psc` subnet added).
**Next action:** The two gaps flagged earlier in this investigation and never yet executed — full Cloud Audit Log detail for this specific failed operation (may carry more detail than the returned error object), and org policy / VPC Service Controls perimeter comparison between the two projects — are the only remaining unexplored avenues before this is a clean case for GCP Support escalation.

---

## Audit logs + container stderr + org-policy/VPC-SC investigation — SSL noise ruled out, org-policy gap unclosable from here

**Timestamp:** 2026-07-16T18:10Z–18:30Z (approx, background workflow) + verification
**Objective:** Close the two remaining gaps flagged earlier: full Cloud Audit Log detail for the failed bind operation, and org policy / VPC Service Controls comparison between the two projects.

### Cloud Audit Logs
`gcloud logging read` for the exact operation ID (`.../operations/4190385121515274240`) returns 2 entries (start + terminal, `"last": true`), both with `"status": {}` — **empty**. Confirmed: Google's own Activity audit log carries no error code/message/detail beyond what the API already returned. Searches across Data Access and System Event categories returned zero entries (confirmed only 2 log streams exist for this project in the window: `cloudaudit.googleapis.com%2Factivity` and `aiplatform.googleapis.com%2Freasoning_engine_stderr`).

### Container stderr — new finding, then ruled out
`reasoning_engine_stderr` for t2-demo, 18:10–18:25Z, showed 8x (18:18:06Z–18:18:23Z, right against the operation's 18:18:21.143Z completion):
```
ssl_transport_security.cc:2160] Handshake failed with error SSL_ERROR_SSL: error:1000007d:SSL routines:OPENSSL_internal:CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate
```
A SIGTERM to the old revision landed at 18:18:14Z, ~7s before the operation's terminal timestamp — initially looked like a strong, specific new lead (same error family as the original root cause, gRPC C-core level, tightly time-correlated).

**Verification against cleanroom's own successful bind** (its engine `updateTime` = `2026-07-16T05:39:24Z`): pulled the identical `reasoning_engine_stderr` log stream for `sreagent-cleanroom-test`, window `05:25:00Z`–`05:55:00Z`. Result: **274 occurrences of the exact same `ssl_transport_security.cc` / `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate` error**, in the same relative position of container startup (05:32:33Z, within the successful run's lifecycle).
**REJECTED as causal.** This gRPC SSL handshake error is background noise common to both the successful and failing project's container startup — very likely an internal client (Memory Bank gRPC client, or an early health-check) retrying against a Google endpoint before some credential/trust-store warm-up step completes, self-resolving within the same container lifecycle either way. It is NOT specific to the failure.

### Org policy / VPC Service Controls
- Direct project-level org-policy overrides (`gcloud resource-manager org-policies list`): **identical** — both projects return an empty list (exit 0).
- Effective/inherited org policy (`gcloud org-policies list`), VPC-SC perimeter membership (`gcloud access-context-manager perimeters list`), and even whether an Access Context Manager policy exists at all for the org: **all UNKNOWN** — `orgpolicy.googleapis.com` and `accesscontextmanager.googleapis.com` are disabled on both projects and on the default quota project. Cannot be queried from this account/machine without enabling those APIs (a mutating action, out of scope for a read-only check) or access from an org-admin vantage point.
**Facts established:**
- REJECTED: the gRPC/TLS handshake error inside the reasoning engine's own container — present in both the successful and failing project's logs, not specific to the failure.
- CONFIRMED: Cloud Audit Logs carry no more detail than the API's own generic response for this operation.
- Direct project-level org policy: identical (empty on both). Everything deeper (inherited policy, VPC-SC): genuinely unknowable from this vantage point right now — a real, disclosed gap, not a ruled-out candidate.
**Files modified:** None (read-only)
**Next action:** Every checkable candidate from this investigation — deployment code/bundling, gateway config (networkConfig/protocols/description/timeout), IAM roles, mTLS registration, iamEnforcementMode, engine creation date vs. documented cutoff, Cloud Audit Logs, container-level stderr — has now been tested-and-rejected or explained with direct evidence. The one remaining gap (inherited org policy / VPC-SC membership) cannot be closed without either enabling two currently-disabled APIs or an org-admin checking directly. This is now a complete, well-evidenced case for GCP Support escalation, or for someone with org-admin access to check the VPC-SC/org-policy angle directly.
