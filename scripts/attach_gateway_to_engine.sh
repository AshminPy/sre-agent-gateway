#!/usr/bin/env bash
# Attach the deployed reasoning engine to the Agent Gateway.
#
# WHY A SCRIPT: the reasoning engine's `agent_gateway_config` field is not yet
# exposed by the Terraform Google provider, so Terraform builds the gateway and
# the engine, and this post-apply step binds them via a REST PATCH.
#
# WHY BUNDLED WITH SOURCE (critical, see TROUBLESHOOTING_LOG.md 2026-07-16):
# the Agent Gateway does TLS inspection using a dynamically-provisioned,
# self-signed root CA. The reasoning engine's trust store only gets that CA
# baked in when a source-code deployment request ALREADY includes the gateway
# association in the SAME atomic call. A standalone `agentGatewayConfig`-only
# PATCH (the old design of this script) does NOT trigger that pipeline —
# every call succeeds at the API level but the engine still fails with
# `SSLError: certificate verify failed: self-signed certificate in
# certificate chain` on outbound Vertex AI calls afterward. This is why this
# script re-sends the full `agent.tar.gz` on every run, not just the gateway
# config — re-run this any time you also run `terraform apply` on the engine
# (which silently wipes this out-of-band binding since it isn't in Terraform's
# state — always re-run this script after ANY terraform apply touching the
# engine resource).
#
# Run after `terraform apply` in iac/agent when enable_agent_gateway = true,
# and after any subsequent terraform apply touching the engine resource.
# Idempotent (re-sends the same source unchanged if code hasn't moved).
# Requires: gcloud (authenticated), curl, python3. Polls to a real terminal
# state and fails loudly on error — does not just submit-and-hope.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF="terraform -chdir=${REPO_ROOT}/iac/agent"

PROJECT_ID="$($TF output -raw project_a_id)"
REGION="$($TF output -raw region)"
ENGINE_ID="$($TF output -raw reasoning_engine_id)"
GATEWAY_ID="$($TF output -raw agent_gateway_id 2>/dev/null || echo '')"

if [ -z "$GATEWAY_ID" ] || [ "$GATEWAY_ID" = "null" ]; then
  echo "No agent_gateway_id output — the gateway is disabled (enable_agent_gateway=false). Nothing to attach."
  exit 0
fi

if [ ! -f "${REPO_ROOT}/agent.tar.gz" ]; then
  echo "ERROR: ${REPO_ROOT}/agent.tar.gz not found. Run 'make package-agent' first." >&2
  exit 1
fi

GATEWAY_SHORT="projects/${PROJECT_ID}/locations/${REGION}/agentGateways/$(basename "$GATEWAY_ID")"
TOKEN="$(gcloud auth print-access-token)"
ENGINE_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines/${ENGINE_ID}"

echo "Attaching engine ${ENGINE_ID} to gateway ${GATEWAY_SHORT} (bundled with a fresh source deploy)..."

BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

python3 - "$REPO_ROOT/agent.tar.gz" "$GATEWAY_SHORT" "$BODY_FILE" <<'PYEOF'
import base64, json, sys

archive_path, gateway, out_path = sys.argv[1:4]
with open(archive_path, "rb") as f:
    archive_b64 = base64.b64encode(f.read()).decode("ascii")

body = {
    "spec": {
        "sourceCodeSpec": {
            "inlineSource": {"sourceArchive": archive_b64},
            "pythonSpec": {
                "entrypointModule": "agent.main",
                "entrypointObject": "SREAgent",
                "version": "3.11",
                "requirementsFile": "agent/requirements.txt",
            },
        },
        "deploymentSpec": {
            "agentGatewayConfig": {
                "agentToAnywhereConfig": {"agentGateway": gateway}
            }
        },
    }
}
with open(out_path, "w") as f:
    json.dump(body, f)
PYEOF

OP_NAME=$(curl -sS -X PATCH \
  "${ENGINE_URL}?updateMask=spec.sourceCodeSpec,spec.deploymentSpec.agentGatewayConfig" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d @"$BODY_FILE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('name', ''))")

if [ -z "$OP_NAME" ]; then
  echo "ERROR: PATCH did not return an operation name — submission itself failed." >&2
  exit 1
fi

echo "PATCH submitted (operation: ${OP_NAME}). Polling to terminal state (this can take several minutes)..."

for i in $(seq 1 60); do
  OP_JSON=$(curl -sS -H "Authorization: Bearer ${TOKEN}" \
    "https://${REGION}-aiplatform.googleapis.com/v1beta1/${OP_NAME}")
  DONE=$(echo "$OP_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin,strict=False); print(d.get('done', False))")
  if [ "$DONE" = "True" ]; then
    ERROR=$(echo "$OP_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin,strict=False); e=d.get('error'); print(json.dumps(e) if e else '')")
    if [ -n "$ERROR" ]; then
      echo "ERROR: operation failed: $ERROR" >&2
      exit 1
    fi
    echo "Attach succeeded — engine ${ENGINE_ID} is bound to ${GATEWAY_SHORT}."
    exit 0
  fi
  sleep 10
done

echo "ERROR: operation did not complete after 10 minutes of polling — check manually: ${OP_NAME}" >&2
exit 1
