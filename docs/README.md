# SRE AI Agent — Knowledge Base and Operations Handbook

**Implementation Status:** This entire knowledge base documents the currently-deployed system in GCP project `sreagent-t2-demo`, built by direct inspection of the repository, Terraform, and live configuration — not from design documents or assumptions. Every page carries its own status header (Implementation Status, Last Verified, Source of Truth, Owner) and marks claims IMPLEMENTED / PARTIALLY IMPLEMENTED / PLANNED / DEPRECATED / UNKNOWN.

**Last Verified:** 2026-08-20 (index and page count re-verified; individual pages carry their own dates — several were last verified 2026-08-08/09 and predate ~80 commits, so trust current code over any page that disagrees)
**Owner:** SRE Agent platform team

---

## Start here

- **New to this system?** Start with [System Overview](architecture/system-overview.md), then follow the [Run-Team Onboarding Guide](onboarding/run-team-guide.md).
- **On call / operating today?** Start with [Daily Health Check](operations/daily-health-check.md), then bookmark the [Troubleshooting Runbooks](#part-2--operations-administration-governance-and-runbooks).
- **Evaluating this for governance/security/risk?** Start with [Risks and Limitations](management/risks-and-limitations.md), then [Security Operations](governance/security.md) and [AI Governance](governance/ai-governance.md).
- **Just need a term explained?** [Glossary](onboarding/glossary.md).

---

## Part 1 — How the SRE AI Agent Works

| Page | Covers |
|---|---|
| [System Overview](architecture/system-overview.md) | What it is, what it does and doesn't do, the end-to-end flow |
| [Agent Engine](architecture/agent-engine.md) | Vertex AI Agent Engine — what Google manages vs. what we control |
| [LangGraph Workflow](architecture/langgraph-workflow.md) | Every node, every edge, the full graph |
| [Context and State](architecture/context-and-state.md) | What "context" means here, the full `AgentState` reference |
| [Investigation Loop](architecture/investigation-loop.md) | Who decides what, exact loop limits, every exit reason, a worked example |
| [Tool Selection](architecture/tool-selection.md) | How the agent picks a tool, what prevents an invalid call |
| [MCP Architecture](architecture/mcp-architecture.md) | What MCP is, our two sources, current status of each |
| [Agent Gateway](architecture/agent-gateway.md) | Egress governance, IAP authorization, the atomic-PATCH TLS mechanism |
| [Dynamic MCP Routing](architecture/dynamic-mcp-routing.md) | How the MCP *source* is selected |
| [Cluster Routing](architecture/cluster-routing.md) | How the target *cluster* is identified, the 5-tier chain |
| [GKE vs. Non-GKE Kubernetes Access](architecture/gke-vs-nongke.md) | Both paths, and which parts are actually production-ready |
| [Agent Identity](architecture/agent-identity.md) | The no-static-key identity model, the full auth chain |
| [Evidence Architecture](architecture/evidence-architecture.md) | Raw vs. structured evidence, redaction, retention |
| [Confidence Scoring](architecture/confidence.md) | The two-axis deterministic scoring system |
| [Memory](architecture/memory.md) | Session state vs. GCS archive vs. long-term Memory Bank |
| [Evaluation](architecture/evaluation.md) | The golden dataset, deterministic scoring, what does NOT exist (LLM-as-judge) |
| [RCA Generation](architecture/rca-generation.md) | The final RCA schema and what must be evidence-backed |

## Part 2 — Operations, Administration, Governance, and Runbooks

### Operations
| Page | Covers |
|---|---|
| [Operations Overview](operations/operations-overview.md) | Entry point for day-to-day operation |
| [Daily Health Check](operations/daily-health-check.md) | The shift-start checklist |
| [Observability](operations/observability.md) | Every metric, what's normal, what to do |
| [Logs](operations/logging.md) | Every log source, how to trace one investigation |
| [Tracing](operations/tracing.md) | Cloud Trace, trace_id correlation |
| [Alerting](operations/alerting.md) | Every configured alert, and what's missing |
| [Updating the Agent / CI/CD](operations/deployment.md) | The full deploy pipeline |
| [Rollback](operations/rollback.md) | How to undo a bad change |
| [Terraform / Infrastructure Management](operations/terraform.md) | Repo layout, what must never be hand-edited |
| [Disaster Recovery](operations/disaster-recovery.md) | What's durable, what's rebuildable |

### Runbooks
| Page | Covers |
|---|---|
| [Gateway Failures](runbooks/gateway-failure.md) | Gateway unavailable, auth failure, TLS/cert failure |
| [Identity/IAM Failures](runbooks/identity-failure.md) | Agent Identity failure, permission denied |
| [MCP Failures](runbooks/mcp-failure.md) | Server unavailable, tool missing, schema mismatch, timeout, GKE/custom/Connect Gateway failures |
| [Routing Failures](runbooks/routing-failure.md) | Wrong cluster, unreachable cluster |
| [Investigation-Level Failures](runbooks/investigation-failure.md) | Model failures, loop limits, low confidence, cost/latency spikes |
| [Deployment Failures](runbooks/deployment-failure.md) | Deploy failure, regression, CI/CD failure, Terraform drift |
| [Data and Integration Failures](runbooks/data-and-integration-failure.md) | Evidence/memory failures, PagerDuty (N/A today) |
| [Adding a New GKE Cluster](runbooks/add-gke-cluster.md) | Full onboarding runbook |
| [Adding a Non-GKE / On-Prem Cluster](runbooks/add-non-gke-cluster.md) | The full build (not just a config change) |
| [Adding a New MCP Server](runbooks/add-mcp-server.md) | Template for future sources (Elastic, Prometheus, etc.) |
| [Updating Existing MCP Tools](runbooks/update-mcp-tool.md) | Add/remove/rename/change-schema procedures |

### Governance
| Page | Covers |
|---|---|
| [Component Ownership](governance/ownership-raci.md) | Full component inventory |
| [Security Operations](governance/security.md) | Full IAM matrix, read-only proof, **Model Armor finding** |
| [AI Governance](governance/ai-governance.md) | Transparency, human control, reliability, accountability, data/model governance |
| [Cost Management](governance/cost-management.md) | Every cost driver, how to measure per-investigation cost |
| [Scaling](governance/scaling.md) | What changes at 10x/100x volume, clusters, tools |
| [Reliability and Failure Modes](governance/reliability.md) | The full failure-mode matrix, recommended SLIs |
| [Capacity and Quotas](governance/capacity.md) | Where to check live GCP quotas |
| [Change Management](governance/change-management.md) | Recommended review tiers by change type |

### Management
| Page | Covers |
|---|---|
| [Executive Overview](management/executive-overview.md) | One-paragraph summary + honest current-state |
| [Executive FAQ](management/executive-faq.md) | Plain-English answers to the questions leadership will ask |
| [Risks and Limitations](management/risks-and-limitations.md) | **The canonical, ranked list of what's actually still open** |

### Onboarding
| Page | Covers |
|---|---|
| [Run-Team Onboarding Guide](onboarding/run-team-guide.md) | A 10-day path with labs |
| [Glossary](onboarding/glossary.md) | Every term, explained simply |

### Onboarding (continued) — code traceability

| Page | Covers |
|---|---|
| [Code Reference Map](onboarding/code-reference-map.md) | **"Where is X implemented?"** — the master lookup table: file, function, Terraform, tests, and how to prove each capability is running |
| [Code Ownership Map](onboarding/code-ownership-map.md) | Which component belongs to which area |

### Status and decisions

| Page | Covers |
|---|---|
| [Implemented vs Planned Matrix](management/implemented-vs-planned-matrix.md) | **The current capability truth** — every capability, ✅/🟡/🔵/❌, with file:line or live-command evidence |
| [Show Me the Implementation](management/show-me-the-implementation.md) | Management-facing "prove it" answers |
| [Documentation Validation Report](DOCUMENTATION-VALIDATION-REPORT.md) | **Historical snapshot (2026-08-08/09)** — audit evidence, not current status |
| ADR-001 … ADR-012 | Architecture decision records — [001 two-project split](ADR-001-two-project-split.md) · [002 agent identity and gateway](ADR-002-agent-identity-and-gateway.md) · [003 LangGraph orchestration](ADR-003-langgraph-orchestration.md) · [004 MCP tool-access protocol](ADR-004-mcp-tool-access-protocol.md) · [005 read-only by design](ADR-005-read-only-by-design.md) · [006 evidence before RCA](ADR-006-evidence-before-rca.md) · [007 two confidence dimensions](ADR-007-two-confidence-dimensions.md) · [008 confidence not accuracy](ADR-008-confidence-not-accuracy.md) · [009 GCS durable evidence archive](ADR-009-gcs-durable-evidence-archive.md) · [010 human approval before trusted memory](ADR-010-human-approval-before-trusted-memory.md) · [011 Terraform-managed cluster registry](ADR-011-terraform-managed-cluster-registry.md) · [012 GKE Remote MCP vs custom MCP](ADR-012-gke-remote-mcp-vs-custom-mcp.md) |

### Promotion to the company repo

| Page | Covers |
|---|---|
| [Test-to-Work Process](promotion/01-test-to-work-process.md) | Safe personal-repo → company-repo promotion, with a worked example |
| [Migration Manifest Template](promotion/02-migration-manifest-template.md) | The per-promotion checklist |

### Design notes, baselines and test evidence

| Page | Covers |
|---|---|
| [Confidence Framework Design](confidence-framework-design.md) | The design behind the scoring system. **Referenced directly by source code** — do not move |
| [Least-Privilege IAM](least-privilege-iam.md) | Full live role inventory |
| [Connect Gateway / On-Prem](connect-gateway-onprem.md) | The non-GKE access path |
| [Custom K8s MCP](custom-k8s-mcp.md) | The Cloud Run MCP fallback |
| [Trace Content Capture](trace-content-capture.md) | What tracing does and does not record |
| [Tool-Scaling Baseline (corrected)](baselines/tool-scaling-baseline-2026-08-09-corrected.md) | The official baseline. [Original run](baselines/tool-scaling-baseline-2026-08-09.md) kept as history |
| [E2E Honest Baseline](testing/e2e-honest-baseline-2026-08-09-notification-relay.md) | Real end-to-end test evidence. **Referenced by tests and `agent/mcp_client.py`** — do not move |

---

## Final deliverables checklist

1. Complete knowledge-base directory — ✅ this tree, 81 markdown pages across 8 sections
2. Architecture documentation — ✅ Part 1, 17 pages
3. Operations handbook — ✅ 10 pages
4. Troubleshooting runbooks — ✅ 11 pages covering all 30 requested scenarios + 4 onboarding-flow runbooks
5. Scaling procedures — ✅ [Scaling](governance/scaling.md)
6. Security documentation — ✅ [Security Operations](governance/security.md)
7. AI governance documentation — ✅ [AI Governance](governance/ai-governance.md)
8. Cost-management documentation — ✅ [Cost Management](governance/cost-management.md)
9. Reliability documentation — ✅ [Reliability and Failure Modes](governance/reliability.md)
10. Executive/management FAQ — ✅ [Executive FAQ](management/executive-faq.md)
11. Run-team onboarding guide — ✅ [Run-Team Onboarding Guide](onboarding/run-team-guide.md)
12. Glossary — ✅ [Glossary](onboarding/glossary.md)
13. Mermaid architecture diagrams — ✅ embedded in [System Overview](architecture/system-overview.md), [LangGraph Workflow](architecture/langgraph-workflow.md), [Dynamic MCP Routing](architecture/dynamic-mcp-routing.md) — see the [Documentation Validation Report](DOCUMENTATION-VALIDATION-REPORT.md) for the full diagram count against the requested 18
14. Component inventory — ✅ [Component Ownership](governance/ownership-raci.md)
15. IAM/permission matrix — ✅ [Security Operations](governance/security.md)
16. Metric/alert inventory — ✅ [Observability](operations/observability.md), [Alerting](operations/alerting.md)
17. Failure-mode matrix — ✅ [Reliability and Failure Modes](governance/reliability.md)
18. Current-vs-planned implementation matrix — ✅ every page's status header, consolidated in the [Documentation Validation Report](DOCUMENTATION-VALIDATION-REPORT.md)
19. Known gaps — ✅ [Risks and Limitations](management/risks-and-limitations.md)
20. Documentation coverage report — ✅ [Documentation Validation Report](DOCUMENTATION-VALIDATION-REPORT.md)

**→ [Read the full Documentation Validation Report](DOCUMENTATION-VALIDATION-REPORT.md)**
