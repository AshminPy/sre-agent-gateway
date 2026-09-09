# Implemented vs Planned — Master Status Matrix

> **Last Verified:** partially re-verified 2026-08-20 (see the 2026-08-20 delta section below;
> rows not named there still carry their 2026-08-09 verification date).
> **Original verification:** 2026-08-09 — via 6 parallel deep-read audits, each covering a distinct
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
| Custom K8s MCP (Cloud Run) | ✅ | **Corrected 2026-09-07 — this row previously read 🟡 "built, not deployed," which is now stale.** Live in production: the GitHub repo variable `ENABLE_CUSTOM_MCP=true` has driven every CI apply since 2026-08-07 (`sre-k8s-mcp`, serving 100% of its traffic); ingress was changed to `INGRESS_TRAFFIC_ALL` (the Load Balancer/NEG this row used to say was missing was never the real call path and isn't needed). Live E2E proof (2026-09-04, run `run_20260904_215412_kiny`): a real investigation via Connect Gateway to the `sre-lab` cluster produced a correct, zero-fabrication RCA. See `docs/architecture/mcp-architecture.md` for full detail. |
| Dynamic MCP discovery / `tools/list` | ❌ | Agent uses a static compile-time allowlist (`GKE_REMOTE_TOOLS`/`CUSTOM_K8S_TOOLS` frozensets); no runtime `tools/list` call in the agent's runtime path |
| Connect Gateway | ✅ | **Corrected 2026-09-07 — this row previously read 🔵 "proven manually only," which is now stale.** Live production path: the custom MCP has run dozens of real investigations against the `sre-lab` `kind` cluster via Connect Gateway (2026-09-04 through 2026-09-07). One thing this row got right and still holds: no `google_gke_hub_membership` Terraform resource exists anywhere in `iac/` — fleet membership is referenced by `var.onprem_fleet_membership`, not created by Terraform (deliberate: registration is a manual, authorized-operator action per `iac/agent/variables.tf`). |
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
| Kubernetes RBAC scoping | 🟡 | **Corrected 2026-08-20.** Still zero *Terraform-managed* RBAC — but `k8s/rbac.yaml` now exists in the repo (added by PR #121, 2026-08-12) with a real Role + RoleBinding granting `pods/log` `get`, applied to the live cluster with `kubectl`, out of Terraform's control. The earlier "zero RBAC anywhere" reading is wrong; the accurate gap is that this grant is **not** managed by Terraform |

## Observability / CI-CD / deployment

| Capability | Status | Evidence |
|---|---|---|
| Structured logging | ✅ | 4 named loggers + 3 stdout event types confirmed across `rca_builder.py`, `tool_executor.py`, `mcp_router.py`, `gcs_client.py`, `main.py` |
| Distributed tracing | ✅ collection / 🟡 Console view | `agent/otel.py` — `get_tracer()`, `trace_node()`, fail-open design confirmed. **Trace collection works**: live run `run_20260816_083409_lpak` produced 54 correctly-parented spans in Cloud Trace. **Console view is the open part** — issue #164, see the note below the table |
| Log-based metrics | ✅ | **12/12** counted directly in `iac/agent/monitoring.tf` (re-counted 2026-08-20; was 11). The double-counting caveat is **resolved** — issue #75 closed, 8 metrics scoped to `event_type="sre_agent_run"` |
| Alert policies | ✅ | **12/12** counted directly in `iac/agent/monitoring.tf` (re-counted 2026-08-20; was 11) |
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
| Model Armor content-safety inspection | ✅ floor settings + CONTENT_AUTHZ (request/response) / ❌ response-body for MCP tool responses (permanent platform limitation) | **Corrected 2026-09-07 — the `Error 400` finding below no longer holds.** Floor settings live, `inspect_only = true`, HIGH confidence malicious-URI detection (`iac/agent/model_armor.tf`'s `google_model_armor_floorsetting`, live since 2026-08-25/26). CONTENT_AUTHZ extension wired and live at Agent Gateway (`google_network_services_authz_extension.model_armor`, `iac/agent/agent_gateway.tf`, `enforcement_type = "INSPECT_AND_BLOCK"`, templates `sre_agent_request`/`sre_agent_response`). Google's Streamable HTTP transport never invokes RESPONSE_BODY/MCP-tool-response inspection — a disclosed, permanent Google platform limitation, not a local implementation gap; custom MCP responses are separately covered by the application-level `mcp/response_guard.py` guard. |
| VPC-SC | 🔵 | Not implemented per 2026-08-08 audit; not re-checked today |
| PSC (Private Service Connect) | 🔵 | Not implemented per 2026-08-08 audit; not re-checked today |
| Production networking hardening | 🔵 | See [Risks and Limitations](risks-and-limitations.md) |

## 2026-08-20 delta — changes since the 2026-08-09 audit

Between 2026-08-09 and 2026-08-20, 81 commits landed on `main`. This section records what
changed. Rows above not mentioned here were **not** re-verified on 2026-08-20 and still
carry their 2026-08-09 evidence.

### New capability, previously undocumented anywhere

| Capability | Status | Evidence |
|---|---|---|
| Native `stream_query()` long-running transport | ✅ | `SREAgent.stream_query()` (`agent/main.py:1260`) and its `investigate_stream()` counterpart (`agent/main.py:691`), added by PRs #156/#160 for issue #103. Deployed; a real live investigation ran **315.3 s** — past the old ~300 s managed-transport boundary that used to abort the call — returning a truthful RCA with real tool calls. Native streaming is now the supported invocation for long investigations. Before this update, no document in the repo mentioned it. |
| Cross-investigation state isolation | ✅ | Issue #74 closed. `LLMClient.reset_session()` called at the start of every investigation (`agent/main.py`), so token/cost/latency counters no longer leak between investigations sharing a warm process. |

### Rows whose underlying issues have since closed

`#65`–`#72` (confidence scoring: claim grounding, `resource_identity_match`, time
correlation/freshness, evidence-domain exit gating, self-assigned `observed_fact`, MCP
fallback audit trail and trigger conditions) are **all now CLOSED**, fixed by PRs #123,
#124 and #125. The confidence rows above predate those fixes and describe the
pre-fix behaviour.

Also closed since: `#29` (Cloud Trace hostname), `#31`, `#60`, `#63`, `#64`, `#73`
(missing-cluster safe-stop), `#75`, `#76`, `#87`, `#91`, `#92`, `#103`, `#130`, `#161`.

### Open items a reader should know about

| Item | State |
|---|---|
| #164 | **OPEN.** *Fixed:* our own OTel provider race (PR #165 stopped `get_tracer()` competing for the global provider slot, restoring the managed tracer path). *Still unresolved:* the Agent Platform Console Traces tab has not updated since 2026-08-13 and Telemetry collection still reads "Learn more" not "Enabled". **The Console-side root cause is not confirmed** — three config hypotheses were tested live and ruled out (2026-08-14 to 2026-08-16). Cloud Trace itself is unaffected; use it directly instead of the Console tab. |
| #139 | **RESOLVED for `rca_builder.py`** (3/3 clean live runs, 2026-08-23) — root cause was Agent Gateway never resolving a registry match for the gRPC transport; fixed via `_use_grpc=False` + re-registering `us-central1-logging` as `protocolBinding=HTTP_JSON`. Same fix deployed to 3 sibling call sites (`tool_executor.py`, `mcp_router.py`, `gcs_client.py`, PR #175) but **not yet independently live-validated** — each only writes on a genuine failure that hasn't occurred naturally yet. Implemented ≠ live-validated for those 3. Investigation results were never affected either way. |
| #77, #78 | **OPEN** — CI test/lint/security coverage and workflow path filters are **implemented, merged and deployed**, and the implementation is correct. They remain In Progress only because the required post-deployment live canary has not yet passed (blocked by the #103 condition). Implemented ≠ Completed. |
| #32 | **OPEN** — Model Armor output-sanitization verdict handling is implemented and deployed; the actual `MATCH_FOUND` / `blocked=True` path has never been exercised live. |
| #94 | **OPEN** — a fix is proposed in PR #157, which is **open and unmerged**. Not fixed. |

**Note on `iac/gke-access/`:** no CI workflow manages that Terraform stack today. This is
deliberately **not** part of #78 (see that issue's 2026-08-14 Correction — the workflows
run with `working-directory: iac/agent`, so adding the path would validate the wrong
stack). Whether it should get its own plan-only CI job is an open design question, not
yet filed.

---

**How to keep this table honest going forward:** update the relevant row immediately when a
capability's real status changes — don't let this drift the way `evaluation.md`,
`deployment.md`, and 3 other pages drifted between 2026-08-08 and 2026-08-09 (see
[Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md) for that specific
gap list and what was fixed).

**Related pages:** [Risks and Limitations](risks-and-limitations.md) · [Documentation Validation Report](../../archive/SUPERSEDED_2026-08-20_DOCUMENTATION-VALIDATION-REPORT.md) · [Executive FAQ](executive-faq.md)
