## Why

The agent sometimes produces RCAs that are confident but not accurate enough (for example, a
symptom restated as the root cause). A newer version of the agent code exists outside this
repository and must be measured against the current agent on the SAME cases, SAME environment,
SAME tools before anything replaces the current agent.

## What Changes

- Add an optional, off-by-default **candidate agent slot** to `iac/agent`: a second Vertex AI
  Agent Engine (`sre-agent-candidate`) plus its own memory bank, sharing the existing gateway,
  Model Armor, buckets, cluster registry and GKE access.
- The candidate's source is packaged from a directory given at build time
  (`CANDIDATE_AGENT_SRC`) into a git-ignored `agent-candidate.tar.gz`. Candidate source code is
  never committed to this (public) repository.
- The candidate identity gets exactly the same runtime grants as the current agent, declared as
  separate resources so the current agent's grants are never modified.
- `make package-candidate` target; `package_agent.py` accepts optional source/output overrides
  (defaults unchanged).

## Impact

- Current agent: no resource changes (verified by `terraform plan` showing zero update/destroy
  on existing resources).
- Cost: candidate runs with `min_instances = 0`; idle cost ~0, per-run Gemini cost only.
- Rollback: set `enable_candidate_agent = false` and apply (destroys only candidate resources),
  or revert to tag `pre-candidate-agent-2026-09-24`.
