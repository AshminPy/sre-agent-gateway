# Change Management

> **Implementation Status:** Recommended process — the technical gate (PR + CI) is IMPLEMENTED; the review-tiering below is a recommendation, not an enforced policy in this repo.
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

Every change already goes through PR → CI (`terraform-plan.yml` on the PR, `terraform-apply.yml` on merge — see [CI/CD](../operations/deployment.md)). This page recommends which changes need **additional** human review beyond a routine code-review, given what each type of change actually touches.

| Change type | Recommended review bar | Why |
|---|---|---|
| New cluster | Platform team + whoever owns the target project's IAM | Grants real cross-project access — see [Adding a New GKE Cluster](../runbooks/add-gke-cluster.md) |
| New MCP server | Platform team + security review before enabling in production | New network path + new tool surface — see [Adding a New MCP Server](../runbooks/add-mcp-server.md) |
| New MCP tool | Platform team, confirm read-only verb + allowlist correctness | Directly affects the read-only guarantee — see [Security Operations](security.md) |
| Additional permissions (any IAM grant) | Two-reviewer minimum, security-aware reviewer | This is the highest-blast-radius change type in this system |
| Model change | Platform team + run the golden eval suite before promoting (not automated — a manual step, see [Evaluation](../architecture/evaluation.md)) | Silent regression risk — the CI pipeline doesn't gate on eval pass rate today |
| Prompt change | Same as model change | Same risk — prompts directly shape what the model proposes, which the confidence scorer then evaluates |
| Graph change (adding/removing/reordering nodes) | Platform team, architectural review | Changes the actual safety-rail shape described in [LangGraph Workflow](../architecture/langgraph-workflow.md) |
| Confidence logic change (`agent/confidence/`) | Platform team + whoever owns the eval dataset | This is the mechanism the whole system's trustworthiness rests on — see [Confidence Scoring](../architecture/confidence.md) |
| Memory logic change | Platform team, explicit consideration of the poisoning-risk implications | See [Memory](../architecture/memory.md) |
| Automatic-remediation capability (if ever proposed) | Executive/Security sign-off, not a routine PR | This would be a fundamental change to the system's core safety property (read-only) — see [AI Governance](ai-governance.md) |
| Network/security changes (gateway, Model Armor, networking) | Security review mandatory | See the Model Armor and fail-open findings in [Security Operations](security.md) |

## What's not currently enforced by tooling

None of the review tiers above are technically gated (e.g., no CODEOWNERS-based required-reviewer enforcement was confirmed in this pass, no branch-protection rule specifically requiring a security reviewer for IAM changes). This table is a **recommended process**, not a described-as-implemented control — be accurate about that distinction if this document is used in an audit context.

---

**Related pages:** [Security Operations](security.md) · [AI Governance](ai-governance.md) · [Updating the Agent](../operations/deployment.md)
