# Final RCA — Agent Gateway mTLS certificate-verify failure

**Status:** VERIFIED — fixed and confirmed end-to-end on `sreagent-cleanroom-test`.
**Not yet applied to:** `sreagent-t2-demo` (this repo's original deployment) or the `testing-gcp-sre-agent` repo — tracked as follow-up work, see bottom.

## Problem

`make smoke` / `invoke_agent.py` against the deployed SRE agent failed at the
Gemini `generateContent` call with an SSL error targeting the mTLS-specific
Vertex AI hostname:

```
HTTPSConnectionPool(host='us-central1-aiplatform.mtls.googleapis.com', port=443):
SSLError(SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate
verify failed: self-signed certificate in certificate chain'))
```

First observed as a **gateway-attach failure** on `sreagent-t2-demo`
(`UpdateReasoningEngine` PATCH for `agentGatewayConfig` returning
`error.code: 3` with no further detail) — 8 consecutive failures there
before the investigation moved to a fresh, isolated project
(`sreagent-cleanroom-test`) to determine whether the fault was code-level or
project-state-level.

## Root cause

The Agent Gateway performs TLS inspection (Secure Web Proxy) using a
dynamically-provisioned, self-signed root CA — this is documented Google
platform behavior (known-issue #17 in the `agent-platform-debugger` skill
reference, from `GoogleCloudPlatform/cloud-networking-solutions`). The
reasoning engine's trust store only receives that CA when **a source-code
deployment request already includes the gateway association in the same
atomic `UpdateReasoningEngine` call**.

Our deployment process split these into two separate calls: Terraform
deploys `sourceCodeSpec` (source code, env vars, identity), and a
standalone script (`attach_gateway_to_engine.sh`) later PATCHes only
`spec.deploymentSpec.agentGatewayConfig`. Every deploy in this
investigation's history — on `sreagent-t2-demo` and the first several
`sreagent-cleanroom-test` attempts — pushed source while the engine was
*not yet* gateway-bound, so the certificate-provisioning pipeline had
nothing to bake certs for. The subsequent standalone gateway-attach PATCH
succeeded at the API level (in the cases where it succeeded at all — see
"Related, separate finding" below) but never triggered cert provisioning,
so outbound Vertex AI calls kept hitting the gateway's self-signed
inspection CA with no trust anchor.

### Ruled out, with evidence (not assumption)

- **`GOOGLE_API_USE_MTLS_ENDPOINT` / `GOOGLE_API_USE_CLIENT_CERTIFICATE`** —
  confirmed dead code for this call path. Direct inspection of the
  installed `google-genai` 1.47.0 source (`_api_client.py`) shows the
  Vertex endpoint URL is hardcoded (`https://{location}-aiplatform.googleapis.com/`),
  with no mTLS branch and no read of either env var anywhere in the
  package. `google-genai` never imports `google-api-core` (the library
  that *does* implement this logic, but only inside its generated-client
  LRO transport, a path `google.genai.Client` doesn't touch).
- **Model Armor** — confirmed optional (Google docs, direct quotes, twice)
  and confirmed empirically (gateway created and engine bound successfully
  with it off). Not the cause.
- **The `base_url` override** (PR #20's original workaround, pinning the
  plain endpoint) — undocumented, no Google reference agent does this,
  removed. Not required once the bundling fix is in place.
- **mTLS hostname registration in Agent Registry** — was genuinely missing
  and is a real, separately-documented requirement (exact-match hostname
  registration, confirmed via the official troubleshooting doc), but fixing
  it alone did not resolve the failure. Still correct to keep registered.
- **ADK vs LangGraph** — no evidence framework choice matters. Google's own
  ADK-based `mortgage-agent` reference hit the same failure class
  (different hostname, `telemetry.mtls.googleapis.com`) in its own
  documented control run.
- **Python 3.9** — the deployed runtime was already on Python 3.11
  (`sourceCodeSpec.pythonSpec.version`). Only the local test-invocation
  machine runs 3.9; irrelevant to the deployed failure.

## Fix

1. **Bundle `sourceCodeSpec` and `deploymentSpec.agentGatewayConfig` in one
   atomic `UpdateReasoningEngine` PATCH**, matching Google's own reference
   `deploy_agent.py` pattern (which never separates these). This is the
   operative fix — confirmed by direct A/B test: sequential calls failed,
   the identical body content submitted bundled succeeded immediately after.
2. Removed the undocumented `base_url` override in `agent/gemini_client.py`.
3. Pinned `google-auth>=2.56.0` and `google-cloud-aiplatform>=1.160.0` in
   `agent/requirements.txt` — real, recent (within ~2 weeks of this
   investigation) upstream fixes specifically for Agent-Identity mTLS
   credential/discovery bugs and Vertex AI endpoint handling. Good hygiene,
   not independently sufficient (tested — the bundling was still required
   on top of these).
4. Added the defensive `urllib3.contrib.pyopenssl.extract_from_urllib3()`
   import to `agent/__init__.py`, matching every Google reference agent
   (prevents a known OTEL exporter crash; not itself the fix, but no
   downside to matching Google's own pattern).
5. Rewrote `scripts/attach_gateway_to_engine.sh` to always bundle a fresh
   source deploy with the gateway attach, and to poll its own operation to
   a real terminal state and fail loudly on error (previously it submitted
   the PATCH and exited 0 unconditionally — a silent-failure bug that let
   CI report green on failed attaches throughout this investigation).

## Verification

| Check | Command | Result |
|---|---|---|
| Original failing path reproduced | `invoke_agent.py --scenario imagepull` against `sreagent-cleanroom-test` (pre-fix) | FAIL — `certificate verify failed: self-signed certificate in certificate chain` |
| Root cause isolated | Bundled `sourceCodeSpec` + `agentGatewayConfig` PATCH, then re-ran the identical test | PASS |
| End-to-end functional test | `invoke_agent.py --scenario imagepull` | PASS — `status: done`, `selected_mcp: gke_remote_mcp`, `tools_called: 3`, `confidence: 0.9`, correct RCA (`ImagePullBackOff` from nonexistent image), full formatted report generated |
| Cross-project MCP access | Same run | PASS — `cluster: sre-test-cluster`, `project_id: sreagent-demo` (different project than the engine), confirms the `iac/gke-access` IAM grants work correctly |
| Regression check | None applicable — no existing automated test suite beyond `make smoke` / `invoke_agent.py` in this repo | N/A |
| Lint/type/build | Not run — Python script changes only, no CI lint step in this repo for `agent/` | N/A |
| Git diff reviewed | Yes — `agent/gemini_client.py`, `agent/__init__.py`, `agent/requirements.txt`, `scripts/attach_gateway_to_engine.sh` | Reviewed, see "Changed files" |

## Lessons learned

- **When a platform-managed async provisioning step depends on request
  bundling, splitting a logically-atomic update into two API calls — even
  when both individually succeed — can silently break the thing that only
  works when they're combined.** Nothing in the API surface signals this;
  it only surfaced by finding and matching Google's own reference
  implementation's exact call shape.
- **A script that "succeeds" (exit 0, no error) is not the same as a script
  that verifies its own result.** The original `attach_gateway_to_engine.sh`
  masked this entire class of failure from CI for the whole investigation.
- **When an investigation stalls, a controlled clean-room reproduction
  (fresh project, known-good reference components) is worth the setup cost
  — it converted "23 failures with no pattern" into a precise, fixable root
  cause.**
- **Terraform silently wiping out-of-band fields it doesn't manage** (this
  session also found `agentGatewayConfig` and manually-removed env vars
  both get reset by any `terraform apply` touching the engine, even when
  the plan shows no diff in those fields) is a related, separate footgun —
  worth remembering for any future manual-PATCH-alongside-Terraform pattern.

## Remaining risks

- The exact mechanism of *why* the gateway's self-signed CA only gets
  trusted via a bundled call (as opposed to, say, a timing/propagation
  issue that a bundled call happens to sidestep) is inferred from Google's
  documented known-issue plus a clean A/B test — not confirmed via Google's
  internal implementation. If this platform behavior changes in a future
  Agent Gateway release, this fix may need revisiting.
- `requirements.txt` pins are floors (`>=`), not exact pins — a future
  `pip install` could resolve to a version that regresses the specific
  `google-auth`/`google-cloud-aiplatform` fixes this relies on. Consider
  exact-pinning if this proves fragile.
- Not yet verified whether `sreagent-t2-demo`'s original 8/8 failure history
  is *fully* explained by this same root cause, or whether it has
  additional project-specific issues on top — needs the same fix applied
  and tested there directly (tracked as follow-up, not yet done as of this
  document).

## Changed files
- `agent/gemini_client.py` — removed `base_url` override, added `[mtls-diag]` startup diagnostics
- `agent/__init__.py` — added defensive pyopenssl import
- `agent/requirements.txt` — pinned `google-auth>=2.56.0`, `google-cloud-aiplatform>=1.160.0`, added `certifi>=2026.6.17`
- `scripts/attach_gateway_to_engine.sh` — rewritten to bundle source+gateway config, poll to real completion, fail loudly on error

## Follow-up work (not yet done)
1. Apply this same bundled-deploy fix to `sreagent-t2-demo` and re-verify — very likely resolves the original 8/8 failure history there too, but not yet directly confirmed.
2. Apply the same fix pattern to the `testing-gcp-sre-agent` repo (original, separate repo) — it has its own uncommitted in-progress port of earlier fixes; needs review before applying this on top.
3. Consider exact-pinning the two dependency versions if the floor pins prove fragile over time.
