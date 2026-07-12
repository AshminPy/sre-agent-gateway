#!/usr/bin/env bash
# Package the agent/ source tree into agent.tar.gz for inline deployment.
#
# The Terraform agent-engine module embeds this archive via filebase64() at plan
# time, so it MUST exist before `terraform plan`/`apply` (CI runs this first).
#
# Uses a reproducible tar (sorted entries, fixed mtime) so the archive's hash is
# stable — infra-only changes then won't spuriously re-deploy the reasoning
# engine. GNU tar is required for reproducibility; on macOS install it with
# `brew install gnu-tar` (gtar). Falls back to a plain tar with a warning.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

OUT="agent.tar.gz"
EXCLUDES=(--exclude='__pycache__' --exclude='*.pyc' --exclude='.env' --exclude='.env.example')

if command -v gtar >/dev/null 2>&1; then
  TAR=gtar
elif tar --version 2>/dev/null | grep -qi 'gnu'; then
  TAR=tar
else
  TAR=""
fi

if [ -n "$TAR" ]; then
  COPYFILE_DISABLE=1 "$TAR" --sort=name --mtime='2024-01-01 00:00:00' \
    --owner=0 --group=0 --numeric-owner \
    "${EXCLUDES[@]}" -czf "$OUT" agent/
  echo "Built $OUT (reproducible) from agent/"
else
  echo "WARNING: GNU tar not found — building a non-reproducible archive." >&2
  echo "         Install gnu-tar (brew install gnu-tar) for stable hashes." >&2
  COPYFILE_DISABLE=1 tar "${EXCLUDES[@]}" -czf "$OUT" agent/
  echo "Built $OUT from agent/"
fi
