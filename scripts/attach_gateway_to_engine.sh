#!/usr/bin/env bash
# Attach the deployed reasoning engine to the Agent Gateway.
#
# WHY A SCRIPT: the reasoning engine's `agent_gateway_config` field is not yet
# exposed by the Terraform Google provider, so Terraform builds the gateway and
# the engine, and this post-apply step binds them via a REST PATCH.
#
# WHY BUNDLED WITH SOURCE (critical, see archive/RESOLVED_2026-07-17_TROUBLESHOOTING_LOG.md 2026-07-16):
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
# WHICH GATEWAY: this script uses iac/agent's own gateway resource
# (enable_agent_gateway=true there, the default — see iac/agent/agent_gateway.tf).
# The gateway's resource path is used EXACTLY as Terraform output it — never
# reconstructed from the engine's own project/region — so a real
# project/region mismatch is caught as an error instead of silently
# producing a wrong (and possibly nonexistent) path.
#
# DIAGNOSTICS (2026-07-16 review, tightened same day): this script does NOT
# change agent code or gateway configuration. It only adds visibility into an
# existing failure: full pre-flight resource snapshots (with HTTP status
# checked before trusting the response body), a real readiness gate, hard
# failures on identity/API/project-region problems that would make the PATCH
# meaningless to even attempt, drift detection against the engine's current
# binding, SHA-256 hashes of the deployed artifact and PATCH body (to prove
# identical bytes were used across projects), and full HTTP/operation
# diagnostics (status code, headers, body, metadata, error.details) instead
# of just the top-level error message. Every diagnostic file this run
# produces is saved under the path printed as "Diagnostics dir" below —
# check there first when debugging a failure, before re-running anything.
#
# Run after `terraform apply` in iac/agent, and after any subsequent
# terraform apply touching the engine resource. Idempotent (re-sends the
# same source unchanged if code hasn't moved). Requires: gcloud
# (authenticated), curl, jq, python3, and either sha256sum or shasum. Polls
# to a real terminal state and fails loudly on error — does not just
# submit-and-hope.
set -euo pipefail

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF="terraform -chdir=${REPO_ROOT}/iac/agent"

DIAG_DIR="$(mktemp -d)"
echo "Diagnostics dir (all raw responses saved here): ${DIAG_DIR}"

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

# GATEWAY_ID is already a full resource path (e.g.
# projects/P/locations/R/agentGateways/NAME), exactly as Terraform output it.
# Used as-is everywhere below — never reconstructed from PROJECT_ID/REGION —
# so a gateway that's actually in a different project/region than the engine
# is fetched and reported correctly instead of silently rewritten to a path
# that doesn't match reality.
GATEWAY_URL="https://networkservices.googleapis.com/v1beta1/${GATEWAY_ID}"
TOKEN="$(gcloud auth print-access-token)"
ENGINE_URL="https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines/${ENGINE_ID}"
# TOKEN is never echoed or written to any diagnostic file below — only used
# directly as a curl header value.

# ── Pre-flight: fetch full current state, validating HTTP status first ─────
echo ""
echo "=== Pre-flight: fetching current engine and gateway resources ==="

ENGINE_BEFORE="${DIAG_DIR}/engine_before.json"
GATEWAY_JSON="${DIAG_DIR}/gateway.json"

ENGINE_GET_CODE=$(curl -sS -H "Authorization: Bearer ${TOKEN}" "$ENGINE_URL" -o "$ENGINE_BEFORE" -w "%{http_code}")
if [ "$ENGINE_GET_CODE" -lt 200 ] || [ "$ENGINE_GET_CODE" -ge 300 ]; then
  echo "ERROR: fetching the engine resource failed (HTTP ${ENGINE_GET_CODE}). Refusing to continue." >&2
  echo "Response body: ${ENGINE_BEFORE}" >&2
  cat "$ENGINE_BEFORE" >&2
  exit 1
fi
echo "Engine resource fetched (HTTP ${ENGINE_GET_CODE}): ${ENGINE_BEFORE}"

GATEWAY_GET_CODE=$(curl -sS -H "Authorization: Bearer ${TOKEN}" "$GATEWAY_URL" -o "$GATEWAY_JSON" -w "%{http_code}")
if [ "$GATEWAY_GET_CODE" -lt 200 ] || [ "$GATEWAY_GET_CODE" -ge 300 ]; then
  echo "ERROR: fetching the gateway resource failed (HTTP ${GATEWAY_GET_CODE}). Refusing to continue." >&2
  echo "Response body: ${GATEWAY_JSON}" >&2
  cat "$GATEWAY_JSON" >&2
  exit 1
fi
echo "Gateway resource fetched (HTTP ${GATEWAY_GET_CODE}): ${GATEWAY_JSON}"

GATEWAY_PROJECT="$(echo "$GATEWAY_ID" | sed -n 's#projects/\([^/]*\)/.*#\1#p')"
GATEWAY_REGION="$(echo "$GATEWAY_ID" | sed -n 's#.*/locations/\([^/]*\)/.*#\1#p')"
echo ""
echo "Gateway project: ${GATEWAY_PROJECT}  (engine project: ${PROJECT_ID})"
echo "Gateway region:  ${GATEWAY_REGION}  (engine region: ${REGION})"
if [ "$GATEWAY_PROJECT" != "$PROJECT_ID" ] || [ "$GATEWAY_REGION" != "$REGION" ]; then
  echo "ERROR: gateway project/region does not match the engine's. Refusing to continue." >&2
  echo "  gateway: ${GATEWAY_ID}" >&2
  echo "  engine:  projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines/${ENGINE_ID}" >&2
  exit 1
fi
echo "Gateway project/region match: OK"

GATEWAY_NETWORK_CONFIG="$(jq -c '.networkConfig // "ABSENT"' "$GATEWAY_JSON")"
echo "Gateway networkConfig: ${GATEWAY_NETWORK_CONFIG}"

# This resource type has NO explicit state/health/ready field (verified
# directly against the live API — checked, does not exist). The best
# available readiness signal is agentGatewayCard being fully populated:
# that sub-object is only filled in once the gateway's tenant-side
# provisioning (mTLS endpoint, service-extensions SA, root CA) completes.
GATEWAY_READY="$(jq -r '
  if (.agentGatewayCard.mtlsEndpoint // "") != ""
     and (.agentGatewayCard.serviceExtensionsServiceAccount // "") != ""
     and ((.agentGatewayCard.rootCertificates // []) | length) > 0
  then "READY" else "NOT_READY" end
' "$GATEWAY_JSON")"
echo "Gateway readiness (agentGatewayCard populated — proxy signal, this API has no explicit state field): ${GATEWAY_READY}"

if [ "$GATEWAY_READY" != "READY" ]; then
  echo "ERROR: gateway is not ready — agentGatewayCard is not fully populated yet. Refusing to continue." >&2
  echo "Full gateway resource for inspection: ${GATEWAY_JSON}" >&2
  exit 1
fi

ENGINE_IDENTITY_TYPE="$(jq -r '.spec.identityType // "ABSENT"' "$ENGINE_BEFORE")"
echo "Engine identityType: ${ENGINE_IDENTITY_TYPE}"
if [ "$ENGINE_IDENTITY_TYPE" != "AGENT_IDENTITY" ]; then
  echo "ERROR: engine identityType is '${ENGINE_IDENTITY_TYPE}', expected AGENT_IDENTITY. Refusing to continue." >&2
  exit 1
fi

# ── Required APIs — the two the PATCH cannot possibly succeed without are
# hard failures; the other two (registry endpoint / authz policy features)
# stay as warnings — see the closing note in the PR/commit for why. ─────────
echo ""
echo "=== Required APIs ==="
ENABLED_APIS="$(gcloud services list --project="$PROJECT_ID" --enabled --format='value(config.name)' 2>/dev/null || echo '')"

HARD_REQUIRED_APIS=(aiplatform.googleapis.com networkservices.googleapis.com)
for api in "${HARD_REQUIRED_APIS[@]}"; do
  if echo "$ENABLED_APIS" | grep -qx "$api"; then
    echo "  ${api}: ENABLED"
  else
    echo "ERROR: required API ${api} is not enabled on ${PROJECT_ID}. Refusing to continue." >&2
    exit 1
  fi
done

SOFT_REQUIRED_APIS=(networksecurity.googleapis.com agentregistry.googleapis.com)
for api in "${SOFT_REQUIRED_APIS[@]}"; do
  if echo "$ENABLED_APIS" | grep -qx "$api"; then
    echo "  ${api}: ENABLED"
  else
    echo "  ${api}: WARN — not enabled"
  fi
done

# Drift check: does the engine already have an agentGatewayConfig, and if
# so, does it point at the gateway we're about to attach? Informational only
# — NOT a hard failure, because a drifted/missing binding is the exact
# scenario this script exists to fix (see the WHY BUNDLED WITH SOURCE note
# above). Blocking here would make the script unable to do its job.
EXISTING_GW_CONFIG="$(jq -c '.spec.deploymentSpec.agentGatewayConfig // "ABSENT"' "$ENGINE_BEFORE")"
echo ""
echo "Engine's CURRENT agentGatewayConfig (before this run): ${EXISTING_GW_CONFIG}"
if [ "$EXISTING_GW_CONFIG" = '"ABSENT"' ]; then
  echo "  -> drift check: no existing binding (expected on first attach, or if a Terraform apply wiped it)."
elif echo "$EXISTING_GW_CONFIG" | grep -qF "$GATEWAY_ID"; then
  echo "  -> drift check: OK, matches the intended gateway (${GATEWAY_ID})."
else
  echo "  -> drift check: WARNING — engine is currently bound to a DIFFERENT gateway than intended (${GATEWAY_ID})."
fi

# ── Preflight platform-state comparison (informational — does not block) ───
echo ""
echo "=== Agent Registry endpoint registration ==="
REGISTERED_SERVICES="$(gcloud alpha agent-registry services list --project="$PROJECT_ID" --location="$REGION" --format='value(displayName)' 2>/dev/null || echo '')"
for host in "${REGION}-aiplatform.googleapis.com" "${REGION}-aiplatform.mtls.googleapis.com"; do
  if echo "$REGISTERED_SERVICES" | grep -qx "$host"; then
    echo "  ${host}: REGISTERED"
  else
    echo "  ${host}: WARN — not found in Agent Registry"
  fi
done

echo ""
echo "=== Required Google-managed service agents ==="
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
IAM_POLICY_JSON="${DIAG_DIR}/iam_policy.json"
IAM_POLICY_ERR="${DIAG_DIR}/iam_policy_err.txt"

# Distinguish "checked the IAM policy and the binding is absent" from
# "couldn't read the IAM policy at all" — these mean very different things
# and reporting both as "not found" would be misleading.
if gcloud projects get-iam-policy "$PROJECT_ID" --format=json > "$IAM_POLICY_JSON" 2>"$IAM_POLICY_ERR"; then
  IAM_POLICY_READABLE=true
else
  IAM_POLICY_READABLE=false
  echo "  WARN: could not read the IAM policy for ${PROJECT_ID} — every check below is UNKNOWN, not confirmed absent."
  echo "  Error: $(cat "$IAM_POLICY_ERR")"
fi

check_service_agent() {
  local sa="$1" role="$2"
  if [ "$IAM_POLICY_READABLE" != true ]; then
    echo "  ${role} on ${sa}: UNKNOWN — IAM policy could not be read"
    return
  fi
  local found
  found="$(jq -r --arg role "$role" --arg member "serviceAccount:${sa}" \
    '[.bindings[]? | select(.role == $role) | .members[]? | select(. == $member)] | length > 0' \
    "$IAM_POLICY_JSON")"
  if [ "$found" = "true" ]; then
    echo "  ${role} on ${sa}: YES — confirmed present"
  else
    echo "  ${role} on ${sa}: WARN — IAM policy read successfully, binding is absent"
  fi
}
check_service_agent "service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com" "roles/aiplatform.serviceAgent"
check_service_agent "service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com" "roles/aiplatform.reasoningEngineServiceAgent"
check_service_agent "service-${PROJECT_NUMBER}@gcp-sa-agentgateway.iam.gserviceaccount.com" "roles/agentgateway.serviceAgent"

# ── Build the PATCH body ────────────────────────────────────────────────────
echo ""
echo "Attaching engine ${ENGINE_ID} to gateway ${GATEWAY_ID} (bundled with a fresh source deploy)..."

BODY_FILE="$(mktemp)"
NORMALIZED_BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE" "$NORMALIZED_BODY_FILE"' EXIT

python3 - "$REPO_ROOT/agent.tar.gz" "$GATEWAY_ID" "$BODY_FILE" "$NORMALIZED_BODY_FILE" <<'PYEOF'
import base64, json, sys

archive_path, gateway, out_path, normalized_out_path = sys.argv[1:5]
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

# Normalized copy for cross-project comparison: the raw body's hash will
# always differ between projects because it contains the project-specific
# gateway resource path (projects/<PROJECT>/locations/<REGION>/agentGateways/
# <GATEWAY>). Replace just that one field with a fixed placeholder so the
# hash reflects the STRUCTURE of the request (code, entrypoint, requirements,
# how the gateway config is shaped) rather than which project it targets.
normalized = json.loads(json.dumps(body))
normalized["spec"]["deploymentSpec"]["agentGatewayConfig"]["agentToAnywhereConfig"]["agentGateway"] = "<GATEWAY_PATH>"
with open(normalized_out_path, "w") as f:
    json.dump(normalized, f, sort_keys=True)
PYEOF

# SHA-256 of the deployed artifact and the PATCH body — lets you prove
# byte-identical deploys across projects instead of trusting that two runs
# "should" have used the same input. The RAW body hash will always differ
# across projects (it embeds this project's gateway path) — compare the
# NORMALIZED hash instead to prove the request is structurally identical.
ARCHIVE_SHA256="$(sha256_of "${REPO_ROOT}/agent.tar.gz")"
BODY_SHA256="$(sha256_of "$BODY_FILE")"
NORMALIZED_BODY_SHA256="$(sha256_of "$NORMALIZED_BODY_FILE")"
echo "agent.tar.gz SHA-256:            ${ARCHIVE_SHA256}"
echo "PATCH body SHA-256 (raw):        ${BODY_SHA256}  (will differ across projects — contains this project's gateway path)"
echo "PATCH body SHA-256 (normalized): ${NORMALIZED_BODY_SHA256}  (compare THIS one across projects to prove structural equivalence)"
printf '%s  agent.tar.gz\n%s  patch_body.json (raw)\n%s  patch_body.json (normalized, gateway path replaced with <GATEWAY_PATH>)\n' \
  "$ARCHIVE_SHA256" "$BODY_SHA256" "$NORMALIZED_BODY_SHA256" > "${DIAG_DIR}/sha256sums.txt"

# ── Submit the PATCH with full diagnostics ──────────────────────────────────
RESPONSE_BODY="${DIAG_DIR}/patch_response_body.json"
RESPONSE_HEADERS="${DIAG_DIR}/patch_response_headers.txt"

HTTP_CODE=$(curl -sS \
  -X PATCH \
  "${ENGINE_URL}?updateMask=spec.sourceCodeSpec,spec.deploymentSpec.agentGatewayConfig" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -D "$RESPONSE_HEADERS" \
  -o "$RESPONSE_BODY" \
  -w "%{http_code}" \
  -d @"$BODY_FILE")

echo "HTTP status: ${HTTP_CODE}"
jq . "$RESPONSE_BODY" 2>/dev/null || cat "$RESPONSE_BODY"

echo ""
echo "Relevant Google response headers:"
grep -iE 'x-goog|trace|request-id|uploadid' "$RESPONSE_HEADERS" || echo "  (none present)"

if [ "$HTTP_CODE" -lt 200 ] || [ "$HTTP_CODE" -ge 300 ]; then
  echo "ERROR: PATCH submission failed (HTTP ${HTTP_CODE})." >&2
  echo "Full response body: ${RESPONSE_BODY}" >&2
  echo "Full response headers: ${RESPONSE_HEADERS}" >&2
  exit 1
fi

OP_NAME=$(jq -r '.name // empty' "$RESPONSE_BODY")

if [ -z "$OP_NAME" ]; then
  echo "ERROR: PATCH returned HTTP ${HTTP_CODE} but no operation name — response did not look like a long-running operation." >&2
  echo "Full response body: ${RESPONSE_BODY}" >&2
  exit 1
fi

echo ""
echo "PATCH submitted (operation: ${OP_NAME}). Polling to terminal state (this can take several minutes)..."

OP_LATEST="${DIAG_DIR}/operation_latest.json"
POLL_FAIL_COUNT=0

for i in $(seq 1 60); do
  POLL_HTTP_CODE=$(curl -sS -H "Authorization: Bearer ${TOKEN}" \
    "https://${REGION}-aiplatform.googleapis.com/v1beta1/${OP_NAME}" -o "$OP_LATEST" -w "%{http_code}")

  if [ "$POLL_HTTP_CODE" -lt 200 ] || [ "$POLL_HTTP_CODE" -ge 300 ]; then
    # A temporary 401/403/429/5xx here doesn't mean the operation failed —
    # it means the poll request itself failed. Don't parse this response as
    # operation state (it's an error body, not an LRO). Save it, warn, and
    # keep polling — the bounded 60-attempt/10-minute cap still applies.
    POLL_FAIL_COUNT=$((POLL_FAIL_COUNT + 1))
    FAILED_POLL_FILE="${DIAG_DIR}/operation_poll_failed_${i}.json"
    cp "$OP_LATEST" "$FAILED_POLL_FILE"
    echo "WARN: poll ${i} returned HTTP ${POLL_HTTP_CODE} (not the operation itself — could be transient). Saved: ${FAILED_POLL_FILE}. Retrying..." >&2
    sleep 10
    continue
  fi

  DONE=$(jq -r '.done // false' "$OP_LATEST")
  if [ "$DONE" = "true" ]; then
    ERROR_PRESENT=$(jq -r 'has("error")' "$OP_LATEST")
    if [ "$ERROR_PRESENT" = "true" ]; then
      echo "ERROR: operation failed." >&2
      echo "" >&2
      echo "Operation metadata:" >&2
      jq '.metadata // {}' "$OP_LATEST" >&2
      echo "" >&2
      echo "Error (message + details):" >&2
      jq '.error // {}' "$OP_LATEST" >&2
      echo "" >&2
      echo "Full raw operation JSON: ${OP_LATEST}" >&2
      echo "Pre-flight engine snapshot (before this run): ${ENGINE_BEFORE}" >&2
      echo "Pre-flight gateway snapshot: ${GATEWAY_JSON}" >&2
      echo "SHA-256 of deployed artifacts: ${DIAG_DIR}/sha256sums.txt" >&2
      echo "All diagnostics for this run: ${DIAG_DIR}" >&2
      exit 1
    fi
    echo "Attach succeeded — engine ${ENGINE_ID} is bound to ${GATEWAY_ID}."
    exit 0
  fi
  sleep 10
done

echo "ERROR: operation did not complete after 10 minutes of polling — check manually: ${OP_NAME}" >&2
echo "Latest poll result: ${OP_LATEST}" >&2
if [ "$POLL_FAIL_COUNT" -gt 0 ]; then
  echo "NOTE: ${POLL_FAIL_COUNT} poll attempt(s) returned a non-2xx status during this run — see operation_poll_failed_*.json in ${DIAG_DIR}." >&2
fi
exit 1
