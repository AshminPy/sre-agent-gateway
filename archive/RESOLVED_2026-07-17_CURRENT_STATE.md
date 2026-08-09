# Current problem
None — RESOLVED. `sreagent-t2-demo`'s Reasoning Engine could not bind to
its Agent Gateway (`UpdateReasoningEngine` PATCH returning
`{"code": 3, "message": "The Reasoning Engine failed to be updated."}`
on every attempt). Fixed 2026-07-17 by recreating the engine resource.

# Current status
✅ **FULLY RESOLVED AND VERIFIED END-TO-END**, on both projects.
- `sreagent-cleanroom-test`: working since 2026-07-16.
- `sreagent-t2-demo`: working since 2026-07-17T09:44Z, using its own real
  project, its own real (previously always-failing) gateway, its own real
  code — engine ID `6299408329517039616` (recreated; old engine
  `8599129257987276800` had accumulated bad state from 8+ historical
  failed bind attempts and was destroyed).

## The fix (mechanism)
Bundle `spec.sourceCodeSpec` + `spec.deploymentSpec.agentGatewayConfig` in
ONE atomic `UpdateReasoningEngine` PATCH — the Agent Gateway's self-signed
TLS-inspection CA only gets baked into the engine's trust store when both
are submitted together. Implemented in `scripts/attach_gateway_to_engine.sh`.

## The fix (t2-demo specifically)
The bundling mechanism above was correct from the start, but t2-demo's
*original* engine resource (`8599129257987276800`) had accumulated some
form of backend state — plausibly from 8+ historical failed
`UpdateReasoningEngine` attempts across this investigation — that blocked
every subsequent bind attempt regardless of configuration. Proven via three
independent controlled experiments (2026-07-17), each holding everything
else constant:
1. **Module** — our own `iac/agent/agent_gateway.tf`, deployed fresh into
   `agent-works-502620` → bound first try. Rules out the module.
2. **Project + gateway** — a temporary new engine created inside
   `sreagent-t2-demo` itself, bound to the real, existing, always-failing
   gateway (`sre-agent-egress`) → bound first try. Rules out the project
   and the gateway.
3. **Engine identity** — recreated the *original* engine resource via
   `terraform apply -replace='google_vertex_ai_reasoning_engine.sre_agent'`
   (16 resources: the engine + 15 dependent IAM/registry bindings that
   reference its identity, all correctly cascaded by Terraform) → bound
   first try, full functional test passed.

**Fix for any future recurrence of this failure mode:** recreate the engine
resource. Nothing in this codebase hardcodes the engine ID — everything
(the attach script, `invoke_agent.py`) looks it up dynamically via
`terraform output`, so recreation requires no other code changes.

## Full audit (separate effort, still relevant)
A full 49-check audit of the implementation against Google's docs found
several real, independent issues unrelated to this investigation — tracked
separately as GitHub issues #29–#36 (Cloud Trace/Model Armor hostname
mismatches, two Python correctness bugs, script diagnostics gaps). See
`RESOLVED_2026-07-17_AUDIT_REPORT.md`. Not blocking; not part of this resolution.

## Repos
- **This repo** (`testing2-gcp-sre-agent`) — the live `t2-demo` deployment,
  now fully working. Source of truth.
- **`sreagent-cleanroom-proof`** (private) — point-in-time cleanroom config
  reference.
- **`sreagent-gateway-verified`** (private) — curated repo proving our own
  module works fresh (Experiment 1's evidence). The GCP project it was
  deployed to (`agent-works-502620`) has been torn down; the repo is the
  durable record.

# Important commands
```bash
export PATH="/Users/ashmin/Downloads/google-cloud-sdk/bin:$PATH"
cd ~/projects/testing2-gcp-sre-agent

# retest t2-demo end to end:
TOKEN=$(gcloud auth print-access-token)
export PROJECT_ID=sreagent-t2-demo REGION=us-central1
export REASONING_ENGINE_ID=$(terraform -chdir=iac/agent output -raw reasoning_engine_id)
python3 invoke_agent.py --scenario imagepull --verbose
```

# Live resources
- `sreagent-t2-demo`: engine `6299408329517039616` (working), memory bank
  `3347932278464` (unchanged throughout), gateway `sre-agent-egress`.
- `sreagent-cleanroom-test`: engine `3983291483653406720` (working), gateway
  `agent-gateway`.
- `sreagent-demo`: GKE cluster `sre-test-cluster` (shared cross-project MCP
  target for both).
- `agent-works-502620`: deleted (2026-07-17). Proof preserved in
  `sreagent-gateway-verified`.

# Next action
None required for this investigation — closed. See `RESOLVED_2026-07-17_FINAL_RCA.md` for the
permanent record. Follow-up production-readiness items tracked separately
in GitHub issues #29–#36.
