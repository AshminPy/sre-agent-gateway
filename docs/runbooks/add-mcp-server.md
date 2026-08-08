# Runbook: Adding a New MCP Server

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

Use this for any future source — Elastic, Prometheus, Grafana, PagerDuty, Cloud Logging, Confluence, GitHub, etc. None of these exist today; this is a template based on how the existing two sources (GKE Remote MCP, custom K8s MCP) were built.

## Steps

1. **MCP server creation/deployment**: either use a Google-managed one if it exists for your target (like GKE Remote MCP), or build a custom server following the `mcp/server.py` pattern (FastMCP-based, one Python function per tool).
2. **Tool schema**: define each tool's name, description, and input schema explicitly — these are what the LLM sees when deciding what to call (see [Tool Selection](../architecture/tool-selection.md)).
3. **Input/output rules**: read-only verbs only, if this source is meant for investigation (not remediation). Follow the `@guarded()` pattern (`mcp/security.py`) for validation, rate limiting, and redaction if building a custom server.
4. **Authentication**: identity token (Cloud Run) or access token (direct GCP API), matching whichever the target expects — never a static long-lived credential (see [Agent Identity](../architecture/agent-identity.md)).
5. **Secrets**: none should be needed if using Workload Identity / Agent Identity correctly. If the target genuinely requires an API key (e.g., a non-GCP SaaS), use Secret Manager, never an env var with the raw value in Terraform.
6. **IAM**: least-privilege, resource-scoped where the resource type supports it — see the pattern in [Authorization and Permissions](../governance/security.md).
7. **Network access**: confirm the actual network path works — this is where the existing custom MCP fell short (see [MCP Architecture](../architecture/mcp-architecture.md)); don't repeat that gap. If using `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`, build the Load Balancer + Serverless NEG *before* considering this done.
8. **Agent Gateway registration**: use `--mcp-server-spec-type=tool-spec` (not `--endpoint-spec-type=no-spec`, which is for plain API passthrough only) — follow `scripts/register_custom_mcp.py`'s pattern, respect the 10KB content limit.
9. **Tool discovery**: static allowlist in `agent/mcp_client.py`, same pattern as `GKE_REMOTE_TOOLS`/`CUSTOM_K8S_TOOLS` — the agent does not dynamically discover tools at runtime (see [Tool Selection](../architecture/tool-selection.md)).
10. **Routing rules**: does this new source need its own `mcp_router` Phase 1 branch (deterministic source selection), or is it an addition to an existing cluster-type mapping? Answer this before writing code — see [Dynamic MCP Routing](../architecture/dynamic-mcp-routing.md).
11. **Evidence normalization**: make sure `evidence_extractor`'s extraction prompt/logic handles this source's response shape reasonably — test with real output, not assumptions.
12. **Observability**: add a `mcp_source` label value for this source everywhere `tool_failures` is already tracked; consider whether a dedicated alert is warranted (see [Alerting](../operations/alerting.md)).
13. **Evaluation dataset updates**: add golden cases exercising this new source (see [Evaluation](../architecture/evaluation.md)).
14. **Load testing**: not currently a standard step anywhere in this codebase — decide the bar for a new source given its expected call volume.
15. **Security review**: mandatory before enabling in production — verify read-only, verify no secret exposure, verify least-privilege IAM.
16. **Deployment**: Terraform + registration script, following the existing CI pattern in `.github/workflows/terraform-apply.yml`.

## Does changing LangGraph require this?

Only if the new source needs genuinely new *reasoning* behavior beyond "pick this tool from this source's allowlist" — e.g., if it needs its own dedicated node rather than fitting into the existing `mcp_router`/`tool_executor` pattern. For most new sources that follow the existing MCP tool-calling shape, no graph change is needed — only new allowlist entries and (if it's a new source-selection branch) an `mcp_router` Phase 1 update.

---

**Related pages:** [MCP Architecture](../architecture/mcp-architecture.md) · [Tool Selection](../architecture/tool-selection.md) · [Updating Existing MCP Tools](update-mcp-tool.md)
