# ADR-005: Read-only by design — the agent never gets a write/mutate tool

Status: Accepted

## Context

An LLM-driven SRE agent investigating production incidents is a high-blast-
radius surface if it can act, not just observe: a hallucinated or
manipulated tool call that deletes a pod, patches a deployment, or reads a
Secret would turn an investigation tool into an incident of its own. The
agent's investigation loop (`task_planner` → `mcp_router` → `tool_executor`)
is designed to run with minimal human-in-the-loop oversight per tool call, so
the safety property "this agent cannot mutate anything" has to be true
independent of how well the model behaves.

## Decision

Give the agent **no mutating tool, at any layer**, and enforce that as a
structural fact rather than a policy statement:

1. **Application allowlist**: every tool in `GKE_REMOTE_TOOLS`/`CUSTOM_K8S_TOOLS`
   is a `list_*`/`get_*`/`describe_*` verb. A hard-coded `BLOCKED_ACTIONS` set
   (`delete`, `create`, `patch`, `update`, `apply`, `exec`, `port-forward`,
   `scale`, `rollout`) is checked against every proposed call regardless of
   allowlist membership (`agent/mcp_client.py:92,250`).
2. **`@guarded()` decorator** on the custom MCP: rate limiting, input
   validation, namespace-scope enforcement, secret redaction, audit logging —
   defense-in-depth, not itself the source of read-only-ness, since there is
   nothing to block underneath it (see #3).
3. **Underlying client-library verb usage**: every function in `mcp/tools/*.py`
   calls only `list_namespaced_*`/`read_namespaced_*`/`list_node`/`read_node`.
   No `create_*`/`patch_*`/`delete_*`/`.exec(`/`port_forward` exists anywhere
   in `mcp/`. Enforced by a real regression test (`mcp/tests/test_no_mutation.py`,
   confirmed passing locally: `2 passed`) — **not currently wired into CI**, though
   (checked directly: neither `.github/workflows/terraform-plan.yml` nor
   `terraform-apply.yml` runs `pytest` at all, only `terraform validate`/`terraform
   test`/`terraform plan`/`apply`). Today this test protects against regression only
   when someone remembers to run it manually; it does not yet block a merge.
4. **IAM ceiling**: the real `roles/container.viewer` permission set granted
   to the Agent Identity contains zero create/patch/delete/update
   permissions, confirmed live via `gcloud iam roles describe` — so even a
   successfully-crafted mutating call would still be rejected by GCP itself.
5. **No secrets tool exists in the tool set at all** — confirmed by a real
   test (`test_no_secret_resource_tools_exposed`), same CI-wiring caveat as #3.

## Alternatives Considered

- **Expose mutating tools but gate them behind human approval per call** —
  rejected for this agent's scope: the agent's value proposition is
  autonomous triage/RCA, not remediation, and adding an approval workflow for
  actions is a materially larger trust and audit surface than the project
  needs today. Suggested remediation is surfaced in every RCA as text for a
  human to act on, never auto-executed (see [System
  Overview](architecture/system-overview.md#what-it-intentionally-does-not-do)).
- **Rely on IAM alone (grant read-only roles, skip the application-level
  allowlist/blocklist)** — rejected because it leaves a single point of
  failure: a misconfigured IAM binding (human error, a future role change) at
  a production incident's most stressful moment would have no code-level
  backstop. Layering the allowlist, `BLOCKED_ACTIONS`, and a CI regression
  test on top of IAM means no single misconfiguration is enough to introduce
  a mutating capability.
- **Trust the model/prompt to only call read operations** — rejected outright;
  this is exactly the kind of property a prompt cannot guarantee under
  adversarial input or model drift, which is the whole reason for the
  four-layer, code-and-IAM-enforced design above instead.

## Reason

Read-only-ness needed to be provable, not just intended — a claim like "the
agent can't delete anything" is only trustworthy if it's true at every layer
that could otherwise let a mutation through: the tool the model is offered,
the code executing the call, the underlying client library, and the IAM
grant backing all of it. Four independent, differently-mechanized layers mean
no single bug, misconfiguration, or prompt-injection-style manipulation is
sufficient on its own to produce a real mutating call — see [Security
Operations](governance/security.md#kubernetes-access-is-read-only--the-full-evidence-chain).

## Tradeoffs

- The agent genuinely cannot remediate anything itself — every "next step"
  it proposes is text for a human to execute, which caps its operational
  value at triage/RCA rather than auto-healing. This is an accepted, explicit
  scope boundary, not an oversight.
- Kubernetes RBAC scoping on the live GKE Remote MCP path is IAM-only — no
  Terraform-managed Role/ClusterRole/RoleBinding was found for it (GKE Remote
  MCP handles Kubernetes-layer authorization internally); a `view`
  ClusterRole exists only for the separate, non-production Connect Gateway
  prototype. This is a real, currently-unverified gap for that specific path,
  not a contradiction of the read-only guarantee (the IAM-level and
  application-level layers above still hold regardless).
- Proving read-only-ness this thoroughly costs ongoing maintenance: a new
  tool or a new `mcp/tools/*.py` function has to keep passing
  `test_no_mutation.py`, which is a real constraint on future tool additions,
  by design.

## Related ADRs

- [ADR-004: MCP as the tool-access protocol](ADR-004-mcp-tool-access-protocol.md)
- [ADR-002: Agent Identity and Agent Gateway binding](ADR-002-agent-identity-and-gateway.md)
