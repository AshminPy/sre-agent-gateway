## Architecture boundary questions (architecture-quality.md §2)

**What is likely to change? What should be configuration vs. code?**
The cluster identity (name, kubeconfig context, project, environment, Fleet membership name) changes per cluster and per environment (kind lab today, work non-prod later). This is all inventory data (`ansible/inventories/<env>/hosts.yml` or `group_vars`), never baked into a role or playbook. The role logic (preflight → register → RBAC → verify → report) does not change per cluster — it stays code, reused via `ansible/roles/onprem_cluster_onboarding/`.

**How would another instance (project, provider, environment, cluster) be added?**
Add one inventory entry (see schema below) and re-run the same playbook — no new playbook, no new role. A different GCP project or fleet host project is also inventory data (`fleet_project_id`), not hardcoded. Distinct from cloud provider entirely (e.g. if a future cluster used a different fleet/hub concept) — out of scope; not evidenced as a near-term need, so no abstraction built for it now.

**Which manual operational steps can reasonably be automated?**
Steps 1-6 of the current runbook (proposal.md's mapping table) are automatable and idempotent by construction (state-check-then-act). Step 7 (Terraform `additional_clusters` entry + `apply`) is deliberately **not** auto-applied — `terraform apply` is a global "always ask before doing" action in this environment (`~/.claude/rules/security.md`), and `iac/agent/onprem_fleet.tf`'s own header already established this exact boundary for a stronger reason: a prior CI incident where a local-exec provisioner ran `gcloud` against `$HOME/.kube/config` inside GitHub Actions, silently tearing down registration on every apply. Ansible renders the HCL snippet; a human reviews and applies it.

**What fails if a dependency is unavailable, and how is that failure handled and surfaced?**
- GCP APIs unreachable / `gcloud` not authenticated → preflight fails before any mutation, names the exact missing credential/API.
- Target cluster unreachable / bad kube-context → preflight fails, names the cluster and context.
- Fleet API returns a registration error (e.g. membership name collision) → the run fails at that cluster only; per `MULTI-CLUSTER DESIGN`, cluster B's failure must not block or roll back cluster A (each cluster's tasks run to completion or failure independently — no shared transaction).
- Partially completed prior onboarding (e.g. membership exists but RBAC wasn't applied) → preflight state-detection notices the partial state and resumes from the correct step, rather than either skipping everything or re-registering.

**How will an operator observe and troubleshoot this?**
Every cluster gets a structured per-cluster result (name, stage reached, pass/fail, the exact command that failed if any) written to stdout and a run-scoped JSON/YAML evidence file (`ansible/artifacts/onboarding-<run-timestamp>.json`) — satisfying "produce concise per-cluster success/failure evidence" without inventing a new logging stack.

**How will it be tested? How is it deployed and rolled back?**
Tested against `sre-lab`/`sre-lab-2` kind clusters (idempotency: run twice, second run mostly `ok`; multi-cluster isolation: onboarding B doesn't touch A; failure-path tests per tasks.md). "Deploy" = running the playbook from an operator laptop (or later a private runner) with `ansible-playbook`; "rollback" = the cleanup playbook, scoped to exactly what this workflow created (Fleet membership + the two RBAC binding sets it applied), never a wildcard.

**What security boundary exists?**
Operator's own `kubeconfig`/ADC is the onboarding credential — never stored, never templated into a committed file, never handed to the SRE Agent runtime. Runtime access stays exactly what `iac/agent/onprem_fleet.tf` already grants (`roles/gkehub.gatewayReader`, project-scoped) plus the K8s-side `view` + node-read bindings this workflow applies — no `cluster-admin`, no write verbs, ever, for the runtime identity. Registration-time privilege (`cluster-admin` on the target cluster, required by GCP's own prerequisites — see Evidence below) is scoped to the human/automation identity only, for the duration of the run.

**What are the scaling and cost implications?**
Plain Fleet Membership registration for Connect Gateway is free (verified against current GCP docs — see Evidence). The cost incident this proposal responds to came from an unintended `ENTERPRISE` tier assignment, not from Connect Gateway itself — hence the mandatory tier check being a first-class, fail-loud step rather than a nice-to-have.

## Evidence (repository + current official docs)

- `iac/agent/onprem_fleet.tf` (37 lines) — the only Connect-Gateway Terraform resource: a project-level IAM grant, explicitly NOT covering Fleet registration or K8s RBAC (both documented as deliberately kept out of Terraform/CI after the local-exec provisioner incident).
- `iac/agent/variables.tf:41-147` — `additional_clusters` schema (`aliases`, `project`, `region`, `type`, `environment`, `allowed_namespaces`, `owner`, `enabled`, `kube_context` [legacy static path], `fleet_project_number`, `fleet_membership` [dynamic path, preferred for any new cluster]) and the current `sre-lab`/`sre-lab-2` entries, both `enabled = false` since commit `cc9dbe0`.
- `docs/connect-gateway-onprem.md` — full runbook, the two-part RBAC model (impersonation + `view` permission binding via `generate-gateway-rbac`), the documented `nodes` Forbidden result, the credential-model decision (`--has-private-issuer` over static SA keys, forced by org policy `constraints/iam.disableServiceAccountKeyCreation`), and the exact "Removing a cluster" unregister/revoke commands this design's cleanup playbook reuses.
- `mcp/tools/nodes.py:9,27` (`list_node`, `read_node`) confirmed part of the live custom-MCP tool set (`CUSTOM_K8S_TOOLS`, `agent/mcp_client.py:53-100`) — the node-read parity gap is real, not speculative.
- Current official GCP docs (fetched and read 2026-09-21, full citations in the research agent's report retained in this session's transcript):
  - [Fleet creation overview](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/fleet-creation) — plain Fleet Membership registration is free; Attached Clusters bills per-vCPU. Confirms `gcloud container fleet memberships register` (not `attached clusters register`) is the correct, current, non-deprecated command for Connect-Gateway-only access.
  - [Fleet management prerequisites](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/before-you-begin) — registering identity needs `roles/gkehub.admin`; target cluster needs `cluster-admin` on the registering identity; no Kubernetes version floor for plain registration (unlike Attached Clusters' documented 1.25 floor + platform-version matching, which a `kind` cluster is not guaranteed to satisfy).
  - [Connect Gateway setup](https://docs.cloud.google.com/kubernetes-engine/enterprise/multicluster-management/gateway/setup) — `roles/gkehub.gatewayReader` (read-only) vs. `roles/gkehub.gatewayAdmin` (adds `gateway.stream`, i.e. exec/attach/port-forward — never grant this to the runtime identity). Matches `iac/agent/onprem_fleet.tf`'s existing grant exactly.
  - [Troubleshoot cluster connections](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/troubleshooting) — `kubectl get pods -n gke-connect`, Connect Agent log markers, used as this design's live-health check, not exit-code-only.
  - [Fleet Workload Identity](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/use-workload-identity) — WIF displaces long-lived credentials; matches the existing `--has-private-issuer` choice already validated in this repo.

## DECISION records

```
DECISION            Reuse the existing `sre-lab`/`sre-lab-2` kind clusters (and their
                    Terraform additional_clusters entries) as the validation lab,
                    instead of new `kind-onprem-1/2` clusters.
EVIDENCE            Both clusters already exist, are already wired into
                    additional_clusters, already have dedicated tests
                    (mcp/tests/test_live_connect_gateway.py,
                    test_dynamic_connect_gateway.py, test_multi_cluster_isolation.py),
                    and are the actual clusters that hit the real cost incident this
                    proposal is fixing. `docker ps -a` evidence (repo research,
                    2026-09-04) showed the containers still running.
WHY                 Re-validating on the exact clusters that caused the incident
                    directly proves the fix (STANDARD-tier re-registration). Creating
                    parallel kind-onprem-1/2 fixtures would duplicate existing
                    Terraform state, tests, and docs for no evidenced benefit.
TRADEOFFS           Less "generic-looking" naming in examples/docs; mitigated by
                    keeping the Ansible inventory schema cluster-name-agnostic (any
                    name works, sre-lab/sre-lab-2 are just this repo's inventory
                    values, per PORTABILITY).
VALIDATION METHOD   tasks.md's multi-cluster + idempotency + isolation tests, run
                    against these two named clusters.
UNCERTAINTY         Whether the kind containers are still healthy after months idle
                    is unverified until preflight actually runs against them —
                    tasks.md's first step is a live health check, not an assumption.
```

```
DECISION            Fleet tier is checked and enforced as STANDARD, both before
                    (best-effort) and after (mandatory, fail-loud) registration.
EVIDENCE            Commit cc9dbe0: both prior memberships were registered at
                    clusterTier=ENTERPRISE, causing a real ~$216/month charge,
                    confirmed via live GCP Billing Console data, not caught until a
                    dedicated cost audit.
WHY                 A manual runbook has no way to enforce this as an invariant; the
                    exact current gcloud registration flow's tier-selection behavior
                    was not re-verified live in this session's GCP-docs research
                    (flagged UNVERIFIED there) — so this design cannot assume a flag
                    reliably forces STANDARD at registration time. The safety net is
                    the mandatory postflight check, not a trusted input flag.
TRADEOFFS           None — this is a pure safety addition with no functional cost.
VALIDATION METHOD   tasks.md requires `gcloud container fleet memberships describe`
                    immediately after registration; the playbook task fails (not
                    warns) if `clusterTier != STANDARD`, before any RBAC step runs.
UNCERTAINTY         The exact gcloud flag/condition that produced ENTERPRISE tier
                    originally is not confirmed — RUNTIME VALIDATION REQUIRED at
                    implementation time (re-run registration once, observe the
                    resulting tier, adjust flags if a flag is found to control it).
```

```
DECISION            Ansible applies Kubernetes RBAC directly against the target
                    cluster (impersonation + view + supplemental node-read), and
                    additionally writes the applied manifest as a versioned artifact
                    — it does not wait on or require the separate AshminPy/sre-k8s-rbac
                    repo (not present in this filesystem, content unverifiable).
EVIDENCE            iac/agent/onprem_fleet.tf's header states K8s RBAC "moved
                    entirely to the separate AshminPy/sre-k8s-rbac repo. Applied by an
                    authorized operator via plain `kubectl apply`" — Ansible, run by
                    the same authorized operator with the same access, is a direct
                    substitute for that manual `kubectl apply`, not a new actor.
WHY                 The onboarding operator already needs cluster-admin for
                    registration; requiring a second manual RBAC step in a different
                    repo would break "one playbook onboards a cluster" and reintroduce
                    the exact manual-step gap this proposal removes.
TRADEOFFS           The sre-k8s-rbac repo (if it still exists) would need to
                    separately adopt or ignore these Ansible-applied objects to avoid
                    drift — flagged as a MANUAL OWNER PREREQUISITE for whoever owns
                    that repo, not solved by this change.
VALIDATION METHOD   RBAC objects carry the same `connect.gke.io/owner-feature`-style
                    label convention already used by `generate-gateway-rbac`, plus an
                    Ansible-specific label, so ownership is identifiable and
                    idempotency checks can target exactly these objects.
UNCERTAINTY         Whether sre-k8s-rbac repo still exists or is still the intended
                    system of record — ASSUMED not blocking, since it's absent from
                    this filesystem and no longer referenced as active elsewhere in
                    current docs.
```

```
DECISION            Add a narrowly-scoped supplemental ClusterRole/ClusterRoleBinding
                    granting only `nodes: get,list` (cluster-scoped), layered on top
                    of the built-in `view` ClusterRole, instead of switching to a
                    broader built-in role or a fully custom role duplicating `view`.
EVIDENCE            docs/connect-gateway-onprem.md's own live-tested RBAC table shows
                    `kubectl get nodes` → Forbidden under `view` (expected, since
                    `view` excludes cluster-scoped resources). mcp/tools/nodes.py
                    confirms `list_node`/`read_node` are real, in-use MCP tool calls
                    — a live, uncaught gap today for any non-GKE cluster.
WHY                 Reuse Google's own `view` ClusterRole (matches this repo's
                    existing least-privilege pairing, per docs/least-privilege-iam.md)
                    and add only the one missing resource type, rather than
                    hand-maintaining a full custom role that must be kept in sync
                    with every future MCP tool addition.
TRADEOFFS           If a future MCP tool needs another resource `view` doesn't cover,
                    this supplemental role needs a matching narrow addition — accepted,
                    since the alternative (broad custom role up front) would violate
                    least privilege for a need that doesn't exist yet.
VALIDATION METHOD   tasks.md's "allowed operations succeed / forbidden mutation fails"
                    test explicitly includes `kubectl get nodes` succeeding
                    post-onboarding (currently fails) and `create`/`delete`/`get
                    secrets` still failing.
UNCERTAINTY         None outstanding — directly reproduces and fixes an already-
                    documented, already-tested gap.
```

## Ansible layout (portable vs. personal, per PORTABILITY)

```
ansible/
  roles/
    onprem_cluster_onboarding/     # portable — no personal values
      tasks/
        main.yml                  # orchestrates the numbered steps below
        preflight.yml              # kube-context reachable, gcloud authed, org policy check
        fleet_register.yml         # idempotent register + tier check (fail-loud)
        rbac.yml                   # idempotent impersonation + view + node-read bindings
        verify.yml                 # get-credentials, allowed read, forbidden write/secret/node-before-grant
        report.yml                 # per-cluster evidence artifact
      defaults/main.yml            # safe defaults only (role = clusterrole/view, tier = STANDARD, etc.)
      handlers/main.yml            # only if a genuine idempotent-restart need appears (none evidenced yet)
      meta/main.yml
  playbooks/
    onboard.yml                    # loops external_clusters from inventory, calls the role
    verify.yml                     # re-run just the verification subset (post-hoc health check)
    cleanup.yml                    # unregister + revoke, scoped to this run's created objects only
  inventories/
    kind/                          # personal validation data — sre-lab, sre-lab-2
      hosts.yml
      group_vars/all.yml
    work-nonprod/                  # future, NOT created in this change — placeholder documented in PORTING.md only
Makefile                            # kind-onprem-up / onprem-onboard-kind / onprem-check-kind / kind-onprem-down
PORTING.md
```

Inventory schema (per cluster, matches `external_clusters` conceptual shape from the request, expressed as Ansible host vars):
```yaml
external_clusters:
  - name: sre-lab
    kube_context: kind-sre-lab
    fleet_project_id: sreagent-t2-demo
    fleet_membership: sre-lab          # defaults to name if omitted, matching iac/agent/variables.tf's own convention
    environment: test
    rbac_role: clusterrole/view        # default; overridable per cluster if ever needed
    grant_node_read: true              # the supplemental parity fix; default true
  - name: sre-lab-2
    kube_context: kind-sre-lab-2
    fleet_project_id: sreagent-t2-demo
    fleet_membership: sre-lab-2
    environment: test
```

No personal project IDs, usernames, or paths appear in `roles/` — `fleet_project_id`, `kube_context`, and identity (`--users=`) are all inventory/host-var driven, sourced from `gcloud config`/environment at runtime, never hardcoded in role logic.
