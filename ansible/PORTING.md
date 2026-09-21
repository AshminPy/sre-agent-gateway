# Porting this workflow to a work non-prod environment

> **For the full step-by-step of what to actually run** (inventory, `onboard.yml`,
> `verify.yml`, the Terraform wiring step, a real agent test, cleanup), see
> [`docs/runbooks/add-onprem-cluster.md`](../docs/runbooks/add-onprem-cluster.md).
> This document covers what changes between environments, not the commands themselves.

This workflow was built and validated in the personal `sre-agent-gateway` repo against
local `kind` clusters (`sre-lab`, `sre-lab-2`) standing in for on-prem, including a full
real Agent Engine investigation (2026-09-21, run `run_20260921_211936_nbzv`, CONFIRMED
root cause) — not just Fleet/RBAC plumbing. This document says exactly what carries over
to a real work environment, and what must never be copied.

## What is portable

- `ansible/roles/onprem_cluster_onboarding/` -- the entire role. No personal project IDs,
  usernames, kind cluster names, personal filesystem paths, or personal service-account
  names appear anywhere in it (verified by grep as part of `tasks.md` Phase 1). Every
  cluster-specific value is read from `cluster.*` (inventory) or `runtime_identity`
  (inventory-level var), never hardcoded.
- `ansible/playbooks/*.yml` -- `onboard.yml`, `verify.yml`, `cleanup.yml`, and the shared
  `tasks/onboard_one_cluster.yml` include. Same rule: no personal values.
- `Makefile`'s `onprem-onboard-kind`/`onprem-check-kind`/`onprem-cleanup-kind` targets are
  portable in shape (they just run `ansible-playbook` against whatever inventory
  `ansible.cfg`/`-i` points at) -- only `kind-onprem-up`/`kind-onprem-down` are
  kind-specific and have no equivalent at work (a real cluster isn't created/destroyed by
  this workflow).
- `ansible/requirements.txt` (the `jmespath` Python dependency).

## What is NOT portable -- personal only

- `ansible/inventories/kind/` in its entirety: `hosts.yml`, `group_vars/all.yml`
  (`sre-lab`/`sre-lab-2` names, `sreagent-t2-demo` project ID, the personal
  `sre-k8s-mcp-runtime@sreagent-t2-demo.iam.gserviceaccount.com` runtime identity).
- Do NOT add work credentials, project IDs, cluster names, or any work-specific
  configuration to this personal repository -- consistent with this repo's existing
  `PORTING_BRIEF_TO_APP_INFRA.md`, which already excludes on-prem cluster onboarding from
  the company repo for the same reason, in the other direction.

## What a work inventory needs (`ansible/inventories/work-nonprod/`, created separately, in
the work repo -- NOT in this personal repo)

```yaml
external_clusters:
  - name: <work-cluster-name>
    kube_context: <the operator's own kubeconfig context for that cluster>
    fleet_project_id: <work GCP project that hosts the Fleet>
    fleet_membership: <defaults to name if omitted>
    environment: nonprod
    rbac_role: clusterrole/view
    grant_node_read: true   # or false, if the work MCP tool surface never calls list_node/read_node
runtime_identity: <the work SRE Agent runtime's own service-account email, IAM-granted
                    roles/gkehub.gatewayReader + roles/gkehub.viewer on the Fleet host project>
```

## Required GCP IAM

- **Registering identity** (the human or automation running the playbook):
  `roles/gkehub.admin` on the Fleet host project. Verified 2026-09-21 against
  `docs.cloud.google.com/kubernetes-engine/fleet-management/docs/before-you-begin`.
- **Runtime identity** (the SRE Agent's own service account, at investigation time):
  `roles/gkehub.gatewayReader` (read-only Connect Gateway access) +
  `roles/gkehub.viewer` (kubeconfig retrieval via `get-credentials`). Never
  `roles/gkehub.gatewayAdmin` -- that role adds `gateway.stream` (exec/attach/port-forward),
  which this workflow's whole design exists to avoid granting to a runtime identity.
  **Confirmed live, 2026-09-21**: the actual production MCP code
  (`mcp/server.py`) only needs `gatewayReader` -- it calls the Connect Gateway REST
  API directly with an ADC bearer token, never `gcloud ... get-credentials`. `viewer`
  is required only by Ansible's own verification method (`get-credentials` + `kubectl`)
  and by operator debugging via the CLI. If your work environment's IAM review is
  stricter about granting `viewer`, it's safe to omit for production correctness --
  only Ansible's `verify.yml` step (not the agent) would lose the ability to
  self-check via that specific mechanism.

## Required local/runner tools

- `gcloud` (authenticated; this session used SDK 573.0.0), `kubectl`, `ansible-core` +
  the `jmespath` Python package (`pip install -r ansible/requirements.txt`).
- `kind` and `docker` are ONLY needed for the personal validation lab -- not required to
  run this workflow against a real cluster.

## Required cluster permissions

- The registering identity needs `cluster-admin` on the target cluster for the duration
  of registration (Google's own documented prerequisite -- not something this workflow can
  reduce). This is a temporary, onboarding-time privilege, never granted to the runtime
  identity.
- The target cluster's API server must serve `/.well-known/openid-configuration` and
  `/openid/v1/jwks` (checked by this role's preflight) -- required for keyless
  `--has-private-issuer` registration. If a work cluster cannot serve these (e.g. a very
  old Kubernetes version, or an API server not reachable from wherever `gcloud`/Ansible
  runs), this workflow will refuse to onboard it rather than falling back to a
  service-account key -- that is a deliberate limitation, not a bug to route around.

## Network requirements

- Outbound reachability from the target cluster's own pods (the Connect Agent) to
  `googleapis.com` and `gkeconnect.googleapis.com`.
- Outbound reachability from wherever `gcloud`/`kubectl`/Ansible run (the operator's
  laptop, or later a private runner) to the target cluster's Kubernetes API server AND to
  Google's APIs. Over a work VPN, this means the VPN must route to both the internal
  cluster network and the public internet (for Google's APIs) at the same time.

## A real gotcha to check for in the target Terraform stack

This repo's own `iac/agent` had (and still has, unrelated to this workflow) a
pre-existing gap: its CI (`terraform-apply.yml`) passes several vars at apply time
that are not in the committed `terraform.tfvars` (a documented issue, see that
workflow's own issue #116 comment). A plain local `terraform plan` without them shows
unrelated resources being destroyed. **Before wiring this workflow's Terraform step
into a work repo, check whether the same pattern exists there** -- compare the
target stack's CI apply command against its committed `.tfvars` file for any var
that's only ever set by CI. If so, get the real live values first (e.g.
`gcloud run services describe ... --format=...` for an image tag) and pass them
explicitly on every local plan, or the plan will look far scarier than the actual
on-prem change warrants.

## Running from a laptop over VPN today

1. Connect to the VPN that reaches the target non-prod cluster.
2. Ensure the operator's own `kubeconfig` has a context for that cluster (however the work
   environment normally provides one -- this workflow does not create kubeconfig entries).
3. `gcloud auth login` (or confirm an existing active session) with an identity holding
   `roles/gkehub.admin` on the Fleet host project.
4. `pip install -r ansible/requirements.txt` once.
5. `ansible-playbook -i inventories/work-nonprod/hosts.yml playbooks/onboard.yml`.

## Running from a Spacelift/private runner later, without redesign

The role and playbooks make no assumption about *where* they run -- only that the runner
has network reachability to the target cluster and to Google's APIs, plus `gcloud`/`kubectl`
configured with the same authorization the runner is granted (e.g. via workload identity
federation for the runner itself, not a stored key). Moving from a laptop to a runner is an
inventory + execution-environment change (where `ansible-playbook` runs, and how the runner
authenticates to GCP/the cluster), not a role or playbook change. This is exactly the
portability property required by the original OpenSpec proposal.
