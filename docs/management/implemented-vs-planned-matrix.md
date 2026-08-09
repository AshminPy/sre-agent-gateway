# Implemented vs Planned — Master Status Matrix

> **Last Verified:** 2026-08-09 — via 6 parallel deep-read audits, each covering a distinct
> subsystem, cross-checked against the actual code, Terraform, and tests (not against other
> documentation). Several checks used live evidence, not just static reading: `gcloud iam roles
> describe` was run against the real predefined roles, `mcp/tests/test_no_mutation.py` was
> executed live (2/2 passed), `terraform test` was run live in `iac/agent` (4/4 passed), and
> `pytest tests/test_multi_cluster_registry.py tests/test_mcp_router.py` was run live (13/13
> passed).
> **Purpose:** one table, so a management or engineering conversation never accidentally treats
> planned work as done, or done work as still planned. Every row has a file:line or a live-command
> citation behind it — this file makes no unevidenced claims.

## Legend

- ✅ **IMPLEMENTED AND VERIFIED** — real code + a real test or live command proves it works.
- 🟡 **IMPLEMENTED BUT NOT FULLY VERIFIED** — real code exists, but no direct test or runtime proof was found.
- 🔵 **DESIGNED / PLANNED** — not built yet, or built only as an unproven prototype.
- ❌ **NOT IMPLEMENTED** — confirmed absent by direct code search, not assumed absent.

## LangGraph nodes

| Node | Status | Evidence |
|---|---|---|
| `context_resolver` | ✅ | 7 dedicated tests (`tests/test_context_resolver.py`), covers every routing tier + safe-stop path |
| `mcp_router` | ✅ | 5 dedicated tests (`tests/test_mcp_router.py`) + reuse in `test_eval_scenario_matrix.py` |
| `rca_builder` | ✅ | 3 dedicated integration tests + runtime proof via the `sre-agent-investigations` Cloud Logging entry |
| `input_normalizer` | 🟡 | Real code, zero dedicated or indirect test coverage found |
| `task_planner` | 🟡 | Real code, zero test file references `task_planner` at all |
| `tool_executor` | 🟡 | Real code, no test calls the node itself (only its downstream `mcp_client.call_tool`) |
| `evidence_extractor` | 🟡 | Real code, no test calls the node as a function |
| `task_evaluator` | 🟡 | Real code; its completeness logic is tested via `agent.confidence.scorer` directly, not through this node |
| `loop_controller` | 🟡 | Real, carefully-commented deterministic logic; zero tests call it directly |

No test in the repo exercises the compiled full graph (`compile_graph()`/`graph.invoke()`) — confirmed by grep, zero hits outside a docstring reference.

## MCP / Agent Gateway / cluster routing

| Capability | Status | Evidence |
|---|---|---|
| GKE Remote MCP | ✅ | Live, primary path, `MCP_REGISTRY["gke_remote_mcp"]` |
| Multi-cluster registry | ✅ | `terraform test` 4/4 pass live; `pytest` 13/13 pass live |
| Cluster-type-based routing (gke vs custom) | ✅ | `mcp_router.py:133-136`, deterministic, covered by `test_eval_scenario_matrix.py` |
| Custom K8s MCP (Cloud Run) | ❌ | `enable_custom_mcp` defaults `false`; no Load Balancer/Serverless NEG anywhere in `iac/` (grep confirmed) |
| Dynamic MCP discovery / `tools/list` | ❌ | Agent uses a static compile-time allowlist (`GKE_REMOTE_TOOLS`/`CUSTOM_K8S_TOOLS` frozensets); no runtime `tools/list` call in the agent's runtime path |
| Connect Gateway | 🔵 | Proven manually against a local `kind` cluster only; no `google_gke_hub_membership` Terraform resource anywhere |
| Cross-project IAM for additional clusters | 🔵 | `iac/gke-access/providers.tf:6` hardwires a single `project_b_id` (scalar, not a list) — known, documented gap |

## Confidence / evidence / memory / RCA

| Capability | Status | Evidence |
|---|---|---|
| Investigation Completeness scoring | ✅ | `agent/confidence/scorer.py:32-137`, 7+ passing tests |
| Root Cause Confidence scoring | ✅ | `agent/confidence/scorer.py:140-285`, 9+ passing tests |
| Evidence grounding/citation checking | ✅ | `claim_builder.py:_ground_claim()`, tested in `test_claim_builder.py` |
| Long-term memory write | ✅ | Gate confirmed at `agent/main.py:957`; `_mb_store` at `agent/main.py:749-789` |
| Contradiction detection | 🟡 | Structural check deterministic + verified; the LLM self-report ("semantic") path is explicitly non-adversarial per code comments |
| Confidence calibration measurement | ❌ | `POLICY_VERSION = "1.0.0-uncalibrated"` (`agent/confidence/policy.py:20`); no code computes calibration anywhere |
| Human-approval-before-trusted-memory workflow | ❌ | `sre_feedback`/`validation_status` fields are set (`agent/nodes/rca_builder.py:284-285,434,436`) but never read anywhere (grep-confirmed) |

**Investigation Completeness and Root Cause Confidence are genuinely two separate scores** — disjoint weight sets, computed by two different pure functions, called from two different nodes at two different points in the graph. Neither is blended into the other before `derive_outcome()`.

## Agent Identity / IAM / security

| Capability | Status | Evidence |
|---|---|---|
| Agent Identity (no static creds) | ✅ | No `google_service_account_key` resource anywhere in `iac/`; `identity_type = "AGENT_IDENTITY"` set (`iac/agent/agent_engine.tf:98`) |
| GKE read-only enforcement | ✅ | **Proven at 3 independent layers, live-verified**: (1) real `roles/container.viewer` permission set (queried via `gcloud iam roles describe`) contains zero create/patch/delete/update permissions; (2) all 33 tool names (`GKE_REMOTE_TOOLS` + `CUSTOM_K8S_TOOLS`) are `list_*`/`get_*`/`describe_*` only; (3) `mcp/tests/test_no_mutation.py` ran live, 2/2 passed, blocking any future mutating-call-pattern regression |
| Least-privilege IAM overall | 🟡 | Bindings are genuinely narrow (no `editor`/`owner`/project-wide roles found), but `docs/least-privilege-iam.md` was missing one live role until fixed today (2026-08-09) |
| Kubernetes RBAC scoping | ❌ (live path) / 🔵 (prototype) | Zero Terraform-managed Role/ClusterRole/RoleBinding for the live GKE Remote MCP path; a `view` ClusterRole exists only for the separate, non-production Connect Gateway prototype |

## Observability / CI-CD / deployment

| Capability | Status | Evidence |
|---|---|---|
| Structured logging | ✅ | 4 named loggers + 3 stdout event types confirmed across `rca_builder.py`, `tool_executor.py`, `mcp_router.py`, `gcs_client.py`, `main.py` |
| Distributed tracing | ✅ | `agent/otel.py` — `get_tracer()`, `trace_node()`, fail-open design confirmed |
| Log-based metrics | ✅ | 11/11 counted directly in `iac/agent/monitoring.tf`; double-counting caveat structurally confirmed (not yet confirmed against a live Cloud Logging count) |
| Alert policies | ✅ | 11/11 counted directly in `iac/agent/monitoring.tf` |
| CI Terraform native tests | ✅ | `terraform test` step added to `terraform-plan.yml` 2026-08-09 (PR #52), ran live 4/4 pass, now documented (was undocumented until today) |
| CI Terraform validate/plan | ✅ | Confirmed real steps in `terraform-plan.yml` |
| CD (automatic deploy to Agent Engine) | ✅ | Real `terraform apply` on push to `main` updates the live `google_vertex_ai_reasoning_engine.sre_agent`, followed by gateway re-attach and a real smoke test |
| Rollback procedure | 🟡 | Real, documented git/Terraform procedure exists; no evidence it has ever actually been executed |
| Disaster-recovery drill | ❌ | No full-DR drill has been run — doc's own words: "true in principle, unverified in practice" |

## Evaluation / accuracy

| Capability | Status | Evidence |
|---|---|---|
| Trajectory/keyword eval scoring | ✅ | `score_case()` (`agent/eval/run_eval.py:73-133`); corrected 2026-08-09 baseline confirms 12/14 correct-tool trajectory recall on a real live rerun |
| Golden dataset (14 cases) | ✅ | All 14 cases verified current post-2026-08-09 fix; guarded by `tests/test_golden_cases_tool_names.py` |
| Regression guard: recursion_limit consistency | ✅ | `tests/test_recursion_limit_consistency.py`, added 2026-08-09, mutation-tested by an independent reviewer before merge |
| LLM-judge root-cause scoring | ❌ | `EvalTask` exists but submits only the same trajectory-matching metrics — no generative rubric judge anywhere in the codebase |
| Confidence calibration measurement | ❌ | Same evidence as above — no code measures predicted-vs-actual accuracy |
| Replay/regression testing on prompt/model changes | 🟡 | The golden-case suite itself is a regression test, but there is no automated CI gate blocking a merge on an eval-score regression |
| Living evaluation dataset (growing beyond static cases) | 🔵 | `sre_feedback`/`validation_status` fields exist specifically to support this; the loop that would consume them isn't built |

## Carried forward from the 2026-08-08 knowledge base (not re-audited today, still believed current — see [Risks and Limitations](risks-and-limitations.md) for the living version)

| Capability | Status | Note |
|---|---|---|
| PagerDuty integration | ❌ | No PagerDuty integration exists in code |
| Model Armor content-safety inspection | ❌ (live) / 🔵 (template) | Terraform templates exist; `CONTENT_AUTHZ` chain confirmed not wired at the gateway (`Error 400: unsupported Google API for AuthzExtension`) |
| VPC-SC | 🔵 | Not implemented per 2026-08-08 audit; not re-checked today |
| PSC (Private Service Connect) | 🔵 | Not implemented per 2026-08-08 audit; not re-checked today |
| Production networking hardening | 🔵 | See [Risks and Limitations](risks-and-limitations.md) |

---

**How to keep this table honest going forward:** update the relevant row immediately when a
capability's real status changes — don't let this drift the way `evaluation.md`,
`deployment.md`, and 3 other pages drifted between 2026-08-08 and 2026-08-09 (see
[Documentation Validation Report](../DOCUMENTATION-VALIDATION-REPORT.md) for that specific
gap list and what was fixed).

**Related pages:** [Risks and Limitations](risks-and-limitations.md) · [Documentation Validation Report](../DOCUMENTATION-VALIDATION-REPORT.md) · [Executive FAQ](executive-faq.md)
