# Management / Executive FAQ

> **Implementation Status:** Reference page, plain-English summary of facts established elsewhere in this knowledge base.
> **Last Verified:** 2026-09-06
> **Owner:** SRE Agent platform team.

**What exactly does this system do?**
It automatically investigates Kubernetes incidents — the mechanical first-pass work an SRE would otherwise do by hand (check the pod, check events, check logs) — and produces a written root-cause report with supporting evidence. It never fixes anything itself; a human decides what to do with the report.

**Why use AI here?**
Because the reasoning step — "given these facts, what's the likely cause, and how confident should I be" — genuinely benefits from a language model's ability to synthesize unstructured evidence (log lines, event messages) into a coherent narrative. The *decision of when to stop investigating and how much to trust the result* is deliberately kept in code, not left to the model.

**Why LangGraph?**
It lets us define the investigation's shape (which steps exist, in what order, what can loop) as fixed code, so the AI model can't skip safety steps or invent new ones — see [LangGraph Workflow](../architecture/langgraph-workflow.md).

**Why MCP?**
It's the open standard for how an AI agent calls external tools, and it let us use Google's own managed Kubernetes tool server instead of building and operating our own — see [MCP Architecture](../architecture/mcp-architecture.md).

**Why Agent Gateway?**
Centralized egress authorization for everything the agent calls out to — one auditable choke point instead of per-destination IAM sprawl — see [Agent Gateway](../architecture/agent-gateway.md).

**Why Agent Identity?**
No credential file exists to leak. The identity is scoped to this one specific deployment, platform-managed, short-lived — see [Agent Identity](../architecture/agent-identity.md).

**Can it change production?**
No. Confirmed at four independent, code-enforced layers — see [Security Operations](../governance/security.md#kubernetes-access-is-read-only). There is no delete, create, patch, exec, or scale capability anywhere in its tool set.

**Can it cause an outage?**
Not directly, given the above. Indirectly: if it were ever pointed at a cluster with permissions broader than intended (a misconfiguration, not a capability it has by design), or if a compromised MCP source fed it misleading data that led to a bad *human* decision downstream. The read-only guarantee is about what the *agent* can do, not a guarantee that humans will always act correctly on its output.

**How do we know its answers are reliable?**
Every claim in an RCA is checked against real evidence by deterministic code (not just trusted from the model). Confidence scores are computed from code-checkable facts, not the model's self-assessment. But: the specific scoring thresholds are explicitly labeled "uncalibrated" against real incident data — see [Confidence Scoring](../architecture/confidence.md). Treat current confidence numbers as directionally useful, not statistically proven yet.

**How can we audit its decisions?**
Every investigation is fully logged and traced — evidence, tool calls, confidence breakdown, all tied to one `run_id` — see [Logs](../operations/logging.md). One known gap: Connect Gateway (on-prem access, not yet in production use) doesn't currently log successful reads, only denied writes — see [Security Operations](../governance/security.md#audit-logs).

**How much does it cost?**
Tracked per investigation (model tokens); not currently rolled up into a monthly dashboard — see [Cost Management](../governance/cost-management.md).

**What controls the cost?**
Hard bounds on loop iterations (5), wall-clock time (9 minutes), and total tokens (100,000) per investigation — cost can't run away indefinitely on a single incident.

**How does it scale?**
Reasonably well for the volume it currently handles; several real gaps would need addressing before a large jump (undocumented compute ceiling, single-cluster registry limitation) — see [Scaling](../governance/scaling.md).

**What happens when we add 100 clusters?**
Today, this would hit a real limitation almost immediately — the cluster registry only supports one cluster before Terraform overwrites any additions. This needs to be fixed first — see [Cluster Routing](../architecture/cluster-routing.md).

**What happens when an MCP server fails?**
The primary source (GKE Remote MCP) auto-falls-back to a secondary source on failure — except that secondary source isn't actually deployed in production today (see [MCP Architecture](../architecture/mcp-architecture.md)). A GKE Remote MCP outage today has no working fallback.

**What happens if Gemini is unavailable?**
The specific investigation fails; there's no fallback model provider. Transient rate-limit errors are automatically retried.

**Can we change models?**
Yes, via a Terraform variable — but do so deliberately, with the golden eval suite re-run against the candidate first (not automated today).

**Are we locked into Google?**
Largely, yes — the architecture (Agent Engine, Agent Gateway, Agent Identity, Gemini) is GCP-native by design, not built for portability.

**How is sensitive operational data protected?**
Redacted before storage/model exposure (secrets, tokens, PII patterns). Intended additional content-safety inspection (Model Armor) is **currently not active** in the live configuration — a real, current gap worth Security's attention, not a hypothetical one. See [Security Operations](../governance/security.md).

**Can the AI remember incorrect information?**
It could, in principle — only high-confidence, fully-corroborated investigations are written to long-term memory, but there's no human-approval step before that write happens yet. See [Memory](../architecture/memory.md).

**How do we prevent bad memory?**
An automated confidence gate today (not human review). This is a documented, acknowledged gap the platform team is aware of — the fields needed to build a human-review workflow already exist, the workflow itself doesn't yet.

**What is the human role?**
Review every RCA that isn't in the highest-confidence band (most of them, today, given the current calibration status), decide on and execute any remediation, and eventually validate memory entries once that workflow exists.

**Who owns the service?**
The SRE Agent platform team, per [Component Ownership](../governance/ownership-raci.md).

**What are the major production risks?**
See [Risks and Limitations](risks-and-limitations.md) for the full, honest list.

**What would be required before autonomous remediation could ever be considered?**
A fundamentally different system — this one has no mutating capability anywhere in its tool set today, and adding one would be a major architectural and governance decision requiring executive/Security sign-off (see [Change Management](../governance/change-management.md)), not a routine change.

---

**Related pages:** [Risks and Limitations](risks-and-limitations.md) · [AI Governance](../governance/ai-governance.md) · [Glossary](../onboarding/glossary.md)
