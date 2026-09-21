## What kind can and cannot prove

**Can prove** (plain Fleet Membership registration has no documented Kubernetes-version floor or platform-tier requirement — verified against current GCP docs, see design.md): Fleet registration, Connect Agent install/health, Connect Gateway credential flow, RBAC (impersonation + `view` + node-read), idempotency, multi-cluster isolation, cleanup scoping, failure-path behavior, and a real SRE Agent investigation through Connect Gateway end to end.

**Cannot prove**: real network conditions of a VPN-reachable non-prod cluster at work (latency, intermittent connectivity, corporate firewall/proxy effects on outbound `googleapis.com`/`gkeconnect.googleapis.com` reachability), a non-kind Kubernetes distribution's RBAC/API-server quirks, or whether the target org's IAM/policy constraints (e.g. `constraints/iam.disableServiceAccountKeyCreation`) match this personal project's.

**Remaining real validation**: one live run of this exact Ansible workflow against a real non-prod, VPN-reachable, non-kind cluster, from a laptop over VPN — not substitutable by any amount of additional kind testing. Until that run happens, work-readiness is PARTIAL by definition, regardless of how much kind testing passes.

## Phase 0 — Preflight / scaffolding

- [ ] Confirm `sre-lab`/`sre-lab-2` kind containers are still running and healthy (`docker ps -a`, `kubectl --context kind-sre-lab get nodes`) — do not assume from memory/docs
- [ ] Confirm current `gcloud`/`ansible`/`kubectl` versions on this machine meet the documented prerequisites (design.md Evidence)
- [ ] Confirm both Fleet memberships are indeed currently unregistered (`gcloud container fleet memberships list --project=sreagent-t2-demo`) — expected per commit `cc9dbe0`, verify rather than assume
- [ ] Scaffold `ansible/` directory layout per design.md (roles, playbooks, inventories, Makefile, PORTING.md)
- [ ] `ansible-lint` and `yamllint` configured and passing on the scaffold (empty roles are trivially lintable; keep them green as content is added)

## Phase 1 — Reusable onboarding role (`ansible/roles/onprem_cluster_onboarding/`)

- [ ] `tasks/preflight.yml`: kube-context exists and is reachable; `gcloud auth` valid; target project APIs (`gkehub`, `connectgateway`, `container`) enabled (check, do not enable — EXISTING GCP BOOTSTRAP per proposal.md); org policy `constraints/iam.disableServiceAccountKeyCreation` checked to select WIF (`--has-private-issuer`) vs. SA-key fallback; explicit fail messages naming the cluster
- [ ] `tasks/fleet_register.yml`: check existing membership state first (`gcloud container fleet memberships describe`); register only if absent or not `READY`; **mandatory tier check** — fail loudly if resulting tier ≠ `STANDARD`, before proceeding
- [ ] `tasks/rbac.yml`: check existing `ClusterRoleBinding`s (label-based) before applying; idempotent `generate-gateway-rbac --role=clusterrole/view --apply`; idempotent supplemental `ClusterRole`/`ClusterRoleBinding` for cluster-scoped `nodes: get,list`; never widen beyond what's declared
- [ ] `tasks/verify.yml`: `get-credentials`; one real allowed read (`pods`, `deployments`, `events`, and now `nodes`); one real denied write (`create namespace`) and denied read (`get secrets`) — captured as evidence, not just exit-code-checked
- [ ] `tasks/report.yml`: per-cluster structured result (stage reached, pass/fail, command + short output) appended to a run-scoped artifact file
- [ ] `defaults/main.yml`: safe defaults only (`rbac_role: clusterrole/view`, `expected_tier: STANDARD`, `grant_node_read: true`)
- [ ] Confirm zero personal values (project ID, usernames, kind names, paths) anywhere under `roles/` — `grep`-verify before marking this phase done

## Phase 2 — Playbooks and inventory

- [ ] `playbooks/onboard.yml`: loops `external_clusters` from inventory, calls the role once per cluster, independent failure domains (one cluster's failure must not abort another's — verify with Ansible's `ignore_errors`/`max_fail_percentage` semantics, not a naive fail-fast loop)
- [ ] `playbooks/verify.yml`: re-runs only the verification subset, for post-hoc health checks without re-registering anything
- [ ] `playbooks/cleanup.yml`: revoke RBAC + unregister membership, scoped strictly to inventory-listed clusters — no wildcard Fleet cleanup
- [ ] `inventories/kind/hosts.yml` + `group_vars/all.yml`: `sre-lab` and `sre-lab-2` entries per design.md's schema

## Phase 3 — Kind test lab (disposable, separate from the role)

- [ ] `Makefile` targets: `kind-onprem-up`, `onprem-onboard-kind`, `onprem-check-kind`, `kind-onprem-down` (names may be adjusted to match repo `Makefile` conventions if one already exists — check before adding)
- [ ] Confirm the onboarding role contains zero kind-specific logic (spec requirement) — kind lifecycle lives only in the Makefile/test-lab layer

## Phase 4 — Validation (idempotency, multi-cluster, RBAC correctness)

- [ ] Run `onboard.yml` against both clusters from zero (both currently unregistered) — first run succeeds, both reach `READY`/`STANDARD`
- [ ] Run `onboard.yml` again immediately — second run is predominantly `ok`, no duplicate memberships/RBAC objects, no privilege broadening
- [ ] Simulate a partial prior onboarding (membership registered, RBAC not applied) — confirm the role resumes correctly rather than re-registering or skipping RBAC
- [ ] Confirm onboarding `sre-lab-2` does not modify or break `sre-lab`'s registration/RBAC state (and vice versa)
- [ ] Confirm allowed read operations succeed post-onboarding: `pods`, `deployments`, `events`, and — the parity fix — `nodes`
- [ ] Confirm denied operations remain denied: `create namespace`, `delete pod`, `get secrets`
- [ ] Confirm a real SRE Agent investigation runs successfully against `sre-lab` (or `sre-lab-2`) through the full path — Agent → Agent Gateway → custom Cloud Run MCP → Connect Gateway → cluster — reusing the already-proven live path (`docs/architecture/gke-vs-nongke.md` Layer B/C), now via Ansible-applied registration/RBAC instead of the original hand-run commands
- [ ] Confirm evidence output correctly identifies the target cluster for each investigation (no cross-cluster attribution errors)

## Phase 5 — Failure-path tests

- [ ] Invalid kube context → preflight fails, names cluster + context
- [ ] Unreachable cluster (e.g. kind container stopped) → preflight fails, names cluster
- [ ] Missing `gcloud` authentication → preflight fails before any mutation
- [ ] Insufficient cluster privileges (non-cluster-admin kubecontext) → registration/preflight fails with an explicit privilege error
- [ ] Membership name collision (register against an already-used unrelated name) → fails clearly, does not adopt/overwrite
- [ ] Fleet API unavailable/API not enabled → preflight or registration fails with the specific API error, not a generic timeout message
- [ ] Connect Agent not ready post-registration → verify stage fails and reports agent-pod state, not just a generic gateway error
- [ ] Incorrect/tampered RBAC (simulate a manually-deleted binding) → re-run detects and repairs, does not silently report success
- [ ] Already-registered cluster re-run → confirmed idempotent (covered in Phase 4, cross-referenced here as a failure-adjacent case: "already onboarded" must not be treated as an error)
- [ ] Confirm no run ever reports overall SUCCESS when any required stage did not complete (audit the reporting logic specifically for this, not just individual stage tests)

## Phase 6 — Cleanup

- [ ] `cleanup.yml` run against the kind inventory: memberships unregistered, RBAC revoked, kind clusters deleted
- [ ] Confirm the central `iac/agent/onprem_fleet.tf` IAM grant and any unrelated Fleet memberships/clusters are untouched
- [ ] Confirm no wildcard cleanup logic exists anywhere in `cleanup.yml` (inventory-scoped only — grep/review)
- [ ] State expected cost during the validation window (Fleet Membership registration itself: $0, per current GCP docs; confirm no Attached-Clusters-billing command path was used anywhere in the role) and confirm cleanup was actually run before considering this change validated

## Phase 7 — Portability documentation

- [ ] `PORTING.md`: what's portable (`roles/`, `playbooks/`) vs. personal (`inventories/kind/`); required GCP IAM (`roles/gkehub.admin` for registration, `roles/gkehub.gatewayReader`+`roles/gkehub.viewer` for runtime — per design.md Evidence); required local/runner tools (`gcloud`, `kubectl`, `ansible`, network reachability to `googleapis.com`/`gkeconnect.googleapis.com`); required cluster permissions (`cluster-admin` for the registering identity only); network requirements (outbound reachability from the target cluster's pods, and from wherever `gcloud`/Ansible runs, to Google's APIs); how to run from a laptop over VPN today; how it could later run from a Spacelift/private runner without redesign (same role, different inventory + execution environment)
- [ ] Confirm `PORTING.md` explicitly states no work credentials/configuration are or should be added to this personal repo — consistent with `PORTING_BRIEF_TO_APP_INFRA.md`'s existing exclusion

## Phase 8 — Review and completion

- [ ] Independent review (fresh-context reviewer — e.g. `automation-reviewer` for the Ansible/idempotency surface, `iam-reviewer` for the RBAC/IAM surface) with all MUST FIX findings resolved
- [ ] `ansible-lint`/`yamllint` clean
- [ ] Full Groundwork completion block filed in the PR description, with `Overall: PARTIAL` unless the real non-prod work-cluster validation (see "What kind cannot prove") has also actually run — do not claim COMPLETE or work-ready from kind alone

## Definition of Done (tracked here, checked at close-out)

- [ ] Reusable Ansible onboarding automation exists, with no cluster-specific logic hardcoded into roles
- [ ] Both `sre-lab` and `sre-lab-2` onboarded successfully via this workflow
- [ ] Repeated execution proven idempotent (Phase 4)
- [ ] Fleet/Connect Gateway/RBAC behavior verified against current official Google documentation (design.md Evidence) — not against memory of older commands
- [ ] SRE Agent performs a real investigation against at least one onboarded cluster through the full live path
- [ ] Mutation attempts remain denied (verified, not assumed)
- [ ] Cleanup works without touching unrelated resources (Phase 6)
- [ ] Independent review has no unresolved MUST FIX findings
- [ ] Documentation explains both personal (kind) testing and work portability (`PORTING.md`)
- [ ] If any real external-cluster behavior remains unproven by kind, it is named explicitly (see "What kind cannot prove") and the overall status is reported PARTIAL, not COMPLETE
