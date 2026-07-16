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
1. **`scripts/attach_gateway_to_engine.sh` needs fixing** — its design (a
   narrow, standalone `agentGatewayConfig`-only PATCH, run separately from
   source deployment) is fundamentally the wrong shape. Needs to bundle
   with a source deploy, or the deploy process needs restructuring so
   Terraform's source push and the gateway attach happen atomically.
2. **`sreagent-t2-demo` itself is not yet re-fixed** — this whole
   investigation moved to the clean-room project after 8/8 failures there.
   This root cause fully explains why: every attempt used the same
   narrow-PATCH script. Applying the same bundled fix there should resolve
   it, but hasn't been done yet.
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
