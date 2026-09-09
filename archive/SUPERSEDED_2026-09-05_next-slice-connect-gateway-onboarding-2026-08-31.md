> **SUPERSEDED 2026-09-05.** This document planned the "full Connect Gateway / non-GKE
> cluster onboarding" as a deferred follow-up workstream. That work has since been built and
> proven — see
> [`docs/management/PHASE1_RELEASE_VALIDATION_REPORT.md`](../docs/management/PHASE1_RELEASE_VALIDATION_REPORT.md)
> (2026-09-05), which documents the exact scoped work (Agent → Agent Gateway → custom Cloud
> Run MCP → Connect Gateway → the `sre-lab` `kind` cluster) built and validated PASS with live
> evidence, and
> [`docs/architecture/mcp-architecture.md`](../docs/architecture/mcp-architecture.md) for the
> current, live architecture. Kept below unmodified as historical planning evidence.

---

# Next slice: full Connect Gateway / non-GKE cluster onboarding

> **Status: PLANNED — documentation only. Nothing in this file has been built.**
> **Last Verified:** 2026-08-31
> **Owner:** SRE Agent platform team
> **Relationship to the current run:** the approved multi-workstream execution plan for
> this session explicitly keeps "full non-GKE/Connect Gateway onboarding (Fleet Terraform,
> baked auth plugin, live external-cluster E2E)" **out of scope** — it is deferred here as
> its own follow-up workstream, not started. This document defines what that follow-up
> workstream actually is, in concrete, buildable terms, so it doesn't stay a vague bullet.

## Why this is a separate workstream, not a task inside another one

Connect Gateway infrastructure has already been prototyped and proven manually
(`docs/connect-gateway-onprem.md`, 2026-08-06/07) against a local `kind` cluster standing
in for on-prem. That work answered the hard open questions — credential model, RBAC shape,
audit-log gap, outage/recovery behavior. What's described below is the **next** slice:
turning that proven-by-hand prototype into Terraform-managed infrastructure, a real Cloud
Run deployment that can reach it, and a live test against a real external (non-`kind`,
non-GKE-Autopilot) cluster. That is a multi-file, cross-cutting change (new Terraform
module, Dockerfile, cluster-registry schema, `mcp/server.py` code path, CI test policy) —
too large and too separable to fold into any other current-run workstream, and it depends
on infrastructure (a real reachable external cluster) that doesn't exist in this repo's
CI/test environment today.

## Current state — what already exists, cited to real files

Two of the three layers named in `docs/architecture/gke-vs-nongke.md` are done; the third
(the one that would make this real in production) is not:

| Layer | State | Evidence |
|---|---|---|
| Connect Gateway infra (fleet membership, RBAC) | Proven manually against `kind`, **not Terraform-managed** | `docs/connect-gateway-onprem.md` — no `google_gke_hub_membership` resource anywhere in `iac/` (confirmed by grep) |
| `mcp/server.py`'s Connect Gateway auth branch | Proven locally, 18/19 tool calls succeeded via `kubectl` context | `docs/custom-k8s-mcp.md:177`; `docs/management/implemented-vs-planned-matrix.md:48` ("Connect Gateway 🔵 — proven manually only... no `google_gke_hub_membership` Terraform resource anywhere") |
| Deployed Cloud Run MCP service reaching a cluster via Connect Gateway | **Not done** — no network path, no baked auth plugin, no IAM binding on the runtime SA | `PRODUCTION-LAUNCH-PLAN.md:588`; `docs/runbooks/add-non-gke-cluster.md` steps 2, 5, 7 |

Deep per-item research already exists for this exact slice: `NEXTSTEPS.md`, item 10
("(Item #5) On-prem Kubernetes cluster investigation via GKE Fleet Connect Gateway",
lines 790-843) — effort estimate, implementation steps, risks, and 7 live-fetched Google
doc citations. This document restates that research as a concrete, ordered build slice;
it does not replace it as the source of the underlying decisions (project choice, identity
choice, module placement) — see that file for the "why" behind each choice below.

Current GKE-only access model, for contrast (`iac/gke-access/`):
- `iac/gke-access/gke.tf` — an optional demo GKE cluster in Project B (`google_container_cluster.sre_test`, Autopilot) plus its VPC/subnet. No fleet/Connect Gateway resource exists here or anywhere else in `iac/`.
- `iac/gke-access/crossproject_iam.tf` — exactly 4 project-level roles (`container.viewer`, `mcp.toolUser`, `logging.viewer`, `monitoring.viewer`) granted to the agent's `AGENT_IDENTITY` principalSet, plus `container.viewer` for the custom-MCP runtime SA. Pure Cloud IAM — **zero Kubernetes RBAC**, because GKE Remote MCP never needed it. This is the model that does *not* extend to non-GKE clusters (an on-prem API server has no native Cloud IAM trust), which is why layer 1 below adds a genuinely new authorization layer.

## What "baked auth plugin" means here

This term is not new — it already appears in this repo in four places, describing the
same unbuilt step each time:

- `PRODUCTION-LAUNCH-PLAN.md:588` — "needs `gke-gcloud-auth-plugin`/`gcloud` baked into `mcp/Dockerfile`'s slim image"
- `docs/custom-k8s-mcp.md:177` — "`gke-gcloud-auth-plugin`/`gcloud` installed in `mcp/Dockerfile`'s slim [image]"
- `docs/runbooks/add-non-gke-cluster.md:30` — "bake `gke-gcloud-auth-plugin`/`gcloud` into `mcp/Dockerfile` — neither exists today"
- `docs/architecture/gke-vs-nongke.md:54` — same, listed as open item #2

Concretely: `mcp/server.py`'s Connect Gateway auth branch reaches the cluster through a
kubeconfig context whose credential plugin is `gke-gcloud-auth-plugin`
(`docs/connect-gateway-onprem.md:124-134` shows the exact kubeconfig `exec` block). That
plugin is a real binary `kubectl`/the Python K8s client invoke on every request to mint a
fresh ~1-hour OAuth token from the local `gcloud` credential — it is **not** part of the
Python `kubernetes` client library and is not installed by `pip install`. Today,
`mcp/Dockerfile` builds a slim image with only the Python runtime and dependencies — no
`gcloud` SDK, no `gke-gcloud-auth-plugin` component. "Baking it in" means adding both to
the Docker image at build time (the `google-cloud-sdk` apt/tar install plus
`gcloud components install gke-gcloud-auth-plugin`, or the equivalent slim binary-only
install) so the deployed Cloud Run container can actually exec that plugin at runtime,
the same way a developer's local machine already does when testing this by hand.

## The next executable slice — concrete build order

Each numbered step names the real file(s) it touches. No step here has been implemented —
this is the plan, not the diff.

1. **Terraform: fleet membership + IAM (new module, not `iac/gke-access/`).**
   Create `iac/onprem-fleet/` as its own module — not folded into `iac/gke-access/`,
   because that stack's own file header scopes it to "a GCP-project-owned GKE cluster +
   project-level cross-project IAM" (`iac/gke-access/crossproject_iam.tf:1-5`), a
   different resource shape and trust boundary than a fleet membership + in-cluster RBAC.
   Resources:
   - `google_project_service` for `connectgateway.googleapis.com`, `gkeconnect.googleapis.com`, `gkehub.googleapis.com` in the fleet-host project (reuse Project B / `sreagent-t2-demo` per `docs/connect-gateway-onprem.md`, to preserve the existing two-project topology rather than adding a third project).
   - `google_gke_hub_membership` — the Terraform resource type that represents the fleet membership object itself (confirmed to exist in the Google provider: `registry.terraform.io/providers/hashicorp/google/latest/docs/resources/gke_hub_membership`). Note the provider's own docs describe this resource via `endpoint.gke_cluster.resource_link` for GKE-hosted clusters; for a genuinely non-GKE (kind/k3s/on-prem) cluster the membership object itself is representable in Terraform, but the Connect Agent's installation into that cluster is not — see step 2.
   - `google_project_iam_member` granting the **existing** `mcp_runtime` service account (already defined in `iac/agent/cloudrun_mcp.tf`) `roles/gkehub.gatewayReader` and `roles/gkehub.viewer` in the fleet-host project — mirroring the existing `custom_mcp_crossproject` pattern already in `iac/gke-access/crossproject_iam.tf:34-40`. Do **not** grant `gatewayAdmin`/`gatewayEditor` — those add `gkehub.gateway.stream` (exec/attach/port-forward), which this read-only agent must never have.
   - Reuse the existing `mcp_runtime` SA for Kubernetes RBAC binding, not the agent's `AGENT_IDENTITY` SPIFFE principalSet — Google's documented Connect Gateway RBAC/impersonation patterns use plain user/SA identities or Workforce Identity Federation principals, a different WIF pool family from Agent Identity's `agents.global.org-*.system.id.goog` pool (`NEXTSTEPS.md:805`, `:817`). This avoids an unverified identity combination rather than guessing it will work.

2. **Bake the auth plugin into the container.**
   Update `mcp/Dockerfile` to install `gcloud` and the `gke-gcloud-auth-plugin` component
   in the image build (see "What 'baked auth plugin' means" above). Add the Cloud Run
   connectivity env vars this branch needs — `K8S_MCP_KUBE_CONTEXT` (or equivalent),
   `CONNECT_GATEWAY_MEMBERSHIP`, `FLEET_PROJECT_NUMBER`, `CLUSTER_LOCATION` — to
   `iac/agent/cloudrun_mcp.tf`'s environment block. None of these exist in Terraform
   today (`docs/architecture/gke-vs-nongke.md:45`, Layer C: "the deployed Cloud Run
   service's only env var is `PROJECT_ID`").

3. **Cluster registry schema.**
   Add a distinct `cluster_type: onprem` value (replacing today's ambiguous `custom`
   catch-all) to `iac/agent/variables.tf`'s `additional_clusters` object type and to
   `agent/mcp_client.py`'s registry loader, carrying the fleet membership name, project
   number, and location that `mcp/server.py` needs to build the Connect Gateway context.
   This is a schema change, not a routing-logic change — `agent/nodes/mcp_router.py:82-88`
   already routes non-`gke` cluster types to `k8s_mcp`; it just has no working transport
   behind that decision yet.

4. **Code: `mcp/server.py`'s `get_k8s_clients()`.**
   Add a Connect Gateway auth branch selected by the new env vars from step 2, setting
   `configuration.host` to `https://connectgateway.googleapis.com/v1/projects/{project_number}/locations/{location}/gkeMemberships/{membership}`,
   keeping the existing Workload Identity bearer-token logic, and dropping the GCS-CA-cert
   download path (Connect Gateway presents a normal publicly trusted certificate, unlike
   the direct-GKE-endpoint branch).

5. **Live external-cluster E2E test.**
   This is the step this slice cannot skip or fake with `kind`. Requirements for it to
   count as real:
   - **A genuinely external cluster** — not `kind` (used for the 2026-08-06/07 prototype)
     and not a GKE Autopilot cluster (the existing demo cluster in `iac/gke-access/gke.tf`
     — that's the GKE path, not the non-GKE path this slice is about). A real candidate:
     a k3s/kubeadm cluster on a VM with genuine outbound-only network posture, per
     `NEXTSTEPS.md:825`.
   - **Against the deployed Cloud Run revision**, not a local `pytest` run. Today's only
     Connect Gateway test, `mcp/tests/test_live_connect_gateway.py`, is auto-skipped in
     CI unless a specific env var is set (`docs/runbooks/add-non-gke-cluster.md` step 8) —
     it has never run against a deployed service, only locally by hand. This slice's exit
     criterion is that same class of test (or its successor) executed against the actual
     Cloud Run URL, through the Agent Gateway, the way a real investigation would call it.
   - **Read succeeds, write is refused, at two independent layers** — `agent/mcp_client.py`'s
     `BLOCKED_ACTIONS` allowlist (app layer) and the on-prem cluster's own `view`
     ClusterRoleBinding (K8s RBAC layer), per `NEXTSTEPS.md:832`'s defense-in-depth
     requirement — not just one or the other.
   - **Outage/recovery behavior reproduced against the real cluster**, not re-assumed from
     the `kind` result in `docs/connect-gateway-onprem.md` §8. Scale the Connect Agent to
     0 replicas, confirm the failure surfaces (expect the same undiagnosable generic
     `BadRequest` documented in that section — a real gap for the agent's own error
     handling, not fixed by this slice), then confirm automatic reconnection on scale-back.
   - **A same-day decision recorded on `DATA_READ` audit logging** for
     `connectgateway.googleapis.com` — either turn it on
     (`docs/connect-gateway-onprem.md` §6's proposed `auditConfigs` block) or explicitly
     accept the "no audit trail of successful reads" gap in writing before calling this
     slice done. Today it is silently off; that is not an acceptable end state for a
     slice whose entire point is production readiness.

6. **Docs.**
   Update `docs/least-privilege-iam.md` with the new `gatewayReader`/`gatewayViewer`
   grants, and add a new ADR (e.g. `docs/ADR-013-onprem-connect-gateway.md`, matching the
   existing ADR numbering in `docs/README.md`) documenting three decisions made without
   a live spike: the fleet-host-project choice (reuse Project B), the SA-not-`AGENT_IDENTITY`
   choice for Kubernetes RBAC, and the resulting 4-layer failure surface (Cloud IAM
   `gatewayReader`, Kubernetes RBAC, Connect Agent health, on-prem egress) that runbooks
   will need to cover.

### Why this order

Terraform/IAM (1) has to exist before the container can authenticate through it, so it's
first. The baked auth plugin (2) and registry schema (3) are independent of each other and
could be done in parallel, but both must land before the code branch (4) has anything to
select between. The live E2E test (5) is deliberately last and treated as a hard gate, not
a nice-to-have at the end — the entire point of this slice, per the governing plan, is
proving the path works against a real external cluster, not just merging Terraform that
looks right. Docs (6) close the loop so this doesn't join the pile of "proven manually,
never wired in" work the way the current prototype already has.

## Explicitly out of scope for this slice

- **#30 (Model Armor endpoint hostname mismatch / sreagent-demo Agent Registry cleanup,
  24 orphaned manual entries).** Unrelated system (Model Armor / Agent Registry hostname
  config, not cluster connectivity) and already tracked as its own item
  (`docs/management/CURRENT-STATE.md` §5). Stays PARKED, not folded into this plan.
- **Binding `AGENT_IDENTITY` directly to on-prem Kubernetes RBAC.** Explicitly called out
  as unverified/unsupported in `NEXTSTEPS.md:837` — this slice avoids the question by
  reusing the existing `mcp_runtime` service account instead of attempting it.
  Only revisit if a live spike specifically proves the WIF pool families are compatible.
- **Migrating GKE Remote MCP itself to support Connect Gateway.** Confirmed via live doc
  fetch that GKE Remote MCP has no fleet/Connect Gateway/non-GKE support at all
  (`NEXTSTEPS.md:796`, `:818`) — on-prem traffic can only ever go through the custom
  Cloud Run MCP fallback, not this path.
- **Any actual `terraform apply`, `gcloud` write command, or code change.** This document
  is the plan only, per this task's docs-only constraint.

## Effort and risk (carried from the existing research, not re-estimated here)

`NEXTSTEPS.md`'s own estimate for this item is "Medium-large... the main driver of the
higher-than-'medium' estimate" being that real end-to-end validation requires an actual
reachable external cluster, which doesn't exist in this repo's infrastructure today
(`NEXTSTEPS.md:792`). Key risks worth restating here rather than re-deriving:
- Connect Agent installation is not fully Terraform-native — it requires a working
  kubeconfig/network path to the external cluster at apply time, which breaks this repo's
  "CI deployer SA does everything from GitHub Actions" pattern unless a self-hosted runner
  or bastion with external reachability is introduced.
- A call can now fail at four independent layers (Cloud IAM, Kubernetes RBAC, Connect
  Agent health, on-prem egress) instead of today's one (project IAM) — runbooks and
  alerting need to account for this before it goes live, not after.
- Google's own docs cite p95<500ms/p99<1s added round-trip latency per call; this needs
  measuring against the real external cluster before assuming latency parity with the
  direct GKE Remote MCP path.

## Related pages

[On-prem Connect Gateway prototype evidence](../connect-gateway-onprem.md) ·
[GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md) ·
[Adding a Non-GKE Cluster (runbook)](../runbooks/add-non-gke-cluster.md) ·
[Implemented vs Planned Matrix](implemented-vs-planned-matrix.md) ·
[Risks and Limitations](risks-and-limitations.md) ·
[NEXTSTEPS.md, item 10](../../NEXTSTEPS.md) (deep research this document summarizes into a build order) ·
[PRODUCTION-LAUNCH-PLAN.md, Priority 3](../../PRODUCTION-LAUNCH-PLAN.md) ·
[Current State](CURRENT-STATE.md)
