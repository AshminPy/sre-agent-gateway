#!/usr/bin/env bash
# Package the agent/ source tree into agent.tar.gz for inline deployment.
#
# The Terraform agent-engine module embeds this archive via filebase64() at plan
# time, so it MUST exist before `terraform plan`/`apply` (CI runs this first).
#
# Actual packaging lives in package_agent.py (pure Python stdlib — reproducible
# on any machine, no GNU tar dependency). This wrapper exists so every existing
# caller (Makefile, CI workflows, README) keeps working unchanged.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${REPO_ROOT}/scripts/package_agent.py"
