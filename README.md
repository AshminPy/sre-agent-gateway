# testing2-gcp-sre-agent

An AI **Site Reliability Engineering** agent that investigates Kubernetes
incidents on GKE — read-only — and produces a structured Root Cause Analysis.
It runs on **Vertex AI Agent Engine**, reasons with **Gemini**, remembers past
investigations in a **Memory Bank**, filters inputs/outputs with **Model Armor**,
and (optionally) has all of its egress governed by an **Agent Gateway**.

Everything is Terraform. The agent runs in **Project A**; the GKE cluster it
investigates lives in **Project B** (yours, existing or demo). Clone, fill in two
project IDs, and deploy.

**Proven working end-to-end, including the governed Agent Gateway path** —
see [`RCA_REPORT.md`](RCA_REPORT.md) for the full investigation and fix if
you hit a gateway-binding failure (`error.code: 3` on the attach step). The
short version is in [Troubleshooting](#troubleshooting) below.

![Architecture](docs/architecture.png)

---

## What it does

- **9-node LangGraph investigation** — normalize → resolve context → plan →
  route MCP → execute tools → extract evidence → evaluate → loop → build RCA.
- **Read-only, safe** — a hard allowlist blocks any mutating Kubernetes verb.
- **GKE Remote MCP** (Google-managed) as the primary tool source, with an
  optional custom Cloud Run MCP fallback.
- **Model Armor** on inputs and outputs (prompt-injection, PII, malicious URLs).
- **Memory Bank** — remembers prior RCAs per cluster/namespace.
- **Full observability** — structured run logs, 6 log-based metrics, 3 alert
  policies, OpenTelemetry traces.
- **Agent Gateway** (optional) — decodes and authorizes the agent's MCP calls
  and inspects content, with IAP `REQUEST_AUTHZ` + Model Armor `CONTENT_AUTHZ`.
- **Least-privilege IAM** throughout (see [docs/least-privilege-iam.md](docs/least-privilege-iam.md)).

## Repository layout

```
agent/                LangGraph agent (deployed inline into Agent Engine)
mcp/                  Optional custom Cloud Run MCP server
k8s/                  Demo incident manifests to deploy into Project B
eval/                 Evaluation dataset
scripts/              package_agent, register_endpoints, attach_gateway, smoke, init-env
docs/                 Architecture, ADRs, least-privilege IAM
.github/workflows/    Terraform plan (PR) + apply (main) via Workload Identity
iac/
  agent/              Stack A — Project A (agent + gateway + everything)
  gke-access/         Stack B — Project B (GKE + read-only cross-project IAM)
```

## Prerequisites

1. Two GCP projects with billing linked — **Project A** (agent) and **Project B**
   (GKE). They may be new, or Project B may be an existing project with a GKE
   cluster you already own.
2. `gcloud`, `terraform >= 1.6`, `python >= 3.11`. Agent packaging
   (`scripts/package_agent.sh`) uses only the Python standard library, so it's
   reproducible out of the box on macOS, Linux, and CI — no extra tools needed.
3. Enable the bootstrap API in each project (Terraform can't enable the API it
   uses to enable APIs):
   ```bash
   gcloud services enable serviceusage.googleapis.com --project PROJECT_A
   gcloud services enable serviceusage.googleapis.com --project PROJECT_B
   ```
4. A GCS bucket for Terraform state in Project A (versioned):
   ```bash
   gcloud storage buckets create gs://PROJECT_A-tfstate --project PROJECT_A --uniform-bucket-level-access
   gcloud storage buckets update gs://PROJECT_A-tfstate --versioning
   ```
5. Authenticate: `gcloud auth application-default login` (or use the WIF CI flow).
6. Check Vertex AI's Gemini quota for Project A (Console → IAM & Admin → Quotas,
   filter `aiplatform.googleapis.com`, model `gemini-2.5-flash` or whichever
   model you set via `gemini_model`). New/low-usage projects can default to a
   very low per-minute request quota (seen as low as 1-5 RPM on some models) —
   `make smoke` will fail with `429 RESOURCE_EXHAUSTED` if you hit it, which
   looks like a broken deploy but isn't. Request a quota increase, or point
   `gemini_model` at a model with more headroom on your project, before
   assuming something is wrong.

## Deploy

### Step 1 — GKE + cross-project access (Project B)

```bash
cd iac/gke-access
cp terraform.tfvars.example terraform.tfvars   # fill in project_a_id, project_b_id
terraform init -backend-config="bucket=PROJECT_A-tfstate" -backend-config="prefix=gke-access"
terraform plan
terraform apply
```

Already have a GKE cluster? Set `create_gke_cluster = false` and
`gke_cluster_name` to your cluster — this stack then only applies the four
read-only cross-project grants.

### Step 2 — Agent + Gateway (Project A)

```bash
cd ../agent
cp terraform.tfvars.example terraform.tfvars   # fill in project_a_id, project_b_id, notification_email, github_repo
make -C ../.. package-agent                    # build agent.tar.gz (filebase64 reads it at plan time)
terraform init -backend-config="bucket=PROJECT_A-tfstate" -backend-config="prefix=agent"
terraform plan
terraform apply
```

The command above runs a **self-contained local apply** (`create_wif = true`,
the default): it bootstraps the CI/CD deployer identity *and* deploys the agent
in one shot. That's the quickest way to stand the stack up.

#### Alternative — deploy through GitHub Actions (git-driven)

Prefer every apply to flow through pull requests? Bootstrap the CI deployer
identity once with gcloud, then let GitHub Actions run plan (on PRs) and apply
(on merge to `main`, behind the `production` environment approval gate):

```bash
# 1. Seed the deployer identity out-of-band (a deployer can't create the
#    identity it runs as — see scripts/bootstrap_wif.sh for why).
PROJECT_A_ID=my-agent-proj \
GITHUB_REPO=my-org/testing2-gcp-sre-agent \
TFSTATE_BUCKET=my-agent-proj-tfstate \
bash scripts/bootstrap_wif.sh

# 2. Set the repo secrets it prints (plus GCP_PROJECT_B_ID, GCP_REGION,
#    NOTIFICATION_EMAIL) via `gh secret set` or the GitHub UI.

# 3. Open a PR → terraform-plan runs. Merge → terraform-apply runs.
```

The CI workflows pass `create_wif=false`, so Terraform manages everything
*except* the pre-seeded deployer identity. See
[docs/least-privilege-iam.md](docs/least-privilege-iam.md) for the deployer's
scoped roles.

### Step 3 — Post-apply (only when the gateway is enabled)

```bash
cd ../..
make register-endpoints    # register Google-API + MCP hosts in Agent Registry
make attach-gateway        # bind the reasoning engine to the gateway (REST PATCH)
```

> The gateway's data plane provisions asynchronously on Google's side and may
> take a while before traffic flows — see
> [docs/ADR-002](docs/ADR-002-agent-identity-and-gateway.md). For a deploy that
> works immediately with no post-apply steps, set `enable_agent_gateway = false`.

### Step 4 — Try it

```bash
source scripts/init-env.sh                      # writes agent/.env, exports engine id
kubectl apply -f k8s/namespace.yaml             # create the test-incidents namespace (first time only)
kubectl apply -f k8s/imagepull-pod.yaml         # create a failing pod in Project B's cluster
make smoke                                      # invoke the agent, assert an RCA
```

## Two deploy modes

| Mode | `enable_agent_gateway` | Behaviour |
|---|---|---|
| **Gateway-free** (simplest) | `false` | Agent works immediately; no gateway, no post-apply steps. |
| **Governed** (default) | `true` | Egress routed through the Agent Gateway; run the Step-3 post-apply scripts. |

## Local development & evaluation

```bash
source scripts/init-env.sh
python invoke_agent.py --list                   # list incident scenarios
python invoke_agent.py --scenario oomkilled --verbose
python eval.py --project $PROJECT_ID --region $REGION --engine-id $REASONING_ENGINE_ID
```

## Verify / lint

```bash
make fmt        # terraform fmt both stacks
make validate   # terraform validate both stacks
```

## Troubleshooting

### Gateway binding fails with `error.code: 3`

If `make attach-gateway` (or `scripts/attach_gateway_to_engine.sh` directly)
fails with `{"code": 3, "message": "The Reasoning Engine failed to be
updated."}`, work through these in order — this exact sequence resolved a
real, multi-day production incident (full writeup: [`RCA_REPORT.md`](RCA_REPORT.md)):

1. **Confirm the script bundles source + gateway config in one call.**
   `scripts/attach_gateway_to_engine.sh` does this correctly as shipped —
   the reasoning engine only trusts the gateway's TLS-inspection
   certificate when the source deployment and `agentGatewayConfig` are
   submitted together, in one atomic `PATCH`. Two sequential calls (even
   seconds apart) do not work. If you've modified the script, verify this
   didn't regress.
2. **Re-run after every `terraform apply` that touches the engine.**
   Terraform doesn't manage `agentGatewayConfig` (not yet exposed by the
   provider), so any apply on the engine resource silently wipes the
   binding. Always re-run the attach script immediately after.
3. **Check the script's own pre-flight diagnostics.** It fetches and
   prints the live engine and gateway state, checks required APIs,
   endpoint registration, and IAM service agents before attempting the
   bind — read that output first; it usually rules things in or out
   directly.
4. **If every check passes and it still fails identically, suspect the
   engine resource itself, not its configuration.** A reasoning engine
   that has failed several bind attempts can accumulate internal state
   that blocks all further attempts, independent of what configuration is
   applied to it. The fix: recreate it —
   ```bash
   terraform apply -replace='google_vertex_ai_reasoning_engine.sre_agent' -var="create_wif=false"
   bash scripts/attach_gateway_to_engine.sh
   ```
   This is a clean, Terraform-tracked destroy+recreate — every dependent
   IAM/registry binding that references the engine's identity is
   automatically replaced along with it, and nothing in this codebase
   hardcodes the engine ID (everything is looked up dynamically via
   `terraform output`), so no other files need updating afterward.

### Isolating whether it's your code, your project, or your gateway

If you're debugging a similar failure and want to isolate the variable,
the two tests that actually settled it in production:

- **Is it your code?** Deploy `iac/agent` fresh into a brand-new,
  untouched project, changing nothing but the project ID. If it binds,
  your Terraform/code is fine.
- **Is it your project or your gateway?** Create a second, temporary
  reasoning engine (a standalone REST `POST`, not added to Terraform state)
  inside the *same* project, and try binding *it* to your existing gateway.
  If that succeeds, the project and gateway are both fine — the problem is
  specific to the original engine resource (see step 4 above).

## Clean up

```bash
# reverse order: agent stack, then gke-access stack
terraform -chdir=iac/agent destroy
terraform -chdir=iac/gke-access destroy
```

## License

Apache 2.0 — see [LICENSE](LICENSE).
