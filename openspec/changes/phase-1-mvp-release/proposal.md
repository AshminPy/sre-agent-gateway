## Why

Management wants to release Phase 1 next week. Nine acceptance-scope areas were verbally agreed as the Phase 1 bar. No single document currently states these as testable requirements, so "is Phase 1 done" has been answered ad hoc. This change captures the agreed acceptance criteria as a spec, then the repo's actual state is validated against it once, in writing, before any release decision.

## What Changes

- Establishes a new capability, `phase-1-release-criteria`, that encodes the 9 management-agreed Phase 1 acceptance areas as testable requirements.
- No code changes. This proposal only documents the bar; it does not modify `agent/`, `mcp/`, or `iac/`.

## Capabilities

### New Capabilities
- `phase-1-release-criteria`: the 9 Phase 1 MVP acceptance-scope requirements (cluster connectivity, onboarding, LLM switching, MCP tool parity, routing accuracy, gateway enforcement, observability, RCA accuracy, Model Armor/security) that current `main` must satisfy before release.

### Modified Capabilities
(none — this is a documentation-only spec-authoring change)

## Impact

- No affected code. Read-only audit inputs: `agent/mcp_client.py`, `agent/llm/`, `mcp/server.py`, `mcp/tools/*.py`, `iac/agent/*.tf`, `iac/gke-access/*.tf`, `docs/management/risks-and-limitations.md`, GitHub issues/PRs.
- Downstream: this spec becomes the reusable bar for future phase-gate decisions (Phase 2 can diff against it).
