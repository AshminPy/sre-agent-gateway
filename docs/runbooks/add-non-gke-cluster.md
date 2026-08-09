# Runbook: Adding a Non-GKE / On-Prem Cluster

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team
> **Prerequisite reading**: [GKE vs Non-GKE Kubernetes Access](../architecture/gke-vs-nongke.md) — read the whole page, this path is NOT production-ready today.

## Reality check before you start

Nothing described here is wired into the deployed agent yet. What's proven: Connect Gateway infrastructure works when tested manually with `kubectl`, and the custom MCP server's code has a working Connect Gateway auth branch when run locally. What's missing: the deployed Cloud Run MCP service has no network path and no connectivity configuration at all. This runbook describes the full build, in order, not a quick config change.

## Steps (build order)

1. **Prerequisites**: Fleet-registerable Kubernetes cluster (kubeconfig access), an owner who can run `gcloud` commands with appropriate project permissions.
2. **Connect Gateway requirements**: register the cluster to the fleet using a private-issuer workload identity (no static SA key — this org's policy blocks key creation):
   ```bash
   gcloud container fleet memberships register <name> \
     --gke-cluster=<location>/<cluster> \
     --enable-workload-identity --has-private-issuer
   ```
   Apply read-only RBAC:
   ```bash
   gcloud container fleet memberships generate-gateway-rbac \
     --membership=<name> --role=clusterrole/view \
     --users=<identity> --project=<project> --kubeconfig=<path> --apply
   ```
   This is currently manual — there is no Terraform resource or wrapper script for either step. Consider whether to build one before repeating this for multiple clusters.
3. **Custom Kubernetes MCP — deploy it for real**: set `enable_custom_mcp=true`, but first build the missing Internal Load Balancer + Serverless NEG (`iac/agent/cloudrun_mcp.tf` currently has neither — see [MCP Architecture](../architecture/mcp-architecture.md)).
4. **Registration**: `scripts/register_custom_mcp.py` (registers the tool spec with Agent Registry — this part is already correct code, just currently unexercised).
5. **Identity/auth**: grant the custom MCP's runtime SA `roles/gkehub.gatewayReader` (it currently only has `roles/container.viewer`, which doesn't cover Connect Gateway).
6. **Cluster mapping**: `clusters.json` needs a new field to distinguish "reach via Connect Gateway" from "reach via direct GKE endpoint" — this field doesn't exist in the schema today; add it to `agent/mcp_client.py`'s registry parser.
7. **Container connectivity config**: set `K8S_MCP_KUBE_CONTEXT` (or equivalent) on the Cloud Run service, and bake `gke-gcloud-auth-plugin`/`gcloud` into `mcp/Dockerfile` — neither exists today.
8. **Testing**: `mcp/tests/test_live_connect_gateway.py` exists but is auto-skipped in CI unless a specific env var is set — decide whether to make this a real, always-run CI test once the path is wired.
9. **Network requirements**: confirm the built Load Balancer/NEG actually allows the agent's Agent-Gateway-routed traffic to reach the Cloud Run service — this is the piece most likely to need iteration.
10. **Failure modes**: test the outage/recovery behavior deliberately (scale `gke-connect-agent` to 0, confirm the error is at least somewhat diagnosable, confirm automatic recovery on scale-back — this was tested and works for the prototype membership).
11. **Audit logging**: turn on `DATA_READ` audit logging for `connectgateway.googleapis.com` at the project level (currently off, meaning successful reads leave no audit trail) — or explicitly, consciously accept that gap and document the risk acceptance.
12. **Add to `clusters.json`**: use `var.additional_clusters` with `type = "custom"` — same mechanism as [Adding a New GKE Cluster](add-gke-cluster.md), fixed 2026-08-09, no separate template fix needed anymore.

---

**Related pages:** [GKE vs Non-GKE Kubernetes Access](../architecture/gke-vs-nongke.md) · [MCP Architecture](../architecture/mcp-architecture.md) · [MCP Failure runbook](mcp-failure.md)
