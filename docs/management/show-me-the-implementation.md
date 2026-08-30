# Show Me The Implementation — Management Q&A

> **Purpose:** a management/engineering conversation frequently needs to go from "the agent does
> X" to "prove it" in under a minute. Each question below gives: 1) simple answer, 2) how it
> works, 3) exact source location, 4) a real code snippet, 5) Terraform/config, 6) the test that
> proves it (or an honest "no direct test" if that's the truth), 7) a runtime check you can run
> against the live system.
>
> **Status labels** (✅/🟡/🔵/❌) match [Implemented vs Planned — Master Status
> Matrix](implemented-vs-planned-matrix.md) exactly — this document does not invent its own
> labels. Where this page adds detail beyond the matrix, it cites the matrix row it agrees with.
>
> **Last verified:** 2026-08-09, against the live repository (`~/projects/sre-agent-gateway`, GCP
> project `sreagent-t2-demo`). Every file:line citation was re-opened and confirmed at write time.

---

## 1. "Show me how cluster routing works."

**Simple answer:** the agent never guesses which cluster to investigate. It runs a fixed,
5-step priority chain — exact ID, then case-insensitive ID, then alias, then
project/environment/namespace uniqueness, then "stop and ask a human" — against a cluster
registry that Terraform builds and uploads to GCS. ✅ (matrix: "Cluster-type-based routing").

**How it works — the complete chain:**

```
User/alert query (cluster hint, e.g. "sre-test-cluster")
        │
        ▼
agent/nodes/context_resolver.py  ── reads resolved_context (cluster_hint, cluster_guess,
        │                            namespace/project/environment hints)
        ▼
agent/mcp_client.py:resolve_cluster_routing()   (~line 648-777)
        │  Tier 1 exact_id → Tier 2 verified_alert_metadata → Tier 3 approved_alias →
        │  Tier 4 project_env_namespace → Tier 5 unresolved (safe-stop)
        ▼
agent/mcp_client.py:_get_cluster_registry()   ← loads clusters.json from GCS (5-min TTL cache)
        ▼
agent/nodes/mcp_router.py   ── reads cluster_info["cluster_type"] to pick gke_remote_mcp vs
                                k8s_mcp (deterministic, no LLM call in this phase)
```

**Exact source code location:** `agent/mcp_client.py`, function `resolve_cluster_routing()`,
lines ~648-777. Called from `agent/nodes/context_resolver.py` (whole file, 134 lines). The MCP
selection that follows a resolved cluster is in `agent/nodes/mcp_router.py`, lines ~110-136.

**Code snippet** (the priority chain's structure — `agent/mcp_client.py` ~656-671):

```python
def resolve_cluster_routing(
    cluster_hint: str = "",
    cluster_guess: str = "",
    namespace_hint: str = "",
    project_hint: str = "",
    environment_hint: str = "",
) -> Dict[str, Any]:
    """
    Priority order:
      1. exact_id                — verified hint matches a canonical cluster id exactly.
      2. verified_alert_metadata — verified hint matches a canonical id case-insensitively.
      3. approved_alias          — hint (or, absent a hint, the unverified LLM guess)
                                    matches a registered alias.
      4. project_env_namespace   — project/environment/namespace hints uniquely identify
                                    exactly one enabled cluster. Ambiguous (>1 match) = unresolved.
      5. unresolved              — none of the above. Caller must safe-stop.
    """
```

And how a safe-stop propagates — `agent/nodes/context_resolver.py` ~57-68:

```python
    if not routing["resolved"]:
        message = f"context_resolver: cluster could not be safely determined — {routing['reason']}"
        log.error(message)
        # Safe stop — no guessed cluster, no cluster_name set, investigation marked failed.
        # graph.py's conditional edge after context_resolver routes this straight to
        # rca_builder instead of task_planner/mcp_router/tool_executor.
        return {
            "errors": [message],
            "investigation": {"status": "failed", "loop_exit_reason": "cluster_unresolved"},
            ...
```

**Terraform/config:** the registry itself is built in `iac/agent/main.tf` (~lines 32-68) —
`local.all_clusters = merge(local.default_cluster, var.additional_clusters)`, rendered via
`jsonencode(...)` into `clusters_json`, uploaded to the `cluster_config` GCS bucket
(`iac/agent/buckets.tf`). The extension point is `iac/agent/variables.tf`'s
`additional_clusters` variable (~lines 41-67), a `map(object({...}))` with a Terraform
`validation` block that rejects a name collision with the default cluster.

**Test proving it:** ✅ 7 dedicated tests in `tests/test_context_resolver.py`, covering every
tier of the priority chain plus the safe-stop path (per the matrix's LangGraph-nodes table).
Multi-cluster registry mechanics are covered separately by `tests/test_multi_cluster_registry.py`
(8 tests, live-passing per the matrix, PR #52). Cluster-type routing (gke vs custom) is covered
by `test_eval_scenario_matrix.py` per the matrix's MCP-routing table.

**Runtime evidence:** query Cloud Logging for the routing decision on a real run —

```bash
gcloud logging read '
  resource.type="aiplatform.googleapis.com/ReasoningEngine"
  jsonPayload.message=~"context_resolver cluster="
' --project=sreagent-t2-demo --freshness=1d --limit=20 --format=json
```

`context_resolver.py`'s own log line (~101-109) is `"context_resolver cluster=%s method=%s
project=%s region=%s primary=%s fallback=%s"` — the `method` field is one of `exact_id`,
`verified_alert_metadata`, `approved_alias`, `project_env_namespace`, or absent (safe-stop
logged instead as an `ERROR` with `context_resolver: cluster could not be safely determined`).

---

## 2. "Show me how confidence works."

**Simple answer:** there are **two separate scores**, not one. "Investigation Completeness"
asks "did we look in the right places?" "Root Cause Confidence" asks "is the answer we found
actually well-supported?" Neither is blended into the other. Both are 100% deterministic
Python — the LLM proposes evidence/claims, it never sets the number. ✅ (matrix: both scoring
functions, "genuinely two separate scores... Neither is blended into the other before
`derive_outcome()`").

**How it works:**

- **Investigation Completeness** — `score_investigation_completeness()`,
  `agent/confidence/scorer.py` lines ~32-137. Six weighted components: `routing_confirmed`,
  `identity_confirmed`, `required_evidence_coverage`, `freshness`, `tool_success`,
  `iteration_budget`. Bands: `complete` (≥0.85) / `complete_with_gaps` (≥0.60) / `incomplete`.
  Called from `agent/nodes/task_evaluator.py`.
- **Root Cause Confidence** — `score_root_cause_confidence()`, `agent/confidence/scorer.py`
  lines ~140-285. Five weighted components (`direct_support`, `independent_corroboration`,
  `resource_identity_match`, `time_correlation`, `claim_grounding`) **minus** three penalties
  (contradiction, unresolved alternative hypothesis, missing required evidence domain). Called
  from `agent/nodes/rca_builder.py`.
- **Evidence coverage** is domain-based, not count-based — `required_evidence_coverage` compares
  the *set* of evidence domains collected (via `classify_tool()`,
  `agent/confidence/evidence_domains.py`) against the domains a given `incident_type` requires;
  duplicate evidence in the same domain does not inflate the score.
- **Contradiction handling** — `agent/confidence/claim_builder.py`'s `detect_contradictions()`
  (~143-197), two sources: (1) a **deterministic structural check** — supporting evidence tagged
  with a cluster different from the resolved investigation cluster; (2) the **LLM's own
  self-reported** `contradicting_evidence_ids` per claim. Source (2) is explicitly documented as
  non-adversarial (the model flags its own conflicts, nothing independently audits it) — matrix
  marks this 🟡, and this page agrees, not re-derives a different label.
- **Failed-tool impact** — `tool_success` in the completeness score directly penalizes failed
  tool calls (`(total_calls - failed_calls) / total_calls`, 0 calls attempted = hard 0). On the
  confidence side, `missing_evidence_penalty` and a **hard cap**
  (`max_score_missing_critical_evidence`) apply if a required evidence domain was never covered
  — a high weighted average cannot buy past that cap.

**Exact source code location:** `agent/confidence/scorer.py` (both functions),
`agent/confidence/claim_builder.py` (`detect_contradictions()`),
`agent/confidence/policy.py` (weights, thresholds, `POLICY_VERSION`).

**Code snippet** (the "two separate scores, never blended" mechanic —
`agent/confidence/scorer.py` ~118-137 and ~257-278):

```python
    # Investigation Completeness — its own weighted sum, its own bands
    score = sum(components[k] * w for k, w in policy.completeness_weights.items())
    score = round(min(1.0, max(0.0, score)), 4)
    if score >= 0.85:
        band = "complete"
    elif score >= 0.60:
        band = "complete_with_gaps"
    else:
        band = "incomplete"
```
```python
    # Root Cause Confidence — separate weighted sum, separate penalties, separate hard caps
    score = base_score - contradiction_penalty - alt_penalty - missing_penalty
    if missing_required:
        score = min(score, policy.max_score_missing_critical_evidence)
    if contradictions:
        score = min(score, policy.max_score_unresolved_contradiction)
    if active_alt:
        score = min(score, policy.max_score_unresolved_hypothesis)
```

**Terraform/config:** not Terraform-driven — this is pure application policy. The config surface
is `agent/confidence/policy.py`, notably `POLICY_VERSION = "1.0.0-uncalibrated"` (line ~20) —
these weights are reasoned defaults, not measured/calibrated against historical outcomes (matrix:
"Confidence calibration measurement" = ❌). The legacy 3-value `confidence_band` vocabulary
(`auto`/`review`/`escalate`) that `iac/agent/monitoring.tf`'s alert filters key on is derived from
the outcome in `confidence_band_from_scores()` (`scorer.py` ~319-329) — do not change those 3
string values without also updating `monitoring.tf`.

**Test proving it:** ✅ `tests/test_scorer.py` — 24 test functions total, split across both
scoring functions (matrix: "7+ passing tests" for completeness, "9+ passing tests" for root
cause confidence — both hold, live counted). Contradiction detection is 🟡 for the structural
half (deterministic, tested) and explicitly unverifiable-by-test for the LLM self-report half
(it's a self-report by design, not something a unit test can independently confirm is honest).

**Runtime evidence:**

```bash
gcloud logging read '
  resource.type="aiplatform.googleapis.com/ReasoningEngine"
  jsonPayload.confidence_band != ""
' --project=sreagent-t2-demo --freshness=7d --limit=20 --format=json \
  | jq '.[] | {run_id: .jsonPayload.run_id, confidence_band: .jsonPayload.confidence_band}'
```

`iac/agent/monitoring.tf` line ~45 defines the exact production alert filter used for this:
`jsonPayload.confidence_band="escalate"`.

---

## 3. "Show me why the investigation stopped."

**Simple answer:** one function, `loop_controller`, decides continue-or-stop every iteration
using a fixed **priority-ordered if/elif chain** — not a scoring function, a real ordered list
of conditions checked top to bottom. The first one that's true wins. 🟡 (matrix: real code,
carefully commented, zero direct tests call it).

**How it works — the exact priority order** (`agent/nodes/loop_controller.py` ~112-153):

1. `confidence_sufficient` — evaluator said evidence is enough. Always wins, checked first.
2. `timeout` — wall-clock exceeded `max_duration_seconds`. Always exits regardless of other signals.
3. `token_budget_exceeded` — `MAX_TOKENS_PER_RUN` (env var, default 100,000) hit.
4. `max_iterations` — hard step cap (`investigation.max_steps`) reached.
5. `tool_signaled_done` — router returned `tool="done"` **and** `min_steps` already satisfied.
6. *(tool said done but `min_steps` not met → explicitly does NOT exit, keeps running)*
7. `consecutive_tool_failures` — last 2 tool calls both failed **and** `min_steps` met.
8. `oscillation_detected` — A→B→A→B pattern in the last 4 successful tool calls.
9. `stuck_detected` — same tool + same args called successfully twice in a row.
10. `zero_new_facts` — last 2 consecutive successful tool calls both returned empty `key_facts`.

**Exact source code location:** `agent/nodes/loop_controller.py`, whole file (179 lines) — the
helper predicates (`_is_stuck`, `_is_oscillating`, `_is_timed_out`,
`_consecutive_tool_failures`, `_token_budget_exceeded`, `_zero_new_facts`) at lines ~23-93, the
ordered decision at ~112-153.

**Code snippet** (`agent/nodes/loop_controller.py` ~112-153):

```python
    # ── Exit decision — order matters ─────────────────────────────
    exit_reason = None

    if enough:
        exit_reason = "confidence_sufficient"
    elif timed_out:
        exit_reason = "timeout"
    elif over_budget:
        exit_reason = "token_budget_exceeded"
    elif step >= max_steps:
        exit_reason = "max_iterations"
    elif tool_done and step >= min_steps:
        exit_reason = "tool_signaled_done"
    elif tool_done and step < min_steps:
        exit_reason = None  # keep running
    elif consec_failures and step >= min_steps:
        exit_reason = "consecutive_tool_failures"
    elif oscillating:
        exit_reason = "oscillation_detected"
    elif stuck:
        exit_reason = "stuck_detected"
    elif zero_facts:
        exit_reason = "zero_new_facts"
```

**Terraform/config:** `MAX_TOKENS_PER_RUN` is an environment variable read at import time
(`loop_controller.py` ~13-20, default 100000, parsed defensively so a bad value doesn't crash
agent startup); `max_duration_seconds` and `max_steps`/`min_steps` are per-investigation fields
set in `agent/state.py`'s initial state, not Terraform-managed.

**Test proving it:** 🟡 **no direct test exists.** Per the matrix, zero tests call
`loop_controller` directly — this is real, carefully-commented deterministic logic with no unit
test coverage today. Do not claim otherwise.

**Runtime evidence:**

```bash
gcloud logging read '
  resource.type="aiplatform.googleapis.com/ReasoningEngine"
  jsonPayload.loop_exit_reason != ""
' --project=sreagent-t2-demo --freshness=7d --limit=20 --format=json \
  | jq '.[] | {run_id: .jsonPayload.run_id, loop_exit_reason: .jsonPayload.loop_exit_reason}'
```

`iac/agent/monitoring.tf` line ~94 already filters production logs on this exact field:
`jsonPayload.loop_exit_reason != ""`.

---

## 4. "Show me how the agent selected this MCP."

**Simple answer:** MCP source selection is a **deterministic, non-LLM, two-value decision** made
in Phase 1 of `mcp_router` — GKE-type clusters route to `gke_remote_mcp`, everything else routes
to `k8s_mcp`. No tokens are spent making this choice; the LLM only picks the *tool* inside the
already-chosen MCP (Phase 2). ✅ (matrix: "Cluster-type-based routing... deterministic").

**How it works:** `agent/nodes/mcp_router.py` reads `cluster_info["cluster_type"]` (from the
same registry `context_resolver` already resolved a cluster against) and maps it directly —
`"gke"` → `gke_remote_mcp`, anything else → `k8s_mcp`, with a registry-membership sanity check.
If the resolved cluster has no registry entry or is disabled, the router refuses to guess and
safe-stops instead of defaulting to `gke_remote_mcp`.

**Exact source code location:** `agent/nodes/mcp_router.py`, lines ~110-136 (Phase 1, MCP
selection); lines ~142-163 (Phase 2, tool selection via `llm_json` against
`MCP_ROUTER_PHASE2_SYSTEM`/`MCP_ROUTER_PHASE2_USER` from `agent/prompts.py`).

**Code snippet** (`agent/nodes/mcp_router.py` ~110-136):

```python
    # ── Phase 1: deterministic MCP source selection (no LLM tokens) ─
    # GKE clusters → gke_remote_mcp first. On-prem → k8s_mcp first.
    cluster_name = ctx.get("cluster_name", "")
    cluster_info = _get_cluster_registry().get(cluster_name, {}) if cluster_name else {}

    if not cluster_info or not cluster_info.get("enabled", True):
        reason = (
            f"No registry entry for cluster '{cluster_name}'" if not cluster_info
            else f"Cluster '{cluster_name}' is registered but disabled"
        )
        log.error("mcp_router SAFE-STOP: %s — refusing to guess an MCP destination", reason)
        _log_routing_failure(state["run_id"], cluster_name, reason)
        return {
            "current_action": {"tool": "done", "arguments": {}, "mcp_source": "none"},
            "errors": [f"mcp_router: {reason} — cannot route safely, investigation stopped."],
        }

    cluster_type = cluster_info.get("cluster_type", "gke")
    selected_mcp = "gke_remote_mcp" if cluster_type == "gke" else "k8s_mcp"
```

**Terraform/config:** the `cluster_type` field per cluster comes from the same
`clusters_json`/`all_clusters` mechanism as Q1/Q10 (`iac/agent/main.tf`, `type` field, defaulting
to `"gke"` per `iac/agent/variables.tf`'s `additional_clusters` object schema). The two MCP
sources themselves are declared in `agent/mcp_client.py`'s `MCP_REGISTRY` dict (~98-113), not
Terraform — Terraform's role is only in whether `k8s_mcp` is actually reachable
(`iac/agent/cloudrun_mcp.tf`, gated behind `var.enable_custom_mcp`, default `false`).

**Test proving it:** ✅ `tests/test_mcp_router.py` — 5 dedicated tests (matrix). Also exercised
indirectly by `test_eval_scenario_matrix.py`.

**Runtime evidence:**

```bash
gcloud logging read '
  resource.type="aiplatform.googleapis.com/ReasoningEngine"
  jsonPayload.message=~"mcp_router → source="
' --project=sreagent-t2-demo --freshness=1d --limit=20 --format=json
```

`mcp_router.py`'s own log line (~223-226): `"mcp_router → source=%s tool=%s args=%s
tokens=%d evidence_count=%d"`.

---

## 5. "Show me how the agent selected this Kubernetes cluster."

**This is the one to get exactly right — same underlying mechanism as Q1, restated from "which
cluster did THIS run pick and why" angle, since that's the question that actually gets asked in
an incident retro.**

**Simple answer:** every resolved investigation carries a `cluster_routing_method` and
`cluster_routing_reason` string, set once by `context_resolver` and never overwritten, that says
in plain terms which of the 5 tiers matched and why. That pair is written into the RCA output and
into Cloud Logging — you can always answer "why this cluster" for any specific run without
guessing. ✅ (matrix: "Cluster-type-based routing").

**How it works:** identical chain to Q1 —
`resolve_cluster_routing()` (`agent/mcp_client.py` ~648-777) returns
`{resolved, cluster_name, method, reason}`. `context_resolver.py` (~101-132) copies `method` and
`reason` verbatim into `resolved_context["cluster_routing_method"]` /
`["cluster_routing_reason"]`, which flows downstream into `rca_builder.py`'s
`investigation_context` for the final RCA record — so the "why this cluster" answer is
attached to the *output*, not something you'd have to reconstruct from logs after the fact.

**Exact source code location:** `agent/mcp_client.py:resolve_cluster_routing()` (~648-777);
propagation in `agent/nodes/context_resolver.py` (~94-132); surfaced in RCA output via
`agent/nodes/rca_builder.py`'s `investigation_context` (per that node's design, referenced in
`context_resolver.py`'s own module docstring, line ~23-24).

**Code snippet** (`agent/nodes/context_resolver.py` ~101-119 — the field actually carried
forward into the run's record):

```python
    log.info(
        "context_resolver cluster=%s method=%s project=%s region=%s primary=%s fallback=%s",
        resolved_cluster, routing["method"], project_id, region, primary, fallback,
    )

    return {
        "resolved_context": {
            **ctx,
            "cluster_name": resolved_cluster,
            "cluster_region": region,
            "project_id": project_id,
            "cluster_explicitly_provided": True,
            "cluster_routing_method": routing["method"],
            "cluster_routing_reason": routing["reason"],
            "primary_mcp_source": primary,
            "mcp_source": primary,
            "mcp_fallback": fallback,
            ...
```

**Terraform/config:** same as Q1 — `iac/agent/variables.tf`'s `additional_clusters`,
`iac/agent/main.tf`'s `clusters_json` render. Nothing cluster-specific is hardcoded in Terraform
beyond the one `default_cluster` block (`var.gke_cluster_name`/`var.project_b_id`); every other
cluster is data, not code.

**Test proving it:** ✅ `tests/test_context_resolver.py` (7 tests) — this is the same evidence
as Q1, since it's the same function; listed again here because this question gets asked
independently in practice.

**Runtime evidence** — pull the routing decision for one specific `run_id`:

```bash
gcloud logging read '
  resource.type="aiplatform.googleapis.com/ReasoningEngine"
  jsonPayload.run_id="<RUN_ID>"
  jsonPayload.message=~"context_resolver cluster="
' --project=sreagent-t2-demo --freshness=7d --format=json
```

Or, for the permanent record on a completed investigation, the `sre-agent-investigations` log
(written by `agent/nodes/rca_builder.py:_write_observability_log()`, ~133-141) carries the same
`cluster_routing_method`/`cluster_routing_reason` fields attached to that run's full RCA record.

---

## 6. "Show me what permissions the agent has."

**Simple answer:** the runtime identity gets 9 project-level roles plus a handful of
resource-scoped bucket/Cloud-Run roles — no `editor`, no `owner`, no project-wide storage or
compute role. This is real, applied Terraform, not a design doc. 🟡 overall (matrix:
"Least-privilege IAM overall" — narrow bindings confirmed, but the human-maintained
`docs/least-privilege-iam.md` audit had a documented sync gap, fixed 2026-08-09).

**How it works:** `iac/agent/iam.tf` grants roles to `local.agent_identity_member` — the Agent
Identity SPIFFE principal (see Q7/Q3's identity note), not a service account. Roles split into
three tiers: (1) project-level roles used by every run (inference, memory, logging, tracing,
metrics, registry read), (2) bucket-level roles scoped to exactly 3 buckets (evidence, eval,
cluster-config) rather than project-wide storage access, (3) a conditional `roles/run.invoker`
on the custom MCP Cloud Run service, only granted when `var.enable_custom_mcp` is true.

**Exact source code location:** `iac/agent/iam.tf`, whole file (127 lines). Justification
narrative for every role: `docs/least-privilege-iam.md`.

**Code snippet** (`iac/agent/iam.tf` ~9-31, the full project-level role list):

```hcl
locals {
  runtime_project_roles = [
    "roles/aiplatform.expressUser",            # inference / sessions / memory (Agent Identity baseline)
    "roles/aiplatform.user",                   # Memory Bank generate/retrieve
    "roles/aiplatform.agentDefaultAccess",     # Agent Runtime default access
    "roles/serviceusage.serviceUsageConsumer", # quota / API access
    "roles/browser",                           # resourcemanager.projects.get (Agent Identity prereq)
    "roles/agentregistry.viewer",              # read Agent Registry (mcpServers discovery)
    "roles/logging.logWriter",                 # structured run logs
    "roles/cloudtrace.agent",                  # OpenTelemetry traces
    "roles/monitoring.metricWriter",           # metrics
  ]
}

resource "google_project_iam_member" "runtime_identity" {
  for_each = toset(local.runtime_project_roles)
  project  = var.project_a_id
  role     = each.value
  member   = local.agent_identity_member
}
```

Bucket-level (not project-wide) roles, same file (~47-76): `roles/storage.objectCreator` +
`roles/storage.objectViewer` on the `evidence` and `eval` buckets separately, and
`roles/storage.objectViewer`-only on `cluster_config` — the agent can read `clusters.json` but
cannot write it, matching the "Terraform is the sole source of truth for clusters.json" design
from Q1/Q10.

**GKE-specific read access** (the permission that actually touches Kubernetes) is
`roles/container.viewer` on Project B, documented in `docs/least-privilege-iam.md` line ~46 and
also granted to the custom MCP's own service identity (`docs/least-privilege-iam.md` line ~102)
— this is the permission proven read-only in Q7 below.

**Test proving it:** no automated test asserts the IAM bindings match an expected minimal set —
this is Terraform config, proven by `terraform plan`/`apply` succeeding and by direct
`gcloud` inspection (see below), not by a unit test.

**Runtime evidence** — list the actual bindings on the live project:

```bash
gcloud projects get-iam-policy sreagent-t2-demo \
  --flatten="bindings[].members" \
  --filter="bindings.members:principal://agents.global" \
  --format="table(bindings.role)"
```

This should return exactly the 9 roles in `runtime_project_roles` above (plus the conditional
Model Armor role only when the gateway is off) — no `roles/editor`, `roles/owner`, or
project-wide `roles/storage.admin`.

---

## 7. "Show me that the agent is read-only."

**Simple answer:** this is proven at **3 independent layers**, live-verified — not asserted from
a design doc. ✅ (matrix: "GKE read-only enforcement", same row this section summarizes rather
than re-derives, per the task's own instruction not to redo that audit).

**The important distinction:** "we intend read-only" (a design goal, weak claim) is different
from "permissions technically prevent writes" (a proven property, strong claim). The evidence
below is the strong claim — even if every prompt, tool-name check, and code review failed
simultaneously, the underlying IAM role itself contains no write verb, so a mutating call would
be rejected by GCP before it ever reached the cluster.

**Layer 1 — the real IAM permission set contains zero write verbs.** The agent's only GKE-facing
role is `roles/container.viewer` (Q6). A live `gcloud iam roles describe roles/container.viewer`
run during the 2026-08-09 audit confirmed this predefined role contains no `create`, `patch`,
`delete`, or `update` permissions — this is a GCP-defined role, not a custom one the agent's own
Terraform could accidentally broaden.

**Layer 2 — every exposed tool name is read-shaped.** All 33 tool names across both MCP sources
(`GKE_REMOTE_TOOLS` — 6 tools, `CUSTOM_K8S_TOOLS` — 27 tools, both in `agent/mcp_client.py`) are
`list_*` / `get_*` / `describe_*` only — confirmed by direct enumeration, not sampling.

**Layer 3 — a real, passing regression test guards against any future mutating call pattern.**
`mcp/tests/test_no_mutation.py` walks every `.py` file under `mcp/` (excluding tests) and fails
if any line matches a real `kubernetes-client` mutating method signature —
`create_namespaced_*`, `delete_namespaced_*`, `patch_*`, `replace_*`, `.exec(`, or
`port_forward`. Confirmed passing locally (`2 passed`). **Not yet wired into CI** — checked
directly: neither `.github/workflows/terraform-plan.yml` nor `terraform-apply.yml` runs
`pytest` at all today, only `terraform validate`/`terraform test`/`terraform plan`/`apply`. So
this test protects against regression when someone runs it by hand, but a merge is not
currently blocked if it fails — a real gap, not a nitpick, since it's the strongest single
piece of read-only proof this repo has. A second test in the same file
(`test_no_secret_resource_tools_exposed`) independently confirms no
`get_secret`/`list_secrets`/`describe_secret` tool exists at all — Secrets are excluded from
the tool surface entirely, on top of RBAC already excluding them. Same CI-wiring caveat applies.

**Exact source code location:** `mcp/tests/test_no_mutation.py`, whole file (64 lines);
`agent/mcp_client.py` lines ~30-37 (`GKE_REMOTE_TOOLS`) and ~40+ (`CUSTOM_K8S_TOOLS`).

**Code snippet** (`mcp/tests/test_no_mutation.py` ~13-30 — the actual mutation-pattern
denylist, not a paraphrase):

```python
_MUTATING_PATTERNS = [
    r"\bcreate_namespaced_\w+\(",
    r"\bcreate_node\(",
    r"\bcreate_persistent_volume\(",
    r"\bdelete_namespaced_\w+\(",
    r"\bdelete_node\(",
    r"\bdelete_persistent_volume\(",
    r"\bdelete_collection_\w+\(",
    r"\bpatch_namespaced_\w+\(",
    r"\bpatch_node\w*\(",
    r"\breplace_namespaced_\w+\(",
    r"\breplace_node\w*\(",
    r"\breplace_persistent_volume\w*\(",
    r"\bconnect_\w*(exec|proxy|attach)\w*\(",
    r"\.exec\(",
    r"port_forward",
]
```

**Terraform/config:** `roles/container.viewer` grant lives in `iac/gke-access/` (cross-project
IAM stack), justified per-role in `docs/least-privilege-iam.md`. No Kubernetes-side RBAC
(Role/ClusterRole/RoleBinding) is Terraform-managed for the live GKE Remote MCP path — the matrix
flags this as a real, separate gap (❌ live path / 🔵 prototype-only) — read-only is enforced by
the IAM role and the tool-name allowlist, not by cluster-side RBAC, on the path that's actually
live today.

**Test proving it:** ✅ `mcp/tests/test_no_mutation.py`, ran live 2026-08-09, **2/2 passed**.

**Runtime evidence** — confirm the actual bound role has no write verbs, right now:

```bash
gcloud iam roles describe roles/container.viewer --format="value(includedPermissions)" \
  | tr ';' '\n' | grep -E 'create|update|patch|delete'
# Expected: no output — zero matching permissions.
```

---

## 8. "Show me where the logs are."

**Simple answer:** structured JSON logs go to Cloud Logging, filterable by `run_id`, `cluster`,
`confidence_band`, `loop_exit_reason`, and more — plus 4 dedicated named loggers for specific
failure classes, so an alert doesn't need a fragile text grep. ✅ (matrix: "Structured logging").

**How it works:** two log shapes exist. (1) Standard Python `logging` calls (via
`logging.getLogger("sre-agent.<component>")`) go to stdout, which Cloud Run/Agent Engine's
runtime automatically ships to Cloud Logging as `jsonPayload`-bearing entries with fields like
`run_id`, `cluster`, `confidence_band`. (2) Dedicated `google.cloud.logging` client calls write
to 3 specifically-named loggers for events that need their own log-based metric without relying
on a generic text match.

**Exact source code location — the 4 named loggers:**
- `sre-agent.server` (`agent/main.py`), `sre-agent.rca_builder`, `sre-agent.mcp_router`,
  `sre-agent.tool_executor`, `sre-agent.gcs`, `sre-agent.context_resolver`,
  `sre-agent.graph` — standard component loggers (`logging.getLogger(...)` calls in each file).
- `sre-agent-investigations` — full structured audit event per completed run, written by
  `agent/nodes/rca_builder.py:_write_observability_log()` (~133-220-ish), queryable by `run_id`,
  `cluster`, `confidence_band` per its own docstring.
- `sre-agent-tool-failures` — `agent/nodes/tool_executor.py` ~69, on any failed tool call.
- `sre-agent-routing-failures` — `agent/nodes/mcp_router.py` ~81, on an mcp_router-level safe-stop.
- `sre-agent-evidence-storage-failures` — `agent/gcs_client.py` ~74, on a GCS write that
  exhausted both retry attempts.

**Code snippet** (`agent/nodes/rca_builder.py` ~139-141 — the primary audit-trail logger):

```python
    try:
        from google.cloud import logging as cloud_logging
        client   = cloud_logging.Client(project=os.environ.get("PROJECT_ID"))
        logger_c = client.logger("sre-agent-investigations")
```

**Terraform/config:** `iac/agent/monitoring.tf` — 11 log-based metrics and 11 alert policies
built directly on these `jsonPayload` fields (matrix: both counted 11/11). Sample filters
straight from that file:

```hcl
filter = "jsonPayload.run_id:*"
filter = "jsonPayload.status=\"error\""
filter = "jsonPayload.confidence_band=\"escalate\""
filter = "jsonPayload.estimated_cost_usd > 0"
filter = "jsonPayload.loop_exit_reason != \"\""
```

**Test proving it:** no test asserts log schema — this is operational infrastructure, verified
by the `monitoring.tf` metrics actually matching real field names (confirmed by direct grep
cross-reference between the Terraform filters and the Python `log_struct`/log-line calls that
produce those fields), not by a pytest.

**Runtime evidence — example queries:**

```bash
# All entries for one run, full audit trail
gcloud logging read '
  resource.type="aiplatform.googleapis.com/ReasoningEngine"
  jsonPayload.run_id="<RUN_ID>"
' --project=sreagent-t2-demo --freshness=7d --format=json

# Every tool failure in the last 24h
gcloud logging read 'logName="projects/sreagent-t2-demo/logs/sre-agent-tool-failures"' \
  --project=sreagent-t2-demo --freshness=1d --format=json

# Every routing safe-stop in the last 7 days
gcloud logging read 'logName="projects/sreagent-t2-demo/logs/sre-agent-routing-failures"' \
  --project=sreagent-t2-demo --freshness=7d --format=json
```

---

## 9. "Show me how we measure accuracy."

**Simple answer:** a fixed 14-case golden dataset, scored by deterministic trajectory/keyword
matching against expected tool calls. **There is no judge model and no LLM-as-judge anywhere in
this repo today** — confirmed by direct code search, not assumed absent. ✅ for the trajectory
scoring itself; ❌ for LLM-judge (matrix: both rows).

**How it works:** `agent/eval/golden_cases.py` defines 14 cases, each with an
`expected_trajectory` (ordered tool-call list), `expected_keywords` (must appear in the stated
root cause), and `expected_confidence_min`. `agent/eval/run_eval.py`'s `score_case()` computes
`trajectory_precision`, `trajectory_recall`, `trajectory_in_order_match`, and `keyword_accuracy`
— all pure string/set comparisons, no model call. A case **passes** only if `recall >= 0.5 AND
keyword_accuracy >= 0.5 AND confidence_ok AND outcome_ok AND max_confidence_ok`.

**Exact source code location:** `agent/eval/golden_cases.py` (291 lines, `GOLDEN_CASES` list of
14 dicts); `agent/eval/run_eval.py`, `score_case()` function (~lines 73-133).

**Code snippet** (`agent/eval/run_eval.py` ~98-112 — the actual pass/fail gate, no model in
the loop):

```python
    prec   = trajectory_precision(predicted_tools, expected_traj)
    recall = trajectory_recall(predicted_tools, expected_traj)
    order  = trajectory_in_order_match(predicted_tools, expected_traj)
    kw_acc = keyword_accuracy(root_cause, expected_kw)
    conf_ok = confidence >= case.get("expected_confidence_min", 0.0)

    expected_outcome = case.get("expected_outcome")
    outcome_ok = outcome in expected_outcome if expected_outcome else True

    max_confidence = case.get("max_confidence")
    max_conf_ok = confidence <= max_confidence if max_confidence is not None else True

    passed = (
        recall >= 0.5 and kw_acc >= 0.5 and conf_ok and outcome_ok and max_conf_ok
    )
```

**No LLM-judge — the proof, not just the claim.** `run_eval.py` does reference
`vertexai.preview.evaluation.EvalTask` (~line 226, inside `run_vertex_eval()`, gated behind an
explicit `--vertex-eval` flag and an optional pip extra). But its `metrics=[...]` list
(~256-261) is:

```python
    eval_task = EvalTask(
        dataset=dataset,
        metrics=[
            "trajectory_precision",
            "trajectory_recall",
            "trajectory_in_order_match",
            "trajectory_any_order_match",
        ],
        experiment=exp_name,
    )
```

Same structural trajectory-matching metrics as local scoring — no generative rubric/judge metric
is submitted anywhere in this file. No other file in the repo imports an LLM-judge construct
(grep-confirmed).

**Terraform/config:** not Terraform-managed — `GRAPH_RECURSION_LIMIT = 60` (`agent/graph.py`
line 29) is the one shared constant both `agent/main.py` and `agent/eval/run_eval.py` import, so
the eval harness's step ceiling can never drift from production's again (this exact drift caused
one of the two real bugs fixed 2026-08-09, PR #52/#53).

**Test proving it:** ✅ `tests/test_golden_cases_tool_names.py` guards all 14 cases against
stale/cross-source tool names. ✅ `tests/test_recursion_limit_consistency.py` (added 2026-08-09)
guards the shared-constant fix above from regressing.

**Runtime evidence — the current official baseline** (a real, corrected rerun, not a claim):
**12/14 correct-tool trajectory recall**, established 2026-08-09 after fixing 2 real
evaluation-harness bugs (recursion-limit drift, stale expected tool names). Full detail:
`docs/baselines/tool-scaling-baseline-2026-08-09-corrected.md`. To rerun it yourself:

```bash
cd /Users/ashmin/projects/sre-agent-gateway
python -m agent.eval.run_eval --mode local
```

---

## 10. "Show me how we add another cluster."

**Simple answer:** it's a Terraform variable edit and an `apply` — zero Python code changes.
✅, and the "zero Python changes" half of that claim is independently verified below, not just
asserted.

**Exact Terraform steps:**

1. Add an entry to `var.additional_clusters` (`iac/agent/variables.tf` ~41-67), keyed by the
   new canonical cluster name:
   ```hcl
   additional_clusters = {
     "new-cluster-name" = {
       project = "some-gcp-project"
       region  = "us-central1"
       type    = "gke"          # or "custom" for a non-GKE / on-prem cluster
       aliases = ["nc", "newcluster"]
       environment = "production"
       allowed_namespaces = []   # empty = no namespace restriction
       enabled = true
     }
   }
   ```
2. `terraform plan` in `iac/agent/` — review that only the `clusters.json` GCS object and any
   new IAM bindings (if the cluster is in a new project) change.
3. `terraform apply` — this re-renders `clusters_json` via `jsonencode(local.all_clusters)`
   (`iac/agent/main.tf` ~52-68) and overwrites the GCS object. No redeploy of the agent itself is
   needed — `agent/mcp_client.py`'s `_get_cluster_registry()` re-reads it on a 5-minute TTL
   (`agent/mcp_client.py` line ~209).
4. If the cluster is in a project the runtime identity doesn't already have `roles/container.viewer`
   on, add a grant in the `iac/gke-access/` stack (cross-project IAM — currently hardwired to one
   `project_b_id`, a documented gap for a *second* cross-project cluster, matrix: 🔵).

**Which application Python files do NOT require modification — verified, not assumed:**
`agent/mcp_client.py`'s `_get_cluster_registry()`, `resolve_cluster_routing()`, and `call_tool()`
all read cluster identity **generically** from the registry dict — none of them contain a
cluster name, project ID, or region as a literal. Confirmed directly in `call_tool()`
(`agent/mcp_client.py` ~359-367):

```python
    cluster_info  = _get_cluster_registry().get(cluster_name, {})
    source_config = MCP_REGISTRY.get(mcp_source, {})
    is_gke_remote = (mcp_source == "gke_remote_mcp")

    if is_gke_remote:
        url     = source_config.get("url", "https://container.googleapis.com/mcp/read-only")
        project = cluster_info.get("project", os.environ.get("PROJECT_ID", "your-gcp-project-id"))
        region  = cluster_info.get("region",  os.environ.get("CLUSTER_1_REGION", "us-central1"))
        parent  = _build_parent(project, region, cluster_name)
```

Every value (`project`, `region`, `parent`) comes from the registry dict passed in at call time —
grep across `agent/*.py` and `agent/nodes/*.py` for any literal cluster name (e.g.
`"sre-test-cluster"`) turns up zero hits outside test files and the one `default_cluster` block
in Terraform itself. So `agent/nodes/context_resolver.py`, `agent/nodes/mcp_router.py`,
`agent/nodes/tool_executor.py`, and `agent/mcp_client.py` all need **no code change** to support
a new cluster — this is the concrete evidence behind "the system scales by config, not code."

**Test proving it:** ✅ `tests/test_multi_cluster_registry.py` — 8 tests, live-passing
(matrix), specifically covers the multi-cluster merge/render mechanism this procedure relies on.
A Terraform-native test also covers the `clusters_json` render itself: `terraform test` in
`iac/agent`, 4/4 passing live (matrix).

**Runtime evidence — confirm the new cluster actually landed in the live registry:**

```bash
gsutil cat gs://<CLUSTER_CONFIG_BUCKET>/clusters.json | jq '.clusters[] | select(.name=="new-cluster-name")'
```

---

## 11. "Show me how we add another MCP server."

**Simple answer:** register the new source in `agent/mcp_client.py`'s `MCP_REGISTRY` dict and
give it a tools frozenset — most of the LangGraph pipeline (evidence extraction, tool execution,
loop control, RCA building) needs no change, because they all take `mcp_source` as a runtime
string, not a hardcoded branch. One place *does* need a code change: `mcp_router.py`'s Phase 1
selection logic, since it's currently a hardcoded binary choice (gke vs not-gke). 🟡 — real, but
only 2 sources exist today, so this is a described procedure, not a proven-by-precedent one
(no third source has actually been added).

**What you'd do:**

1. Add an entry to `MCP_REGISTRY` (`agent/mcp_client.py` ~98-113) — `url`, `auth` type,
   `description`, `tools` (a new frozenset of `list_*`/`get_*`/`describe_*` tool names, matching
   the read-only pattern enforced by `mcp/tests/test_no_mutation.py` if the new source is also a
   Kubernetes-facing MCP).
2. Extend `agent/mcp_client.py:call_tool()`'s dispatch (~337 onward) with the new source's
   request-building logic (how it maps generic tool args to that MCP's wire format) — this is
   the one place per-source logic genuinely lives, by design.
3. Update `agent/nodes/mcp_router.py`'s Phase 1 selection (~133-136) — today it's a strict
   `"gke_remote_mcp" if cluster_type == "gke" else "k8s_mcp"` binary; a third source needs this
   logic extended (e.g. keyed off a new `cluster_type` value or an explicit per-cluster
   `mcp_primary` override — `context_resolver.py` already reads `cluster_info.get("mcp_primary",
   ...)`, so the registry schema has a hook for this, but `mcp_router.py`'s Phase 1 doesn't
   consume it yet — a real, small gap to close, not hidden here).

**What stays unchanged — the LangGraph logic that never references a specific MCP name:**
`agent/nodes/tool_executor.py` (whole file) calls `call_tool(mcp_source=mcp_source, ...)`
generically and records `mcp_source` as a plain string field in `tool_history` — it contains
zero conditional branches on which MCP was used:

```python
    result = call_tool(
        mcp_source=mcp_source,
        tool_name=tool_name,
        arguments=args,
        run_id=state["run_id"],
        cluster_name=cluster_name,
    )
```

`agent/nodes/evidence_extractor.py` stores `mcp_source` as one more field on `ev_entry`
(`agent/nodes/evidence_extractor.py` ~131-143) without branching on its value.
`agent/nodes/loop_controller.py`'s exit-reason logic (Q3) never inspects `mcp_source` at all.
`agent/confidence/scorer.py`'s two scoring functions (Q2) classify evidence by *tool name*
(`classify_tool()`), not by which MCP produced it — a new source's tools would need their own
`classify_tool()` mapping in `agent/confidence/evidence_domains.py`, but the scorer functions
themselves need no change.

**Exact source code location:** `agent/mcp_client.py` (`MCP_REGISTRY` ~98-113, `call_tool()`
~337+), `agent/nodes/mcp_router.py` (~110-136 for the part that would need a change).

**Terraform/config:** a genuinely new MCP server (not just a new tool on an existing one) would
need its own Terraform — the existing precedent is `iac/agent/cloudrun_mcp.tf` for `k8s_mcp`
(Cloud Run service, `enable_custom_mcp`-gated) plus its `roles/run.invoker` grant in
`iac/agent/iam.tf` (~79-87).

**Test proving it:** no test exists for "adding a third MCP source" specifically, since only 2
sources exist today — this section is a grounded procedure derived from reading the real
dispatch code, not evidence of a third source having actually been added and tested. Flagging
that distinction explicitly per this document's own grounding rules.

**Runtime evidence:** not applicable until a third source is actually added — there is nothing
live to query yet. The closest available runtime check is confirming today's 2-source registry:

```bash
python -c "from agent.mcp_client import MCP_REGISTRY; print(list(MCP_REGISTRY.keys()))"
# Expected: ['gke_remote_mcp', 'k8s_mcp']
```

---

**Related pages:** [Implemented vs Planned — Master Status Matrix](implemented-vs-planned-matrix.md)
· [Risks and Limitations](risks-and-limitations.md) · [Executive FAQ](executive-faq.md) ·
[Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md)
