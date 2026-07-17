# Current problem
`sreagent-t2-demo`'s Reasoning Engine (`8599129257987276800`) cannot bind to
its Agent Gateway. Every `UpdateReasoningEngine` PATCH attempt (bundled
`sourceCodeSpec` + `agentGatewayConfig`, the proven-correct pattern) fails
identically: `{"code": 3, "message": "The Reasoning Engine failed to be
updated."}`. No further detail available from Cloud Audit Logs or the
operation's own error object — confirmed complete, not truncated.

# Current status
✅ The underlying mTLS/gateway-binding **mechanism** is fixed and proven —
verified end-to-end three separate times now (see "What's proven working"
below). ⚠️ `sreagent-t2-demo`'s specific, original engine still cannot bind,
and as of 2026-07-17 every alternative explanation has been directly,
empirically eliminated except one.

## The fix (mechanism — proven, not in question)
Bundle `spec.sourceCodeSpec` + `spec.deploymentSpec.agentGatewayConfig` in
ONE atomic `UpdateReasoningEngine` PATCH — the Agent Gateway's self-signed
TLS-inspection CA only gets baked into the engine's trust store when both
are submitted together. Implemented in `scripts/attach_gateway_to_engine.sh`
(hardened with full pre-flight diagnostics, hard-fail gates, SHA-256
artifact hashing — see PRs #24, and the diagnostics-hardening PRs on
`sreagent-gateway-verified` #1/#2, ported back here in #26).

## What's proven working (3 independent confirmations)
1. **`sreagent-cleanroom-test`** — codelab's gateway module + our engine
   code. Full agent → gateway → GKE Remote MCP (cross-project) → Gemini →
   RCA pipeline, confidence 0.9. (2026-07-16)
2. **`agent-works-502620`** (torn down 2026-07-17 after proof captured) —
   **our own** `iac/agent/agent_gateway.tf` module, deployed fresh. Bound
   first try, full functional test passed, confidence 1.0. **This is the
   test that rules out our own Terraform module as the cause.**
3. **A brand-new temporary engine inside `sreagent-t2-demo` itself** —
   created via standalone REST call, bound to t2-demo's real, existing,
   always-failing gateway (`sre-agent-egress`) on the first attempt, zero
   errors. **This rules out the project and the gateway as the cause.**
   (Runtime call hit an unrelated, expected 403 — the temp engine's own
   principal was never granted `iap.egressor`; not comparable to the
   original engine's admin-plane failure. Temp engine deleted after the
   test; original engine never touched.)

## What this leaves: one remaining candidate
Module — cleared. Project — cleared. Gateway — cleared. IAM (deep-dived,
nothing maps to an admin-plane PATCH) — cleared. Org policy — cleared
(193 constraints, identical). VPC-SC — still genuinely UNKNOWN (the one
thing that can't be checked from this account; needs org-admin access or
enabling a currently-disabled API). Everything else from the original
investigation — tested and rejected (see TROUBLESHOOTING_LOG.md for the
full history: `networkConfig`/PSC-I, `protocols`/`description`/`timeout`,
`iamEnforcementMode`, container-level SSL noise, engine creation date vs.
documented cutoff, all ruled out with direct evidence).

**The only remaining candidate: the original engine resource
(`8599129257987276800`) itself carries accumulated backend state from 8+
historical failed bind attempts that a fresh engine — same project, same
gateway — does not carry.** Not yet confirmed as a Google backend defect
(per explicit instruction, not claiming this without direct proof). The one
untested experiment that would confirm it: recreate the original engine
itself. Disruptive, real action on the actual production-path resource —
not yet approved, not yet run.

## Full audit (separate, broader effort)
A full 49-check audit of the entire implementation (Terraform, Python,
scripts, IAM, endpoints, env vars) against Google's docs and against the
investigation's own rigor was completed 2026-07-17 — see `AUDIT_REPORT.md`.
Found several real, independent issues (Cloud Trace/Model Armor hostname
mismatches, two Python correctness bugs, script diagnostics gaps) — tracked
as separate GitHub issues (#29–#36), explicitly kept out of this
gateway-bind investigation per direct instruction.

## Repos
- **This repo** (`testing2-gcp-sre-agent`) — the live, actual `t2-demo`
  deployment. Source of truth for the ongoing investigation.
- **`sreagent-cleanroom-proof`** (private) — point-in-time reference for the
  cleanroom config.
- **`sreagent-gateway-verified`** (private, public-facing-ready) — the
  curated, reviewable repo built for the AGENT-Works experiment: our own
  agent code + our own `iac/agent` + our own `iac/gke-access` + a vendored
  copy of the codelab's gateway module (`iac/gateway-codelab`) for
  reference, plus the hardened attach script. The GCP project it was
  deployed to (`agent-works-502620`) has been torn down — the repo remains
  as the durable record.

# Important commands
```bash
export PATH="/Users/ashmin/Downloads/google-cloud-sdk/bin:$PATH"
cd ~/projects/testing2-gcp-sre-agent

# retry the bind (full diagnostics, hard-fails on real problems):
bash scripts/attach_gateway_to_engine.sh

# check current live engine state:
TOKEN=$(gcloud auth print-access-token)
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/sreagent-t2-demo/locations/us-central1/reasoningEngines/8599129257987276800" \
  | jq '{identityType: .spec.identityType, agentGatewayConfig: .spec.deploymentSpec.agentGatewayConfig}'
```

# Live resources
- `sreagent-t2-demo`: engine `8599129257987276800` (still not bound — the
  open problem), memory bank `3347932072473278464`, gateway `sre-agent-egress`
  (healthy, PSC-I connected, proven to bind other engines successfully).
- `sreagent-cleanroom-test`: engine `3983291483653406720` (working), gateway
  `agent-gateway`.
- `sreagent-demo`: GKE cluster `sre-test-cluster` (shared cross-project MCP
  target for all of the above).
- `agent-works-502620`: **deleted** (2026-07-17, `DELETE_REQUESTED`, 30-day
  recovery window). Its proof is preserved in `sreagent-gateway-verified`.

# Next action (awaiting decision)
Recreate the original t2-demo engine and re-test the bind (disruptive, not
yet approved), or escalate to GCP Support with this now-doubly-controlled
reproduction (module/project/gateway all independently cleared by direct
experiment, not just process of elimination).
