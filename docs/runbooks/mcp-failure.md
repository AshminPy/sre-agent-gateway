# Runbook: MCP Server and Tool Failures

> **Last Verified:** 2026-08-08 · **Owner:** SRE Agent platform team

## 8. MCP server unavailable

**Symptom**: `tool_failures` metric spikes for one `mcp_source`.

**How to verify**: Cloud Logging, `logName="projects/sreagent-t2-demo/logs/sre-agent-tool-failures" AND jsonPayload.mcp_source="<source>"`. Check the error text in each entry.

**Resolution — if `gke_remote_mcp`**: this is Google-managed (and Preview/Pre-GA per Google's own labeling) — check the GCP status dashboard first. There is no self-hosted component to restart.
**Resolution — if `k8s_mcp`**: **the custom MCP isn't deployed in production today** (`enable_custom_mcp=false`, no Load Balancer/NEG exists) — see [MCP Architecture](../architecture/mcp-architecture.md). A failure here likely means it's misconfigured to be "on" without its network path built. Don't try to "fix" it live; treat as a known architectural gap, not a live-service outage.

## 9. MCP tool missing

**Symptom**: the model proposes a tool the router safe-stops on (never executes, logs `sre-agent-routing-failures` or just silently returns `done`).

**Likely cause**: the tool genuinely isn't in the allowlist for the selected source (`GKE_REMOTE_TOOLS` / `CUSTOM_K8S_TOOLS`, `agent/mcp_client.py:30-87`) — this is enforced deliberately, not a bug.

**Resolution**: if the tool *should* exist, see [Updating Existing MCP Tools](update-mcp-tool.md) to add it properly (allowlist + underlying implementation + security review).

## 10. MCP schema mismatch

**Symptom**: a tool call that used to work now returns unexpected/malformed data, or fails outright.

**Likely cause**: an upstream change (Google's GKE Remote MCP API, or our own `mcp/server.py` code) changed a tool's expected arguments or response shape.

**How to verify**: check `sre-agent-tool-failures` for the specific error; for the custom MCP, check `mcp/server.py`'s tool implementation against what's actually registered in the Agent Registry (`scripts/register_custom_mcp.py` builds the spec from live introspection of the code, so a mismatch usually means registration is stale).

**Resolution**: re-run the registration script after any tool code change; for `gke_remote_mcp`, there's nothing to re-register on our side — file a case with Google if their API changed unexpectedly.

## 11. MCP timeout

**Symptom**: a tool call hangs or exceeds a reasonable duration.

**How to verify**: check `tool_history`'s `duration_s` for the failing call via the `sre-agent-investigations` log entry (or trace spans — see [Tracing](../operations/tracing.md)).

**Resolution**: no explicit per-call timeout override was found configured in application code beyond whatever the underlying HTTP client's default is — this is **UNKNOWN/needs verification** if you need a hard SLA on individual tool calls. Escalate to platform team if this becomes a recurring issue.

## 12. Wrong MCP selected

**Symptom**: an investigation used `k8s_mcp` when it should have used `gke_remote_mcp` or vice versa.

**How to verify**: check the resolved cluster's `type` field in `clusters.json` — source selection is 100% deterministic based on this field (`agent/nodes/mcp_router.py:110-136`), so a "wrong" selection almost always means the registry entry itself has the wrong `type`.

**Resolution**: fix `clusters.json` (see [Adding a New GKE Cluster](add-gke-cluster.md) for how to edit it, and the wipe-on-apply caveat).

## 15. GKE Remote MCP failure

See #8 above (gke_remote_mcp case).

## 16. Custom MCP failure

See #8 above (k8s_mcp case) — also see [MCP Architecture](../architecture/mcp-architecture.md) for the full "why it's not deployed" picture before spending time debugging a live-service issue that doesn't exist.

## 17. Connect Gateway failure

**Symptom**: an on-prem/non-GKE investigation fails to reach its cluster.

**Reality check first**: per [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md), the production agent cannot reach any cluster via Connect Gateway today — this path was only proven manually/locally. If you're seeing this in production, the actual root cause is almost certainly "this path was never wired up," not a transient Connect Gateway outage.

**How to verify (for the manual/prototype path only)**:
```bash
gcloud container fleet memberships describe sre-lab \
  --project=sreagent-t2-demo --location=global
```
Confirm `state: READY`.

**Exact error observed when this was deliberately tested** (Connect Agent scaled to 0 to simulate the on-prem side going away, then a read attempted through Connect Gateway):
```
$ kubectl --context connectgateway_...sre-lab get pods -A
Error from server (BadRequest): Unable to list "/v1, Resource=pods":
the server rejected our request for an unknown reason (get pods)
```

**Why this matters / root-cause analysis**: the failure surfaces as a generic `BadRequest`, not a clearly labeled "cluster unreachable" or "tunnel down" error. This means neither a human operator nor the agent's own error handling can distinguish "the on-prem Connect Agent is down" from "some unrelated server-side problem" from this message alone — treat **any** Connect Gateway `BadRequest` as a possible connectivity failure first, and confirm via `gcloud container fleet memberships describe` (membership state) or a check of the Connect Agent pods' health in the `gke-connect` namespace, rather than assuming the request itself was malformed.

**Resolution**: if you're testing the prototype path specifically, scaling `gke-connect-agent` back up in the `gke-connect` namespace (if it was scaled to 0) resolves connectivity — recovery is automatic, no re-registration needed (documented, tested behavior — `docs/connect-gateway-onprem.md`).

**Escalation**: if the actual goal is production on-prem support, this isn't a "fix the failure" task — it's the multi-step build documented in [Adding a Non-GKE / On-Prem Cluster](add-non-gke-cluster.md).

---

**Related pages:** [MCP Architecture](../architecture/mcp-architecture.md) · [GKE vs Non-GKE Access](../architecture/gke-vs-nongke.md) · [Routing Failure](routing-failure.md)
