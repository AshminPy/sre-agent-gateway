# Porting this workflow to a work non-prod environment

This workflow was built and validated in the personal `sre-agent-gateway` repo against
local `kind` clusters (`sre-lab`, `sre-lab-2`) standing in for on-prem. This document says
exactly what carries over to a real work environment, and what must never be copied.

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
