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
BLOCKED. 8/8 consecutive gateway-attach attempts have failed since
2026-07-13T16:24:13Z, including a full gateway recreation (approved,
completed cleanly) and both an immediate and a 10-minutes-later bind
attempt against the fresh gateway. Every constructed hypothesis has been
tested and rejected. No further diagnostic signal available from Google's
API, Cloud Logging (reasoning-engine side AND gateway side both checked,
zero relevant entries), or docs. This is a genuine stop point, not
something more retrying will fix — see "what's needed to unblock" below.

**The rest of the stack is fine and unaffected:** code is correct (PR #20
fix confirmed present and deployed), the engine itself works, the
gateway-free path (`enable_agent_gateway=false`) is a separately-documented,
tested, working mode not touched by this bug.

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
Retry after ~10-minute settle time post-recreation (operation `4639222054489423872`, 2026-07-15T10:38:51Z–10:43:28Z) — FAILED, same `error.code: 3`. This was the last well-evidenced hypothesis (settle time); also rejected. Checked gateway-side Cloud Logging for the failure window — zero entries, no new signal.

# Next planned step
NONE PENDING FROM MY SIDE — awaiting user decision on how to proceed. This
needs a human call because every remaining option is either (a) something
only the user can do (open a GCP support case), or (b) a decision with real
tradeoffs I shouldn't make unilaterally (fall back to `enable_agent_gateway=false`
to unblock production now, at the cost of no egress governance until the
platform bug is understood/fixed), or (c) more blind retrying with no new
evidence, which the mandatory troubleshooting rules explicitly say not to do.

**All 8 attempts, for reference:**
1. PR #21 CI auto-attach (2026-07-14T05:23Z) — FAIL
2. Manual retry, documented mask (2026-07-15T08:57Z) — FAIL
3. Manual retry #2 (2026-07-15T09:31Z) — FAIL
4. `updateMask=env` experiment, matching codelab (09:52Z) — FAIL
5. Bundled env+gatewayConfig in one call (09:56Z) — FAIL
6. Explicit unbind (10:09Z) — FAIL
7. Fresh gateway, immediate bind (10:28Z) — FAIL
8. Fresh gateway, bind after ~10min settle (10:39Z) — FAIL

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
