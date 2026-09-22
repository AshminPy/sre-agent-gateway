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
  - [`gcloud container fleet memberships register` reference](https://docs.cloud.google.com/sdk/gcloud/reference/container/fleet/memberships/register) — re-fetched 2026-09-21, direct quotes: `--enable-workload-identity`, `--has-private-issuer`, `--public-issuer-url` confirmed current; `--service-account-key-file` confirmed still present (never used by this design); no `--tier` flag exists.
  - [Fleet `Membership` REST reference](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/reference/rest/v1/projects.locations.memberships) — `clusterTier` confirmed output-only (`CLUSTER_TIER_UNSPECIFIED`/`STANDARD`/`ENTERPRISE`).
  - [GKE editions](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/gke-editions) — "GKE clusters no longer have a `tier`" (2025-09-23 release note referenced).
  - [GKE 10th-birthday pricing announcement](https://cloud.google.com/blog/products/containers-kubernetes/gke-gets-new-pricing-and-capabilities-on-10th-birthday) — Fleets/Connect Gateway consolidated into GKE Standard "at no additional cost" as of September 2025. Note: the primary `cloud.google.com/kubernetes-engine/pricing` page itself could not be fetched in full this session (tool truncation) — treat the exact current fee wording as UNVERIFIED against the pricing page directly, this blog post is official-adjacent, not the pricing page.

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
DECISION            Fleet tier is checked as STANDARD after every registration,
                    detection-only (there is no register-time flag to request a
                    tier), and the run fails loud + immediately unregisters if it is
                    not — re-verified against current docs, not assumed unchanged.
EVIDENCE            Commit cc9dbe0: both prior memberships were registered at
                    clusterTier=ENTERPRISE, causing a real ~$216/month charge,
                    confirmed via live GCP Billing Console data. Re-verified 2026-09-21
                    against current docs (correction #4): the REST reference for
                    `Membership` (docs.cloud.google.com/kubernetes-engine/fleet-
                    management/docs/reference/rest/v1/projects.locations.memberships)
                    shows `clusterTier` as an **output-only** enum
                    (`CLUSTER_TIER_UNSPECIFIED`/`STANDARD`/`ENTERPRISE`) — confirmed no
                    `--tier`/`--enterprise`/`--standard` flag exists anywhere in the
                    current `gcloud container fleet memberships register` flag list.
                    Separately, docs.cloud.google.com/kubernetes-engine/docs/concepts/
                    gke-editions states (dated to a 2025-09-23 release note) "GKE
                    clusters no longer have a `tier`", and the official GKE 10th-
                    birthday blog post (cloud.google.com/blog/products/containers-
                    kubernetes/gke-gets-new-pricing-and-capabilities-on-10th-birthday)
                    states Fleets/Connect Gateway moved into a single GKE Standard
                    tier "at no additional cost" as of September 2025.
WHY                 Because tier is backend-assigned with no client-side flag to
                    request STANDARD, the only available control is detect-and-abort,
                    not prevent-by-flag — this was already true, but is now confirmed
                    rather than assumed. Separately, the docs above raise a real,
                    unresolved question this design does NOT paper over: if Fleet-tier
                    billing was genuinely retired project-wide in September 2025, the
                    `clusterTier=ENTERPRISE` finding in commit cc9dbe0 may not fully
                    explain the ~$216/month charge on its own, or the retirement may
                    not (yet, or ever) have applied identically to this project/product
                    surface. This design does not assume either explanation — it keeps
                    the `clusterTier` check as a cheap, zero-downside guard AND adds a
                    live Cloud Billing/cost check as a second, independent signal,
                    rather than trusting one field to fully explain historical billing.
TRADEOFFS           None on the `clusterTier` check — pure safety, no functional cost.
                    The added billing-console check is extra implementation work with
                    no guarantee of a clean real-time cost signal (billing data often
                    lags by hours), accepted because the alternative is repeating the
                    exact "not caught until a dedicated cost audit" failure mode.
VALIDATION METHOD   `gcloud container fleet memberships describe` runs immediately
                    after registration; the task fails (not warns) if
                    `clusterTier == ENTERPRISE` (or any value other than `STANDARD`/
                    `CLUSTER_TIER_UNSPECIFIED`), before any RBAC step runs, and
                    triggers an automatic unregister of that membership. Additionally,
                    `tasks.md` Phase 4 checks actual GCP Billing Console / SKU-level
                    cost data some time after registering both clusters, rather than
                    trusting `clusterTier` alone to mean "this is free."
UNCERTAINTY         **RESOLVED BY LIVE EVIDENCE, 2026-09-21 — updates the hypothesis
                    above.** Ran the real registration live against both `sre-lab` and
                    `sre-lab-2`: BOTH landed on `clusterTier: ENTERPRISE`, confirmed via
                    `gcloud container fleet memberships describe` immediately after
                    registration. The fail-loud check fired correctly for both and
                    auto-unregistered both within seconds (confirmed clean state
                    afterward: `gcloud container fleet memberships list` → 0 items).
                    This directly falsifies the "maybe `clusterTier` is vestigial
                    post-Sept-2025" branch of the earlier hypothesis. Follow-up
                    research (2026-09-21, same session) found a CURRENT official page
                    (docs.cloud.google.com/kubernetes-engine/docs/how-to/creating-fleets,
                    last updated 2026-09-18 — three days before this test) stating
                    plainly: "Any third-party clusters you register will incur a
                    per-vCPU charge as part of your GKE pricing." The Sept-2025
                    "no additional cost" announcement names only four specific features
                    (Fleets, Teams, Config Management, Policy Controller) — Connect
                    Agent/third-party cluster registration is not among them, and this
                    current page's billing statement is not superseded anywhere found.
                    No flag, Fleet-level setting, or org policy was found (gcloud
                    reference, Fleet feature list/describe on this exact project, or
                    docs) that can select `STANDARD` tier for this registration path —
                    the only documented reduced-membership mechanism ("lightweight
                    membership") is scoped exclusively to GKE clusters already running
                    on Google Cloud, via a completely different command
                    (`gcloud container clusters create/update --membership-type=
                    LIGHTWEIGHT`), not usable for an external cluster at all.
                    CONCLUSION: registering a non-GKE cluster into this Fleet via
                    `gcloud container fleet memberships register` for Connect Gateway
                    access appears to be billed, unconditionally, at ENTERPRISE tier,
                    with no currently-documented way to avoid it. This is not a defect
                    in this workflow's design — the mandatory fail-loud check is
                    exactly the correct behavior given this evidence, not a false
                    positive to route around. Whether to accept a bounded, short-lived
                    ENTERPRISE-tier registration (register → verify → immediately
                    unregister, to complete Phase 4's live RBAC/investigation proof)
                    is a real cost decision requiring the repo owner's explicit
                    authorization, not an engineering judgment call — flagged back to
                    the user rather than decided unilaterally. Real GCP Billing Console
                    data for the ~1-2 minute exposure window from this test has not yet
                    been checked (billing data typically lags hours) and remains the
                    strongest still-available evidence if this needs to be settled
                    further. The primary GKE pricing page itself still could not be
                    fetched in full (tool-side truncation) — exact current per-vCPU
                    rate remains UNVERIFIED against the pricing page directly; the
                    `creates-fleets` page's plain-language billing statement is treated
                    as the strongest currently-available evidence in its place.
```

```
DECISION            CORRECTED 2026-09-22 -- SUPERSEDES the DECISION and CONCLUSION
                    above. Fleet-tier safety is now an explicit approval gate
                    (`allow_billable_external_cluster`, default false, checked BEFORE
                    any Fleet mutation), not a post-registration `clusterTier ==
                    ENTERPRISE` check-and-unregister. `clusterTier` is now recorded
                    as informational evidence only and never triggers an automatic
                    unregister.
EVIDENCE            The user independently re-verified current Google documentation
                    and identified a flaw in the reasoning above: it treated
                    `clusterTier == ENTERPRISE` as proof of billing, but a current
                    Google doc (docs.cloud.google.com/kubernetes-engine/docs/concepts/
                    gke-editions) states GKE no longer has separate Standard/Enterprise
                    commercial editions at all -- `clusterTier` is legacy, output-only
                    metadata the API can still return, and its value alone does not
                    prove a cluster is billed. The actual documented cost driver,
                    confirmed independently and consistent with the CONCLUSION above,
                    is registering a THIRD-PARTY/non-GKE cluster into a Fleet at all
                    (current GKE pricing lists GKE Multicloud Attached Clusters at a
                    per-vCPU/hour charge; the same creating-fleets page cited above
                    confirms this for third-party Fleet registration specifically) --
                    independent of what `clusterTier` later reports.
WHY                 The old design's safety boundary (detect ENTERPRISE, roll back)
                    conflated two different things: a legacy metadata field, and the
                    real, documented cost driver (the registration itself). Gating on
                    the metadata field is both potentially over-broad (it could
                    trigger on a value that no longer means anything, given the tier
                    concept's retirement) and under-protective in spirit (it implies
                    a cluster reporting `STANDARD`/`CLUSTER_TIER_UNSPECIFIED` would be
                    "safe," when the actual cost driver -- third-party registration --
                    applies regardless of tier). Gating on explicit, per-cluster human
                    approval of "registering this non-GKE cluster is a documented,
                    accepted cost" is the correct boundary: it matches what's actually
                    documented, doesn't depend on interpreting a field whose meaning
                    is now uncertain, and requires a deliberate decision rather than
                    an automatic tier-based verdict.
TRADEOFFS           The workflow can no longer detect, after the fact, whether a
                    registration "should" have been cheaper -- it simply requires
                    up-front acceptance that registering a non-GKE cluster is billed,
                    which is the conservative, doc-grounded default. `clusterTier` is
                    still captured as evidence (it may still correlate with real
                    billing behavior even if its name is now legacy) -- just not
                    acted upon automatically.
VALIDATION METHOD   `ansible/roles/onprem_cluster_onboarding/tasks/fleet_register.yml`:
                    a `fail` task fires before the `register` command whenever
                    registration would create a new membership AND
                    `allow_billable_external_cluster` is not explicitly true for that
                    cluster -- zero Fleet mutation occurs in that case (live-verified,
                    2026-09-22: ran against `sre-lab` with the approval unset, failed
                    at exactly this task, `gcloud container fleet memberships list`
                    confirmed 0 items throughout). When approval is true, the
                    unregister-on-tier code path has been removed entirely (grep-
                    confirmed: no `unregister` task exists in `fleet_register.yml`
                    outside this comment) -- structurally guarantees a cluster is
                    never rolled back based on `clusterTier` alone.
UNCERTAINTY         Whether `clusterTier` correlates with actual billing at all
                    post-tier-retirement remains genuinely unclear from documentation
                    alone (the strongest available evidence is the creating-fleets
                    page's plain statement about third-party registration, not a
                    `clusterTier`-specific pricing statement) -- this is exactly why
                    the design no longer depends on that field for its safety
                    decision. The exact current per-vCPU rate remains UNVERIFIED
                    against the primary GKE pricing page (still not fetchable in full
                    in this session).
```

```
DECISION            No service-account-key code path exists anywhere in this workflow.
                    Registration uses keyless Fleet Workload Identity only
                    (`--enable-workload-identity`, plus `--has-private-issuer` for a
                    cluster whose API server is not publicly routable). If a target
                    cluster cannot satisfy keyless prerequisites, preflight fails with
                    an explicit, named-prerequisite error. There is no fallback branch
                    to a static SA key to remove later — it is simply never written.
EVIDENCE            `constraints/iam.disableServiceAccountKeyCreation` is confirmed
                    LIVE ENFORCED on `sreagent-t2-demo` (`gcloud resource-manager
                    org-policies describe ... --effective` → `booleanPolicy: {enforced:
                    true}`, checked 2026-09-21) — a key-based path would not even work
                    here, so building one would be dead code that only creates risk if
                    the policy ever changes. `docs/connect-gateway-onprem.md` already
                    hit `FAILED_PRECONDITION: Key creation is not allowed` when it
                    tried key creation as a fallback test.
WHY                 A code path that is never supposed to execute is still an attack
                    surface and a maintenance burden, and "temporarily fall back to a
                    key" is exactly the kind of exception that survives past its
                    justification. The architecture requirement is unconditional: no
                    long-lived/static keys, full stop.
TRADEOFFS           A cluster whose API server cannot be reached directly by whoever
                    runs `gcloud` (so `--has-private-issuer` cannot read its issuer/
                    JWKS) and that also has no public OIDC discovery endpoint (so plain
                    `--enable-workload-identity` cannot verify it either) cannot be
                    onboarded by this workflow at all. This is treated as a correct,
                    intentional limitation, not a gap to route around with a key.
VALIDATION METHOD   Re-verified 2026-09-21 against the current `gcloud container fleet
                    memberships register` reference
                    (docs.cloud.google.com/sdk/gcloud/reference/container/fleet/
                    memberships/register — fetched directly, flag text quoted):
                    `--enable-workload-identity`, `--has-private-issuer`, and a third
                    current option `--public-issuer-url` (mutually exclusive with
                    `--has-private-issuer`) are confirmed current and unchanged in
                    meaning. `--service-account-key-file` is CONFIRMED to still exist
                    as a live flag ("stored as a secret named `creds-gcp` in
                    gke-connect namespace") — `preflight.yml`/`fleet_register.yml`
                    never construct a command containing this flag, and a static
                    assertion (grep / Ansible `assert`) confirms it appears nowhere in
                    the role's rendered command. No Kubernetes-version or platform-
                    version prerequisite specific to `--has-private-issuer` was found
                    on the plain `fleet memberships register` prerequisites page (that
                    kind of version gate exists only for the separate `attached
                    clusters register` product/command, confirmed as a different
                    command family) — so preflight uses a FUNCTIONAL check instead of a
                    version gate: confirm the target cluster's API server serves
                    `/.well-known/openid-configuration` and `/openid/v1/jwks`
                    (`kubectl get --raw ...` against the target context), which is what
                    `--has-private-issuer` needs to read directly from the cluster.
UNCERTAINTY         The precise internal mechanism of `--has-private-issuer` (does
                    `gcloud` read the issuer/JWKS from the API server and upload it to
                    the Membership once, vs. some other flow) was not confirmed via a
                    direct quote from an official page this session — only inferred
                    from the flag's own description and secondary sources. This does
                    not block implementation (the functional preflight check above
                    tests the actual prerequisite, not the mechanism), but is flagged
                    as UNVERIFIED rather than stated as settled fact.
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
VALIDATION METHOD   The `connect.gke.io/owner-feature: connect-gateway` label that
                    `generate-gateway-rbac` applies is shared across every gateway RBAC
                    object it has ever created for any user/membership — it identifies
                    the *mechanism*, not *this workflow's specific run*, and is
                    therefore NOT sufficient on its own to distinguish "created by this
                    Ansible workflow" from "pre-existing/adopted" (correction #3). This
                    workflow additionally applies its own label
                    (`sre-agent-gateway.internal/onboarded-by: ansible-onprem-onboarding`
                    — a label, not an annotation, deliberately: `kubectl -l` selectors
                    only match labels, and cleanup needs to select by this signal) to
                    every object it creates, and cleanup only ever acts on objects
                    carrying that label — never on name-match or cluster-membership
                    alone. Before relying on this, Phase 1 inspects the actual live
                    output of `generate-gateway-rbac --apply` (`kubectl get
                    clusterrolebinding -l connect.gke.io/owner-feature=connect-gateway
                    -o yaml`) to confirm what it does and does not set, rather than
                    assuming. A dedicated fixture test (a pre-existing, unrelated RBAC
                    object without this annotation) proves cleanup leaves it untouched.
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
                    `kubectl get nodes` → Forbidden under the `view` binding applied to
                    `sre-lab` — the evidenced fact is that this specific binding, in
                    this environment, does not permit the `nodes` API operation the
                    MCP tools need, not a generic claim about what `view` covers.
                    mcp/tools/nodes.py confirms `list_node`/`read_node` (i.e. `list`
                    and `get`, not `watch` — no `watch` call exists anywhere in
                    `mcp/tools/`, confirmed by repo-wide grep) are real, in-use MCP
                    tool calls — a live, uncaught gap today for any non-GKE cluster.
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

```
DECISION            Per-cluster isolation uses an explicit `block`/`rescue` around each
                    cluster's onboarding tasks plus a hand-built result-aggregation
                    list, not Ansible's broad `ignore_errors: true` (which marks a
                    failed task "ignored" and lets the overall play report success even
                    though something real failed) or `max_fail_percentage` (a
                    percentage threshold, not a per-cluster PASS/FAIL record).
EVIDENCE            `ignore_errors: true` on a task suppresses that task's failure for
                    play-recap purposes; a play can end "successful" (exit 0) with a
                    cluster silently broken. That is the literal failure mode
                    correction #2 rules out: "failures are collected... reports must
                    never show SUCCESS when a required stage failed."
WHY                 A `block`/`rescue` per cluster lets every stage run to whatever
                    point it reaches, captures the exact failing task/stage into a
                    per-cluster result record (not just a generic "failed" flag), and
                    still allows the outer loop to continue to the next cluster — then
                    a final task inspects the aggregated results and explicitly calls
                    `fail()`/sets a non-zero return code if any REQUIRED cluster's
                    result is FAIL, using Ansible's own `failed_when`/`meta: end_play`
                    plus a final `assert`-style gate, not a suppressed error.
TRADEOFFS           Slightly more verbose task structure per cluster (a `block` wrapper
                    plus an explicit `rescue` that records the failure) than a flat
                    task list with `ignore_errors`. Accepted — the whole point of
                    correction #2 is that the flat version is exactly the failure mode
                    to avoid.
VALIDATION METHOD   Phase 5's failure-path tests explicitly assert: (a) cluster B still
                    completes when cluster A is forced to fail, (b) the playbook's own
                    process exit code is non-zero when any required cluster failed, (c)
                    the printed/artifact report shows FAIL for the broken cluster, not
                    a rolled-up "ok".
UNCERTAINTY         None outstanding — this is a well-established Ansible pattern
                    (`block`/`rescue`/`always` plus manual result aggregation), not a
                    new mechanism requiring live discovery.
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
