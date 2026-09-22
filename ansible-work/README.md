# ansible-work — Work-safe on-prem/external cluster onboarding

**PERSONAL LAB VERSION:** `ansible/` (this repo's original, kind-cluster validation
lab -- includes cleanup/unregister/revoke, used for proving the design).

**WORK-SAFE PORTABLE VERSION:** `ansible-work/` (this folder) -- onboarding-only,
built to be copied into a work repo and run against a real non-prod cluster.

This version has **not** been run against a real work cluster yet. It is a
production-hardened rewrite of the proven personal design, reviewed here before
porting -- not a workflow with its own independent live track record.

## 1. What this folder does

Registers a new external/non-GKE Kubernetes cluster into GCP Fleet, deploys the
Google-supported Connect Gateway agent via the standard registration flow, grants
the minimum read-only RBAC an SRE investigation agent needs, and verifies all of it
with real, read-only Kubernetes API calls. One playbook, idempotent, safe to re-run.

## 2. What this folder intentionally does NOT do

- No `cleanup.yml` / `cleanup_one_cluster.yml` -- doesn't exist in this folder.
- No `gcloud container fleet memberships unregister` -- never called.
- No `generate-gateway-rbac --revoke` -- never called.
- No `kubectl delete` of any kind -- never called (including no create/delete
  namespace test in verification).
- No `kind delete` -- this folder has no kind-lifecycle logic at all.
- No Terraform apply/destroy.
- No automatic rollback that removes resources.
- No auto-repair of an existing, unhealthy Fleet membership -- if one is found and
  isn't `READY`, the run FAILS with zero mutation instead of trying to fix it.
- No overwrite/adopt/relabel of pre-existing RBAC it didn't create -- if an object
  it needs already exists under someone else's ownership, the run FAILS and touches
  nothing.
- No `cluster-admin`, `gatewayAdmin`, write verbs, or secret access granted to the
  runtime identity, ever.

If you need any of the above, that is a deliberate, reviewed, out-of-band action --
not something this folder will do for you.

## 3. Prerequisites

- `gcloud` and `kubectl` on PATH (or set `gcloud_bin` in your inventory).
- `gke-gcloud-auth-plugin` installed (`gcloud components install gke-gcloud-auth-plugin`).
- The operator running this has `cluster-admin` on the target cluster (required by
  Google's Fleet registration) and an authenticated `gcloud` session.
- The target cluster serves a discoverable OIDC issuer
  (`/.well-known/openid-configuration`, `/openid/v1/jwks`) -- required for keyless
  `--has-private-issuer` registration. There is no service-account-key fallback.
- `gkehub.googleapis.com`, `connectgateway.googleapis.com`, and
  `container.googleapis.com` already enabled on the target GCP project (this
  workflow checks this, it never enables APIs itself).
- **Python 3.12 or newer on the control machine** (the one running
  `ansible-playbook` -- not the target cluster). This is a real, verified
  constraint: `ansible-core~=2.21` (pinned in `requirements.txt`) requires it.
  Check with `python3 --version` before creating the venv below. If it's older,
  install a newer Python first (e.g. via `pyenv` or Homebrew) rather than assuming
  it'll work.
- Real, reviewed team approval to register a new billable external cluster (see
  §5) before setting `allow_billable_external_cluster: true` for it.

### Python environment (venv) -- required, not optional

Ansible itself is a Python package. Installing it into whatever Python happens to
be active on a given machine is exactly how "works on my machine" problems start --
a different Python version, a different pre-installed Ansible, or a stray global
package can silently change behavior. This folder is meant to behave the same on
your personal Mac tonight and your work laptop tomorrow, so every command below
assumes an isolated virtual environment created fresh from `requirements.txt`:

```bash
cd ansible-work
python3 --version                     # confirm 3.12+ before proceeding
python3 -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Verified tonight (2026-09-22), on the personal machine, in a completely fresh venv
built only from this repo's pinned `requirements.txt`: `ansible-playbook [core
2.21.4]`, `jmespath 1.1.0`, both syntax-checked clean. **Not yet verified**: that
this same install succeeds on the work laptop -- its Python version and OS have not
been checked from this session. Run the version check above there before assuming
it works.

`.venv/` is gitignored -- never commit it. Every machine (including the work
laptop tomorrow) creates its own from the same pinned `requirements.txt`, which is
what actually makes this reproducible, not a shared/copied virtual environment.

Non-Python CLI tools (`gcloud`, `kubectl`, `gke-gcloud-auth-plugin`) can't be pinned
by a Python venv -- they're separate binaries. Versions verified tonight on the
personal machine: Google Cloud SDK 573.0.0, `kubectl` client v1.32.0,
`gke-gcloud-auth-plugin` v0.1.0-gke.3. Check these are present and reasonably
current on the work laptop (`gcloud --version`, `kubectl version --client`) --
this workflow doesn't pin or verify their versions itself.

## 4. Inventory fields

Copy `inventories/work-nonprod/group_vars/all.example.yml` to `all.yml` in the same
directory and fill in:

| Field | Meaning |
|---|---|
| `runtime_identity` | The SRE Agent's runtime service account (must end in `.iam.gserviceaccount.com` for impersonated verification to run). |
| `gcloud_bin` (optional) | Full path to `gcloud`, only if it isn't already on PATH. |
| `external_clusters[].name` | Logical cluster name. |
| `external_clusters[].kube_context` | Local kubeconfig context for this cluster. |
| `external_clusters[].fleet_project_id` | GCP project the Fleet membership lives in. |
| `external_clusters[].fleet_membership` | Fleet membership name (defaults to `name` if omitted). |
| `external_clusters[].environment` | Free-text label, e.g. `nonprod`. |
| `external_clusters[].rbac_role` | Defaults to `clusterrole/view`; override only with real justification. |
| `external_clusters[].grant_node_read` | Defaults to `true` -- grants the narrow `nodes: get,list` supplement `view` alone doesn't cover. |
| `external_clusters[].allow_billable_external_cluster` | Defaults to `false`. See §5. |

`all.yml` is gitignored by this folder -- real values never get committed from here.

## 5. Cost-approval behavior

Registering any third-party/non-GKE cluster into a GCP Fleet is documented by
Google as a per-vCPU-billed operation (GKE Multicloud Attached Clusters pricing),
independent of what `clusterTier` the resulting membership later reports.
`clusterTier` is legacy, informational-only metadata in this workflow -- it is
**never** used as a pass/fail condition, and `ENTERPRISE` is never treated as a
failure or trigger for any action.

Because of that real cost, registering a genuinely **new** cluster requires
`allow_billable_external_cluster: true` set explicitly for that one cluster, after
real team approval -- the run fails loudly, before any Fleet mutation, if it's not
set. This flag only gates a *new* registration:

- **Membership doesn't exist yet:** requires the approval flag, then registers.
- **Membership exists and is `READY`:** left alone, verified, run continues --
  the approval flag is irrelevant here, it's not re-registering anything.
- **Membership exists but is NOT `READY`, or its state can't be determined:** the
  run **FAILS immediately with zero mutation** and reports the exact observed
  state. This workflow will never try to "fix" or re-register an existing
  membership on your behalf.

## 6. Exact onboarding command

```bash
cd ansible-work
source .venv/bin/activate    # created once per §3's "Python environment (venv)"
cp inventories/work-nonprod/group_vars/all.example.yml inventories/work-nonprod/group_vars/all.yml
# edit all.yml with your real values
ansible-playbook playbooks/onboard.yml
```

## 7. Exact read-only verification command

```bash
cd ansible-work
source .venv/bin/activate
ansible-playbook playbooks/verify.yml
```
Re-runs only real, read-only checks (namespaces/pods/deployments/events/nodes GET
calls, plus the negative secret-access check below) against an already-onboarded
cluster. Never registers or applies RBAC.

## 8. Expected PASS/FAIL behavior

Positive reads (`get namespaces`, `get pods -A`, `get deployments -A`,
`get events -A`, `get nodes` if `grant_node_read` is true) must all succeed, or the
run fails naming the exact failing call.

Negative secret-access check -- `kubectl get secret sre-agent-verification-does-not-exist -n default`:

| Result | Verdict | Meaning |
|---|---|---|
| `Forbidden` | **PASS** | RBAC correctly denies secret access (Kubernetes checks authorization before object existence, so this is a valid test even though the secret doesn't exist). |
| `NotFound` | **FAIL** | The identity IS authorized to read secrets -- a real RBAC problem. |
| anything else (network/auth/timeout/other error) | **FAIL** | Inconclusive -- treated as a failure, never as a pass. |

No create/delete namespace test exists. No server-side dry-run write is ever
attempted. Verification is fully read-only, always.

## 9. No cleanup/destructive workflow exists

This folder cannot unregister a Fleet membership, revoke or delete RBAC, or delete
a cluster. If a registration needs to be undone, that is a separate, deliberate,
out-of-band action taken by whoever owns the target project/cluster -- not
something to add here without a fresh, explicitly-scoped review.

## 10. Porting into the work repo

```bash
rsync -av \
  --exclude='inventories/work-nonprod/group_vars/all.yml' \
  --exclude='artifacts/*.json' \
  --exclude='.ansible/' \
  --exclude='.venv/' \
  ~/projects/sre-agent-gateway/ansible-work/ \
  <path-to-work-repo>/ansible/
```

After copying: **create a fresh `.venv` inside the work repo** (§3) -- do not reuse
or copy a venv from this machine; a venv is tied to the Python interpreter and OS
it was built with, and copying one across machines is a common source of exactly
the inconsistency this setup is meant to avoid. Re-run every static check in this
repo's own validation pass (syntax-check, lint, the personal/destructive-value
greps) inside the work repo too, before trusting anything about the copy. Then
create a real `all.yml` there with your work project's actual values -- never copy
this repo's `all.example.yml` values verbatim, they are placeholders. This exact
workflow has not been run against a real work cluster; treat the first work-repo
run as a genuine first test, not a replay of an already-proven result.
