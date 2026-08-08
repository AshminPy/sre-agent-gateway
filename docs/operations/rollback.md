# Rollback

> **Implementation Status:** PARTIALLY IMPLEMENTED — no dedicated rollback mechanism exists; this is a manual procedure using standard git/Terraform tools
> **Last Verified:** 2026-08-08
> **Owner:** SRE Agent platform team.

## Agent code / LangGraph workflow / model configuration / prompts

There is **no versioned rollback resource** for the reasoning engine — it's a single Terraform resource updated in place. Rollback means:

```bash
git checkout <last-known-good-commit>
bash scripts/package_agent.sh
terraform apply   # re-submits the older source against the same engine resource
bash scripts/attach_gateway_to_engine.sh   # required after any engine-touching apply
```

Do this via the same CI pipeline (revert the merge, push to main) rather than manually where possible — see [Terraform / Infrastructure Management](terraform.md) for why manual applies are discouraged.

## MCP server / MCP tool schema

For the custom MCP: same pattern — redeploy an older container image tag (Artifact Registry retains prior tags by SHA), re-run `scripts/register_custom_mcp.py` if the tool spec itself needs to revert too.

## Gateway configuration

The gateway's own Terraform resources (`iac/agent/agent_gateway.tf`) can be rolled back via normal `terraform apply` against an older commit — but remember the atomic-PATCH requirement (see [Agent Gateway](../architecture/agent-gateway.md)): any gateway-touching change needs `attach_gateway_to_engine.sh` re-run afterward regardless of direction (forward or rollback).

## Terraform (general)

Standard `terraform apply` against a prior commit's `.tf` files. **Never** `terraform destroy` + recreate as a "rollback" strategy without understanding what state that destroys — several resources have `prevent_destroy = true` specifically to guard against this (evidence bucket, eval bucket).

## Memory changes

Memory Bank entries are not tied to a deployable "version" — a bad memory entry would need manual deletion via the Vertex AI Memory Bank console/API (see [Memory](../architecture/memory.md)), not a rollback in the deployment sense.

## What's genuinely missing here

- No blue/green or canary deployment pattern.
- No automated "detect regression, auto-rollback" mechanism — a human has to notice (see the CI eval-gate gap in [Updating the Agent](deployment.md)) and initiate the rollback manually.
- No dedicated rollback runbook tooling beyond "re-apply an older commit."

If your organization needs a stronger rollback guarantee than this, that's a real gap to plan for, not something already built and just undocumented.

---

**Related pages:** [Updating the Agent](deployment.md) · [Terraform / Infrastructure Management](terraform.md) · [Deployment Failure runbook](../runbooks/deployment-failure.md)
