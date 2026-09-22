# Runbook: Adding a Non-GKE / On-Prem Cluster (via Ansible)

> **Last Verified:** 2026-09-21 — full live run against `sre-lab`, including a real
> Agent Engine investigation (`run_20260921_211936_nbzv`, CONFIRMED root cause).
> **Owner:** SRE Agent platform team.
> **Supersedes the manual steps in** [Connect Gateway on-prem setup](../connect-gateway-onprem.md)
> **as the current way to onboard** — that document still explains *why* each step
> exists (credential model, RBAC model, audit posture) and is worth reading once;
> this runbook is what you actually run.

## What this replaces

Before this Ansible workflow existed, onboarding a non-GKE cluster meant running the
`gcloud`/`kubectl` sequence in [Connect Gateway on-prem setup](../connect-gateway-onprem.md)
by hand, in the right order, remembering every flag. That's how the real cost incident
in commit `cc9dbe0` happened — a manual step's outcome (Fleet tier) was never checked.
This workflow automates every one of those steps, checks its own outcome before
proceeding, and is idempotent (safe to re-run).

**What is still NOT automated, on purpose**: Fleet membership registration and
Kubernetes RBAC are deliberately kept **out of Terraform** (`iac/agent/onprem_fleet.tf`'s
own header explains why — a prior CI incident where a Terraform local-exec provisioner
tore down registration on every apply). Ansible replaces the *manual* steps, not by
moving them into Terraform, but by scripting the exact same operator-run commands
safely. Terraform still owns exactly two things here: the one project-level IAM grant,
and the cluster's entry in the `clusters.json` registry.

## Prerequisites

### Tools (one-time machine setup)
```bash
# gcloud SDK (if not already installed/on PATH)
gcloud version   # if "command not found", find your install and add its bin/ to PATH

# The kubectl exec-credential plugin Connect Gateway needs — this bit us live
# (a real bug hunt) if it's missing: kubectl fails with
# "exec: executable gke-gcloud-auth-plugin not found"
gcloud components install gke-gcloud-auth-plugin

# Ansible + the one Python dependency the role's json_query filter needs
pip install ansible jmespath
# or: pip install -r ansible/requirements.txt

# Lint tooling (optional but recommended before any real run)
pip install ansible-lint yamllint
```

### Access
- `gcloud auth login` as an identity with `roles/gkehub.admin` on the Fleet host
  project (this repo's: `sreagent-t2-demo`) — needed for registration.
- `cluster-admin` on the target cluster's own kubeconfig context — needed for
  registration and RBAC application. (For `kind` clusters this is automatic; for a
  real cluster, use whatever your platform's normal admin-access path is.)
- The target cluster's API server must serve `/.well-known/openid-configuration` and
  `/openid/v1/jwks` (checked automatically by preflight) — required for keyless
  Workload Identity Federation registration. **No service-account-key path exists in
  this workflow at all** — if a cluster can't satisfy this, onboarding fails clearly
  rather than falling back to a static key.

## Step 1 — Add the cluster to inventory

Personal validation clusters live in `ansible/inventories/kind/group_vars/all.yml`.
Add an entry to `external_clusters`:

```yaml
external_clusters:
  - name: sre-lab-2                  # becomes the Fleet membership name by default
    kube_context: kind-sre-lab-2     # must already exist in your local kubeconfig
    fleet_project_id: sreagent-t2-demo
    fleet_membership: sre-lab-2      # optional, defaults to `name`
    environment: test
    rbac_role: clusterrole/view      # optional, this is the default
    grant_node_read: true            # optional, this is the default
    allow_billable_external_cluster: true   # REQUIRED for a new registration -- see Cost note
```

`allow_billable_external_cluster` defaults to `false` and has no effect on a
cluster that's already registered (idempotent re-runs/`verify.yml` are never
blocked by it) — it only gates a genuinely NEW Fleet registration. Set it
per-cluster, explicitly, only once you've accepted the documented cost.

`runtime_identity` (the SRE Agent's own service account — already IAM-granted, see
Step 3) is set once, above `external_clusters`, and doesn't change per cluster:
```yaml
runtime_identity: "sre-k8s-mcp-runtime@sreagent-t2-demo.iam.gserviceaccount.com"
```

For a **real** (non-kind) cluster, or when porting to a different environment
(different project, work laptop, etc.), create a separate inventory directory instead
of editing the personal one — see [`ansible/PORTING.md`](../../ansible/PORTING.md).

## Step 2 — Run the onboarding playbook

```bash
cd ansible
ansible-playbook playbooks/onboard.yml
```

This does, per cluster, in order — all idempotent, safe to re-run:
1. Preflight (kube-context reachable, cluster-admin confirmed, required GCP APIs
   enabled, keyless-registration prerequisites checked)
2. **Mandatory cost-approval gate** — if this cluster isn't already registered, and
   `allow_billable_external_cluster` isn't explicitly `true` for it (inventory,
   default `false`), the run **fails loudly here, before any Fleet mutation at
   all**. See [Cost note](#cost-note) below for why this is the gate now, instead of
   the old (incorrect) "unregister if `ENTERPRISE`" logic.
3. Fleet registration (`gcloud container fleet memberships register`, keyless WIF)
   — only reached once the gate above has passed
4. `clusterTier` is read back and recorded as **informational evidence only** — no
   action is taken based on its value; a cluster is never unregistered just because
   it reports `ENTERPRISE`
5. RBAC: impersonation + `view` permission (Google's own `generate-gateway-rbac`
   helper), plus a narrow supplemental grant for `nodes: get,list` (the MCP tool suite
   calls `list_node`/`read_node`; `view` alone doesn't cover cluster-scoped `nodes` —
   this was a real, documented parity gap, now closed for every cluster onboarded
   through this workflow)
6. Verification: real read calls (`pods`, `deployments`, `events`, `nodes`) and real
   denied calls (`create namespace`, `delete pod`, `get secrets`) through Connect
   Gateway, **as the real runtime identity** (impersonated), not just your own
   operator access

**Expected output on success**: exit code 0, a per-cluster `PASS` in the printed
summary, and a JSON evidence file in `ansible/artifacts/onboarding-<timestamp>.json`.

**If it fails**: the error names the exact cluster and stage. Re-running is safe —
completed steps are skipped, not redone.

### Re-verifying without re-registering

```bash
ansible-playbook playbooks/verify.yml
```
Runs only the verification subset (real read/deny checks) — no mutation. Useful for
"is this cluster still correctly configured?" without re-touching anything.

## Step 3 — Wire the cluster into the live agent (Terraform)

Ansible does **not** touch Terraform state or apply anything — this is deliberate
(Terraform apply is a real infrastructure change and needs a human decision each
time, not something a onboarding script does silently). Two things need a Terraform
apply before the agent will actually route to the new cluster:

1. **The registry entry** (`var.additional_clusters` in `iac/agent/variables.tf` /
   `terraform.tfvars`) — set `enabled = true` for the cluster you just onboarded, with
   `fleet_project_number`/`fleet_membership` matching what you registered. `sre-lab`
   and `sre-lab-2` already have entries in the variable's default (both currently
   `enabled = false` after the cost-incident fix, `cc9dbe0`) — flip the one you want
   to test to `true`.
2. **The runtime SA's Fleet IAM** — `var.onprem_fleet_membership` must be non-empty
   (any registered membership name works; this activates a project-level grant, not a
   per-cluster one) to create `google_project_iam_member.mcp_runtime_gateway_reader`
   and `..._gateway_viewer` in `iac/agent/onprem_fleet.tf`.

**Before running `terraform plan` on `iac/agent`, read this** — a real, pre-existing,
unrelated gotcha this session hit and had to work around:

> `iac/agent`'s CI (`.github/workflows/terraform-apply.yml`) passes several vars at
> apply time (`enable_custom_mcp`, `create_wif`, `custom_mcp_image`) that are **not**
> in the committed `terraform.tfvars` (a documented gotcha, see that workflow's own
> issue #116 comment). A plain local `terraform plan` without these will show the
> entire custom MCP Cloud Run service and its runtime SA being destroyed — this is
> **not** caused by the on-prem change, it's pre-existing local/CI drift. Get the
> current live values first:
> ```bash
> gcloud run services describe sre-k8s-mcp --project=sreagent-t2-demo \
>   --region=us-central1 --format="value(spec.template.spec.containers[0].image)"
> ```
> Then pass all three explicitly:
> ```bash
> terraform -chdir=iac/agent plan \
>   -var="create_wif=false" \
>   -var="enable_custom_mcp=true" \
>   -var="custom_mcp_image=<the image tag from the command above>" \
>   -var-file=<your on-prem override, or edit terraform.tfvars directly>
> ```

**Review the plan carefully.** If it shows anything beyond the on-prem IAM grant(s)
and the `clusters.json` update, scope the apply with `-target` to just those two
resource types rather than applying everything — this is exactly what this session
did (`Plan: 1 to add, 1 to change, 0 to destroy`) to avoid touching unrelated,
pre-existing drift:
```bash
terraform -chdir=iac/agent apply \
  -target='google_project_iam_member.mcp_runtime_gateway_reader' \
  -target='google_project_iam_member.mcp_runtime_gateway_viewer' \
  -target='google_storage_bucket_object.clusters_json' \
  <same -var flags as the plan above>
```
**Never apply a plan that shows unexpected replacement or destruction.** Stop and
investigate first.

## Step 4 — Run a real investigation

`invoke_agent.py` already has ready-made scenarios targeting the standard on-prem
test fixtures:

```bash
export PROJECT_ID=$(terraform -chdir=iac/agent output -raw project_a_id)
export REASONING_ENGINE_ID=$(terraform -chdir=iac/agent output -raw reasoning_engine_id)
python3 invoke_agent.py --scenario onprem --verbose    # targets sre-lab
python3 invoke_agent.py --scenario onprem2 --verbose   # targets sre-lab-2
```
(One-time local dependency, if you hit `ModuleNotFoundError: No module named 'google'`:
`pip install "google-cloud-aiplatform[agent_engines]>=1.150.0,<2.0.0"`.)

**Confirm the response actually targeted your cluster, not the default GKE
cluster** — check the response's own `"cluster"` field and
`"cluster_routing_reason"`, and cross-check `"primary_mcp_source": "k8s_mcp"`
(the custom MCP / Connect Gateway path — if you instead see the GKE Remote MCP
source, something is misrouted). For independent confirmation beyond the agent's own
claim, check the Cloud Run MCP's own logs for the exact cluster ID:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="sre-k8s-mcp"' \
  --project=sreagent-t2-demo --limit=20 --format="value(timestamp,textPayload)"
```
Look for `"Initializing K8s client via dynamic Connect Gateway (cluster_id=<yours>,
...)"`.

## Step 5 — Cleanup (personal/kind validation only)

```bash
ansible-playbook playbooks/cleanup.yml
```
Removes only Fleet memberships and RBAC objects this workflow itself created
(ownership-labeled, verified live against a fixture that a pre-existing unrelated
object survives untouched). Does **not** delete the kind cluster itself — that's a
separate, explicit step if you want it gone:
```bash
kind delete cluster --name sre-lab-2
```

If you also flipped the Terraform registry entry (Step 3) for testing only, revert it
the same way — apply again without the on-prem overrides (falls back to the committed
`enabled = false` default), or explicitly set `enabled = false` again.

## Cost note (corrected 2026-09-22)

**GKE no longer has separate Standard/Enterprise commercial editions** — current
Google documentation states GKE is now a single offering with no tiers. Registering
a non-GKE cluster's Fleet membership still, reproducibly, shows `clusterTier:
ENTERPRISE` in `gcloud describe` output — but **that field alone is legacy metadata,
not proof of billing**, and this workflow no longer treats it as one. The actual
documented cost driver is registering a **third-party/non-GKE cluster into a Fleet
at all**: current GKE pricing lists GKE Multicloud Attached Clusters at a per-vCPU/
hour charge, and a current, dated GCP doc states third-party clusters registered to
a Fleet "will incur a per-vCPU charge as part of your GKE pricing" — independent of
what `clusterTier` later reports.

**The safety gate is now explicit approval, not a tier check.** Every cluster this
workflow onboards is non-GKE by definition, so registering a NEW one always requires
`allow_billable_external_cluster: true` set for that specific cluster in inventory
(default `false` — the playbook fails clearly, before any Fleet mutation, until you
set it). `clusterTier` is still recorded as informational evidence in the run
artifact, but a cluster is never auto-unregistered just because it reports
`ENTERPRISE` — that was the old, incorrect logic (see
`openspec/changes/onprem-cluster-ansible-onboarding/tasks.md`'s 2026-09-22 correction
entry for the full before/after). Keep test registrations short regardless — onboard,
verify, test, clean up, in one sitting — since the underlying registration is still a
real, accepted cost, not a free operation.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `gke-gcloud-auth-plugin not found` | Not installed, or SDK `bin/` not on PATH | `gcloud components install gke-gcloud-auth-plugin`; add the SDK's `bin/` dir to PATH |
| `PERMISSION_DENIED: ... gkehub.memberships.list ...` during verification | The runtime SA lacks `roles/gkehub.viewer` (needed only by the `get-credentials` CLI mechanism `verify.yml` uses — NOT needed by the actual production path, which calls the Connect Gateway REST API directly) | Confirm Step 3's Terraform apply included `google_project_iam_member.mcp_runtime_gateway_viewer`; if this repo predates 2026-09-21, that resource may not exist yet — it does now, in `iac/agent/onprem_fleet.tf` |
| `Failed to impersonate ... roles/iam.serviceAccountTokenCreator` | The identity running Ansible isn't authorized to impersonate `runtime_identity` | Grant `roles/iam.serviceAccountTokenCreator` on the SA (not project-wide) to your own identity — temporary, remove it after testing. This is a real IAM change; do it deliberately, not by default |
| `require explicit cost approval` failure | Expected on any first registration — see [Cost note](#cost-note) | Set `allow_billable_external_cluster: true` for that specific cluster in inventory, only after you've explicitly accepted the documented per-vCPU cost |
| Registration lands on `ENTERPRISE` tier | Expected, informational only — see [Cost note](#cost-note) | No action needed; this is no longer the safety gate and does not cause a failure or unregister |
| `terraform plan` on `iac/agent` shows the custom MCP service being destroyed | Pre-existing, unrelated CI-vs-local var drift (see Step 3) | Pass the CI-equivalent vars explicitly, as shown above |

---

**Related pages:** [Connect Gateway on-prem setup](../connect-gateway-onprem.md) (the
underlying mechanism and its design rationale) · [GKE vs. Non-GKE Access](../architecture/gke-vs-nongke.md)
· [Adding a New GKE Cluster](add-gke-cluster.md) (the GKE-only equivalent, no Ansible
needed) · [`ansible/PORTING.md`](../../ansible/PORTING.md) (porting this workflow to a
different environment/project) · `openspec/changes/onprem-cluster-ansible-onboarding/`
(the full design + live validation evidence)
