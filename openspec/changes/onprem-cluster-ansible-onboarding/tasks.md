## What kind can and cannot prove

**Can prove** (plain Fleet Membership registration has no documented Kubernetes-version floor or platform-tier requirement — verified against current GCP docs, see design.md): Fleet registration, Connect Agent install/health, Connect Gateway credential flow, RBAC (impersonation + `view` + node-read), idempotency, multi-cluster isolation, cleanup scoping, failure-path behavior, and a real SRE Agent investigation through Connect Gateway end to end.

**Cannot prove**: real network conditions of a VPN-reachable non-prod cluster at work (latency, intermittent connectivity, corporate firewall/proxy effects on outbound `googleapis.com`/`gkeconnect.googleapis.com` reachability), a non-kind Kubernetes distribution's RBAC/API-server quirks, or whether the target org's IAM/policy constraints (e.g. `constraints/iam.disableServiceAccountKeyCreation`) match this personal project's.

**Remaining real validation**: one live run of this exact Ansible workflow against a real non-prod, VPN-reachable, non-kind cluster, from a laptop over VPN — not substitutable by any amount of additional kind testing. Until that run happens, work-readiness is PARTIAL by definition, regardless of how much kind testing passes.

## Phase 0 — Preflight / scaffolding

- [x] Confirm `sre-lab`/`sre-lab-2` kind containers are still running and healthy — verified live 2026-09-21: `docker ps -a` shows all 4 containers `Up` (`sre-lab-control-plane` 6wk, `sre-lab-worker`/`worker2` 6wk, `sre-lab-2-control-plane` 12d); `kubectl --context kind-sre-lab/-2 get nodes` → all `Ready`, `v1.35.0`
- [x] Confirm current `gcloud`/`kubectl`/`kind`/`docker` versions meet documented prerequisites — verified live 2026-09-21: `gcloud` 573.0.0 (SDK present at `~/Downloads/google-cloud-sdk`, not on shell PATH — used via full path), authenticated as `ashmin.sub@gmail.com`, active project `sreagent-t2-demo`; `kind` 0.31.0; `docker` 29.5.3. `ansible` was not installed — installed via `brew install ansible` this session (tracked as a one-time local machine setup, not part of the portable workflow)
- [x] Confirm both Fleet memberships are indeed currently unregistered — verified live 2026-09-21: `gcloud container fleet memberships list --project=sreagent-t2-demo` → `Listed 0 items`; `gke-connect` namespace absent on both clusters (clean prior unregister, matches commit `cc9dbe0`)
- [x] Confirm required project APIs already enabled — verified live 2026-09-21: `gkehub`, `connectgateway`, `container` all present in `gcloud services list` (no EXISTING GCP BOOTSTRAP step needed for this project)
- [x] Confirm the org policy that rules out any SA-key path is actually enforced (correction #1) — verified live 2026-09-21: `gcloud resource-manager org-policies describe constraints/iam.disableServiceAccountKeyCreation --effective` → `booleanPolicy: {enforced: true}`
- [x] Verify the exact current `gcloud container fleet memberships register` flags for keyless registration, the current field reporting Fleet Membership tier, and current tier/billing semantics, against official docs fetched now (correction #1, #4) — verified 2026-09-21: `--enable-workload-identity`/`--has-private-issuer`/`--public-issuer-url` current and unchanged; `--service-account-key-file` confirmed still exists (never used); no `--tier` flag exists anywhere on `register`; `clusterTier` on the `Membership` REST resource is output-only; GKE docs state tier was retired project-wide 2025-09-23 with Fleets/Connect Gateway folded into GKE Standard "at no additional cost" — full citations in design.md's DECISION records. Two follow-ups carried into Phase 4/6: (a) no documented version gate for `--has-private-issuer` under this command — preflight uses a functional OIDC-endpoint reachability check instead of a version check; (b) the primary GKE pricing page itself couldn't be fetched (tool truncation) — cross-check real GCP Billing Console data during Phase 4/6 rather than trusting the blog-post summary alone
- [ ] Scaffold `ansible/` directory layout per design.md (roles, playbooks, inventories, Makefile, PORTING.md)
- [ ] `ansible-lint` and `yamllint` configured and passing on the scaffold (empty roles are trivially lintable; keep them green as content is added)

## Phase 1 — Reusable onboarding role (`ansible/roles/onprem_cluster_onboarding/`)

- [ ] `tasks/preflight.yml`: kube-context exists and is reachable; `gcloud auth` valid; target project APIs (`gkehub`, `connectgateway`, `container`) enabled (check, do not enable — EXISTING GCP BOOTSTRAP per proposal.md); confirm the target cluster can satisfy keyless Fleet Workload Identity registration via a FUNCTIONAL check — `kubectl get --raw /.well-known/openid-configuration` and `/openid/v1/jwks` against the target context succeed (no documented version gate exists for `--has-private-issuer` under this command, so a version check would be a guess) — **no service-account-key branch exists to fall back to**; if keyless prerequisites aren't met, fail with the specific missing prerequisite named, per cluster; a static check confirms `--service-account-key-file` never appears in any rendered `gcloud` command this role builds
- [ ] `tasks/fleet_register.yml`: check existing membership state first (`gcloud container fleet memberships describe`); register only if absent or not `READY`; **mandatory tier check** — since no `register`-time flag can request a tier (`clusterTier` is output-only, confirmed), immediately `describe` after registering and fail loudly + auto-unregister if `clusterTier == ENTERPRISE`, before any RBAC step runs
- [ ] `tasks/rbac.yml`: check existing `ClusterRoleBinding`s (label-based, correction #3) before applying; idempotent `generate-gateway-rbac --role=clusterrole/view --apply`; idempotent supplemental `ClusterRole`/`ClusterRoleBinding` granting cluster-scoped `nodes: get,list` — verbs confirmed by re-reading `mcp/tools/nodes.py`'s actual calls (`list_node` → `list`, `read_node` → `get`) and confirming (grep `mcp/tools/`) that no tool anywhere calls `watch`, so `watch` is deliberately NOT granted (correction #5); never widen beyond what's declared
- [ ] `tasks/verify.yml`: `get-credentials`; one real allowed read (`pods`, `deployments`, `events`, and now `nodes`); one real denied write (`create namespace`) and denied read (`get secrets`) — captured as evidence, not just exit-code-checked
- [ ] `tasks/report.yml`: per-cluster structured result (stage reached, pass/fail, command + short output) appended to a run-scoped artifact file
- [ ] `defaults/main.yml`: safe defaults only (`rbac_role: clusterrole/view`, `expected_tier: STANDARD`, `grant_node_read: true`)
- [ ] Confirm zero personal values (project ID, usernames, kind names, paths) anywhere under `roles/` — `grep`-verify before marking this phase done

## Phase 2 — Playbooks and inventory

- [ ] `playbooks/onboard.yml`: loops `external_clusters` from inventory; each cluster's onboarding runs inside an explicit `block`/`rescue` that records a per-cluster PASS/FAIL result (stage reached, error if any) into a shared results list — **no `ignore_errors: true`, no `max_fail_percentage`** (correction #2); a final task inspects the aggregated results and fails the play (non-zero exit) if any required cluster is FAIL, after every cluster has been attempted
- [ ] `playbooks/verify.yml`: re-runs only the verification subset, for post-hoc health checks without re-registering anything
- [ ] `playbooks/cleanup.yml`: revoke RBAC + unregister membership, scoped strictly to inventory-listed clusters — no wildcard Fleet cleanup
- [ ] `inventories/kind/hosts.yml` + `group_vars/all.yml`: `sre-lab` and `sre-lab-2` entries per design.md's schema

## Phase 3 — Kind test lab (disposable, separate from the role)

- [ ] `Makefile` targets: `kind-onprem-up`, `onprem-onboard-kind`, `onprem-check-kind`, `kind-onprem-down` (names may be adjusted to match repo `Makefile` conventions if one already exists — check before adding)
- [ ] Confirm the onboarding role contains zero kind-specific logic (spec requirement) — kind lifecycle lives only in the Makefile/test-lab layer

## Live validation findings, 2026-09-21 (read before touching this workflow again)

User-authorized a bounded live test against `sre-lab` only (accepting the known
ENTERPRISE-tier cost risk, real math: ~$0.0025/min based on the historical
$216/month run-rate, for a session totaling well under half an hour of real
exposure across all attempts). Real bugs found and fixed, in the order hit:

1. **`gather_facts: false` broke `ansible_env.HOME`** on all three playbooks —
   incidentally proved correction #2 works: two clusters failed independently,
   both recorded, playbook exited non-zero naming both. Fixed:
   `gather_facts: true` (cheap on `localhost`).
2. **Real ENTERPRISE-tier assignment reproduced live**, twice (once per
   cluster) — the fail-loud check fired and auto-unregistered correctly both
   times, confirmed via a clean `memberships list` afterward. This resolved the
   design's open UNCERTAINTY: there is no register-time flag or Fleet setting
   that yields `STANDARD` for a non-GKE cluster; the safety check is not a
   false positive, it is load-bearing. See design.md's updated DECISION record.
3. **`.items` dot-notation bug** (`rbac.yml`): `(parsed_json).items` resolved
   to Python's real `dict.items()` BOUND METHOD, not the JSON `"items"` key
   (every `kubectl -o json` list response uses that exact key) — `json_query`
   then silently returned `None`. This would have made idempotency detection
   ALWAYS think "nothing exists" and re-apply RBAC on every single run,
   forever. Fixed: subscript `['items']`. Verified offline (empty-list and
   real-match cases) before any further live testing, to avoid burning another
   live cycle on a re-guess.
4. **`default([])`/`default({})` without the second arg only catches
   Undefined, not an explicit `None`** (two spots: `rbac.yml`'s
   `_gateway_rbac_present`, `cleanup_one_cluster.yml`'s `_membership_labels`) —
   fixed with `default(X, true)` in both.
5. **`gke-gcloud-auth-plugin` not installed/not on PATH** on this machine —
   local one-time setup gap, not a role bug. Installed via
   `gcloud components install gke-gcloud-auth-plugin`, added the SDK `bin/` dir
   to PATH.
6. **`verify.yml` tested the wrong identity.** `get-credentials` mints a
   context using the CALLER's own gcloud identity, not `runtime_identity` — the
   RBAC this role grants is scoped to `runtime_identity` only (least privilege;
   we deliberately do not also grant the operator broad K8s access). Every read
   came back `Forbidden: unknown` even though the RBAC objects were
   independently confirmed correct by direct `kubectl` inspection. Fixed:
   impersonate `runtime_identity` via a persistent
   `gcloud config set auth/impersonate_service_account`, wrapped in
   `block`/`always` so it's never left configured globally. **NOT yet
   re-verified live** — the identity running this session has no
   `roles/iam.serviceAccountTokenCreator` on the runtime SA (confirmed empty
   IAM policy), and granting that is a separate IAM decision, not bundled into
   this bounded test. The fix's logic was reasoned through carefully but the
   actual impersonated read/deny checks remain unproven live.
7. **`cleanup.yml` had a real ownership-cleanup gap**: RBAC revoke/delete was
   nested inside "only if we own the membership" — hit for real, twice, when
   the membership was already unregistered (via the tier rollback) but RBAC
   objects were still live on the cluster, orphaned, because cleanup never
   looked at them. Fixed: RBAC cleanup (both `generate-gateway-rbac --revoke`
   and the node-read label-delete) now always runs, tolerant of "already
   revoked/never applied" (confirmed idempotent-safe via real `gcloud` output:
   `"... not exist."` messages, `rc=0`); only the Fleet membership unregister
   step stays gated behind the ownership-label check.
8. **`cleanup.yml` was missing `onboarded_by_label_key`/`value`** — these are
   role `defaults/main.yml` values, but `cleanup.yml` never calls
   `include_role`, so they were never loaded. Fixed: `vars_files:` pointing at
   the role's own defaults file (single source of truth, no duplication/drift
   vs. `rbac.yml`).

**What this proved, with real evidence, not staged:**
- RBAC creation is correctly scoped and idempotent: the "gateway-permission"
  binding (subject = `runtime_identity`) was correctly labeled as
  workflow-owned; the "gateway-impersonate" binding (subject =
  `connect-agent-sa`) was correctly LEFT UNLABELED (the loop's `when` condition
  matches subjects, and `connect-agent-sa` != `runtime_identity` — confirmed in
  the real task output, not assumed).
- The ownership fixture test (correction #3) passed with real evidence: an
  unrelated, unlabeled `ClusterRoleBinding` manually created on `sre-lab`
  survived two full real `cleanup.yml` runs untouched, while the workflow's own
  RBAC state was correctly reported (`ALREADY_ABSENT` once no membership
  existed).
- `cleanup.yml` now runs cleanly end-to-end (exit 0) against both clusters from
  a fully-torn-down state.

**What remains unproven (scope for a follow-up session, not blocking this
change's core design):**
- A full live run reaching `verify.yml`'s actual read/deny assertions AS
  `runtime_identity` (needs the IAM impersonation grant decision above).
- `sre-lab-2`'s live registration/RBAC/verify path (this bounded test was
  deliberately scoped to `sre-lab` only, to minimize cost exposure; `sre-lab-2`
  only exercised its preflight and cleanup-tolerance paths this session).
- A second consecutive successful run proving idempotency's "mostly `ok`"
  behavior (blocked on the same ENTERPRISE-tier cost question — no cluster
  currently holds a stable, accepted registration to idempotently re-run
  against).
- The real SRE Agent investigation through the full production path (requires
  a separate, explicitly-authorized `terraform apply` to flip `sre-lab`'s
  registry entry to `enabled: true` — deliberately kept out of this bounded
  test's scope).

## Phase 4 — Validation (idempotency, multi-cluster, RBAC correctness)

- [x] **First live run, 2026-09-21** (`ansible-playbook playbooks/onboard.yml`): failed on
  both clusters at the fleet-registration step with `'ansible_env' is undefined` (root
  cause: `gather_facts: false` on all three playbooks meant `ansible_env.HOME` was never
  populated). This is a real bug, not a design flaw -- and it incidentally proved
  correction #2 works: `sre-lab` and `sre-lab-2` both failed independently, `sre-lab-2`'s
  attempt was unaffected by `sre-lab`'s failure (`rescued=2` in the play recap), and the
  playbook correctly exited non-zero (2) with `"Onboarding failed for: sre-lab, sre-lab-2"`
  naming both. Fix: `gather_facts: true` on all three playbooks (cheap on `localhost`, more
  robust than replacing every `ansible_env` reference with `lookup('env', ...)`).

- [ ] Run `onboard.yml` against both clusters from zero (both currently unregistered) — first run succeeds, both reach `READY`/`STANDARD`
- [ ] Run `onboard.yml` again immediately — second run is predominantly `ok`, no duplicate memberships/RBAC objects, no privilege broadening
- [ ] Simulate a partial prior onboarding (membership registered, RBAC not applied) — confirm the role resumes correctly rather than re-registering or skipping RBAC
- [ ] Confirm onboarding `sre-lab-2` does not modify or break `sre-lab`'s registration/RBAC state (and vice versa)
- [ ] Confirm allowed read operations succeed post-onboarding: `pods`, `deployments`, `events`, and — the parity fix — `nodes`
- [ ] Confirm denied operations remain denied: `create namespace`, `delete pod`, `get secrets`
- [ ] Confirm a real SRE Agent investigation runs successfully against `sre-lab` (or `sre-lab-2`) through the full path — Agent → Agent Gateway → custom Cloud Run MCP → Connect Gateway → cluster — reusing the already-proven live path (`docs/architecture/gke-vs-nongke.md` Layer B/C), now via Ansible-applied registration/RBAC instead of the original hand-run commands
- [ ] Confirm evidence output correctly identifies the target cluster for each investigation (no cross-cluster attribution errors)
- [ ] **Correction #4 cross-check**: some hours after both clusters are registered, check real GCP Billing Console / SKU-level cost data for `sreagent-t2-demo` (not just `clusterTier` from `describe`) — confirm no unexpected new charge appears, since current docs suggest Fleet-tier billing may have been retired project-wide in Sept 2025 and `clusterTier` may no longer be the operative billing signal it was during the original incident

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
- [ ] **Correction #2, deliberate test**: force `sre-lab` to fail (invalid context) while `sre-lab-2` is healthy in the same inventory/run → confirm `sre-lab-2` still completes successfully, the per-cluster report shows `sre-lab: FAIL` / `sre-lab-2: PASS`, and `ansible-playbook`'s own process exit code is non-zero (`echo $?` after the run — not just reading the printed summary)
- [ ] Confirm no run ever reports overall SUCCESS when any required stage did not complete (audit the reporting logic specifically for this, not just individual stage tests) — grep the finished role/playbook source for `ignore_errors` and `max_fail_percentage` and confirm neither appears

## Phase 6 — Ownership-safe cleanup (correction #3)

- [ ] Before writing `cleanup.yml`: inspect the actual live objects `generate-gateway-rbac --apply` created against `sre-lab` (`kubectl get clusterrole,clusterrolebinding -l connect.gke.io/owner-feature=connect-gateway -o yaml`) — confirm in writing what labels/annotations are and are not present; do not assume
- [ ] Confirm every object this workflow's `rbac.yml` creates (the `view` binding, the supplemental node-read binding) carries the workflow's own ownership label (`sre-agent-gateway.internal/onboarded-by: ansible-onprem-onboarding`) — verified by reading the object back after apply, not just by reading the task source
- [ ] **Fixture test, required**: manually create one unrelated `ClusterRoleBinding` on `sre-lab` (simulating a pre-existing/adopted object, e.g. named similarly or matching the same `view`-role pattern but without the ownership annotation) → run `cleanup.yml` → confirm this fixture object still exists afterward, untouched
- [ ] `cleanup.yml`: unregister Fleet membership + revoke only label-matched RBAC objects; scoped strictly to inventory-listed clusters — no wildcard Fleet or RBAC cleanup (grep/review `cleanup.yml` to confirm no name-only or cluster-membership-only matching logic exists)
- [ ] Run `cleanup.yml` against the kind inventory for real: memberships unregistered, only workflow-owned RBAC objects revoked, kind clusters deleted
- [ ] Confirm the central `iac/agent/onprem_fleet.tf` IAM grant and any unrelated Fleet memberships/clusters are untouched
- [ ] State expected cost during the validation window (Fleet Membership registration itself: re-confirmed against current pricing docs during Phase 0's live-doc check, not assumed $0 from the earlier proposal) and confirm cleanup was actually run before considering this change validated

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
