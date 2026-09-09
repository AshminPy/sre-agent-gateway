# On-prem / non-GKE connectivity via GKE Fleet Connect Gateway

Status: **prototype validated** against a real non-GKE cluster (kind, standing in
for on-prem), and now also wired into and proven through the live production
agent path — see the Open Items section below for current status. This
document is the P3 (`PRODUCTION-LAUNCH-PLAN.md`) deliverable: prove the
connectivity path, its credential model, its RBAC/audit posture, and its
failure behavior, ahead of Priority 4 (custom read-only K8s MCP server)
routing through it.

## What was proven, and how (2026-08-06/07)

All commands below were run for real against project `sreagent-t2-demo` and a
local `kind` cluster (`sre-lab`, context `kind-sre-lab`) standing in for an
on-prem cluster. Full command transcript lives in the session log this doc was
produced from; the essential evidence is reproduced here.

### 1. APIs enabled (were not enabled before this work)

```
gcloud services enable gkehub.googleapis.com connectgateway.googleapis.com \
  container.googleapis.com --project=sreagent-t2-demo
```
Confirmed via `gcloud services list --project=sreagent-t2-demo` — all three
`ENABLED`.

### 2. Cluster registered with the fleet

```
gcloud container fleet memberships register sre-lab \
  --project=sreagent-t2-demo \
  --context=kind-sre-lab \
  --kubeconfig=$HOME/.kube/config \
  --enable-workload-identity \
  --has-private-issuer
```

Result: membership `projects/sreagent-t2-demo/locations/global/memberships/sre-lab`,
state `READY`, Connect Agent (`gke-connect-agent-20260109-00-00`, 2 replicas)
deployed into the `gke-connect` namespace on the cluster itself.

**Credential model note (why `--has-private-issuer`, not a service-account
key):** the standard non-GKE registration path offers two options for the
Connect Agent's own auth to Google — `--service-account-key-file` (a static,
long-lived JSON key) or `--enable-workload-identity`. This project has the org
policy `constraints/iam.disableServiceAccountKeyCreation` enforced —
`gcloud iam service-accounts keys create` failed with
`FAILED_PRECONDITION: Key creation is not allowed on this service account`.
That ruled out the key-file path (which is the right outcome — it forced the
no-static-keys option). `kind`'s API server isn't publicly routable, so plain
`--enable-workload-identity` (which expects a public OIDC discovery endpoint)
wasn't usable either. `--has-private-issuer` is the correct flag for exactly
this case: `gcloud`, running locally, reads the cluster's own OIDC issuer URL
and JWKS directly from the API server (which it *can* reach, being local) and
uploads that JWKS to the fleet membership once, at registration time. From
then on Google validates the Connect Agent's Kubernetes-issued, auto-rotated
service-account tokens against that JWKS — no static credential of any kind
persists outside the cluster.

A minimally-scoped service account (`sre-lab-connect-agent@sreagent-t2-demo.iam.gserviceaccount.com`,
role `roles/gkehub.connect` only) was created in case a key-based fallback was
needed; it was not used for a key and holds no key material (key creation is
blocked by the org policy above). It can be deleted if not needed for future
registrations — it grants no access on its own without a key or WIF binding.

### 3. RBAC — two-part model, read-only

Connect Gateway requires **two separate RBAC grants** on the target cluster,
confirmed against Google's official docs
(`docs.cloud.google.com/anthos/multicluster-management/gateway/setup`) before
applying anything:

1. **Impersonation** — lets the Connect Agent's own service account
   (`system:serviceaccount:gke-connect:connect-agent-sa`) impersonate the
   specific calling identity. Without this, every call fails with:
   `User "system:serviceaccount:gke-connect:connect-agent-sa" cannot
   impersonate resource "users"` — hit this for real before applying the
   fix below.
2. **Permission** — the actual Kubernetes RBAC grant for that identity.

Applied via the official helper (not hand-written YAML) bound to the built-in
read-only `view` ClusterRole:

```
gcloud container fleet memberships generate-gateway-rbac \
  --project=sreagent-t2-demo \
  --membership=sre-lab \
  --users=ashmin.sub@gmail.com \
  --role=clusterrole/view \
  --context=kind-sre-lab \
  --kubeconfig=$HOME/.kube/config \
  --apply
```

This created three cluster objects (all labeled
`connect.gke.io/owner-feature: connect-gateway`, so they're identifiable and
revocable as a set):
- `ClusterRole` + `ClusterRoleBinding` `gateway-impersonate-sreagent-t2-demo_ashmin.sub_sre-lab`
  (lets the agent impersonate `ashmin.sub@gmail.com` only — no one else)
- `ClusterRoleBinding` `gateway-permission-sreagent-t2-demo_ashmin.sub_sre-lab`
  → built-in `view` ClusterRole (read-only, namespaced resources; excludes
  Secrets by design, excludes cluster-scoped resources like Nodes)

**Read-only was proven, not assumed**, with four real calls through
`connectgateway_sreagent-t2-demo_global_sre-lab`:

| Call | Result |
|---|---|
| `kubectl get pods -A` | ✅ succeeded, real pod list returned |
| `kubectl get deployments -A` | ✅ succeeded |
| `kubectl get events -A` | ✅ succeeded |
| `kubectl get nodes` | ❌ Forbidden — `view` doesn't cover cluster-scoped Nodes (expected K8s behavior, not a bug) |
| `kubectl create namespace ...` | ❌ Forbidden — `cannot create resource "namespaces"` |
| `kubectl delete pod ...` | ❌ Forbidden — `cannot delete resource "pods"` |
| `kubectl get secrets -A` | ❌ Forbidden — `view` role excludes Secrets |

### 4. Credential shape used for access — confirmed short-lived

`gcloud container fleet memberships get-credentials sre-lab --project=sreagent-t2-demo`
writes a kubeconfig context (`connectgateway_sreagent-t2-demo_global_sre-lab`)
pointed at `https://connectgateway.googleapis.com/v1/projects/<num>/locations/global/memberships/sre-lab`.
Inspected the generated user auth block directly — it is **not** a static
bearer token:

```json
"exec": {
  "command": "gke-gcloud-auth-plugin",
  "apiVersion": "client.authentication.k8s.io/v1beta1",
  "provideClusterInfo": true
}
```

`gke-gcloud-auth-plugin` is invoked by `kubectl` on every request and mints a
fresh short-lived (≈1 hour) OAuth access token from the local `gcloud`
credential each time — nothing long-lived is stored in the kubeconfig itself.
This satisfies the P3 acceptance bar ("short-lived creds, no stored
kubeconfig [bearer token]").

### 5. IAM — role used in this test vs. recommended production role

The identity used for this validation (`ashmin.sub@gmail.com`) holds
`roles/owner` on `sreagent-t2-demo`, which is sufficient but **not**
least-privilege — it was not scoped down for this test because it's the
existing project-owner account. **This is a known gap, not a recommendation.**
For production, bind the purpose-built role instead:

```
roles/gkehub.gatewayReader   # GA role — verified via:
  gcloud iam roles describe roles/gkehub.gatewayReader
```
grants exactly: `gkehub.gateway.generateCredentials`, `gkehub.gateway.get`,
`gkehub.memberships.get` — nothing else. Combined with the K8s-side `view`
ClusterRoleBinding above, this is the correct least-privilege pairing for a
read-only on-prem investigation identity (e.g. the SRE agent's own principal,
once P4 wires MCP calls through Connect Gateway instead of a direct GKE
endpoint).

### 6. Audit logging — confirmed partially working; real gap found

Checked `gcloud logging read 'protoPayload.serviceName="connectgateway.googleapis.com"'`
after generating both denied-write and successful-read traffic.

**What's captured today (no config changes made):**
```
PostResource   (our forced-forbidden namespace create) → 403, principal + method + resource logged
DeleteResource (our forced-forbidden pod delete)        → 403, principal + method + resource logged
```
Admin Activity audit logs (always-on, free) captured both denied mutation
attempts correctly, with the real caller identity attached.

**What's NOT captured today — a real, confirmed gap:** the successful `GetResource`
read calls (the `kubectl get pods/deployments/events` calls that worked) did
**not** appear in Cloud Logging. Checked the project's IAM audit config
(`gcloud projects get-iam-policy --format=json`, `auditConfigs` field) — it is
empty, meaning DATA_READ audit logs are off by default for
`connectgateway.googleapis.com` (standard GCP default: only BigQuery gets
DATA_READ logging for free; everything else needs an explicit audit config
entry). **Consequence:** today, a full read-only investigation via Connect
Gateway leaves no Cloud Audit Log trail of *what was actually read* — only
denied/blocked mutation attempts are logged. For a production SRE agent doing
read-only investigations, this is a real observability/compliance gap.

**Not fixed here** — enabling DATA_READ audit logging is a project-wide audit
config change (`gcloud projects set-iam-policy` with an `auditConfigs` block
for `connectgateway.googleapis.com`, or `allServices`), which is a security/
logging posture change outside this task's scope to make unilaterally.
Recommended follow-up (needs an explicit decision, not a default-on change):
```yaml
auditConfigs:
- service: connectgateway.googleapis.com
  auditLogConfigs:
  - logType: DATA_READ
  - logType: DATA_WRITE
```
Track as a Priority 9/10 item — ties directly into P10's existing
"connect-gateway failures" missing-alert gap.

### 7. Latency — 5 real sequential calls, `kubectl get pods -A`

| # | Wall time | Exit |
|---|---|---|
| 1 | 621 ms | 0 |
| 2 | 568 ms | 0 |
| 3 | 804 ms | 0 |
| 4 | 623 ms | 0 |
| 5 | 539 ms | 0 |

5/5 succeeded, ~630ms average. This is `kubectl` process startup + TLS +
gateway hop + kind API server round trip combined, not isolated
gateway-only latency — treat as a rough ceiling, not a tight SLO number. No
failures or retries observed across the 5 calls.

### 8. Outage behavior — reproduced for real, not assumed

Scaled the Connect Agent to 0 replicas (`kubectl -n gke-connect scale
deployment gke-connect-agent-20260109-00-00 --replicas=0`) to simulate the
on-prem side going away, then attempted a read through Connect Gateway:

```
$ kubectl --context connectgateway_...sre-lab get pods -A
Error from server (BadRequest): Unable to list "/v1, Resource=pods":
the server rejected our request for an unknown reason (get pods)
```

Notable: the failure surfaces as a generic `BadRequest`, **not** a clearly
labeled "cluster unreachable" or "tunnel down" error — a caller (or the SRE
agent) cannot distinguish "agent is down" from "some other server-side
problem" from this message alone. Document this for P10 (alerting) and for
the agent's own error handling: treat any Connect Gateway `BadRequest` as a
possible connectivity failure, not just a bad request, and check membership
state (`gcloud container fleet memberships describe`) or agent pod health
(`kubectl -n gke-connect get pods`) to disambiguate.

Recovery: scaled the Connect Agent back to 2 replicas; `kubectl rollout
status` confirmed the deployment healthy within ~20s; a subsequent
`get pods -A` through Connect Gateway succeeded immediately — **no manual
re-registration or credential refresh was needed**. The tunnel/session
re-establishes automatically once the agent pods are healthy again
(confirmed via `gke-connect` namespace events: `TunnelConnected` /
`SessionEstablished` reappear).

## Onboarding a new on-prem/non-GKE cluster (runbook)

1. Confirm APIs enabled on the target project (`gkehub`, `connectgateway`,
   `container`) — one-time per project, see §1.
2. Confirm the org does **not** allow static SA keys (check
   `constraints/iam.disableServiceAccountKeyCreation`) — if keys are blocked
   (as here), use `--enable-workload-identity --has-private-issuer` when the
   cluster's API server is reachable from wherever you run `gcloud`, but not
   publicly routable. If keys are allowed and WIF isn't viable, fall back to
   `--service-account-key-file` with a single-purpose SA holding only
   `roles/gkehub.connect`, and rotate/delete the key after registration
   confirms `READY`.
3. Register:
   ```
   gcloud container fleet memberships register <name> \
     --project=<project> --context=<kubeconfig-context> \
     --kubeconfig=<path> --enable-workload-identity --has-private-issuer
   ```
4. Verify `READY`: `gcloud container fleet memberships describe <name>`.
5. Grant RBAC for each identity that needs access, read-only by default:
   ```
   gcloud container fleet memberships generate-gateway-rbac \
     --project=<project> --membership=<name> \
     --users=<email-or-group> --role=clusterrole/view \
     --context=<kubeconfig-context> --kubeconfig=<path> --apply
   ```
   Never grant `cluster-admin` through this path for an automation identity.
6. Validate: `gcloud container fleet memberships get-credentials <name>
   --project=<project>`, then a real read call
   (`kubectl get pods -A --context connectgateway_...`) and a real denied
   write call, before calling onboarding done.
7. Add the cluster to `agent/mcp_client.py`'s `clusters.json` registry (P4/P5
   work — not done as part of this task; today's `clusters.json` schema has
   no field for "reach via Connect Gateway" vs. direct GKE endpoint, see
   `PRODUCTION-LAUNCH-PLAN.md` Priority 5's "registry (thin)" gap).

## Removing a cluster

```
gcloud container fleet memberships generate-gateway-rbac \
  --project=<project> --membership=<name> \
  --users=<email-or-group> --role=clusterrole/view \
  --context=<kubeconfig-context> --kubeconfig=<path> --revoke

gcloud container fleet memberships unregister <name> --project=<project> \
  --context=<kubeconfig-context> --kubeconfig=<path>
```
`unregister` removes the Connect Agent from the cluster and deletes the fleet
membership. Also remove the entry from `clusters.json` once P4/P5 wire this
path in, and delete any single-purpose service account created for
registration if the key-file fallback (step 2 above) was used.

## Open items / not covered by this task

- **Now wired into the agent and live in production.** `mcp_client.py` /
  `mcp_router.py` route non-GKE clusters to the custom K8s MCP server, which
  reaches them through the Connect Gateway path this document proved
  standalone. Real end-to-end proof exists (Agent → Agent Gateway → custom
  Cloud Run MCP → Connect Gateway → the `sre-lab` cluster), verified
  2026-09-04 and independently re-confirmed 2026-09-05/06/07 — see
  [MCP Architecture](architecture/mcp-architecture.md) for the evidence.
- **DATA_READ audit logging gap** (§6) — needs an explicit decision + IAM
  audit-config change, not made here.
- **IAM role used for this test was `roles/owner`**, not the recommended
  `roles/gkehub.gatewayReader` (§5) — re-validate with a scoped-down identity
  before this path is used for anything beyond this prototype.
- **Latency numbers (§7)** are a single local run against a `kind` cluster on
  the same machine as `gcloud` — not representative of real network latency
  to an actual on-prem site; re-measure once a real on-prem cluster is in
  scope.
- **No alert exists yet** for Connect Gateway/agent failures — tracked
  already in `PRODUCTION-LAUNCH-PLAN.md` Priority 10's missing-alerts list;
  this task adds real evidence of what a failure looks like (§8) to make that
  alert buildable.

## Live resources created by this task (for cleanup/audit)

- Fleet membership: `projects/sreagent-t2-demo/locations/global/memberships/sre-lab`
- Service account: `sre-lab-connect-agent@sreagent-t2-demo.iam.gserviceaccount.com`
  (role `roles/gkehub.connect`, **no key material exists** — org policy
  blocked key creation)
- Cluster objects in `sre-lab` (kind), namespace `gke-connect`: Connect Agent
  Deployment `gke-connect-agent-20260109-00-00` (2 replicas) + supporting
  ClusterRoles/ClusterRoleBindings installed by `register`
- Cluster RBAC objects (cluster-wide, not namespaced): `gateway-impersonate-
  sreagent-t2-demo_ashmin.sub_sre-lab` (Role+Binding),
  `gateway-permission-sreagent-t2-demo_ashmin.sub_sre-lab` (Binding → `view`)

## Adding a second on-prem cluster (2026-09-07 update — read this first)

Everything above this section documents how `sre-lab`'s Connect Gateway path was
originally proven. That original path connects via a **static kubeconfig context
baked into the MCP's Docker image** (`mcp/connect-gateway-kubeconfig.yaml`) — meaning
a second physical on-prem cluster would have needed a new context added to that file
plus an image rebuild + redeploy. Not a new MCP deployment, and not an agent-code
change, but not truly zero-touch either.

**This has been replaced with a dynamic mechanism.** Section 5/B correction
(2026-09-08): the paragraph below used to say `sre-lab` was deliberately left on the
static path, unmigrated -- that is now stale. `sre-lab` **was** migrated to the
dynamic path (commit `bc35959`, "migrate sre-lab to dynamic Connect Gateway,
live-verified end to end", 2026-09-07) -- `iac/agent/variables.tf`'s `sre-lab` entry
sets `fleet_project_number`, and `mcp/server.py`'s dynamic-Connect-Gateway branch
takes priority over the static kube_context path whenever it's set. The static
kubeconfig file (`mcp/connect-gateway-kubeconfig.yaml`) is still present in the
image, but `sre-lab` no longer uses it day to day -- it now only matters for
`K8S_MCP_KUBE_CONTEXT`'s local-dev/registry-outage fallback path (see this repo's
Section 2 correction for why that fallback is now gated behind an explicit
distinction between "not configured" and "configured but unreadable", rather than
silently used interchangeably with the real per-cluster registry). Adding a
genuinely new on-prem cluster now needs only:

1. Fleet registration + Connect Agent install (manual, authorized-operator action —
   steps 1-3 above, unchanged).
2. Kubernetes RBAC in the separate `AshminPy/sre-k8s-rbac` repo (unchanged).
3. One `additional_clusters` Terraform entry with `fleet_project_number` (find it via
   `gcloud projects describe <project-id> --format='value(projectNumber)'`) and
   `fleet_membership` (defaults to the map key if the Fleet membership name matches).
4. `terraform apply` — no image rebuild, no code change.

`mcp/server.py`'s `get_k8s_clients()` builds the Connect Gateway connection at request
time from these two values (`https://connectgateway.googleapis.com/v1/projects/
{fleet_project_number}/locations/global/memberships/{fleet_membership}`, authenticated
with a plain Application Default Credentials bearer token) instead of looking up a
context in the static file. Verified against Google's own documented Connect Gateway
membership resource path and confirmed the URL format matches what `sre-lab`'s
already-live-proven static kubeconfig already used successfully (WebSearch, 2026-09-07
— see `PHASE1_EVIDENCE_LOG.md`).

**What is and isn't verified:** the dynamic path is covered by unit tests
(`mcp/tests/test_dynamic_connect_gateway.py`) with the Kubernetes SDK and Google auth
mocked, AND is live-verified end to end for `sre-lab` itself (commit `bc35959`,
2026-09-07 -- `sre-lab` runs this exact dynamic path in production today, not the
static one, correcting this section's own earlier claim otherwise). What is **not**
yet proven: a genuinely SECOND on-prem cluster, onboarded from zero using only the
4 steps above -- confirmed via git history and live GCS/Fleet queries (2026-09-08)
that no second on-prem cluster has ever existed in this project. That live proof
(`sre-lab-2`) is the subject of a dedicated onboarding exercise, not yet claimed here.
