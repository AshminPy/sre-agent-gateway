# Runbook: Updating Existing MCP Tools

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## Add a tool

1. Add the implementation (for the custom MCP: a new function in `mcp/tools/*.py`, wrapped by `@guarded()`; for GKE Remote MCP: nothing to do, it's Google-managed).
2. Add it to the allowlist (`GKE_REMOTE_TOOLS` or `CUSTOM_K8S_TOOLS` in `agent/mcp_client.py`) with a clear description — this is what the LLM sees.
3. Confirm the underlying implementation only uses read (`list_*`/`get_*`) Kubernetes-client calls — the CI regression test (`mcp/tests/test_no_mutation.py`) will fail the build if a mutating call sneaks in, but review it yourself too.
4. Re-register with Agent Registry (`scripts/register_custom_mcp.py` for the custom MCP).
5. Add or update golden eval cases that should exercise it.

## Remove a tool

1. Remove from the allowlist first (this is what actually stops the model from calling it).
2. Remove or deprecate the implementation.
3. Re-register.
4. Check golden eval cases don't reference it in `expected_trajectory`.

## Rename a tool

Treat as remove + add — the model matches on exact tool name strings; a rename with no allowlist update means the old name silently stops being callable and the new one doesn't exist yet from the model's perspective until the allowlist and registration are both updated together.

## Change schema

Update the tool's input schema wherever it's defined (allowlist description, and — for the custom MCP — the actual function signature/`@guarded()` validation). Test against the exact prompt the model sees (Phase 2 of `mcp_router`) — a schema the model can't parse from the short description string is a real risk with this system, since there's no rich JSON-schema introspection shown to the model today (see [Tool Selection](../architecture/tool-selection.md)).

## Update tool description

Low risk, but re-run relevant golden cases anyway — description text changes can shift which tool the model picks when multiple tools are plausible.

## Update authentication

Follow [Agent Identity](../architecture/agent-identity.md)'s pattern — never introduce a static credential. If moving the custom MCP behind a new auth mechanism, update `mcp/security.py` and re-test the full `@guarded()` chain.

## Deploy a new MCP version

For the custom MCP: standard container build + `terraform apply` (Cloud Run picks up the new image) — see [CI/CD](../operations/deployment.md#cicd) for the exact pipeline (build → push → apply → health-check via `gcloud run services describe`, since the ingress setting means an external curl can't verify it).

## Compatibility risks with the LLM/tool router

The router's Phase 2 LLM call is only shown the allowlist for the *already-selected* source — it never sees a stale combined list. But if you change a tool's *name* without updating both the allowlist and any golden cases referencing the old name simultaneously, you'll get silent eval failures that look like the model "forgot" a tool, when actually the allowlist and the test expectations just drifted apart. Always update allowlist + registration + eval cases together, in the same change.

---

**Related pages:** [Tool Selection](../architecture/tool-selection.md) · [MCP Architecture](../architecture/mcp-architecture.md) · [Adding a New MCP Server](add-mcp-server.md)
