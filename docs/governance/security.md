# Security Operations

> **Implementation Status:** Kubernetes access — IMPLEMENTED, confirmed read-only across 4 independent layers. Model Armor — PARTIALLY IMPLEMENTED: request-side CONTENT_AUTHZ is real and blocking; response-side inspection has a confirmed Google platform gap, compensated for by an unmerged application-level guard (see below).
> **Last Verified:** 2026-09-06 — `iac/agent/model_armor.tf`, `iac/agent/agent_gateway.tf`, `mcp/response_guard.py`, real gateway/Model Armor logs
> **Owner:** SRE Agent platform team.

## Complete IAM permission matrix

### Runtime Agent Identity — Project A (`sreagent-t2-demo`)

| Role | Scope | Why |
|---|---|---|
| `roles/aiplatform.expressUser` | Project | Inference/sessions/memory |
| `roles/aiplatform.user` | Project | Memory Bank generate/retrieve |
| `roles/aiplatform.agentDefaultAccess` | Project | Agent Runtime default access |
| `roles/serviceusage.serviceUsageConsumer` | Project | Quota/API access |
| `roles/browser` | Project | `resourcemanager.projects.get`, Agent Identity prereq |
| `roles/agentregistry.viewer` | Project | Read Agent Registry (MCP-server discovery) |
| `roles/logging.logWriter` | Project | Structured run logs |
| `roles/cloudtrace.agent` | Project | OpenTelemetry traces |
| `roles/monitoring.metricWriter` | Project | Metrics |
| `roles/modelarmor.user` | Project | Agent's own app-layer `_sanitize()` (query/summary text) — **conditional, `count = enable_agent_gateway ? 0 : 1`, NOT granted in the live config** (gateway is on, so this specific env-gated path is dead code today; see the Model Armor section below for the separate, real mechanisms that ARE active) |
| `roles/storage.objectCreator` + `objectViewer` | Evidence bucket (resource-level) | Write/read RCA evidence |
| `roles/storage.objectCreator` + `objectViewer` | Eval bucket (resource-level) | Write/read eval data |
| `roles/storage.objectViewer` | Cluster-config bucket (resource-level) | Read `clusters.json` |
| `roles/run.invoker` | Specific Cloud Run MCP service (resource-level) | Invoke fallback/on-prem MCP — **granted live**; `enable_custom_mcp` defaults `false` in Terraform code but the GitHub Actions repo variable `ENABLE_CUSTOM_MCP=true` drives every real CI apply, so the service is deployed and this grant is live |
| `roles/iap.egressor` | Agent Registry (resource-level, not project) | Gateway egress authorization — **granted live** |

### Google-managed platform service agents (gateway data plane)

Three service agents get `roles/compute.networkAdmin`/`roles/dns.peer`/`roles/dns.admin` on Project A, scoped to programming the gateway's own networking/DNS — not the agent's own identity, gated on `enable_agent_gateway`.

### Cross-project — Project B (`sreagent-demo`, target GKE)

| Role | Why |
|---|---|
| `roles/container.viewer` | Read-only GKE resources for RCA |
| `roles/mcp.toolUser` | Invoke GKE Remote MCP |
| `roles/logging.viewer` | Read GKE workload logs |
| `roles/monitoring.viewer` | Read GKE metrics |

**No create/update/delete/patch/exec-capable role is granted to the agent identity in Project B.** (A separate, disabled-by-default block grants broader admin roles to a CI/CD deployer SA — not the agent's runtime identity, and currently ungranted since `deployer_sa_email=""` live.)

### CI/CD deployer SA — explicitly NOT least-privilege, and that's a documented, accepted tradeoff

Holds `roles/aiplatform.admin`, `roles/storage.admin`, `roles/iap.admin`, `roles/resourcemanager.projectIamAdmin`, etc. — because it provisions infrastructure. Fenced by Workload Identity Federation scoped to one named GitHub repo. This is CI/CD tooling, not the runtime agent's blast radius — don't conflate the two when reasoning about "what can the agent do."

## Kubernetes access is read-only — the full evidence chain

Four independent layers, each confirmed by direct code/config inspection, not by trusting a comment:

1. **Application allowlist**: every tool in `GKE_REMOTE_TOOLS`/`CUSTOM_K8S_TOOLS` is a `list_*`/`get_*`/`describe_*` verb. A hard-coded `BLOCKED_ACTIONS` set (`delete`, `create`, `patch`, `update`, `apply`, `exec`, `port-forward`, `scale`, `rollout`) is checked against every proposed call regardless of allowlist membership.
2. **`@guarded()` decorator** (custom MCP): rate limiting, input validation, namespace-scope enforcement, secret redaction, audit logging — defense-in-depth around the read calls, not itself the source of read-only-ness (there's nothing to block, since no mutating call exists in the underlying code — see #3).
3. **Underlying Kubernetes-client verb usage**: grep of every function in `mcp/tools/*.py` shows only `list_namespaced_*`/`read_namespaced_*`/`list_node`/`read_node` calls. No `create_*`/`patch_*`/`delete_*`/`.exec(`/`port_forward` call exists anywhere in `mcp/`. **This is enforced as a permanent CI regression test** (`mcp/tests/test_no_mutation.py`), not just a one-time review — the build fails if a mutating call is ever introduced.
4. **RBAC ceiling** (where applied — Connect Gateway prototype): the built-in **`view`** ClusterRole, which by design excludes Secrets and cluster-scoped Nodes. Proven with real `Forbidden` responses on `create namespace`/`delete pod`/`get secrets`.

**No secrets tool exists anywhere in the tool set** — confirmed by a dedicated CI test (`test_no_secret_resource_tools_exposed`).

## Least privilege

Every runtime grant above is resource-scoped where the resource type supports it (buckets, the specific Cloud Run service, the Agent Registry) rather than project-wide. See `docs/least-privilege-iam.md` in this repo for the original audit — note it was found to be **slightly out of date** relative to live Terraform (missing `roles/aiplatform.agentDefaultAccess`) — a minor doc-sync gap, not a real permission drift.

## Cluster RBAC

See the `view` ClusterRole finding above — applies to the Connect Gateway prototype path specifically; the live GKE path's RBAC posture is IAM-only (GKE Remote MCP handles Kubernetes-layer authorization internally, no separate RBAC binding was found as a Terraform-managed resource for it — **STATUS: UNKNOWN**, worth confirming directly against the live cluster if this matters for your audit).

## Network controls

Agent-side VPC + Cloud NAT for egress; no customer VPC/PSC network attachment currently needed for live traffic (Google backbone + Agent Gateway's `AGENT_TO_ANYWHERE` path).

### Custom MCP Cloud Run ingress — security decision (2026-09-04)

**Decision**: `iac/agent/cloudrun_mcp.tf`'s `sre-k8s-mcp` service ingress is `INGRESS_TRAFFIC_ALL`, not an internal-only setting.

**Do not describe this service as private or network-internal.** It is network-reachable from the public internet. The control that protects it is IAM: `roles/run.invoker` is granted to exactly one principal (the agent's `AGENT_IDENTITY`, via `google_cloud_run_v2_service_iam_member.runtime_invoke_mcp`) — no `allUsers`/`allAuthenticatedUsers` grant exists. Verified live (2026-09-04):
- Unauthenticated request → 403/404, no content returned.
- Authenticated-but-unauthorized identity (a real GCP identity without `run.invoker` on this specific service, including this project's own Owner-role account) → 401, no content returned.
- Only the authorized agent identity, via Agent Gateway, successfully invokes it.

**Why `ALL` instead of an internal-only ingress**: the prior setting, `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER`, required an Internal HTTP(S) Load Balancer + Serverless NEG that was never built — confirmed live, it made the service unreachable by anyone, including the agent's own authorized traffic (Agent Gateway calls the service's public `.run.app` hostname with a Bearer token, the same pattern Google's own GKE Remote MCP public endpoint uses; it was never going to route through an internal LB without also re-pointing Agent Gateway's target, a larger change). Per Cloud Run's own documentation, ingress and IAM authorization are independent controls — this change affects only network reachability, not who is authorized to invoke the service.

## Encryption / TLS

Standard GCP encryption-at-rest on all storage. TLS to Vertex AI: see the atomic-PATCH mechanism in [Agent Gateway](../architecture/agent-gateway.md) for the one real historical gotcha (a misconfigured binding causing a cert-verify failure, now fixed and documented).

## Secrets

**Confirmed clean — no static credentials committed.** No `google_service_account_key` resource exists anywhere. `.gitignore` explicitly excludes `.env`/`terraform.tfvars`/state files; only `.example` templates are tracked. Org policy (`constraints/iam.disableServiceAccountKeyCreation`) actively blocks static key creation — confirmed by attempting it during Connect Gateway testing and getting a real, exact rejection, not just an assumed policy:
```
FAILED_PRECONDITION: Key creation is not allowed on this service account
```
(`docs/connect-gateway-onprem.md:47`.) **Why this matters**: this proves the block is an enforced org policy the agent's identity model was actually designed around, not merely a convention this team chose to follow — a fallback SA was created instead (`sre-lab-connect-agent@sreagent-t2-demo.iam.gserviceaccount.com`, `roles/gkehub.connect`), and workload identity federation with a private issuer (`--has-private-issuer`) was used in place of a key, exactly as [Agent Identity](../architecture/agent-identity.md) describes for the production path too. Both the GKE and Connect Gateway paths use short-lived, auto-refreshed tokens (ADC access tokens, Cloud Run identity tokens, `gke-gcloud-auth-plugin`-minted ~1hr tokens) — nothing long-lived is stored anywhere.

## Audit logs

Standard Cloud Audit Logs cover IAM-relevant API calls. **One confirmed, still-open gap**: `DATA_READ` audit logging is off by default for Connect Gateway (`connectgateway.googleapis.com`) — successful reads through that path leave no audit trail today, only denied/blocked mutation attempts do. This is a project-wide audit-config decision that hasn't been made — see [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md).

## Prompt injection

Kubernetes tool output (logs, event messages, resource names) is untrusted input that flows into LLM context. Mitigations: the tool allowlist + blocklist prevent any injected instruction from causing a real mutating action (there's no mutating tool to invoke even if the model were "convinced" to try). Model Armor's PI/jailbreak filter DOES run today on GKE Remote MCP requests and the agent's own Gemini calls (floor settings, detect-only) and on custom-MCP requests (CONTENT_AUTHZ, request-side, blocking) — but **no currently-merged mechanism blocks a malicious/injected payload delivered via a tool RESPONSE** (see [Model Armor](#model-armor--three-distinct-mechanisms-each-with-different-enforcement-corrected-2026-09-06) above for the full picture and the unmerged guard that closes this for the custom MCP specifically). Treat the tool-layer allowlist as the primary defense against a successful injection causing real damage; Model Armor today is real but partial content-safety coverage, not a complete backstop.

## Malicious tool output / data exfiltration

Evidence is redacted (emails, IPs, bearer tokens, secret-shaped JSON fields) before it's written to storage or shown to the model — see [Evidence Architecture](../architecture/evidence-architecture.md#redaction). There's no mechanism preventing a compromised/malicious MCP server from returning misleading data that influences the RCA's conclusion — this is a trust boundary on the MCP source itself, not something the agent's own code can fully defend against; treat "is this MCP source trustworthy" as a prerequisite, not something this system verifies for you.

## Evidence security / memory poisoning

See [Evidence Architecture](../architecture/evidence-architecture.md) and [Memory](../architecture/memory.md) — the memory write-gate (`confidence_band=="auto"` only) is the current poisoning mitigation; a full human-approval review workflow for memory content is **PLANNED**, not built.

## Dependency / Terraform / CI-CD supply-chain security

Not independently audited in this pass — **STATUS: UNKNOWN**, recommend a standard SCA (software composition analysis) pass on `agent/requirements.txt` / `mcp/requirements.txt` and a review of the GitHub Actions workflow's third-party action pins if that hasn't already happened elsewhere.

---

## Model Armor — three distinct mechanisms, each with different enforcement (corrected 2026-09-06)

The August 2026 finding below this heading (that Model Armor provided no enforcement anywhere) is **obsolete**. A later investigation found the actual working wiring (the original attempt used the wrong service hostname). Today there are **three separate Model Armor mechanisms** in this deployment. Do not conflate them — they have different scope and different enforcement modes:

### 1. Gateway CONTENT_AUTHZ — real, request-side, blocking

`iac/agent/model_armor.tf`'s `google_network_services_authz_extension.model_armor` + `google_network_security_authz_policy.model_armor`, targeting the Agent Gateway, using service `modelarmor.us-central1.rep.googleapis.com` (the **regional** endpoint — the bare `modelarmor.googleapis.com` used in the original 2026-08-08 attempt is what actually failed; the regional hostname works). Both `sre_agent_request` and `sre_agent_response` templates are configured with `template_metadata.enforcement_type = "INSPECT_AND_BLOCK"`.

**Live-verified (2026-09-05/06):** a real MCP `tools/call` REQUEST body is genuinely inspected — gateway logs show `serviceExtensionInfo.perProcessingRequestInfo` with `REQUEST_BODY: CONTENT_MODIFIED` via the `sre-agent-model-armor-authz` extension. This is real content inspection, not a passthrough `ALLOWED`.

**Confirmed platform limitation — RESPONSE_BODY never fires.** The identical real traffic never shows a `RESPONSE_BODY` event, with or without a `json_response=True` fix attempted and reverted on the MCP server (tested live, no measurable change). Root cause, per Google's own docs (`docs.cloud.google.com/model-armor/model-armor-agent-gateway-integration`): "Streamable HTTP/SSE for MCP" is explicitly listed as excluded from gateway sanitization. Since Streamable HTTP is the MCP spec's own current transport, and no non-streaming alternative exists for a remote server, this is a genuine, unfixable-by-us Google platform limitation — **CONTENT_AUTHZ never protects an MCP tool response, on either the custom Cloud Run MCP or GKE Remote MCP.** A malicious payload placed in a Kubernetes resource (confirmed with Google's own guaranteed-detection Safe Browsing test URL) reaches the agent's evidence completely unblocked via this path.

### 2. Model Armor floor settings — real, detection-only, never blocking

`google_model_armor_floorsetting.mcp` (same file), `integrated_services = ["GOOGLE_MCP_SERVER", "AI_PLATFORM"]`, `inspect_only = true` on both the `google_mcp_server_floor_setting` and `ai_platform_floor_setting` blocks. This is a **separate resource from #1** — a project-wide floor, not the gateway extension.

Confirmed live: this genuinely inspects GKE Remote MCP `tools/call` requests (a real base64 tool-call payload was observed being sanitized) and the agent's own Gemini prompt/response calls. **It does not, and structurally cannot, protect arbitrary custom-MCP response content** — `GOOGLE_MCP_SERVER` only covers Google-managed MCP servers, and `inspect_only=true` means even a real `MATCH_FOUND` never blocks anything, only logs it. Do not describe floor settings as protecting custom MCP tool responses in any document — that is factually wrong.

### 3. Application-level custom-MCP response guard — NEW, unmerged POC, closes the response-side gap for the custom MCP specifically

Branch `poc/mcp-response-guard-model-armor` (not on `main`, not deployed to production, pending review): `mcp/response_guard.py`, a FastMCP middleware (`on_call_tool` hook, registered once via `mcp.add_middleware()`) that calls `sanitize_model_response` **directly** against the `sre_agent_response` template for every custom-MCP tool response, before it reaches the Streamable HTTP transport.

**Live-proven (2026-09-06):** a pod whose container command embedded Google's guaranteed-detection Safe Browsing URL was described via `describe_pod_detail`; the response was genuinely blocked (`MODEL_ARMOR_SANITIZATION_VERDICT_BLOCK`, `filterMatchState: MATCH_FOUND`, `malicious_uris` filter matched at the exact byte offset), the agent received a controlled MCP error instead of the malicious content, and did not fabricate an RCA. Real `sanitize_operations` log entry exists under `template_id: sre-agent-response-guard` — evidence CONTENT_AUTHZ can never produce for the response side.

**Open decision, not yet made:** this guard fails **open** (delivers the real tool result) if the Model Armor API itself errors/is unreachable — mirroring the existing (dead-code) `agent/main.py::_sanitize()` pattern, but that precedent is not automatic approval for a production port. Whether MCP response enforcement should fail open (with alerting) or fail closed (return a controlled tool error) is an explicit open decision for whoever reviews this branch — do not treat either choice as settled.

**Net effect on Kubernetes tool-output prompt injection (see below):** CONTENT_AUTHZ + floor settings do not close this gap on their own; the response guard (once/if merged) is what actually would.

### Historical note (accurate as of 2026-08-08, since superseded)

The original finding — Model Armor disabled everywhere, a `400: unsupported Google API for AuthzExtension: modelarmor.googleapis.com` API rejection, and unresolved floor-setting REST calls (`500`/`403`) — is preserved in `archive/RESOLVED_2026-08-08_MODEL_ARMOR_CONTENT_AUTHZ_TEST.md`. It was correct when written; the blocker was the wrong hostname (bare `modelarmor.googleapis.com` instead of the regional `modelarmor.{region}.rep.googleapis.com`), not a hard platform limitation as originally concluded.

## IAP fail-open — fix validated, not yet merged

`authz_fail_open = true` is still the default on `main` (`iac/agent/variables.tf`) — an IAP/REQUEST_AUTHZ extension outage degrades to "unauthorized egress allowed" for its duration, rather than the agent stopping. This is a deliberate rollout-safety tradeoff per the variable's own description.

**A validated fix exists, unmerged**: branch `fix/content-authz-json-response-and-fail-closed` flips the default to `false`. Live-tested (2026-09-06): revoking the agent's `roles/iap.egressor` binding and retrying immediately produced a real `403 Forbidden — "Egress request is not authorized"` in 0.17s, no fabricated RCA, no silent bypass; the binding was restored and a post-restore call succeeded normally. What was **not** independently tested: the extension-*unreachable* case itself (distinct from a revoked authorization) — IAP is a Google-managed, always-available service with no safe way to simulate its own outage. Do not treat the extension-unreachable fail-closed path as live-proven; treat it as configured-correctly-per-the-provider's-documented-semantics-and-state-confirmed.

---

**Related pages:** [Agent Identity](../architecture/agent-identity.md) · [Agent Gateway](../architecture/agent-gateway.md) · [AI Governance](ai-governance.md)
