#!/usr/bin/env bash
# Attach the deployed reasoning engine to the Agent Gateway.
#
# WHY A SCRIPT: the reasoning engine's `agent_gateway_config` field is not yet
# exposed by the Terraform Google provider, so Terraform builds the gateway and
# the engine, and this post-apply step binds them via a REST PATCH.
#
# Run after `terraform apply` in iac/agent when enable_agent_gateway = true.
# Re-runnable (idempotent). Requires: gcloud (authenticated), curl, python3.
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

GATEWAY_SHORT="projects/${PROJECT_ID}/locations/${REGION}/agentGateways/$(basename "$GATEWAY_ID")"
TOKEN="$(gcloud auth print-access-token)"
ENGINE_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines/${ENGINE_ID}"

echo "Attaching engine ${ENGINE_ID} to gateway ${GATEWAY_SHORT} ..."

BODY=$(python3 -c "import json,sys; print(json.dumps({'spec':{'deploymentSpec':{'agentGatewayConfig':{'agentToAnywhereConfig':{'agentGateway': sys.argv[1]}}}}}))" "$GATEWAY_SHORT")

OP=$(curl -sS -X PATCH \
  "${ENGINE_URL}?updateMask=spec.deploymentSpec.agentGatewayConfig" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('name', d))")

echo "PATCH submitted (operation: ${OP})."
echo "The gateway data plane provisions asynchronously; allow time before traffic flows."
