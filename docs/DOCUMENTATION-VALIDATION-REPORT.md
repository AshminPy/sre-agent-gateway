# Documentation Validation Report

> ## ⚠️ HISTORICAL SNAPSHOT — NOT CURRENT PROJECT STATUS
>
> **Status:** HISTORICAL SNAPSHOT · **Covers:** 2026-08-08 / 2026-08-09 · **Header added:** 2026-08-20
>
> This is a point-in-time record of the documentation-validation passes run on 2026-08-08
> and 2026-08-09. It is kept in place — not archived — because it remains useful **audit
> evidence**: it shows exactly which doc-vs-code contradictions existed then and how each
> was resolved. Seven documents link to it for that reason.
>
> **Do not read it as the current state of the project.** Everything below was accurate on
> 2026-08-09. More than 80 commits have landed since, changing transport, tracing,
> confidence scoring, metrics, IAM/RBAC and CI.
>
> For current status, use current code and Terraform on `main`, plus:
> - [`docs/management/implemented-vs-planned-matrix.md`](management/implemented-vs-planned-matrix.md) — what is really implemented vs planned
> - [`docs/management/risks-and-limitations.md`](management/risks-and-limitations.md) — current known gaps
> - `docs/management/PROJECT_TRACKER.xlsx` — authoritative task/status tracking

## 2026-08-09 re-verification — read this first, supersedes nothing below (additive)

Per this report's own recommendation #3 ("re-verify this whole knowledge base after any of the
14 risks above gets resolved"), a second 6-parallel-pass re-audit ran today, this time checking
the 2026-08-08 docs against the current code (2 real fixes landed since then: PR #52 multi-cluster
`clusters.json`, PR #53 corrected tool-scaling baseline). Several checks used live evidence, not
just static reading: `gcloud iam roles describe` against real predefined roles, `mcp/tests/
test_no_mutation.py` executed live (2/2 passed), `terraform test` executed live (4/4 passed),
`pytest tests/test_multi_cluster_registry.py tests/test_mcp_router.py` executed live (13/13
passed).

**Real staleness found and fixed today** (10 files):
- `docs/architecture/evaluation.md` — didn't mention either of today's 2 eval-harness bug fixes, the 2 new regression tests, or the corrected baseline at all. Added a full section.
- `docs/architecture/evidence-architecture.md` — the example evidence object had `source` and `tool` **swapped** relative to the real `ev_entry` shape in `agent/nodes/evidence_extractor.py:131-143` (`source` actually holds the tool name; `mcp_source` holds the MCP source). Fixed to match the real code exactly.
- `docs/operations/deployment.md` — claimed CI "posts as a PR comment"; checked the actual workflow YAML, no such step exists. Removed the false claim, added the `terraform test` step that's real but was undocumented.
- `docs/operations/terraform.md` — same missing `terraform test` step; `main.tf`'s row didn't mention it now also renders `clusters.json`; the "wipe-on-apply limitation" framing for `clusters.json` was stale (fixed 2026-08-09, no longer a limitation).
- `docs/architecture/gke-vs-nongke.md`, `docs/runbooks/add-non-gke-cluster.md`, `docs/runbooks/mcp-failure.md`, `docs/governance/scaling.md` — all four still told a reader the cluster registry gets wiped on a second cluster entry and pointed at a dead anchor link (`cluster-routing.md#known-operational-limitation`, which no longer exists) — all fixed to reflect the real, working multi-cluster mechanism. (`scaling.md` was missed in the first pass through this list — an independent reviewer caught it before merge.)
- `docs/architecture/langgraph-workflow.md`, `docs/architecture/investigation-loop.md` — both claimed `recursion_limit=60` was "hardcoded in `agent/main.py:423,443`" — it's now a shared constant (`agent/graph.py:29`) imported by both `agent/main.py` and `agent/eval/run_eval.py`, specifically to prevent the eval-harness drift that caused today's bug #2. Fixed.
- `docs/operations/daily-health-check.md` — linked to a heading in `logging.md` that doesn't exist; the real content lives in `observability.md`. Fixed the link.
- `docs/least-privilege-iam.md` — still missing `roles/aiplatform.agentDefaultAccess`, a gap the 2026-08-08 report already flagged as unfixed (item 4 below) — fixed today.
- `docs/architecture/memory.md`, `docs/architecture/cluster-routing.md`, `docs/architecture/dynamic-mcp-routing.md`, `docs/architecture/mcp-architecture.md` — several citation line-number drifts of 2-3 lines (content still correct, exact line numbers were off) — corrected.

**New deliverable produced from this pass**: [Implemented vs Planned — Master Status Matrix](management/implemented-vs-planned-matrix.md), consolidating every status label (✅/🟡/🔵/❌) found across all 6 audits into one table, each with file:line or live-command evidence.

**Confirmed still accurate** (the large majority of the 51 pages): all 9 LangGraph node descriptions, the confidence-scoring formula, the read-only IAM/security proof, the metrics/alerts inventory (11/11 each, unchanged), the CD-to-Agent-Engine path, and the core evidence pipeline design all held up against direct code re-verification.

**Real finding, not yet acted on**: only 3 of 9 LangGraph nodes (`context_resolver`, `mcp_router`, `rca_builder`) have direct test coverage; the other 6 have real, working code but zero direct or full-graph test evidence. No test in the repo exercises the compiled graph end-to-end. This is a genuine test-coverage gap, not a documentation gap — flagged here since it surfaced during this pass.

---

# Documentation Validation Report (original — 2026-08-08 pass)

> **Last Verified:** 2026-08-08
> **Method:** 6 parallel deep-read research passes against the live repository (`~/projects/sre-agent-gateway`, GCP project `sreagent-t2-demo`), each covering a distinct subsystem, followed by synthesis into 51 documentation pages. Every substantive claim in the resulting docs carries a file:line citation traceable back to the source research. This report is a second pass, checking the documentation's own coverage and honesty against what was actually found.

## Coverage counts

| Item | Count | Notes |
|---|---|---|
| Total documentation pages | **51** | 17 architecture, 10 operations, 11 runbooks, 8 governance, 3 management, 2 onboarding |
| Total word count | **~35,700** | Across all 51 pages |
| LangGraph nodes documented | **9 of 9** | `input_normalizer`, `context_resolver`, `task_planner`, `mcp_router`, `tool_executor`, `evidence_extractor`, `task_evaluator`, `loop_controller`, `rca_builder` — full inventory in [LangGraph Workflow](architecture/langgraph-workflow.md) |
| Terraform resource files documented | **17 of 17** in `iac/agent/` (plus `iac/gke-access/`) | Full inventory in [Terraform / Infrastructure Management](operations/terraform.md) |
| MCP servers documented | **2 of 2** | `gke_remote_mcp` (Google-managed, live), `k8s_mcp` (custom, code complete but not operational live) — see [MCP Architecture](architecture/mcp-architecture.md) |
| MCP tools documented | **33 of 33** | 6 (`GKE_REMOTE_TOOLS`) + 27 (`CUSTOM_K8S_TOOLS`) |
| Log-based metrics documented | **11 of 11** | Full inventory with filters and known double-counting risk in [Observability](operations/observability.md) |
| Alert policies documented | **11 of 11**, plus the 3 deliberately-skipped types | See [Alerting](operations/alerting.md) |
| Golden evaluation cases documented | **14 of 14** | See [Evaluation](architecture/evaluation.md) |
| Runbooks created | **11 files, covering all 30 requested troubleshooting scenarios + 4 build/onboarding runbooks** | Consolidated by theme rather than 34 separate single-scenario files — see the [runbooks index](README.md#runbooks) |
| Mermaid diagrams | **3 built and embedded** | System flow, LangGraph edge-map, MCP routing — see the Diagrams section below for what worked, what was tried, and what's out of scope |
| IAM bindings documented | **All bindings found across `iac/agent/iam.tf`, `iac/agent/iap_egressor.tf`, `iac/gke-access/crossproject_iam.tf`** | Full matrix in [Security Operations](governance/security.md) |

## Diagrams — what worked, what was tried, what didn't work, what's planned

**What worked**: 3 Mermaid diagrams were built and embedded directly in the docs — the system-flow diagram ([System Overview](architecture/system-overview.md)), the LangGraph node/edge map ([LangGraph Workflow](architecture/langgraph-workflow.md)), and the MCP routing-decision diagram ([Dynamic MCP Routing](architecture/dynamic-mcp-routing.md)). These render inline as part of the page, not as separate image files.

**What was tried**: nothing beyond those 3 — no diagram-generation attempt was made and abandoned partway; the other 15 diagram types named in the original request (context/state lifecycle, tool-selection process, Agent→Gateway→MCP request path, cluster routing, Agent Identity auth chain, evidence lifecycle, confidence calculation flow, memory lifecycle, CI/CD flow, observability/trace flow, cluster onboarding flow, MCP onboarding flow, failure/fallback flow, scaling evolution) were simply not attempted in this pass — not a case of "tried and it broke."

**What didn't work**: nothing — there's no failed diagram output to report. This is a scope gap, not a technical failure.

**What's planned**: nothing currently scheduled. The underlying content for every one of those 15 topics already exists in prose/table form on its respective page (e.g., the confidence-calculation flow is fully described in [Confidence Scoring](architecture/confidence.md), the CI/CD flow in [Updating the Agent](operations/deployment.md)) — a diagram would be a visual aid on top of already-complete written coverage, not a documentation gap in the sense of missing information. Build additional diagrams only if/when specifically requested for a particular page.
2. **`agent/prompts.py` exact prompt text** was referenced structurally (which fields each prompt template takes) but not reproduced verbatim in the docs — intentional, to keep prompt-engineering iteration decoupled from this knowledge base, but flag if the Run team needs the literal prompt text for debugging.
3. **`invoke_agent.py`, `run.py`, `eval.py`, `eval_native.py`** were read only partially (enough to confirm the `onprem` scenario and CLI invocation patterns) — a full walkthrough of every CLI scenario wasn't performed.
4. **Live GCP quota numbers** are deliberately NOT stated anywhere in this knowledge base (see [Capacity and Quotas](governance/capacity.md)) — per the explicit instruction not to guess numbers that change over time; only where-to-check pointers are given.
5. **Dependency/supply-chain security** (SCA on `requirements.txt`, GitHub Actions third-party action pinning) was flagged as UNKNOWN in [Security Operations](governance/security.md) — not independently audited in this pass.
6. **Model Armor floor-settings** (org-level overrides) were referenced from prior session evidence (a real REST API call returning `500`/`403`) but not re-verified live during this specific documentation pass.

## Contradictions discovered between existing documentation and actual code/config

These were found during research and corrected in the new documentation — flagging them explicitly here since they represent real doc-drift that existed before this effort:

1. **`iac/agent/monitoring.tf`'s comment claims the gateway's IAP extension runs in DRY_RUN.** The live `iac/agent/terraform.tfvars` sets `iap_iam_enforcement_mode = null`, which means **ENFORCE**, not DRY_RUN (switched 2026-07-14 per that same file's own inline comment). Corrected in [Agent Gateway](architecture/agent-gateway.md).
2. **`iac/agent/model_armor.tf` and `docs/ADR-002-agent-identity-and-gateway.md` both describe/imply a Model Armor `CONTENT_AUTHZ` chain exists at the gateway.** It does not — confirmed by a direct, same-day API rejection (`Error 400: unsupported Google API for AuthzExtension: modelarmor.googleapis.com`). Corrected in [Agent Gateway](architecture/agent-gateway.md) and [Security Operations](governance/security.md).
3. **`PRODUCTION-LAUNCH-PLAN.md` describes the cluster registry schema as thinner than it actually is**, and claims `context_resolver.py` defaults a missing cluster to `sre-test-cluster` — neither matches current code (`agent/mcp_client.py`'s registry parser already handles the full schema; `context_resolver.py`'s own docstring confirms it never defaults). Corrected in [Cluster Routing](architecture/cluster-routing.md).
4. **`docs/least-privilege-iam.md`** (the existing human-maintained IAM audit) is missing `roles/aiplatform.agentDefaultAccess`, which is present in live Terraform — a minor sync gap, noted in [Security Operations](governance/security.md).
5. **The task brief that seeded one research pass assumed `rca_builder.py` still contains a `_validate_citations` function.** It doesn't — that logic was superseded by `agent/confidence/claim_builder.py`'s grounding mechanism during the confidence-framework redesign. Documented correctly in [Confidence Scoring](architecture/confidence.md), with the historical note preserved for context.

## Configuration requiring manual/live validation (not fully confirmable from code alone)

- Whether the double-emission log issue (see [Observability](operations/observability.md)) actually produces duplicate Cloud Logging entries in practice — structurally confirmed from code, not confirmed with a live Cloud Logging count comparison.
- Whether `terraform-apply.yml`'s `environment: production` approval gate is actually configured to require manual approval in GitHub repo settings — not visible from the workflow YAML itself.
- `google_gke_hub_membership`/RBAC state for `sre-lab` — confirmed live at time of research, subject to change if it was scaled down/removed afterward.
- Agent Engine's `max_instances` platform default — not set in Terraform, actual behavior would need to be confirmed against the live resource or current Vertex AI documentation.
- Memory Bank retention policy — not explicitly configured in this repo's Terraform.

## Production risks discovered (full detail in [Risks and Limitations](management/risks-and-limitations.md))

Ranked by significance: (1) Model Armor content-safety inspection is not actually active in the live deployment despite the Terraform templates existing, (2) the confidence-scoring policy is explicitly self-labeled uncalibrated, (3) the fallback Kubernetes MCP path doesn't work and has no working network path today, (4) on-prem cluster support is unproven beyond manual testing, (5) several observability metrics likely double-count, (6) IAP fails open on an outage, (7) the cluster registry is wiped on every `terraform apply`, (8) no automated eval-quality CI gate exists, (9) no human-approval step exists before a memory write, (10) Connect Gateway doesn't audit-log successful reads, (11) no PagerDuty integration exists, (12) no documented compute scaling ceiling, (13) no tested disaster-recovery drill, (14) no formal SLOs are committed to.

## Recommended next documentation updates

1. **Build the remaining 15 Mermaid diagrams** — highest-priority gap from this pass.
2. **Reproduce `agent/prompts.py`'s literal prompt templates** in an appendix, for anyone doing prompt-engineering work directly.
3. **Re-verify this whole knowledge base after any of the 14 risks above gets resolved** — each fix should trigger an update to the relevant page's Implementation Status header, not just a mental note.
4. **Add a live Cloud Logging count check** to confirm or rule out the double-emission hypothesis definitively, then update [Observability](operations/observability.md) accordingly.
5. **Schedule a real disaster-recovery drill** and document the actual results, replacing the current "true in principle, unverified in practice" framing in [Disaster Recovery](operations/disaster-recovery.md).
6. **Revisit this whole knowledge base's currency** on a fixed cadence (recommend quarterly, or immediately after any major architecture change) — every page's "Last Verified" date should move forward with a fresh check, not just an assumption that nothing changed.

---

**This report exists so nobody mistakes this knowledge base for more complete than it is.** Every gap listed above is a real, current gap — not a hedge. Use [Risks and Limitations](management/risks-and-limitations.md) as the living version of the risk list; this report is the point-in-time coverage snapshot.
