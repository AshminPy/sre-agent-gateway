# Security Operations

> **Implementation Status:** Kubernetes access — IMPLEMENTED, confirmed read-only across 4 independent layers. Model Armor — IMPLEMENTED and live on both the Agent Gateway CONTENT_AUTHZ path and the custom MCP's application-level response guard (see below).
> **Last Verified:** 2026-09-06
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
| `roles/modelarmor.user` | Project | App-layer sanitize — **conditional, `count = enable_agent_gateway ? 0 : 1`, NOT granted in the live config** (gateway is on) |
| `roles/storage.objectCreator` + `objectViewer` | Evidence bucket (resource-level) | Write/read RCA evidence |
| `roles/storage.objectCreator` + `objectViewer` | Eval bucket (resource-level) | Write/read eval data |
| `roles/storage.objectViewer` | Cluster-config bucket (resource-level) | Read `clusters.json` |
| `roles/run.invoker` | Specific Cloud Run MCP service (resource-level) | Invoke fallback MCP — **conditional, not granted live** (`enable_custom_mcp=false`) |
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

Kubernetes tool output (logs, event messages, resource names) is untrusted input that flows into LLM context. Mitigations: the tool allowlist + blocklist prevent any injected instruction from causing a real mutating action (there's no mutating tool to invoke even if the model were "convinced" to try); Model Armor's PI/jailbreak filter is now live on two paths — the Agent Gateway's `CONTENT_AUTHZ` extension (inspects gateway-routed traffic) and the custom MCP's application-level response guard (`mcp/response_guard.py`, inspects custom-MCP tool responses directly) — see below for the current state of each.

## Malicious tool output / data exfiltration

Evidence is redacted (emails, IPs, bearer tokens, secret-shaped JSON fields) before it's written to storage or shown to the model — see [Evidence Architecture](../architecture/evidence-architecture.md#redaction). There's no mechanism preventing a compromised/malicious MCP server from returning misleading data that influences the RCA's conclusion — this is a trust boundary on the MCP source itself, not something the agent's own code can fully defend against; treat "is this MCP source trustworthy" as a prerequisite, not something this system verifies for you.

## Evidence security / memory poisoning

See [Evidence Architecture](../architecture/evidence-architecture.md) and [Memory](../architecture/memory.md) — the memory write-gate (`confidence_band=="auto"` only) is the current poisoning mitigation; a full human-approval review workflow for memory content is **PLANNED**, not built.

## Dependency / Terraform / CI-CD supply-chain security

Not independently audited in this pass — **STATUS: UNKNOWN**, recommend a standard SCA (software composition analysis) pass on `agent/requirements.txt` / `mcp/requirements.txt` and a review of the GitHub Actions workflow's third-party action pins if that hasn't already happened elsewhere.

---

## Model Armor — now live on both inspection paths (updated 2026-09-06)

**This section previously reported Model Armor as effectively inactive. That finding is now stale — both inspection paths are live.** Model Armor's Terraform templates (`iac/agent/model_armor.tf`) define two templates (request/response), filtering PI/jailbreak, malicious URI, RAI, and Sensitive Data Protection.

- **Gateway-layer (`CONTENT_AUTHZ`)**: `google_network_services_authz_extension.model_armor` (`iac/agent/agent_gateway.tf`, policy_profile `CONTENT_AUTHZ`) is defined and wired, referencing the same request/response templates. The earlier `Error 400: unsupported Google API for AuthzExtension: modelarmor.googleapis.com` result was real, but it turned out to be a config mistake, not a hard platform blocker: that test used the generic, non-regional `modelarmor.googleapis.com` service string; re-checked 2026-09-05 against the actual installed provider schema (`google-beta 7.43.0`), the regional form `modelarmor.{region}.rep.googleapis.com` — the same format `agent/main.py`'s app-level client already used — is a documented, supported `CONTENT_AUTHZ` service value (see `iac/agent/agent_gateway.tf`'s own comment). Note a separate, still-real platform limitation below: this gateway path never inspects MCP tool-call *response bodies* on the Streamable HTTP transport Google uses for MCP — see "Known, permanent platform gap" below.
- **Custom MCP application-level guard**: `mcp/response_guard.py` is a FastMCP `on_call_tool` middleware, registered once in `mcp/server.py`, that calls Model Armor's `sanitize_model_response` directly on every custom-MCP tool response before it reaches the transport. This is live and deployed. It exists specifically because the gateway-layer `CONTENT_AUTHZ` extension cannot inspect MCP response bodies (see below) — it is the compensating control for that specific gap on the custom MCP path.
- **Floor settings** (`iac/agent/model_armor.tf`'s `google_model_armor_floorsetting`, `enable_floor_setting_enforcement = true`): a separate, always-on mechanism, live since 2026-08-25/26, with `inspect_only = true` (detection/logging, not blocking) at HIGH confidence for malicious-URI detection.

### Known, permanent platform gap (not fixed by the above)

Google's Agent Gateway `CONTENT_AUTHZ` extension never inspects MCP `tools/call` **response** bodies when using the Streamable HTTP/SSE transport MCP uses — confirmed directly (no `RESPONSE_BODY` `serviceExtensionInfo` event appears on real traffic, checked 2026-09-05 and 2026-09-06) and matches Google's own documented exclusion for this transport. **This is an accepted, permanent platform limitation, not a bug being worked** — `mcp/response_guard.py` (above) is the compensating control for the custom MCP path specifically; it does not mean the platform-level gap itself was fixed, and GKE Remote MCP has no equivalent compensating control today.

### Response-guard fail-open behavior and its alert

`mcp/response_guard.py` blocks on a real Model Armor `MATCH_FOUND` result on a tool response. It intentionally **fails open only when the Model Armor API call itself errors** (not on a real detection) — a deliberate, documented availability tradeoff. That fail-open condition has a real Cloud Monitoring alert, `mcp_model_armor_fail_open` (`google_logging_metric` + `google_monitoring_alert_policy`, `iac/agent/monitoring.tf`) — it exists and has never fired (no false detections, no API failures observed as of this writing).

**Update this section again if Google's platform-level MCP response-body exclusion changes** — that would affect GKE Remote MCP too, which currently has no equivalent to `mcp/response_guard.py`.

## IAP fail-open — resolved, now fail-closed (updated 2026-09-06)

**This section previously described `authz_fail_open = true` as the live, enforcing value. That is stale.** The default was changed from `true` to `false` on 2026-09-05 (PR #249, merged) — `iac/agent/variables.tf:169`. Confirmed live in Terraform state: `fail_open = false` on both the `iap` and `model_armor` authz extensions. An IAP (or Model Armor authz) outage now fails **closed** — the agent stops rather than allowing unauthorized egress through. The change was made specifically because normal REQUEST_AUTHZ/IAP enforcement was already proven safe (real revoke/retry test, no bypass, no stale enforcement), leaving the extension-unreachable fail-open behavior as the one remaining silently-permissive gap; it no longer needs a separate ADR/sign-off recommendation since the fail-open posture itself has been removed.

---

**Related pages:** [Agent Identity](../architecture/agent-identity.md) · [Agent Gateway](../architecture/agent-gateway.md) · [AI Governance](ai-governance.md)
