## Purpose

Defines the testable requirements for a reusable, idempotent Ansible workflow that onboards external/non-GKE Kubernetes clusters into the Fleet → Connect Gateway → RBAC → SRE Agent path, validated against local kind clusters (`sre-lab`, `sre-lab-2`) before any use against a real work cluster.

## ADDED Requirements

### Requirement: Idempotent per-cluster onboarding
Running the onboarding playbook twice against an already-onboarded cluster SHALL NOT create duplicate Fleet memberships, duplicate RBAC objects, or broaden privileges, and SHALL report the second run as mostly unchanged.

#### Scenario: Second run is a no-op
- **WHEN** the onboarding playbook is run a second time against a cluster it already onboarded successfully
- **THEN** no new Fleet membership, ClusterRole, or ClusterRoleBinding is created, and the run's task results are predominantly `ok` rather than `changed`

#### Scenario: Partially completed prior onboarding resumes correctly
- **WHEN** a cluster has a Fleet membership already registered but RBAC was never applied (a simulated partial-failure state)
- **THEN** the playbook detects the existing membership, skips re-registration, and proceeds to apply only the missing RBAC step

### Requirement: Fleet tier safety
The workflow SHALL verify, after registration, that the resulting Fleet membership tier is `STANDARD`, and SHALL fail the run for that cluster if it is not, before any RBAC step runs.

#### Scenario: Registration produces the wrong tier
- **WHEN** a cluster is registered and `gcloud container fleet memberships describe` reports a tier other than `STANDARD`
- **THEN** the playbook fails that cluster's run with an explicit tier-mismatch error, and does not proceed to grant RBAC

### Requirement: Multi-cluster onboarding in one invocation
The workflow SHALL onboard one or many clusters from a single playbook invocation, driven entirely by inventory data, with no per-cluster playbook duplication, and a failure on one cluster SHALL NOT affect another cluster's onboarding.

#### Scenario: Two clusters onboarded together
- **WHEN** the playbook runs against an inventory listing `sre-lab` and `sre-lab-2`
- **THEN** both clusters are onboarded using the same role, and per-cluster evidence is reported separately for each

#### Scenario: One cluster's failure does not affect another
- **WHEN** `sre-lab`'s onboarding is forced to fail (e.g. unreachable kube-context) while `sre-lab-2` is healthy
- **THEN** `sre-lab-2` onboards successfully and its Fleet membership/RBAC state is unaffected by `sre-lab`'s failure

#### Scenario: Aggregated failure is never hidden as success
- **WHEN** one cluster in a multi-cluster run fails while others succeed
- **THEN** the run reports an explicit per-cluster PASS/FAIL result for every cluster, and the playbook process itself exits non-zero — achieved via an explicit per-cluster `block`/`rescue` and result aggregation, not `ignore_errors: true` or `max_fail_percentage`, neither of which appears anywhere in the implementation

### Requirement: Least-privilege runtime RBAC, including node-read parity
The workflow SHALL grant the runtime investigation identity only `get`/`list`/`watch`-equivalent read access derived from the actual MCP tool call inventory, including cluster-scoped `nodes`, and SHALL NOT grant `create`, `update`, `patch`, `delete`, or `cluster-admin` to that identity.

#### Scenario: Allowed read operations succeed
- **WHEN** the onboarded identity runs `kubectl get pods -A`, `get deployments -A`, and `get events -A` through Connect Gateway
- **THEN** all three succeed

#### Scenario: Node read now succeeds (parity fix)
- **WHEN** the onboarded identity runs `kubectl get nodes` through Connect Gateway
- **THEN** it succeeds — correcting the previously-documented `Forbidden` result under `view` alone

#### Scenario: Mutation and secret access remain denied
- **WHEN** the onboarded identity attempts `kubectl create namespace ...`, `kubectl delete pod ...`, or `kubectl get secrets -A`
- **THEN** all three are denied

### Requirement: No long-lived credentials distributed to the runtime
The workflow SHALL treat the operator's kubeconfig/ADC as an onboarding-time input only, and SHALL NOT write kubeconfig, service-account keys, tokens, or certificates into Git, Terraform, the Ansible inventory, the SRE Agent, the Cloud Run MCP, or CI.

#### Scenario: Repository scan finds no committed credentials
- **WHEN** the onboarding role and inventory files are scanned after a run
- **THEN** no kubeconfig, private key, token, or certificate material is present in any tracked file

### Requirement: Disposable kind validation lab, separated from reusable onboarding
Kind cluster lifecycle management SHALL be implemented outside the reusable onboarding role, so the same role can run unmodified against a non-kind cluster.

#### Scenario: Onboarding role has no kind-specific logic
- **WHEN** the `onprem_cluster_onboarding` role's tasks are inspected
- **THEN** no task references `kind` cluster creation, deletion, or kind-specific tooling — only generic kubeconfig-context and Fleet/RBAC operations

#### Scenario: Kind lab lifecycle is independently operable
- **WHEN** an operator runs the kind-lab-up, onboard, check, and kind-lab-down make targets in sequence
- **THEN** each completes independently and the onboarding role's behavior is unchanged from a run against any other reachable cluster

### Requirement: Clear, non-misleading failure reporting
The workflow SHALL identify the specific cluster and stage that failed, and SHALL NOT report a cluster as successfully onboarded if any required stage did not complete.

#### Scenario: Invalid kube context
- **WHEN** onboarding is run with a kube context that does not exist
- **THEN** the run fails at the preflight stage, naming the cluster and the missing context, before attempting registration

#### Scenario: Insufficient cluster privileges
- **WHEN** the operator's kubeconfig lacks cluster-admin on the target cluster
- **THEN** the run fails at the preflight or registration stage with an explicit privilege error, not a generic failure

#### Scenario: Already-registered membership name collision
- **WHEN** a different, unrelated Fleet membership already uses the requested membership name
- **THEN** the run fails clearly at the registration stage and does not silently adopt or overwrite the existing membership

### Requirement: Scoped, non-destructive cleanup
Cleanup SHALL remove only the Fleet memberships, RBAC objects, and kind clusters created by this workflow's test-lab runs, and SHALL NOT affect unrelated Fleet memberships, other clusters, or central GCP infrastructure.

#### Scenario: Cleanup of the test lab leaves unrelated resources untouched
- **WHEN** the cleanup playbook is run against the kind test-lab inventory
- **THEN** only `sre-lab`/`sre-lab-2`'s memberships, their RBAC objects, and their kind clusters are removed, and the central `mcp_runtime_gateway_reader` IAM grant in `iac/agent/onprem_fleet.tf` is left untouched (it is Terraform-owned, not this workflow's to delete)

### Requirement: Portable role/inventory separation
Reusable role and playbook logic SHALL NOT contain personal GCP project IDs, usernames, kind cluster names, personal filesystem paths, or personal service-account names; all such values SHALL live only in environment-specific inventory.

#### Scenario: Role source contains no personal values
- **WHEN** `ansible/roles/onprem_cluster_onboarding/` is searched for the personal project ID, kind cluster names, or the operator's username/email
- **THEN** none are found — all appear only under `ansible/inventories/kind/`
