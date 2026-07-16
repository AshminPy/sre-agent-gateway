# Current problem
RESOLVED. `make smoke` / `invoke_agent.py` was failing at the mTLS handshake
step (`SSLError`/`certificate verify failed` calling
`us-central1-aiplatform.mtls.googleapis.com`). Started as a gateway-attach
failure on `sreagent-t2-demo`; root-caused via a clean-room test.

# Current status
✅ FIXED AND VERIFIED END-TO-END on `sreagent-cleanroom-test`
(2026-07-16T05:40:51Z). Full agent → gateway → GKE Remote MCP (cross-project)
→ Gemini → RCA pipeline working, correct diagnosis, confidence 0.9.

## Confirmed root cause
The Agent Gateway does TLS inspection (Secure Web Proxy) with a dynamically
provisioned, self-signed root CA — matches Google's own documented
known-issue #17. The reasoning engine's trust store only gets that CA baked
in when a **source-code deployment request already includes the gateway
association in the same atomic call**. Our process (Terraform for source,
a separate later PATCH for `agentGatewayConfig`) never satisfied that —
every deploy in this entire investigation pushed source while the engine
was not yet gateway-bound, so there was nothing for the pipeline to bake
certs for.

`GOOGLE_API_USE_MTLS_ENDPOINT` / `GOOGLE_API_USE_CLIENT_CERTIFICATE` are
confirmed dead code for this SDK (verified against installed `google-genai`
1.47.0 source directly — the Vertex endpoint URL is hardcoded, no mTLS
branch, these env vars are never read). Not relevant to the fix.

## The fix
One bundled PATCH: `spec.sourceCodeSpec` (the actual agent code) +
`spec.deploymentSpec.agentGatewayConfig`, submitted together in a single
`UpdateReasoningEngine` call — matching Google's own reference
`deploy_agent.py` pattern, which always bundles these. Sequential calls
(source deploy, then attach — however close together) do not trigger the
same cert-provisioning pipeline.

## Files changed (all committed, live-verified)
- `agent/gemini_client.py` — removed the undocumented `base_url` override
  (was PR #20's original workaround); added `[mtls-diag]` startup
  diagnostics (Python version, resolved base_url, both env vars,
  `certifi.where()` — no credentials logged).
- `agent/__init__.py` — added defensive `urllib3.contrib.pyopenssl.extract_from_urllib3()`
  (matches every Google reference agent's pattern).
- `agent/requirements.txt` — `google-cloud-aiplatform>=1.52.0` → `>=1.160.0`,
  `google-auth>=2.29.0` → `>=2.56.0` (real, recent, relevant fixes — good
  hygiene, but the bundling was the operative fix, not these alone).

## What's still open
1. ✅ DONE — `scripts/attach_gateway_to_engine.sh` rewritten to bundle source+gateway, poll to real completion, fail loudly. Merged to `main` (PR #24).
2. ⚠️ **`sreagent-t2-demo` still NOT fixed.** Applied the fix (fresh code
   deploy + fresh gateway recreate + bundled attach, exactly matching the
   clean-room's successful sequence) — still fails identically (`error.code: 3`).
   Ruled out "gateway freshness" as the differentiator. Then did a full,
   systematic side-by-side diff of every config dimension against the
   working clean-room project (env vars, engine IAM roles, gateway
   protocols/networkConfig, authz policies/extensions, mTLS registration)
   — found and tested `iamEnforcementMode` (ENFORCE vs DRY_RUN) directly.
   **Ruled out** — reverted cleanly, clean-room untouched throughout.
   **2026-07-16 platform-state investigation (in progress, per user's
   7-point directive):** re-confirmed independently that t2-demo's gateway
   has **NO `networkConfig` key at all** (not null — entirely absent),
   while cleanroom's gateway has `networkConfig.egress.networkAttachment`
   set to a real PSC-I network attachment. This is the ONE remaining
   candidate that has been found twice but **never empirically tested as
   a fix** (everything else found has now been tested and rejected).
   Also confirmed: engine creation dates (t2-demo engine is OLDER than
   cleanroom's, relevant to a claimed Google doc cutoff date — not yet
   cross-checked, doc-search agent pending), full live engine diff (only
   `agentGatewayConfig` absence + known `GEMINI_MODEL` choice differ),
   engine listing (only 2 engines in t2-demo, no visible gateway conflict,
   caveat: API may not surface bindings), endpoint registration (PASS,
   identical both projects). Model Armor / authz-policy gcloud checks
   returned UNKNOWN (permission/tooling gaps, not evidence).
   **Doc-search complete (2026-07-16).** April 29, 2026 cutoff CONFIRMED
   real in docs, but does NOT explain the failure (both engines' createTime
   postdate it). `identity_type`-at-creation PATCH limitation CONFIRMED
   real but already satisfied on both projects (both show AGENT_IDENTITY
   live, which per docs could only happen if set at creation). Self-signed
   cert-chain limitation is real but describes gateway egress data-plane
   behavior, not the admin-plane bind call that's actually failing —
   not evidenced as applicable. Per-project allowlist gating UNRESOLVED
   (alpha/beta API status confirmed as fact; allowlist mechanism itself
   unconfirmed by any primary source).
   **`networkConfig`/PSC-I deep-dive complete (2026-07-16) — RULED OUT.**
   Confirmed via official docs (exact quote): PSC-I/`networkConfig` is
   "**Optional: Configure VPC connectivity**", scoped explicitly to private
   VPC communication — NOT required for AGENT_TO_ANYWHERE gateways reaching
   public Google APIs (t2-demo's actual use case). Confirmed via source:
   present on cleanroom only because it was built from a DIFFERENT Terraform
   module (mortgage-agent demo, architected for a private internal-LB MCP
   server) that creates it unconditionally; t2-demo's own module (matching
   the actually-correct codelab reference) deliberately never creates it —
   documented intentional difference, not drift. Confirmed cleanroom's
   PSC-I is healthy (`ACCEPTED`). Also architecturally irrelevant regardless:
   PSC-I affects data-plane egress routing, not the admin-plane
   `UpdateReasoningEngine` bind call that is actually failing.
   **Every candidate from the full investigation is now tested-and-rejected
   or explained-with-documented-evidence. None remain.**
   **Recommendation: do NOT build PSC-I in t2-demo** (no evidence it fixes
   an admin-plane failure; real added infra for a documented optional-only
   feature). Recommend GCP Support escalation with the full reproduction
   instead. **User decision (2026-07-16): test it empirically anyway** —
   cheap and reversible, and rules the candidate out definitively either way.

## Proposed change (2026-07-16)
**Hypothesis being addressed:** low-confidence — that the mere presence of
`network_config`/PSC-I on the gateway resource (independent of whether any
traffic actually routes through it) affects whether the admin-plane
`UpdateReasoningEngine` bind PATCH succeeds. Evidence so far argues against
this (PSC-I is documented optional/VPC-only, and t2-demo's real traffic is
public-endpoint), but it is the last untested candidate and the test is cheap.
**Evidence supporting the change:** see TROUBLESHOOTING_LOG.md "networkConfig/PSC-I
deep-dive" entry — real, confirmed structural difference between the two
gateways, never empirically tested as a fix until now.
**Files expected to change:**
- `iac/agent/networking.tf` — add a dedicated `/28` subnet for the PSC-I
  network attachment (mirrors the codelab's own networking module).
- `iac/agent/agent_gateway.tf` — add `google_compute_network_attachment` and
  wire a `network_config { egress { ... } }` block into the existing
  `google_network_services_agent_gateway.sre_egress` resource.
**Expected result:** re-run `attach_gateway_to_engine.sh` after apply; either
the bind succeeds (networkConfig was in fact required, contradicting the
documented "optional" claim) or fails identically (confirms it's unrelated,
closing out the last open candidate from the full investigation).
**Rollback method:** `terraform destroy -target` the two new resources (no
dependents outside this stack); the existing gateway resource's
`network_config` block can be removed and re-applied to fully revert.

## Correction (2026-07-16): match everything in one pass, not one variable at a time
User called out — correctly — that testing PSC-I alone and rationalizing away
`protocols`/`description`/`timeout` differences as "probably not relevant"
repeats the exact piecemeal approach already rejected earlier this
investigation. Standing instruction from the start: match t2-demo to
cleanroom completely, then test once. Re-pulled the authz extension/policy
live REST config for both projects (raw JSON, not `gcloud alpha` — that
subcommand group doesn't exist) and found ONE genuinely new, previously
unflagged difference: `authzExtension.timeout` = `1s` (t2-demo) vs `2s`
(cleanroom). Combined with the already-known `protocols`/`description` gaps,
all three are now being matched in the same apply as PSC-I:
- `agent_gateway.tf`: removed `description` and `protocols = ["MCP"]` from
  the gateway resource (exact match to cleanroom, which sets neither).
- `agent_gateway.tf`: `authz_extension.iap.timeout` changed `"1s"` → `"2s"`.
- PSC-I network_config (previous entry) — apply already in flight when this
  was found; will be combined with these via a second scoped apply
  immediately after the first completes, before any bind-retry test runs.
**Known, intentionally NOT matched** (structurally impossible — Google
auto-provisions these per-project, not controllable via Terraform):
`agentGatewayCard.mtlsEndpoint` tenant project, `serviceExtensionsServiceAccount`,
`rootCertificates` instance. Flagged, not silently ignored.

## Result: full match applied, bind STILL fails identically (2026-07-16)
Both applies succeeded (PSC-I: 4 added/2 destroyed; protocols+description+timeout:
1 added/2 changed/1 destroyed). Re-ran `attach_gateway_to_engine.sh` against the
now fully-matched gateway. **Identical failure**: `{"code": 3, "message": "The
Reasoning Engine failed to be updated."}` — and this generic message is now
confirmed to be the API's COMPLETE response, not truncated by our own logging.
**Every gateway/authz-level field difference ever found between t2-demo and
cleanroom is now either matched-and-retested-and-rejected, or previously
ruled out (`iamEnforcementMode`).** None remain at this layer.
Remaining unexplored (flagged earlier, not yet executed): full Cloud Audit
Log detail for this specific failed operation ID, and org
policy/VPC-Service-Controls perimeter comparison between the two projects.
Running these now before any GCP Support escalation.

## Audit logs + stderr + org-policy check complete (2026-07-16)
- Cloud Audit Log for the failed operation: `status: {}` empty on both start
  and terminal entries — Google's own audit trail has no more detail than
  the generic API response already seen.
- Container stderr showed a promising new lead (gRPC `CERTIFICATE_VERIFY_FAILED:
  unable to get local issuer certificate`, 8x, tightly time-correlated with
  the operation's completion) — **directly verified against cleanroom's own
  successful bind and found the identical error 274 times there too, same
  relative position in container startup. RULED OUT as noise, not causal.**
- Org policy: direct project-level overrides identical (both empty). Deeper
  checks (inherited policy, VPC-SC perimeter membership) are **UNKNOWN, not
  ruled out** — `orgpolicy.googleapis.com`/`accesscontextmanager.googleapis.com`
  disabled on both projects; cannot be queried from this account without
  enabling APIs (mutating, out of scope) or org-admin access.

**Status: every checkable candidate now tested-and-rejected or explained.**
Only unclosable gap: inherited org policy / VPC-SC membership, which needs
either API enablement or an org-admin to check directly. Complete case for
GCP Support escalation now assembled.
3. Separate, smaller finding from this session: `terraform apply` on the
   reasoning engine silently wipes any out-of-band field it doesn't manage
   (confirmed for both `agentGatewayConfig` and env vars like
   `MODEL_ARMOR_TEMPLATE`) — worth fixing in the deploy process regardless.
4. `[mtls-diag]` startup log was added but not yet pulled from Cloud
   Logging to confirm the exact resolved hostname on the successful run —
   the functional result (full RCA delivered) is unambiguous either way.

# Important commands
```bash
export PATH="/Users/ashmin/Downloads/google-cloud-sdk/bin:$PATH"   # gcloud not on default PATH in this shell
cd ~/projects/testing2-gcp-sre-agent

# the WORKING pattern — bundle source + gateway config in one call:
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
        'agentGateway': 'projects/<PROJECT>/locations/<REGION>/agentGateways/<GATEWAY>'}}}
}}
json.dump(body, open('/tmp/body.json','w'))
"
TOKEN=$(gcloud auth print-access-token)
curl -sS -X PATCH \
  "https://<REGION>-aiplatform.googleapis.com/v1beta1/projects/<PROJECT>/locations/<REGION>/reasoningEngines/<ENGINE_ID>?updateMask=spec.sourceCodeSpec,spec.deploymentSpec.agentGatewayConfig" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d @/tmp/body.json

# test:
PROJECT_ID=sreagent-cleanroom-test REGION=us-central1 REASONING_ENGINE_ID=3983291483653406720 \
  python3 invoke_agent.py --scenario imagepull --verbose
```

# Live resources
- `sreagent-cleanroom-test`: engine `3983291483653406720` (WORKING), gateway
  `agent-gateway`, memory bank `5185752584161329152`
- `sreagent-t2-demo`: engine `8599129257987276800` (still broken, not yet
  re-fixed with the bundled approach), gateway `sre-agent-egress`
- `sreagent-demo`: GKE cluster `sre-test-cluster` (cross-project MCP target,
  confirmed reachable from the clean-room engine)
