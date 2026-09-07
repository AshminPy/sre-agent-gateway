# Runbook: Adding a New MCP Server

> **Last Verified:** 2026-09-07 (Phase 1 Final Readiness review, Section 13) · **Owner:** SRE Agent platform team

Use this for any future source — Elastic, Prometheus, Grafana, PagerDuty, Cloud Logging, Confluence, GitHub, etc. None of these exist today; this is a template based on how the existing two sources (GKE Remote MCP, custom K8s MCP) were built. Goal: adding a new source should be primarily **configuration-driven** — new registry/config entries — not a rewrite of the agent's core reasoning code.

## A. Vendor-managed remote MCP vs. custom/self-hosted MCP

Every new source falls into exactly one of these two shapes. Decide which one first — it determines which of the steps below actually apply.

| | **A. Vendor-managed remote MCP** | **B. Custom/self-hosted MCP** |
|---|---|---|
| Example today | GKE Remote MCP (`gke_remote_mcp`) | Our own K8s MCP (`k8s_mcp`, Cloud Run) |
| Who builds/deploys the server | The vendor (Google, or the target SaaS if it ships an MCP endpoint) | Us — `mcp/server.py`-style FastMCP app |
| Steps 1–3 below (server creation, tool schema, input/output rules) | Fixed by the vendor — we cannot change them | Fully ours to define |
| Auth | Whatever the vendor's endpoint requires (access token, API key) | We choose (identity token is the existing pattern) |
| CONTENT_AUTHZ / Model Armor coverage | Whatever the vendor's own MCP transport supports — **not guaranteed**; the same Streamable-HTTP-response exclusion documented for GKE Remote MCP in [MCP Architecture](../architecture/mcp-architecture.md) may or may not apply, vendor by vendor. Verify per-vendor, don't assume it matches GKE's behavior. | Fully in our control — we can add an application-level response guard (`mcp/response_guard.py`'s pattern) regardless of what the platform-level CONTENT_AUTHZ path does or doesn't cover. |
| Our own response-guard option (§4 above) | Not available — we don't own the server process | Available — same pattern as `mcp/response_guard.py` |
| Network path we must build | None (public/managed endpoint + our own auth) | Ours — Cloud Run + IAM, or Connect Gateway for on-prem/non-GCP targets |

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
17. **REQUEST_AUTHZ**: confirm the new source's traffic is fail-closed through Agent Gateway's IAP authz extension (`var.authz_fail_open = false`, see [Agent Gateway](../architecture/agent-gateway.md)) — this applies uniformly to any traffic through the gateway, no per-source config needed, but verify it's actually routed through the gateway (not a direct call that bypasses it).
18. **CONTENT_AUTHZ / Model Armor applicability**: do NOT assume this new source gets the same inspection coverage as an existing one. Check, per source: (a) does the platform-level CONTENT_AUTHZ extension actually process `RESPONSE_BODY` for this source's transport (see the vendor-vs-custom table above), (b) is the always-on floor-setting mechanism (`google_model_armor_floorsetting`) enough on its own, (c) for a custom/self-hosted server, do we need our own application-level response guard (`mcp/response_guard.py`'s pattern) the way the existing custom K8s MCP has one. Record the answer explicitly — silence here reads as "covered" when it might not be.
19. **Timeout / retry**: define an explicit per-tool-call timeout and retry policy for the new source (the existing two sources inherit `google.api_core`'s default retry behavior on the underlying GCP client libraries — a non-GCP REST target needs this decided explicitly, not left to the HTTP client's defaults).
20. **Health checks**: does the new source need a startup/liveness check before the agent will route to it (e.g., a Cloud Run service needs to be warm), or is it always-available (a vendor SaaS API)? Decide whether `mcp_router`'s Phase 1 deterministic selection needs a "source unavailable, skip" branch for this source, distinct from an in-request tool-call failure.
21. **Enable/disable configuration**: add a boolean Terraform variable (matching the existing `enable_custom_mcp` pattern in `iac/agent/variables.tf`) so the new source can be toggled off without a code change or full redeploy — required before this source is considered production-safe to roll back quickly.
22. **Tracing**: confirm calls to the new source appear in the existing OpenTelemetry trace (`agent/otel.py`) under a distinct span name — don't rely on generic HTTP client instrumentation alone if the target library doesn't auto-instrument.
23. **Live validation** (distinct from CI, which only proves code compiles/tests pass): run at least one real investigation against the new source's real endpoint before calling it production-ready — matches this repo's own standing rule that CI-green is never sufficient evidence of a working integration.
24. **Rollback**: because of step 21, rollback is "flip the enable flag off + `terraform apply`," not a code revert — confirm this is actually true for the specific resources this new source adds (a resource with `prevent_destroy` or an irreversible side effect on first creation needs a written rollback note here, not an assumption that disabling it undoes everything).

## Does changing LangGraph require this?

Only if the new source needs genuinely new *reasoning* behavior beyond "pick this tool from this source's allowlist" — e.g., if it needs its own dedicated node rather than fitting into the existing `mcp_router`/`tool_executor` pattern. For most new sources that follow the existing MCP tool-calling shape, no graph change is needed — only new allowlist entries and (if it's a new source-selection branch) an `mcp_router` Phase 1 update.

## Current architectural limitation: routing assumes every source is cluster-scoped

`mcp_router.py`'s Phase 1 source selection is a binary branch keyed on Kubernetes cluster type:
```python
selected_mcp = "gke_remote_mcp" if cluster_type == "gke" else "k8s_mcp"
```
This works because both existing sources answer "which K8s cluster is this investigation about?" A source like Prometheus, Elasticsearch, GitHub, or Confluence often does **not** have that same 1:1 relationship — a Prometheus instance might monitor one cluster, several clusters, or nothing cluster-shaped at all (e.g. a GitHub repo). Adding a source like this will require turning that binary branch into a genuine `source_type -> mcp_source` lookup (and deciding, per new source, whether it's selected by cluster association, by keyword/intent in the query, or explicitly named by the caller) — a small, mechanical refactor, not a redesign, but **not done today** because no second cluster-scoped-only assumption has been broken yet. Do this refactor when the first non-cluster-scoped source is actually being added, not speculatively ahead of time.

## Worked example: adding Prometheus as a new source

Concrete walk-through, following the numbered steps above, for a Prometheus instance that monitors `sre-test-cluster` (read-only metrics queries, e.g. "what was this pod's memory usage over the last hour").

1. **Server**: Prometheus doesn't ship an MCP server itself. Two options: (a) a community/vendor Prometheus MCP server if one meets our security bar, or (b) a small custom FastMCP server (`mcp/prometheus_server.py`, same pattern as `mcp/server.py`) wrapping Prometheus's HTTP query API (`/api/v1/query`, `/api/v1/query_range`). This is case **B (custom/self-hosted)** per the table above — we're deploying and operating the server.
2. **Tool schema**: e.g. `query_metric(metric_name, pod, namespace, range_minutes)`, `list_available_metrics(namespace)` — narrow, purpose-built tools, not a raw PromQL passthrough (avoids giving the LLM a full query language to freely construct).
3. **Input/output rules**: read-only (Prometheus's HTTP API is naturally read-only for `/query*` endpoints; still enforce via `@guarded()` that no admin/write endpoints are ever proxied).
4. **Auth**: if Prometheus is in-cluster, reach it the same way the custom K8s MCP reaches the cluster (Connect Gateway / direct in-cluster access) — no separate credential. If it's a hosted/managed Prometheus (e.g. Google Managed Service for Prometheus's query API), use a GCP access token like `gke_remote_mcp` does.
5. **Secrets**: none needed for the in-cluster or GMP case above. A genuinely external, non-GCP Prometheus would need an API key in Secret Manager.
6. **IAM**: `roles/monitoring.viewer` (or equivalent), scoped to the specific Prometheus/GMP project — not a broader monitoring-admin role.
7. **Network access**: in-cluster Prometheus reached via the same Connect Gateway path already proven for the custom K8s MCP; confirm this explicitly rather than assuming it "just works" because K8s MCP traffic does.
8. **Agent Gateway registration**: new tool-spec registration, same `scripts/register_custom_mcp.py` pattern, new spec file.
9. **Tool discovery**: new `PROMETHEUS_TOOLS` frozenset in `agent/mcp_client.py`, same shape as `CUSTOM_K8S_TOOLS`.
10. **Routing rules**: this is exactly the non-cluster-scoped case flagged above. Smallest safe approach: add `"prometheus"` as a new registry entry, and extend `mcp_router`'s Phase 1 selection to check task intent (does the current investigation gap need metrics evidence?) rather than cluster type alone — a small, additive `if primary_gap involves metrics: selected_mcp = "prometheus_mcp"` branch, not a rewrite of the existing cluster-type branch.
11. **Evidence normalization**: `evidence_extractor` needs a Prometheus-shape handler (time series → a small number of summary facts: min/max/avg over the window, not the raw series).
12. **Observability**: new `mcp_source="prometheus_mcp"` label value everywhere `tool_failures`/`mcp_router` metrics are already tracked.
13. **Evaluation dataset**: add 1-2 golden cases that specifically need a metrics lookup to resolve (e.g. "was this OOMKill preceded by a memory trend" — something logs/events alone can't answer).
14. **Load testing**: Prometheus query load is generally cheap; still decide a per-investigation call cap (e.g. max 3 Prometheus calls per run) matching the existing per-tool iteration budget.
15. **Security review**: confirm no PromQL injection risk from LLM-constructed label matchers (parameterize, don't string-concatenate into the query).
16. **Deployment**: new Cloud Run service (if custom server) + Terraform, same CI pattern.
17. **REQUEST_AUTHZ**: unchanged — inherited automatically once routed through Agent Gateway.
18. **CONTENT_AUTHZ / Model Armor**: verify whether Prometheus's response shape (JSON time series) even triggers the platform's MCP-payload sanitization the same way K8s tool responses do — untested, don't assume yes or no.
19. **Timeout/retry**: Prometheus queries can be slow on wide ranges — set an explicit timeout (e.g. 10s) and a single retry, not the client library's default.
20. **Health check**: a lightweight `up` query against Prometheus at container start, mirroring how `get_k8s_clients()` implicitly validates connectivity today.
21. **Enable/disable**: new `enable_prometheus_mcp` Terraform variable, default `false` until live-validated.
22. **Tracing**: new span name (`prometheus_mcp.query_metric`) under the existing tracer.
23. **Live validation**: one real investigation against the real in-cluster Prometheus, not a mocked response, before flipping the enable flag on for good.
24. **Rollback**: `enable_prometheus_mcp = false` + `terraform apply` — confirm the Cloud Run service (if any) has no `prevent_destroy` complication first.

No code was changed to build this example — it is a design walk-through only, per this task's scope (implement only if a tiny generic foundation change is clearly required and safe; the routing refactor identified above is exactly that kind of change, deferred until an actual non-cluster-scoped source is being built, not spent speculatively here).

---

**Related pages:** [MCP Architecture](../architecture/mcp-architecture.md) · [Tool Selection](../architecture/tool-selection.md) · [Updating Existing MCP Tools](update-mcp-tool.md)
