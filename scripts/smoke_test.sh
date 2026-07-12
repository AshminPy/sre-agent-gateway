#!/usr/bin/env bash
# Smoke test: invoke the deployed agent on one incident scenario and assert it
# returns a well-formed RCA. Run after deploying the agent stack.
#
#   scripts/smoke_test.sh [scenario]     # default scenario: imagepull
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCENARIO="${1:-imagepull}"

# Load engine id / project from Terraform if not already exported.
if [ -z "${REASONING_ENGINE_ID:-}" ] || [ -z "${PROJECT_ID:-}" ]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/scripts/init-env.sh"
fi

echo "Invoking agent (scenario=${SCENARIO}) ..."
OUT="$(cd "$REPO_ROOT" && python3 invoke_agent.py --scenario "$SCENARIO" --verbose 2>&1)"
echo "$OUT" | tail -20

# Assert the response contains an RCA / structured result rather than an error.
if echo "$OUT" | grep -qiE '"status":\s*"done"|rca_report|RCA REPORT'; then
  echo "SMOKE TEST PASSED — agent returned a structured RCA."
else
  echo "SMOKE TEST FAILED — no well-formed RCA in the response (see output above)." >&2
  exit 1
fi
