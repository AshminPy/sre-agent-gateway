# Least-privilege IAM — audit trail

Every identity in this project gets only the roles it needs, scoped to the
narrowest resource possible. **No `roles/editor`, no `roles/owner`, no org-level
roles.** Resource-level (bucket / Cloud Run service / IAP registry) bindings are
preferred over project-level wherever the API supports them.

Legend: **⚑ broad-by-necessity** = the narrowest predefined role that still
grants a needed capability; a custom role could tighten it further.

---

## 1. Runtime Agent Identity (Project A)

The reasoning engine runs as a SPIFFE Agent Identity (no service account). These
are the only roles it holds. Defined in `iac/agent/iam.tf`.

| Role | Scope | Why |
|---|---|---|
| `roles/aiplatform.expressUser` | Project A | Model inference, sessions, memory (Agent Identity baseline) |
| `roles/aiplatform.user` | Project A | Memory Bank generate/retrieve |
| `roles/aiplatform.agentDefaultAccess` | Project A | Agent Runtime default access (live in `iac/agent/iam.tf:15` — added here 2026-08-09, was previously live but missing from this audit page) |
| `roles/serviceusage.serviceUsageConsumer` | Project A | API/quota access |
| `roles/browser` | Project A | `resourcemanager.projects.get` (documented Agent Identity prerequisite) |
| `roles/agentregistry.viewer` | Project A | Read Agent Registry (MCP-server discovery) |
| `roles/logging.logWriter` | Project A | Emit structured run logs |
| `roles/cloudtrace.agent` | Project A | OpenTelemetry traces |
| `roles/monitoring.metricWriter` | Project A | Metrics |
| `roles/modelarmor.user` | Project A | App-layer prompt/response sanitize |
| `roles/storage.objectCreator` + `objectViewer` | **evidence bucket only** | Write/read RCA evidence |
| `roles/storage.objectCreator` + `objectViewer` | **eval bucket only** | Write/read eval data |
| `roles/storage.objectViewer` | **cluster-config bucket only** | Read clusters.json |
| `roles/run.invoker` | **custom MCP service only** | Call the fallback MCP (only if `enable_custom_mcp`) |
| `roles/iap.egressor` | **Agent Registry (IAP) resource** | Gateway egress to registered destinations (only if `enable_agent_gateway`) |

Note the storage and Cloud Run grants are **resource-level**, not project-wide;
`iap.egressor` is granted on the Agent Registry IAP resource, not the project.

## 2. Runtime Agent Identity — cross-project (Project B)

Four read-only predefined roles plus one narrow custom role, so the agent can
investigate GKE incidents in Project B. Defined in
`iac/gke-access/crossproject_iam.tf`.

| Role | Scope | Why |
|---|---|---|
| `roles/container.viewer` | Project B | Read-only GKE resources for RCA |
| `roles/mcp.toolUser` | Project B | Invoke GKE Remote MCP `tools/call` |
| `roles/logging.viewer` | Project B | Read GKE workload logs |
| `roles/monitoring.viewer` | Project B | Read GKE metrics |
| `podLogReader` (custom) | Project B | `container.pods.getLogs` only -- `container.viewer` does not include it, and `roles/container.developer` (the narrowest predefined role that does) also grants broad write access. See issue #92. |

No write access, no broad project roles.

## 3. CI/CD deployer service account

One WIF service account (`iac/agent/oidc.tf`) impersonated by GitHub Actions —
no long-lived keys. It can be created two ways, both producing the identical
identity and role set: managed in-stack (`create_wif = true`, a self-contained
local apply) or seeded out-of-band by `scripts/bootstrap_wif.sh`
(`create_wif = false`, the git-driven flow — CI authenticates *as* this SA, so
it cannot create it). The role list below is the single source of truth; the
bootstrap script mirrors it.

**Project A** (`deployer_a_roles`): `serviceusage.serviceUsageAdmin`,
`iam.serviceAccountAdmin`, `iam.serviceAccountUser`, `iam.workloadIdentityPoolAdmin`,
`resourcemanager.projectIamAdmin` ⚑, `compute.networkAdmin`, `compute.securityAdmin`
(firewall rules — `networkAdmin` lacks `compute.firewalls.create`), `aiplatform.admin` ⚑,
`networkservices.editor`, `networksecurity.editor`, `agentregistry.viewer`
(list the registry the gateway references at create time — the old `editor` deployer had
this implicitly), `modelarmor.admin`, `run.admin`,
`artifactregistry.admin`, `monitoring.editor`, `logging.configWriter`, `storage.admin` ⚑,
`iap.admin` ⚑ (the only predefined role granting `iap.webServiceVersions.setIamPolicy`, needed
to set the agent-registry egress binding). Plus `storage.objectAdmin` on the **tfstate bucket only**.

The CI deployer may hold admin-tier roles by design (it creates infrastructure); its blast
radius is fenced by Workload Identity Federation to a single named GitHub repo
(`oidc.tf` `attribute_condition`). `resourcemanager.projectIamAdmin`, `aiplatform.admin`, and
`storage.admin` are the narrowest predefined roles that create the relevant resources.

**Project B** (`iac/gke-access/crossproject_iam.tf`, only when `deployer_sa_email`
is set): `container.admin` ⚑, `compute.networkAdmin`, `serviceusage.serviceUsageAdmin`,
`resourcemanager.projectIamAdmin` ⚑, `iam.roleAdmin` ⚑ (needed to manage the
`podLogReader` custom role above -- `projectIamAdmin` does not include
`iam.roles.create`). Nothing else — no storage roles in B.

The ⚑ roles are the narrowest predefined roles that create/manage the relevant
resources. Tighten with custom roles if your org requires it.

## 4. Google-managed platform service agents (Project A)

Only granted when `enable_agent_gateway = true`. These are Google-owned
identities the gateway data plane needs. Defined in `iac/agent/iam.tf`.

| Identity | Role | Why |
|---|---|---|
| `gcp-sa-aiplatform` | `aiplatform.serviceAgent` | Required to create an Agent Engine |
| `gcp-sa-aiplatform` | `compute.networkAdmin`, `dns.peer` | Program gateway PSC networking/DNS |
| `gcp-sa-aiplatform-re` | `compute.networkAdmin`, `dns.peer` | Same, for the reasoning-engine service agent |
| `gcp-sa-agentgateway` (P4SA) | `dns.admin` | Program DNS for the data path |
| gateway service-extensions SA (read from the gateway card, never hardcoded) | `modelarmor.calloutUser`, `modelarmor.user`, `serviceusage.serviceUsageConsumer` | Invoke the Model Armor authz extension |

## 5. Custom MCP runtime SA (optional)

When `enable_custom_mcp = true`, the Cloud Run MCP runs as a dedicated SA
(`iac/agent/cloudrun_mcp.tf`) that is granted only `roles/container.viewer` on
**Project B** (`iac/gke-access/crossproject_iam.tf`). No roles in Project A
beyond what Cloud Run needs to run it.

---

## Verify

The repo ships an IAM invariant check (see `docs/` verification notes / CI):
no `roles/editor`, `roles/owner`, or `roles/*.admin` on any **runtime** identity;
the cross-project set is exactly the four roles above.
