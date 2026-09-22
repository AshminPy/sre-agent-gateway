# SRE Agent Gateway — Management Demo Presentation

Minimal slide outline, 5 chapters. Built 2026-09-22 from verified facts in this repo —
every factual claim here has a cited source in [`demo-runbook.md`](./demo-runbook.md).

**Before presenting at work:** this deck's architecture/security/design content is
environment-independent. Only the live command *values* (project ID, cluster names,
reasoning engine ID, region) are specific to the personal proving-ground project
(`sreagent-t2-demo`). Swap those for your work project's real values — see the
placeholder table at the top of `demo-runbook.md`. This exact combination (work
laptop, work network, work project) has not been tested; the design has been proven
twice end-to-end on the personal project.

Honesty rule for every slide below: label anything not yet running as **PLANNED**, not
shipped. Two things in this repo need that label — say so out loud, don't let it pass
as "already live."

---

## Chapter 1 — What did we build?

**Slide 1 — Title**
SRE Agent Gateway: AI-assisted Kubernetes incident investigation.

**Slide 2 — The one-line pitch**
An AI agent investigates a broken pod or cluster and returns an evidence-backed root
cause analysis (RCA) — read-only, across both GKE and non-GKE (on-prem) clusters.

**Slide 3 — Architecture (one diagram)**
Agent Engine (Vertex AI, LangGraph workflow) → Agent Gateway (authz + Model Armor) →
MCP layer (GKE Remote MCP for GKE clusters / custom Cloud Run MCP for on-prem via
Fleet Connect Gateway) → target cluster → evidence extracted to GCS → RCA returned.

**Slide 4 — Phase 1 scope** (source: `openspec/changes/phase-1-mvp-release/specs/phase-1-release-criteria/spec.md`)
- Connect Gateway investigation of a non-GKE cluster, in production.
- Plug-and-play cluster onboarding (GKE same-project, cross-project, non-GKE) — config only.
- Config-only LLM provider/model switching.
- Full read-only tool parity between the two MCP paths.
- Deterministic cluster/MCP routing, fail-closed on ambiguity.
- All agent/tool traffic enforced through the Agent Gateway.
- Evidence-backed, non-fabricated RCA output.
- Model Armor validated against a real malicious payload on both MCP paths.

**Slide 5 — Security boundaries at a glance**
- 4 independent read-only enforcement layers (tool allowlist → `@guarded()` decorator →
  read-only Kubernetes client calls, CI-enforced → Kubernetes RBAC ceiling).
- Identity: Vertex AI Agent Identity (SPIFFE-format, no downloadable key) +
  workload identity federation for on-prem clusters — no static service-account keys
  anywhere in this system.
- Model Armor: live on two paths (Agent Gateway `CONTENT_AUTHZ` extension, and an
  app-level response guard in the custom MCP) — with one documented platform gap
  (Google's MCP transport doesn't invoke response-body inspection on tool-call
  output). State this gap plainly if asked; don't let it be discovered live.

---

## Chapter 2 — Does it actually work?

**Slide 6 — GKE live incident**
A real broken pod (`imagepull-pod`, bad image reference) on a real GKE cluster.
Invoke the agent, watch it return a structured RCA.

**Slide 7 — Verify the GKE RCA independently**
`kubectl describe` / `kubectl get events` on the same pod, shown side by side with the
agent's RCA — same root cause, from raw Kubernetes evidence, not from trusting the agent.

**Slide 8 — On-prem live incident**
Same investigation, same fixture pod pattern, run against a local kind cluster reached
through GCP Fleet + Connect Gateway instead of a native GKE API call.

**Slide 9 — Verify the on-prem RCA independently**
Same independent-verification discipline, through the same Connect Gateway path the
agent's MCP server actually uses (not a shortcut kubeconfig context).

**Slide 10 — Safety behavior: unauthorized cluster refusal**
Ask the agent to investigate a cluster that was never registered. It does not guess or
silently fall back — it stops safely and names exactly why. Two independent layers
enforce this (agent-side routing + MCP-server-side registry check).

---

## Chapter 3 — How do we operate it?

**Slide 11 — Add a GKE cluster**
One Terraform config block (`additional_clusters`), one required Kubernetes
RoleBinding, `terraform apply`. No agent or MCP code change.

**Slide 12 — Add an on-prem cluster (Ansible)**
One playbook: `ansible-playbook playbooks/onboard.yml`. Idempotent, ownership-safe
cleanup, and — the newest safety fix — an explicit cost-approval gate before it will
ever register a new billable external cluster (built this week after a real billing
incident on this exact project — see Chapter 5).

**Slide 13 — Least-privilege proof**
Live: the runtime identity's GCP IAM roles (Fleet gateway access only), the Kubernetes
RBAC objects Ansible created (`view` ClusterRole + a narrow node-read supplement,
nothing else), and a live denial (`kubectl delete pod` → Forbidden).

**Slide 14 — Observability**
Structured logs by `run_id` in Cloud Logging; a live Looker Studio investigation
dashboard built on BigQuery views fed by Cloud Logging sinks.

---

## Chapter 4 — How do we extend and release it?

**Slide 15 — Switch the LLM**
A config value (`gemini_model` in `terraform.tfvars`), not a workflow code change —
with one honest caveat: the CI pipeline currently hardcodes the model in the GitHub
Actions workflow too, so a change made only in `tfvars` would be silently overridden
on the next automated apply. Say this out loud; it's a real gap, not a gotcha to hide.
Switching to a non-Gemini vendor is real, unstarted work today — say PLANNED, not done.

**Slide 16 — Add a new MCP**
The pattern is config-driven by design (new registry entry, optional new Cloud Run
service) and fully documented. **PLANNED** — no second MCP source is actually running
yet. Present it as "here's how it plugs in," not "here's a second one working."

**Slide 17 — CI/CD (infrastructure)**
PR → automated `terraform plan` comment → merge to main → manual-approval-gated
`terraform apply` (GitHub Actions `environment: production`). Terraform is pinned to
the same version the target company's Spacelift platform uses, for a clean handoff —
Spacelift itself is not wired up as the pipeline in this repo; be precise about that
if asked.

**Slide 18 — MCP image CI/CD**
Same GitHub Actions workflow, a gated stage: build/push the custom MCP's container
image only when `mcp/**` changes, re-point Cloud Run at the new image, health-check it,
then regenerate the tool spec.

**Slide 19 — Documentation**
Point at the docs index (`docs/README.md`) and the specific runbooks: add-gke-cluster,
add-onprem-cluster, switch-llm-model, add-mcp-server, operate. Flag plainly that the
index itself notes some pages are stale relative to ~90 recent commits — don't present
every doc as freshly verified.

---

## Chapter 5 — What comes next?

**Slide 20 — Known Phase 1 limitations** (source: `docs/management/risks-and-limitations.md`)
- No PagerDuty integration — every investigation trigger today is manual.
- Connect Gateway successful-read audit logging is off by default (writes are audited; reads aren't).
- Confidence scoring is explicitly self-labeled uncalibrated against real outcomes.
- No automated eval-quality gate in CI beyond a single smoke test.
- No tested disaster-recovery drill, no committed SLOs.
- This week's real incident: on-prem Fleet registration briefly cost ~$216/month
  combined across two test clusters before being caught and fixed with an explicit
  cost-approval gate — a genuine example of the operational discipline this system
  still needs, told honestly rather than omitted.

**Slide 21 — Phase 2 direction** (source: `NEXTSTEPS.md`, dated 2026-07-18 — flag as
directional/not re-validated)
RCA output redesign, full security review, full observability/monitoring maturity,
scalability review for more clusters/MCP sources, an RCA accuracy eval suite, cost
optimization, the PagerDuty pipeline, a repeatable new-MCP onboarding pattern.

**Slide 22 — Close / ask**
State plainly what you're asking management for (budget, headcount, approval to
proceed to Phase 2, whatever it actually is) — fill in before presenting.
