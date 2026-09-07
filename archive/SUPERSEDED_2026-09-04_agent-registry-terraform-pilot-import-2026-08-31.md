> **SUPERSEDED 2026-09-04.** This document's own conclusion ("PARTIAL / BLOCKED-by-design")
> was overtaken by commit `a3f2ba1` (2026-09-04): Terraform now manages Agent Registry
> endpoint registrations directly (`iac/agent/agent_registry.tf`, `agent_registry_mcp.tf`).
> `scripts/register_endpoints.py` was retired to
> `archive/RETIRED_2026-09-04_register_endpoints.py`. See
> [`docs/management/CURRENT-STATE.md`](../docs/management/CURRENT-STATE.md) §7 for current
> status (issue #33, now resolved). Kept below unmodified as historical evidence of the
> pilot-import research that preceded the real fix.

---

# Agent Registry → Terraform pilot-import prep (2026-08-31)

**Status: PARTIAL / BLOCKED-by-design.** Ready-to-import HCL + live evidence are done and
`terraform validate`-clean against the real pinned provider floor. **No `terraform import`,
`terraform apply`, or `gcloud` write was run.** This addresses issue **#33** ("Agent Registry
endpoints have no Terraform representation") from `docs/management/CURRENT-STATE.md` §7, scoped
to workstream 5 of the approved multi-workstream execution plan: pilot-import prep on
`sreagent-t2-demo` only, actual import deferred.

## 1. What "Agent Registry endpoint" actually is (verified, not assumed)

`scripts/register_endpoints.py` creates entries with `gcloud alpha agent-registry services
create ...`. That CLI command maps to a real GCP resource type:
[`google_agent_registry_service`](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/agent_registry_service)
— confirmed directly from the provider's own source, not inferred:

- The resource file exists in **both** `terraform-provider-google` and
  `terraform-provider-google-beta` at tag `v7.43.0` — the exact version this repo's
  `iac/agent/.terraform.lock.hcl` already locks. Checked via
  `GET https://api.github.com/repos/hashicorp/terraform-provider-google/contents/website/docs/r?ref=v7.43.0`.
- It is a **GA resource in the plain `google` provider** — no `google-beta` requirement, no
  `null_resource` + `gcloud` shell-out needed. This corrects the workstream brief's working
  assumption ("likely needs google-beta or a null_resource") — the real answer is simpler.
- Per the provider's `CHANGELOG.md`, `google_agent_registry_service` was added as a
  **New Resource** in **v7.39.0** (2026-06-30) alongside `google_agent_registry_binding` and 3
  new agent-registry data sources (PR
  [#28028](https://github.com/hashicorp/terraform-provider-google/pull/28028)). This repo's
  `iac/agent/versions.tf` floor is `>= 7.40.0, < 8.0.0` (bumped for an unrelated reason — the
  Agent Gateway's `agent_gateway_config` field) — already comfortably above the `7.39.0` floor
  this resource needs. **No provider version bump required for this workstream.**
- Google's own official Terraform module, `GoogleCloudPlatform/terraform-google-agent-registry`,
  confirms the schema shape (`interfaces`, `agent_spec` / `mcp_server_spec` / `endpoint_spec`,
  mutually exclusive) — though that module's own `agent-registry-service` submodule still pins
  `provider = google-nightly` (an older/stricter internal convention, not a hard requirement —
  the GA `google` provider resource works standalone, confirmed by `terraform validate` below).

Resource identity/import format (from the provider docs, GA in the plain `google` provider):
`projects/{{project}}/locations/{{location}}/services/{{service_id}}`.

## 2. The 3 endpoints chosen, and why

All 59 currently-registered services (28 global + 31 regional, live-counted via
`gcloud alpha agent-registry services list`) are either:
- machine-generated from `scripts/googleapis.txt` — standard Google API hostnames
  (`storage.googleapis.com`, `iamcredentials.googleapis.com`, etc.), or
- one hand-registered custom entry: `sre-k8s-mcp` (Custom SRE Kubernetes MCP), whose
  `mcpServerSpec.content.tools` list is actively evolving (21 tool schemas today, growing as
  the K8s MCP server gains tools) — explicitly **not static**, excluded from this pilot.
- the Model Armor regional endpoints (`us-central1-modelarmor-us-central1[-mtls]`), which are
  the subject of the **still-open #30 hostname-mismatch bug** — also excluded, since importing
  a resource under active bugfix would tie the Terraform pilot to a moving target.

The 3 chosen are the most boring, most static entries available — plain `NO_SPEC` /
`JSONRPC` registrations of foundational Google API hostnames, generated identically by the
same script logic, registered on 2026-07-13 and never modified since:

| # | Service ID | Hostname | Location | Why static |
|---|---|---|---|---|
| 1 | `agentregistry` | `agentregistry.googleapis.com` | `global` | The registry's own control-plane API. Rarely-changing GCP API hostname; foundational. |
| 2 | `storage` | `storage.googleapis.com` | `global` | Universal GCS API endpoint. No project-specific config, no bug tickets against it. |
| 3 | `iamcredentials` | `iamcredentials.googleapis.com` | `global` | Universal IAM Credentials API endpoint. Same profile as #2. |

Live evidence — exact `gcloud` commands run and their output (read-only, nothing mutated):

```
$ gcloud alpha agent-registry services describe agentregistry --project=sreagent-t2-demo --location=global --format=json
{
  "createTime": "2026-07-13T01:26:34.588352323Z",
  "displayName": "agentregistry.googleapis.com",
  "endpointSpec": { "type": "NO_SPEC" },
  "interfaces": [ { "protocolBinding": "JSONRPC", "url": "https://agentregistry.googleapis.com" } ],
  "name": "projects/sreagent-t2-demo/locations/global/services/agentregistry",
  "registryResource": "projects/327234009108/locations/global/endpoints/agentregistry-00000000-0000-0000-a17b-12c175c44a59",
  "updateTime": "2026-07-13T01:26:35.446777306Z"
}

$ gcloud alpha agent-registry services describe storage --project=sreagent-t2-demo --location=global --format=json
{
  "createTime": "2026-07-13T01:27:44.695658643Z",
  "displayName": "storage.googleapis.com",
  "endpointSpec": { "type": "NO_SPEC" },
  "interfaces": [ { "protocolBinding": "JSONRPC", "url": "https://storage.googleapis.com" } ],
  "name": "projects/sreagent-t2-demo/locations/global/services/storage",
  "registryResource": "projects/327234009108/locations/global/endpoints/agentregistry-00000000-0000-0000-2fae-d5e2f8da0a87",
  "updateTime": "2026-07-13T01:27:45.167560156Z"
}

$ gcloud alpha agent-registry services describe iamcredentials --project=sreagent-t2-demo --location=global --format=json
{
  "createTime": "2026-07-13T01:27:06.018015556Z",
  "displayName": "iamcredentials.googleapis.com",
  "endpointSpec": { "type": "NO_SPEC" },
  "interfaces": [ { "protocolBinding": "JSONRPC", "url": "https://iamcredentials.googleapis.com" } ],
  "name": "projects/sreagent-t2-demo/locations/global/services/iamcredentials",
  "registryResource": "projects/327234009108/locations/global/endpoints/agentregistry-00000000-0000-0000-b8c2-1b23d7650527",
  "updateTime": "2026-07-13T01:27:07.082229974Z"
}
```

No `create`/`update`/`delete`/`patch` `gcloud` command was ever run — only `describe`/`list`.

## 3. Ready-to-import HCL

This HCL is **not wired into `iac/agent/*.tf`** — it is not part of the live stack CI plans
against, and it is not applied. It's kept here as reviewed, validated reference material for
whenever the import itself is unblocked. (Adding it directly to `iac/agent/` today would make
the next CI `terraform plan` try to *create* these already-existing services and fail with
`ALREADY_EXISTS`, since Terraform state has no record of them — exactly the failure mode
`terraform import` exists to avoid.)

```hcl
resource "google_agent_registry_service" "agentregistry" {
  project      = var.project_a_id
  location     = "global"
  service_id   = "agentregistry"
  display_name = "agentregistry.googleapis.com"

  interfaces {
    url              = "https://agentregistry.googleapis.com"
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

resource "google_agent_registry_service" "storage" {
  project      = var.project_a_id
  location     = "global"
  service_id   = "storage"
  display_name = "storage.googleapis.com"

  interfaces {
    url              = "https://storage.googleapis.com"
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}

resource "google_agent_registry_service" "iamcredentials" {
  project      = var.project_a_id
  location     = "global"
  service_id   = "iamcredentials"
  display_name = "iamcredentials.googleapis.com"

  interfaces {
    url              = "https://iamcredentials.googleapis.com"
    protocol_binding = "JSONRPC"
  }

  endpoint_spec {
    type = "NO_SPEC"
  }
}
```

Field values above are copied verbatim from the live `describe` output in §2 — `display_name`,
`interfaces.url`, `interfaces.protocol_binding`, and `endpoint_spec.type` all match exactly.
`var.project_a_id` is `iac/agent/variables.tf`'s existing project variable (resolves to
`sreagent-t2-demo` for this environment).

### Validation performed (syntax only — no remote state touched)

Run in a throwaway scratch directory (`/tmp/agent-registry-pilot-validate/`, outside the repo,
never committed), mirroring `iac/agent/versions.tf`'s real pins so the check is representative:

```
$ tfenv use 1.4.7
Switching default version to v1.4.7

$ terraform -version
Terraform v1.4.7

$ terraform fmt -check -diff .
(no output — already canonically formatted)

$ terraform init -input=false
Installing hashicorp/google v7.46.0...
Terraform has been successfully initialized!

$ terraform validate
Success! The configuration is valid.
```

Terraform **1.4.7** was used — matching the exact version pinned in
`.github/workflows/claude-merge-gate.yml:179`, `.github/workflows/terraform-apply.yml:65`, and
`.github/workflows/terraform-plan.yml:49` (all confirmed via `grep`, all read `"1.4.7"`), and
matching `iac/agent/versions.tf`'s `required_version = ">= 1.4.7"`. Provider `hashicorp/google
v7.46.0` was resolved, satisfying the repo's pinned floor (`>= 7.40.0, < 8.0.0`) — no version
mismatch caveat needed for either Terraform or the provider.

**What this validation proves:** the HCL is syntactically correct and schema-valid against the
real, currently-pinned provider version. **What it does NOT prove:** that a `terraform plan`
against the real `sreagent-t2-demo` state would show zero diff after import — that can only be
confirmed by actually running the import (see §5, blocked).

## 4. Exact `terraform import` commands that WOULD be run (not executed)

```
terraform import google_agent_registry_service.agentregistry \
  projects/sreagent-t2-demo/locations/global/services/agentregistry

terraform import google_agent_registry_service.storage \
  projects/sreagent-t2-demo/locations/global/services/storage

terraform import google_agent_registry_service.iamcredentials \
  projects/sreagent-t2-demo/locations/global/services/iamcredentials
```

These use the **classic imperative `terraform import <address> <id>` CLI form**, which works
fine on Terraform 1.4.7. The newer declarative `import { ... }` block (visible in a `terraform
plan` diff, PR-reviewable before it runs) requires **Terraform >= 1.5.0** — confirmed directly
from the resource's own provider documentation. None of these commands were run.

## 5. Why actual import is BLOCKED (not just "not yet done")

Two compounding reasons, both real, not hypothetical:

1. **The CI-Terraform-version decision this workstream is scoped to not make.** CI is pinned to
   1.4.7 project-wide (`versions.tf`'s own comment: "lowered 1.9.0 -> 1.4.7 to match company
   Spacelift's pinned version... this repo and sre-agent-app-infra stay deployable on the same
   Terraform and cannot drift apart"). Bumping to >= 1.5.0 is a separate, cross-repo decision
   this workstream is explicitly not authorized to make.
2. **That version gap forces the unsafe import path.** On 1.4.7, importing these 3 resources
   can only be done with the classic imperative `terraform import` command — which mutates
   Terraform state directly, outside any `terraform plan` a reviewer sees before it runs. That
   is exactly the class of action `docs/management/CURRENT-STATE.md` §8 already bans standing:
   *"No local `terraform apply`, ever (2026-08-25 incident: a local apply against the shared
   GCS backend caused real undocumented drift). Every infra change: branch → PR → CI plan →
   merge → CI apply."* `terraform import` carries the same real risk profile as a local apply
   (unreviewed state mutation against the shared GCS backend) and isn't wired into any CI job
   today. Bumping to Terraform >= 1.5.0 would let the import instead go through a normal,
   PR-reviewable `import { }` block that shows up in CI's `terraform plan` before anything
   mutates — the safe path this repo's own standing policy already requires for every other
   infra change.

Net: the fix isn't "run the import command" — it's "decide whether to bump CI's Terraform
floor first," which is out of scope for this workstream by design.

## 6. Confirmation — nothing was imported or applied

- No `terraform import` was run.
- No `terraform apply` or `terraform plan` was run against the real `iac/agent` stack or its
  real GCS backend.
- No `gcloud ... create/update/delete/patch` command was run — only `describe`/`list`
  (read-only) against `sreagent-t2-demo`.
- The only local `gcloud` state change made was `gcloud config set project sreagent-t2-demo`
  (a local CLI config default, not a cloud write) — restored no differently than it would be by
  running any other `gcloud` command against this project.
- The HCL in §3 lives only in this document — it was never added to `iac/agent/*.tf`, so no
  real CI plan/apply run is affected.
