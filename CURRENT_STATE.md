# Current problem
`make smoke` against the deployed agent (Reasoning Engine `8599129257987276800`,
project `sreagent-t2-demo`) fails with the pre-PR#20 mTLS bug:
`SSLError(SysCallError(-1, 'Unexpected EOF'))` against
`us-central1-aiplatform.mtls.googleapis.com`, even though PR #20's fix
(pin plain Vertex endpoint) is present and unchanged in `agent/gemini_client.py`
on `main`. Root cause traced to: the Agent Gateway is not attached to the
engine (`agentGatewayConfig: null`), so calls bypass the path the fix was
validated against.

# Current status
ROOT CAUSE ISOLATED — DECISIVE TEST PASSED. Deployed a clean-room test:
fresh GCP project (`sreagent-cleanroom-test`), gateway created via the
codelab's own proven Terraform (`AshminPy/agent-gateway-codelab`), our own
SRE agent engine deployed via our own `iac/agent` (gateway disabled), bound
to the codelab's gateway using our own PATCH mechanism. **The bind
succeeded on the first attempt** (2026-07-16T03:57:41Z) — first success
anywhere since 2026-07-13T16:24:13Z.

**Conclusion: our code, request format, and bind mechanism are all
correct.** The persistent failure is specific to `sreagent-t2-demo`'s
particular project/gateway state (likely stale server-side state from
repeated gateway destroy/recreate churn across this investigation), not a
systemic bug. `sreagent-t2-demo` itself may still need its own remediation
(support case, or another fresh recreate now that we know a truly clean
state works) — but the code/approach is proven sound.

**Next:** finish end-to-end proof in the clean-room project (register GKE
Remote MCP endpoint, run smoke test against the existing `sreagent-demo`
GKE cluster) to confirm the full agent → gateway → MCP → RCA path works,
then decide what to do about `sreagent-t2-demo` itself.

# Verified facts
- `main` HEAD (`bc03b88`) contains PR #20's mTLS fix — confirmed by reading `agent/gemini_client.py:47-72`.
- Deployed engine code = repo HEAD (CI headSha match + engine `updateTime` inside CI run window). Not a stale-deploy issue.
- Live engine `spec.deploymentSpec.agentGatewayConfig` = `null` — gateway not attached.
- Gateway resource exists, `enable_agent_gateway` defaults `true` — not disabled/destroyed.
- `scripts/attach_gateway_to_engine.sh` submits an async PATCH and exits 0 without checking the result — a real bug; CI shows green even when the attach fails.
- 26 historical `UpdateReasoningEngine`+`agentGatewayConfig` attempts in this project (2026-07-13 to 2026-07-15): **20 succeeded, 6 failed** (all failures: generic `error.code: 3`, no further detail from Google in Cloud Logging, Audit Logs, or docs).
- Our PATCH request format matches Google's documented format exactly (confirmed via official docs).
- Engine already has `identityType: AGENT_IDENTITY` — rules out the one documented gotcha (can't retroactively add AGENT_IDENTITY via patch).
- Cross-project reference (`sreagent-codelab` engine `sre-agent-langgraph-crosstest`) has a working, populated `agentGatewayConfig` — proves the binding mechanism works in principle.
- Codelab's full attach history: 2/2 attempts succeeded (100%), vs testing2's 20/26 (77%) — but sample size is too small (2) to conclude codelab is inherently more reliable.
- Real IAM-scoping difference confirmed: codelab grants engine-facing roles via a project-wide `principalSet` (all engines); testing2 grants the same roles via a `principal` scoped to one specific engine ID (testing2's deliberate least-privilege design). Not a convincing root cause though — auth gaps are deterministic, not 77%-of-the-time intermittent.

# Current hypothesis
Two live candidates, both testable cheaply:
(a) Our `attach_gateway_to_engine.sh` sends a NARROW single-field PATCH
(`updateMask=spec.deploymentSpec.agentGatewayConfig`). Google's own reference
implementation (`deploy_agent.py` in GoogleCloudPlatform/cloud-networking-solutions)
never does this — it always bundles gateway config into ONE combined update
alongside source/env/identity. The one codelab call we found that succeeded
also used a non-`agentGatewayConfig` mask (`env`). Narrow single-field PATCHes
to this field may just be less robust on this beta API.
(b) Google's own known-issues doc for this platform lists a private-preview
limit: only one ReasoningEngine↔AgentGateway bonding allowed per project at a
time, with symptom language close to ours. Checked: no second engine is
currently bonded (memory-bank engine's `agentGatewayConfig` is null) — not
demonstrably active right now, but stale backend state from the gateway's
destroy/recreate history (PR #19) isn't ruled out.
No longer treating this as pure random flakiness — there's now a documented,
named platform limitation in the same problem family.

# Rejected hypotheses
- Local/repo code mismatch (deployed code confirmed = repo HEAD).
- Deployed code is stale (CI headSha + updateTime correlation disproves this).
- Terraform-plan `source_archive` diff = real drift (it's a non-reproducible-tar artifact, `package_agent.sh` warns about this without GNU tar).
- Gateway attach is a hard permanent block (20/26 historical attempts succeeded).
- codelab's `updateMask=env` (vs our `agentGatewayConfig`) is the real working method — testing2 succeeds 20/26 times using the documented `agentGatewayConfig` mask, so mask value isn't the differentiator.
- Malformed PATCH request (confirmed matches Google's documented format exactly).

# Remaining unknowns
- Exact platform-side cause of `error.code: 3` — Google gives no detail anywhere.
- Whether recent failure rate is worse than the 23% historical baseline (last 3 attempts in a row failed) or just variance.
- Whether `GEMINI_MODEL=gemini-2.5-flash` (current live value, CI default) still hits quota exhaustion once gateway is restored — not yet re-tested.

# Last completed step
Clean-room bind succeeded (operation `3442599566883422208`, resolved 2026-07-16T03:57:41Z) — engine `3983291483653406720` in `sreagent-cleanroom-test` successfully bound to gateway `agent-gateway` on the first attempt.

# Next planned step

## Documentation-verified understanding (2026-07-16T04:32Z) — not guessing anymore

Reviewed the user-supplied architecture deck (`Governing_Agentic_Egress.pptx`)
against the actual official docs (`set-up-agent-gateway`, `configure-model-armor`,
`agent-identity-overview`, `scale/runtime/agent-identity`, `delegate-authorization`),
following the real hyperlinks rather than assuming. Direct quotes recorded in
`TROUBLESHOOTING_LOG.md`. Findings:

1. **Model Armor is officially optional** — the docs say "(Optional)" explicitly,
   twice. The deck presents it as a standard paired gate for best-practice
   reasons, not a technical requirement. Our own clean-room test already
   proved this empirically: gateway created and engine bound successfully
   with Model Armor off.
2. **mTLS-to-Google-APIs is Agent Identity's documented DEFAULT behavior**,
   not a bug: *"By default, agent identities use mutual TLS (mTLS) with X.509
   certificates when communicating directly with Google Cloud APIs."* Auto-
   provisioned X.509 certs, enforced via a Google-managed CAA policy.
3. **The only documented opt-out is `GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_
   FOR_GCP_SERVICES=False`** (disables the CAA policy, "strongly discouraged").
   We already have this set on our engine. But the doc frames it as enabling
   *token sharing* for SDKs, not endpoint-hostname selection — these look
   like different mechanisms; the docs don't clarify the relationship.
4. **There is no documented way to force the plain/non-mTLS endpoint.**
   PR #20's `base_url` override (`agent/gemini_client.py`) is an
   **undocumented workaround**, not a Google-sanctioned configuration.
5. **No documented link between Model Armor and the mTLS trust chain** —
   checked specifically in the doc covering how Model Armor wires into
   gateway authz. Nothing there.

**Reframed understanding:** the deck's own diagram shows mTLS is meant to
terminate AT the Agent Gateway (first-party access), with DPoP taking over
beyond it — not at a raw Vertex AI endpoint. PR #20's fix likely "worked"
historically because it happened to route through a properly-configured
gateway terminating that handshake, not because it genuinely avoided mTLS.
It fails when there's no gateway properly positioned/trusted for that call
path — which fits both `sreagent-t2-demo`'s original bug AND the clean-room
cert-verify failure once Model Armor's block was removed and we finally
reached that code path for real.

**Genuine documentation gap** (not resolvable by more reading — worth
asking Google directly): exact relationship between the token-sharing env
var and mTLS endpoint selection; whether there's any supported way to route
Agent Identity's direct Vertex AI calls through gateway mTLS termination.

Still true and unaffected: the BIND mechanism itself (the original, narrow
investigation subject) is proven correct in a clean project — that finding
stands on its own regardless of this deeper mTLS question.

Awaiting user direction: test with Model Armor re-enabled on the clean-room
gateway (a way to keep probing internally), or treat the documentation gap
as the right question to bring directly to Google's team now that we have a
precise, sourced framing instead of a guess.

**All attempts against `sreagent-t2-demo` (for reference — all failed):**
1. PR #21 CI auto-attach (2026-07-14T05:23Z) — FAIL
2. Manual retry, documented mask (2026-07-15T08:57Z) — FAIL
3. Manual retry #2 (2026-07-15T09:31Z) — FAIL
4. `updateMask=env` experiment, matching codelab (09:52Z) — FAIL
5. Bundled env+gatewayConfig in one call (09:56Z) — FAIL
6. Explicit unbind (10:09Z) — FAIL
7. Fresh gateway, immediate bind (10:28Z) — FAIL
8. Fresh gateway, bind after ~10min settle (10:39Z) — FAIL

**Clean-room attempt (`sreagent-cleanroom-test`, different project entirely):**
9. Fresh project, fresh gateway (codelab Terraform), fresh engine (our Terraform), bind (2026-07-16T03:53Z) — **SUCCESS**

# Important commands
```bash
export PATH="/Users/ashmin/Downloads/google-cloud-sdk/bin:$PATH"   # gcloud not on default PATH in this shell
cd ~/projects/testing2-gcp-sre-agent
bash scripts/attach_gateway_to_engine.sh                            # submits async PATCH, does NOT verify result
make smoke                                                          # runs the real smoke test against the deployed engine
# poll an operation's terminal status manually (the script above won't):
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" "https://us-central1-aiplatform.googleapis.com/v1beta1/<operation-name>"
```

# Files currently being investigated
- `agent/gemini_client.py` (has the fix, confirmed correct — not the current suspect)
- `scripts/attach_gateway_to_engine.sh` (has the silent-failure bug — needs a real fix once root cause is closed out)
- `iac/agent/agent_gateway.tf` (gateway definition, not yet suspected of an issue)
- Live GCP resources: reasoning engine `8599129257987276800`, gateway `sre-agent-egress`, both in `sreagent-t2-demo`
