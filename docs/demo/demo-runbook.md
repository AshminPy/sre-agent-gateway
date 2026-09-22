# SRE Agent Gateway — Live Demo Runbook

Built 2026-09-22 from live-verified facts in the personal proving-ground project
(`sreagent-t2-demo`). Every command below was either run live tonight or is cited to
an exact file:line in this repo — none are invented. Where a command is a reasonable
construction rather than something copied verbatim from the repo, it is marked
**(constructed, untested)** — treat those as things to test tonight/tomorrow, not as
proven.

**No live GCP changes were made tonight.** `sre-lab` and `sre-lab-2` remain disabled
(they were turned off yesterday, 2026-09-21, specifically to stop real billing — see
§8.1). This runbook is written to be ported and executed against your **work**
environment tomorrow. It has never been run there. Treat every command as needing a
live rehearsal on work infra before you present it to management.

## Placeholder table — fill in before running anything tomorrow

| Placeholder | Personal reference value (verified tonight) | Your work value |
|---|---|---|
| `<PROJECT_ID>` | `sreagent-t2-demo` | fill in |
| `<REGION>` | `us-central1` | fill in |
| `<REASONING_ENGINE_ID>` | `7801582006105538560` | fill in |
| `<EVIDENCE_BUCKET>` | `sreagent-t2-demo-evidence` | fill in |
| `<GKE_CLUSTER>` | `sre-test-cluster` | fill in |
| `<ONPREM_CLUSTER>` | `sre-lab` | fill in |
| `<RUNTIME_SA>` | `sre-k8s-mcp-runtime@sreagent-t2-demo.iam.gserviceaccount.com` | fill in |
| `<GCLOUD_SDK_BIN>` | `/Users/ashmin/Downloads/google-cloud-sdk/bin` | fill in (work laptop's SDK path) |

**Do not use `presentation/meeting-prep-sheet.html`.** It documents a different,
inconsistent environment (different project, different namespace, different
reasoning-engine ID, `invoke.py` instead of `invoke_agent.py`, scenario names that
don't exist in the current code). It looks like an earlier iteration. Every command
below is drawn from the current, internally-consistent source: `invoke_agent.py`,
`docs/runbooks/*`, and live Terraform state, cross-checked against each other.

---

## Section 0 — Porting checklist (tonight → tomorrow)

1. Confirm the work repo (`sre-agent-app-infra` or equivalent) has its own live
   equivalents of every row in the placeholder table above. Do not assume names match.
2. Confirm the work project has a real, reachable target for the "on-prem" demo — a
   GKE cluster standing in for on-prem (like `sre-lab` here), or a real non-GKE
   cluster. If none exists, the on-prem demo (§4, §5) cannot run live tomorrow —
   fall back to walking through the commands without executing them, and say so
   plainly rather than faking a live run.
3. Port `ansible/roles/onprem_cluster_onboarding/`, `ansible/playbooks/`, and
   `ansible/PORTING.md` as-is — they contain no personal values (verified: grep
   for the personal project ID and username inside the role returns nothing).
4. In your work inventory, set `allow_billable_external_cluster: true` **only** for
   the specific cluster you have team approval to register, and only after that
   approval is real — this is the cost-safety gate this workflow enforces by design;
   don't set it globally "to make the demo easier."
5. Re-run `ansible/PORTING.md`'s own checklist end to end before touching anything else.
6. Budget real rehearsal time. This exact combination (work laptop, work network, work
   project, real/lab cluster) has never been run — expect at least one environment
   surprise, the same pattern seen twice already on the personal machine (missing
   `gke-gcloud-auth-plugin`, PATH issues, a Terraform CI-var mismatch).

---

## Section 1 — 5-minute pre-demo health check (run before screen-sharing)

Run every command in this section first. If any fails, fix it before starting — do
not discover a broken dependency live in front of management.

```bash
# 1. gcloud SDK on PATH (fixes a real gap hit tonight)
export PATH="<GCLOUD_SDK_BIN>:$PATH"
gcloud --version | head -1

# 2. Authenticated as the right account/project
gcloud config get-value account
gcloud config get-value project   # should equal <PROJECT_ID>

# 3. Agent Engine reachable
gcloud ai reasoning-engines describe <REASONING_ENGINE_ID> \
  --project=<PROJECT_ID> --region=<REGION> --format="value(state)"
# expect: ACTIVE (or equivalent healthy state)

# 4. Agent Gateway reachable (confirms the gateway resource itself exists and is not in error)
gcloud alpha ai-gateway agent-gateways describe sre-agent-egress \
  --project=<PROJECT_ID> --location=<REGION> --format="value(state)" 2>&1 || \
  echo "FALLBACK: check via Cloud Console > Vertex AI > Agent Gateway if this CLI surface differs on your gcloud version"

# 5. GKE cluster reachable
gcloud container clusters get-credentials <GKE_CLUSTER> --project=<PROJECT_ID> --region=<REGION>
kubectl get nodes
kubectl get ns test-incidents

# 6. Connect Gateway / on-prem cluster reachable (only if §4 will run live)
gcloud container fleet memberships describe <ONPREM_CLUSTER> --project=<PROJECT_ID> --format="value(state.code)"
# expect: READY. If "not found", it needs onboarding first — see §8.

# 7. Custom MCP (Cloud Run) healthy
gcloud run services describe sre-k8s-mcp --project=<PROJECT_ID> --region=<REGION> \
  --format="value(status.conditions[0].type,status.conditions[0].status)"
# expect: Ready True

# 8. kind cluster reachable (if demoing on-prem against a local kind cluster)
kind get clusters
kubectl --context kind-<ONPREM_CLUSTER> get nodes
```

**If any check fails:** see the fallback table in §17 before improvising a fix live.

---

## Section 2 — GKE live incident (source: `invoke_agent.py:54-60`, `k8s/imagepull-pod.yaml`)

```bash
source scripts/init-env.sh          # writes agent/.env, exports PROJECT_ID/REGION/REASONING_ENGINE_ID
kubectl apply -f k8s/namespace.yaml       # first time only
kubectl apply -f k8s/imagepull-pod.yaml
python invoke_agent.py --scenario imagepull --verbose
```
**Expected:** the pod shows `ErrImagePull`/`ImagePullBackOff` (bad image reference,
`gcr.io/google-containers/nonexistent-image:v99.9.9`); the agent returns a structured
RCA report naming "image not found" as root cause, with `complete` completeness
(matches a real prior run: `docs/management/PHASE1_RELEASE_VALIDATION_REPORT.md:99`).

**Fallback if the live call times out or errors:** narrate from the last saved
run in `docs/management/PHASE1_RELEASE_VALIDATION_REPORT.md` and say plainly "this is
the last verified run, not happening live right now" — never present a saved result as live.

---

## Section 3 — Verify the GKE RCA independently (source: `docs/testing/e2e-honest-baseline-2026-08-09-notification-relay.md:42-53`)

```bash
kubectl get pods,svc -n test-incidents
kubectl describe pod imagepull-pod -n test-incidents
kubectl get events -n test-incidents --field-selector involvedObject.name=imagepull-pod
kubectl logs imagepull-pod -n test-incidents
```
**Expected:** the Events section shows a `Failed to pull image ...` / `ErrImagePull`
line matching the agent's stated root cause. For deeper proof, pull the exact evidence
object the agent used (`run_id` is printed at the top of the RCA):
```bash
gsutil ls gs://<EVIDENCE_BUCKET>/<run_id>/
gsutil cat gs://<EVIDENCE_BUCKET>/<run_id>/<evidence_id>.json
```

---

## Section 4 — On-prem live incident (source: `invoke_agent.py:61-67`, `docs/runbooks/add-onprem-cluster.md:192-204`)

Requires `<ONPREM_CLUSTER>` to be `READY` in the Fleet first (§1 check #6; see §8 if not).

```bash
kubectl --context kind-<ONPREM_CLUSTER> apply -f k8s/namespace.yaml
kubectl --context kind-<ONPREM_CLUSTER> apply -f k8s/imagepull-pod.yaml

export PROJECT_ID=$(terraform -chdir=iac/agent output -raw project_a_id)
export REASONING_ENGINE_ID=$(terraform -chdir=iac/agent output -raw reasoning_engine_id)
python3 invoke_agent.py --scenario onprem --verbose
```
**Expected:** the response's own `"cluster"` and `"cluster_routing_reason"` fields
name `<ONPREM_CLUSTER>` and `"primary_mcp_source": "k8s_mcp"` (not the GKE Remote MCP
path) — this is the proof it actually went through Connect Gateway, not GKE directly.

**Independent confirmation via logs:**
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="sre-k8s-mcp"' \
  --project=<PROJECT_ID> --limit=20 --format="value(timestamp,textPayload)"
```
Look for a line like `"Initializing K8s client via dynamic Connect Gateway (cluster_id=<ONPREM_CLUSTER>, ...)"`.

**Dropped from tomorrow's plan:** the `onprem2` cross-cluster-isolation scenario
(`sre-lab-2` + `labtwo-marker-pod`) is intentionally not included — that fixture pod
doesn't exist in this repo yet (verified: no manifest anywhere), and `sre-lab-2` stays
untouched per standing instruction. If you want it later, the fixture creation
command would be **(constructed, untested)**:
```bash
kubectl --context kind-<second-cluster> run labtwo-marker-pod -n test-incidents \
  --image=busybox --env=UNIQUE_MARKER=LAB_TWO_ONLY --command -- sleep 3600
```

---

## Section 5 — Verify the on-prem RCA independently (source: `docs/connect-gateway-onprem.md:114-131`)

Key difference from §3: go through the *same* Connect-Gateway-routed context the
agent's MCP server actually uses, not a shortcut local kubeconfig context.

```bash
gcloud container fleet memberships get-credentials <ONPREM_CLUSTER> --project=<PROJECT_ID>
kubectl --context connectgateway_<PROJECT_ID>_global_<ONPREM_CLUSTER> get pods -A
kubectl --context connectgateway_<PROJECT_ID>_global_<ONPREM_CLUSTER> describe pod imagepull-pod -n test-incidents
kubectl --context connectgateway_<PROJECT_ID>_global_<ONPREM_CLUSTER> get events -A
```
**Expected:** same evidence as §3, retrieved through the gateway path.

---

## Section 6 — Unauthorized / unknown cluster refusal (source: `agent/nodes/context_resolver.py`, `mcp/server.py:183-238`, `tests/test_cluster_unresolved_safe_stop.py`)

There is **no built-in `invoke_agent.py` scenario for this** — confirmed by reading
the full scenario dict. The previously-run demonstration used a direct local
in-process call (not the deployed Agent Engine) — say this distinction out loud:

```bash
python3 -c "
from agent.main import SREAgent
result = SREAgent.query(
    query='Investigate this incident.',
    cluster='sre-nonexistent-cluster-xyz',
    namespace='test-incidents',
)
print(result)
"
```
**Expected:** a safe-stop response — no tool calls are made, no cluster is guessed.
The real, previously observed message: *"cluster could not be safely determined...
refusing to fall back to namespace-based routing"* (`docs/management/PHASE1_RELEASE_VALIDATION_REPORT.md:127`).

**Easier, more visual alternative for a management audience** — the cost-approval
gate built this week is a live, in-repo, fully mutation-free way to show the same
"refuses rather than guesses/proceeds" principle, and it's a stronger story because
it's tied to a real incident (see §8.1 and Chapter 5 of the presentation):
```bash
# From ansible/, with a cluster NOT approved in inventory (allow_billable_external_cluster unset/false)
ansible-playbook -i inventories/kind/hosts.yml playbooks/onboard.yml
```
**Expected:** the play fails loudly on that cluster with the exact cost message from
`ansible/roles/onprem_cluster_onboarding/tasks/fleet_register.yml:37-51`, and zero
Fleet mutation occurs. Pick ONE of these two refusal demos for tomorrow, not both —
know which one you're running before you're on stage.

---

## Section 7 — Add a GKE cluster (config walkthrough — safe to show without applying)

```hcl
# iac/agent/terraform.tfvars
additional_clusters = {
  "prod-cluster-east" = {
    project = "your-project-id", region = "us-east1",
    environment = "production", aliases = ["prod-east"], owner = "your-team"
  }
}
```
Plus a required Kubernetes RoleBinding (`k8s/rbac.yaml` pattern) granting
`get/list/watch` on `pods`, `pods/log`, `events` to the live Agent Identity principal
(refresh it via `terraform -chdir=iac/agent output -raw reasoning_engine_id`).
Then: `terraform apply` in `iac/agent/`.

**Honesty note to say out loud if asked:** this exact process has never been exercised
end-to-end against a genuinely new second GKE cluster in this repo — it's the correct,
code-verified mechanism, not yet live-proven. Don't claim it's already been demoed.

---

## Section 8 — Add an on-prem cluster via Ansible (the headline "how we operate it" demo)

### 8.1 — Real context to know before running this live

`<ONPREM_CLUSTER>` (`sre-lab` on the personal project) was deliberately **disabled
yesterday** after live GCP Billing Console data showed it and a second test cluster
were together costing **~$216/month** at `clusterTier=ENTERPRISE` — a real,
after-the-fact discovery, not something caught before it happened. This is exactly why
the cost-approval gate below exists. If your work cluster is a similar always-on Fleet
registration, budget for that cost consciously — don't repeat this incident on work
infra by leaving a demo cluster registered indefinitely afterward. Plan to disable it
again right after the demo (§16).

### 8.2 — Commands

```bash
cd ansible
ansible-playbook -i inventories/kind/hosts.yml playbooks/onboard.yml
```
This is idempotent — running it twice against an already-onboarded cluster changes
nothing. Before this will register a *new* cluster, it requires
`allow_billable_external_cluster: true` explicitly set for that cluster in inventory —
if it's not set, the play fails loudly with the exact cost message and registers
nothing (see §6's second option — this is the same mechanism).

```bash
ansible-playbook -i inventories/kind/hosts.yml playbooks/verify.yml
```
**Expected:** real allowed reads (pods, deployments, events, nodes) succeed; real
denied calls (`create namespace`, `delete pod`, `get secrets -A`) are rejected —
proven, not asserted.

### 8.3 — Explain live (grounded in the actual task files, not generic GCP theory)

- **Fleet registration**: tells Google's control plane this non-GKE cluster now has a
  trusted identity, using the cluster's own OIDC issuer (`--enable-workload-identity
  --has-private-issuer`) — no service-account key is ever generated; a preflight check
  refuses onboarding if the cluster can't prove it has a real OIDC issuer.
- **Connect Gateway**: once registered, this opens a proxied Kubernetes API endpoint
  routed through Google's control plane — no VPN or inbound firewall hole needed on
  the cluster side, only outbound reachability to Google's APIs.
- **RBAC**: controls what the identity can do *through* that gateway — the built-in
  read-only `view` ClusterRole plus one narrow, hand-authored addition (`nodes:
  get,list`) to close a real gap (`view` alone doesn't cover cluster-scoped nodes,
  and the MCP tool suite needs `list_node`/`read_node`).

---

## Section 9 — Least-privilege proof

```bash
# GCP IAM — the runtime identity's project-level roles
gcloud projects get-iam-policy <PROJECT_ID> --flatten="bindings[].members" \
  --filter="bindings.members:<RUNTIME_SA>" --format="table(bindings.role)"
# Expected roles: roles/container.viewer, roles/mcp.toolUser, roles/logging.viewer,
# roles/monitoring.viewer, roles/gkehub.gatewayReader, roles/gkehub.viewer — nothing
# with create/update/delete/admin.

# Kubernetes RBAC Ansible created (labeled, so distinguishable from anything pre-existing)
kubectl --context kind-<ONPREM_CLUSTER> get clusterrole,clusterrolebinding \
  -l "sre-agent-gateway.internal/onboarded-by"

# Live denial proof
kubectl --context connectgateway_<PROJECT_ID>_global_<ONPREM_CLUSTER> delete pod imagepull-pod -n test-incidents
# Expected: Error from server (Forbidden): ...
kubectl --context connectgateway_<PROJECT_ID>_global_<ONPREM_CLUSTER> get secrets -A
# Expected: Error from server (Forbidden): ...
```
**If a pre-existing, unlabeled RBAC object shows up on your work cluster too** (this
happened on the personal cluster — a legacy hand-applied binding from before this
Ansible role existed), don't present it as something Ansible created. Only the
labeled objects are the proof.

---

## Section 10 — Observability

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="sre-k8s-mcp"' \
  --project=<PROJECT_ID> --limit=20 --format="value(timestamp,textPayload)"
```
Live dashboard (personal reference — swap for your work project's equivalent once
built there): `https://lookerstudio.google.com/reporting/ac74ffed-09d7-46bd-a874-9fe1500f5f4f`
(built on BigQuery views fed by Cloud Logging sinks — `iac/observability/investigation_dashboard.tf`).

---

## Section 11 — Switch the LLM (source: `docs/runbooks/switch-llm-model.md`)

```bash
# 1. Edit iac/agent/terraform.tfvars: gemini_model = "gemini-2.5-pro" (or target model)
#    also update gemini_price_input_per_1m / gemini_price_output_per_1m in the same file
terraform -chdir=iac/agent apply
```
**Say out loud:** the GitHub Actions CI workflow currently hardcodes
`-var="gemini_model=gemini-2.5-pro"` in every automated apply, so a `tfvars`-only
change is silently overridden by the next CI-driven apply — the workflow file itself
also needs updating for a change to survive through the normal pipeline. This is a
real, documented gap, not a talking point to skip.

---

## Section 12 — Add a new MCP (pattern only — PLANNED, not live)

Point to `docs/runbooks/add-mcp-server.md`'s 21-point checklist and the worked
Prometheus example inside it. State plainly: no second MCP source is registered today.

---

## Section 13 — CI/CD (infrastructure)

Show `.github/workflows/terraform-plan.yml` (runs on PR, posts a plan comment) and
`.github/workflows/terraform-apply.yml` (runs on merge to `main`, gated by a GitHub
`environment: production` manual-approval rule). **Correction to make if Spacelift
comes up:** Spacelift is referenced in this repo only as the *target company's*
Terraform version constraint (`versions.tf` pins 1.4.7 to match it) — the actual
pipeline shown here runs on GitHub Actions, not a wired Spacelift stack.

---

## Section 14 — MCP image CI/CD

Same `terraform-apply.yml`, a gated stage (lines ~200-256): triggers only when
`mcp/**` changed and `ENABLE_CUSTOM_MCP` is true — builds/pushes the image, re-applies
Terraform to point Cloud Run at it, then health-checks the new revision before the
tool spec is regenerated.

---

## Section 15 — Documentation index

`docs/README.md` (index — self-flags some pages as stale relative to ~90 recent
commits), `docs/architecture/system-overview.md`, `mcp-architecture.md`,
`dynamic-mcp-routing.md`, `llm-adapter.md`; `docs/runbooks/{add-gke-cluster,
add-onprem-cluster,switch-llm-model,add-mcp-server,operate}.md`; `docs/operations/
{logging,observability,deployment,terraform}.md`; `ansible/PORTING.md`.

---

## Section 16 — Cleanup (run right after the demo, every time)

```bash
cd ansible
ansible-playbook -i inventories/kind/hosts.yml playbooks/cleanup.yml
```
Ownership-safe: only removes what this workflow's own labels show it created. Then,
if you registered a Fleet membership specifically for this demo and don't need it
running afterward, disable it the same way the personal project did — flip
`enabled = false` on that entry and `terraform apply` — to avoid repeating this week's
billing incident on work infra.

---

## Section 17 — Fallback / troubleshooting quick table

| Symptom | Likely cause | Fix |
|---|---|---|
| `exec: executable gke-gcloud-auth-plugin not found` | SDK `bin/` not on this shell's PATH | `export PATH="<GCLOUD_SDK_BIN>:$PATH"` before anything else |
| `gcloud container fleet memberships describe` → "No memberships available" | Cluster genuinely not registered | Run §8 onboarding first |
| Ansible fails at the cost-approval gate | `allow_billable_external_cluster` not set for that cluster | Confirm real team approval, then set it explicitly for that one cluster only |
| `verify.yml` fails with "Failed to impersonate" | Your account lacks `roles/iam.serviceAccountTokenCreator` on the runtime SA | Grant it temporarily, remove it again after (per `ansible/PORTING.md`) |
| Agent Engine call times out / errors | Deployed service issue, not a demo-script issue | Fall back to narrating the last saved validation report; say plainly it's not live |
| RCA doesn't match `kubectl` evidence | Something is actually broken — do not paper over it | Stop, say so, move to the next slide; this is exactly what the independent-verification step exists to catch |
