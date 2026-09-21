## Corrections applied 2026-09-21 (approved before implementation)

The initial merged design (PR #258) is approved for implementation with six required
corrections, tracked here and reflected throughout this proposal, `design.md`, `tasks.md`,
and `specs/onprem-cluster-onboarding/spec.md`:

1. **No service-account-key fallback** — keyless Fleet Workload Identity only; preflight
   fails clearly (never silently generates/stores a key) if a cluster can't satisfy it.
2. **Multi-cluster failure handling** — explicit per-cluster `block`/`rescue` + result
   aggregation, not a broad `ignore_errors` pattern; overall playbook exits non-zero if any
   required cluster failed.
3. **Cleanup ownership** — cleanup removes only objects this workflow created, identified by
   a deterministic ownership signal it verifies exists (not assumed) on the actual objects
   `generate-gateway-rbac` produces; a pre-existing-object fixture proves this.
4. **Fleet tier safety** — kept, but re-verified against current registration flags,
   `Membership` fields, and billing docs during implementation rather than assumed unchanged.
5. **Node RBAC** — kept narrow, but the exact verbs (`get`/`list` vs. `watch`) are derived
   from the actual MCP call inventory, not assumed.
6. **Wording** — claims about what `view` does/doesn't cover are stated as the evidenced,
   environment-specific fact observed, not a generic Kubernetes RBAC claim.

## Why

Every non-GKE/on-prem cluster this agent has ever reached (`sre-lab`, `sre-lab-2`) was onboarded by hand: an operator ran a sequence of `gcloud`/`kubectl` commands from `docs/connect-gateway-onprem.md`'s runbook, one cluster at a time, with no repeatable automation and no state check before mutating anything. This already caused one real incident: both memberships were registered at Fleet tier `ENTERPRISE` instead of `STANDARD`, which billed a real per-vCPU-hour fee (~$216/month combined, confirmed via GCP Billing Console) that nobody intended — fixed in commit `cc9dbe0` by disabling both memberships (`iac/agent/variables.tf`'s `additional_clusters["sre-lab"/"sre-lab-2"].enabled = false`). A manual runbook has no place to enforce "always register at STANDARD tier" as a checked invariant; a human just has to remember it every time.

Separately, `docs/connect-gateway-onprem.md`'s own "Adding a second on-prem cluster" section (2026-09-07) states plainly: "a genuinely SECOND on-prem cluster, onboarded from zero using only the 4 [documented] steps ... [is] not yet proven" — `sre-lab-2` was in fact registered from zero once, but never through a repeatable, idempotent, tool-driven process, and both clusters are now unregistered again after the cost fix. There is currently **zero live on-prem cluster** reachable via Connect Gateway.

At work, the same problem will recur on non-production clusters reachable over VPN, with no Spacelift private runner yet available to run Terraform against them. The fix belongs in an idempotent, reusable Ansible workflow — proven safely and repeatably here first, on the same `sre-lab`/`sre-lab-2` kind clusters that already stand in for on-prem in this repo, before any of it touches a real work cluster.

## What Changes

- New capability `onprem-cluster-onboarding`: a reusable, idempotent Ansible workflow that onboards external/non-GKE Kubernetes clusters into the Fleet → Connect Gateway → RBAC → SRE Agent path, driven by inventory data (one or many clusters), not per-cluster playbooks or hand-run `gcloud`.
- Re-registers `sre-lab` and `sre-lab-2` (already-running local kind clusters — confirmed still up, see `docs/connect-gateway-onprem.md`'s "Live resources" section and the disable commit) as the validation targets, this time via Ansible and explicitly checked at `STANDARD` Fleet tier, not `ENTERPRISE`.
- Closes a real, currently-live read-parity gap: the `view` ClusterRole used for `sre-lab` returns `Forbidden` on `kubectl get nodes` (documented, expected K8s behavior at the time), but the MCP tool suite's `mcp/tools/nodes.py` calls `list_node`/`read_node` as part of normal investigation — GKE clusters cover this via Cloud IAM (`roles/container.viewer`), non-GKE clusters have no such fallback today. The onboarding RBAC role adds the minimum extra grant (cluster-scoped `nodes: get,list`) so a non-GKE cluster's tool coverage matches what the agent actually calls.
- No change to `agent/`, `mcp/server.py`'s Connect Gateway branch, or the `clusters.json` schema — those are proven and live already (per `docs/architecture/gke-vs-nongke.md`, Layer B/C). This change only replaces the **manual registration/RBAC steps** (Layer A, `docs/connect-gateway-onprem.md` §§1-6) with Ansible, and renders (never auto-applies) the one Terraform `additional_clusters` entry per cluster.
- Adds a disposable local kind test-lab (Makefile targets), kept structurally separate from the reusable onboarding roles, per the existing `iac/agent`/`iac/gke-access` pattern of keeping test fixtures out of production modules.
- Adds `PORTING.md` documenting exactly what carries over to a work non-prod environment later (inventory only, never the roles) — consistent with `PORTING_BRIEF_TO_APP_INFRA.md`'s existing exclusion of on-prem onboarding from the company repo.

## Current Manual Steps → Automation Mapping

Source: `docs/connect-gateway-onprem.md` §§1-6 and its "Onboarding a new on-prem/non-GKE cluster" + "Adding a second on-prem cluster" runbooks (the only two documented, actually-executed procedures in this repo).

| # | Current manual step | Owner today | Automation target |
|---|---|---|---|
| 1 | Enable `gkehub`, `connectgateway`, `container` APIs on the target project | one-time `gcloud services enable`, by hand | **EXISTING GCP BOOTSTRAP** — one-time per project, checked (not re-run) by Ansible preflight; enabling remains a human/Terraform action outside this change's scope (no evidence any project used by this design lacks them today) |
| 2 | Confirm the target cluster can use keyless Fleet Workload Identity registration (`--enable-workload-identity` [+ `--has-private-issuer` for a non-publicly-routable API server]) | manual judgment call, documented after the fact | **ANSIBLE** — preflight verifies the org policy (`constraints/iam.disableServiceAccountKeyCreation`, confirmed **enforced** on `sreagent-t2-demo`) and the cluster's issuer reachability; if keyless prerequisites are not met, preflight **fails clearly, naming the missing prerequisite** — this workflow contains no service-account-key code path at all, so there is nothing to "fall back" to |
| 3 | `gcloud container fleet memberships register` | manual `gcloud`, by hand, per cluster | **ANSIBLE** — idempotent (checks existing membership state first; skips re-register if already `READY` at the correct tier) |
| 4 | Verify membership `READY` **and** Fleet tier is `STANDARD` (not `ENTERPRISE`) | not verified at all before the cost incident | **ANSIBLE** — mandatory postflight check; the run fails loudly (not silently) if tier ≠ `STANDARD`, since this exact miss caused real billing |
| 5 | `generate-gateway-rbac --role=clusterrole/view --apply` (impersonation + `view` binding) | manual `gcloud`, by hand | **ANSIBLE** — idempotent (checks existing `ClusterRoleBinding`s labeled `connect.gke.io/owner-feature: connect-gateway` before applying) |
| 5b | Grant supplementary cluster-scoped `nodes: get,list` (the parity gap above — not previously done at all) | **MISSING** today | **ANSIBLE** — new, narrowly-scoped `ClusterRole`/`ClusterRoleBinding`, additive to `view`, never broader |
| 6 | `get-credentials` + one real read call + one real denied-write call, before calling onboarding "done" | manual, by hand, once, never repeated | **ANSIBLE** — scripted verification step, run every time, with captured evidence output |
| 7 | Add cluster to `additional_clusters` Terraform map + `terraform apply` | manual edit + manual apply | **ANSIBLE renders** the exact HCL block to append (never edits `.tf` files or runs `apply` itself — `terraform apply` requires explicit human authorization per this session's security rules); **TERRAFORM/human** owns the actual apply |
| — | Kubernetes RBAC for on-prem "moved entirely to the separate `AshminPy/sre-k8s-rbac` repo" (`iac/agent/onprem_fleet.tf` header) | repo not present in this filesystem, content unverifiable | **DECISION NEEDED, recorded in design.md** — Ansible applies RBAC directly (it needs the same authorized-operator access to do registration anyway) and additionally emits the applied manifest as an artifact for that repo, rather than blocking on a repo this session cannot inspect |

## Impact

- Affected/added paths: `ansible/roles/onprem_cluster_onboarding/` (new, portable), `ansible/inventories/kind/` (new, personal validation data), `ansible/playbooks/onboard.yml`, `ansible/playbooks/verify.yml`, `ansible/playbooks/cleanup.yml` (new), `Makefile` (new `kind-onprem-*`/`onprem-*` targets), `PORTING.md` (new).
- No changes to `agent/`, `mcp/`, or `iac/*.tf` source in this change; `iac/agent/variables.tf`'s `additional_clusters["sre-lab"/"sre-lab-2"].enabled` flip and any new entries remain a human-reviewed Terraform PR, informed by this workflow's rendered output.
- Read-only inputs used to write this proposal: `docs/connect-gateway-onprem.md`, `iac/agent/onprem_fleet.tf`, `iac/agent/variables.tf:41-147`, `agent/mcp_client.py`, `mcp/server.py`, `mcp/tools/*.py`, `mcp/tests/test_live_connect_gateway.py`, `mcp/tests/test_dynamic_connect_gateway.py`, commit `cc9dbe0`, current official GCP docs (Fleet management, Connect Gateway, Attached Clusters — cited with URLs in `design.md`).
