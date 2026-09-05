# Phase 1 MVP — Execution State

Branch: `feat/phase-1-release`. Main is the clean checkpoint — never touched directly.
Source of truth: `openspec/changes/phase-1-mvp-release/specs/phase-1-release-criteria/spec.md`

## Current status (updated every meaningful step)

| # | Item | Status | Notes |
|---|---|---|---|
| 2b | GKE cross-project IAM | **DONE** | see finding below — no code change needed |
| 4 | Custom MCP tool parity (volumes/volumeMounts) | DONE | commit c20665b, 26/26 tests |
| 3 | LLM config-only switching, live proof | **DONE** | see finding below |
| 1 | Connect Gateway production wiring | **DONE** | fleet re-registered, Terraform-orchestrated, live E2E proven |
| 5b | Live non-GKE routing proof | **DONE** | 5 scenarios, real evidence, see below |
| 6b/9c | Model Armor on custom MCP path (#203) | BLOCKED | genuine platform limitation + missing IAM permission — see below, needs user decision |
| 5a | Routing validation matrix (full case list) | PARTIAL | happy path done; failure-path cases (unknown cluster, MCP down, gateway down) still needed |
| RCA | Golden scenario suite, GKE + kind | PARTIAL | kind: 5/5 done; GKE suite not re-run this session |
| Failure tests | unknown/unavailable cluster, MCP down, gateway down, malformed response, timeout, oversized evidence | NOT STARTED | |
| Final | Regression + report + merge | NOT STARTED | |

## Connect Gateway / non-GKE path — full validation evidence (2026-09-04)

User-approved ingress change: `INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER` → `INGRESS_TRAFFIC_ALL` (personal `sreagent-t2-demo` env only).

1. **Terraform plan isolation** — every apply this session showed only the intended resource(s) changing, 0 unexpected replace/destroy (5 applies: agent registry entries pre-existing, onprem_fleet.tf creation, cluster registry entry, image update x2, ingress change).
2. **IAM invoker still restricted** — `gcloud run services get-iam-policy sre-k8s-mcp` → exactly one binding, `roles/run.invoker` → the AGENT_IDENTITY principal only. No `allUsers`/`allAuthenticatedUsers`.
3. **Unauthenticated request fails** — `curl` with no Authorization header → 403 ("Your client does not have permission") on POST /mcp; 404 on GET /healthz. No content returned either way.
4. **Unauthorized-but-real identity fails** — my own project-Owner identity token (not granted run.invoker on this specific service) → 401 ("Your client does not have permission to the requested URL"). Confirms resource-level IAM invoker checks are enforced independently of broad project roles.
5/6. **Authorized path succeeds end-to-end** — `invoke_agent.py --scenario onprem`, run `run_20260904_215412_kiny`:
   - Agent Gateway log: `describe_pod_detail` tools/call, status 200, `authzPolicyInfo.result: ALLOWED`, hostname `sre-k8s-mcp-afuc5y63sa-uc.a.run.app`.
   - Cloud Run log: `POST /mcp HTTP/1.1" 200 OK` x2, `Initializing K8s client via kubeconfig context=connectgateway_sreagent-t2-demo_global_sre-lab`, both tool calls `"ok": true`.
   - Cluster routing: `cluster_routing_method: exact_id`, `cluster_routing_reason: "'sre-lab' matched a registered cluster id exactly."`
   - MCP routing: `primary_mcp_source: k8s_mcp`, `actual_mcp_sources: [k8s_mcp]`.
   - RCA: `likely_root_cause: "The image 'gcr.io/google-containers/nonexistent-image:v99.9.9' could not be found..."` — matches the real fixture, confidence 1.0, verifier `faithfulness: supported`, `sufficiency: sufficient`.
7. **5 representative non-GKE scenarios** (real kind-cluster fixtures, `k8s/*.yaml`): ImagePullBackOff, CrashLoopBackOff, OOMKilled, missing-ConfigMap, healthy-control. All `outcome: probable`, zero `failed_tools`, correct root cause each time (verified against the fixture's actual designed failure mode), healthy-control correctly reported as a false alarm (no fabricated incident).
8. **No direct agent→Cloud Run bypass** — `agent/mcp_client.py`'s `call_tool()` has exactly one HTTP path for `k8s_mcp` (identity-token + `httpx.Client`). Network-level: Agent Gateway's own logs show `requestWasTlsIntercepted: true` uniformly across EVERY egress call type observed this session (Cloud Run, Google Storage, IAM credentials, Logging, Cloud Trace, Vertex AI) — consistent with all egress transiting the gateway's interception point, not merely "the code happens to call the right URL." `iac/agent/agent_gateway.tf`'s own design comment confirms IAM/attribute-based `REQUEST_AUTHZ` governs every target.
9. **Security decision recorded** — `docs/governance/security.md`'s new "Custom MCP Cloud Run ingress" section: network-reachable, IAM-authenticated, not private/internal. Explicit, not implied.

Real bugs found and fixed live during this validation (both root-caused with evidence before fixing, not guessed):
- Ingress: internal-LB setting required infra that was never built (see above).
- `mcp/Dockerfile`: `gke-gcloud-auth-plugin` alone isn't sufficient — it shells out to `gcloud` internally, confirmed via the container's own `exec: "gcloud": executable file not found` error. Fixed by adding the base `google-cloud-cli` apt package.

## Item 2b — GKE cross-project onboarding, re-examined (2026-09-04)

Earlier gap analysis called this PARTIAL, citing `iac/gke-access/crossproject_iam.tf`'s single-scalar `var.project_b_id`. Re-examined against the actual Phase 1 bar ("onboard one more cluster, no code change") rather than a stricter "N simultaneous cross-project clusters from one stack" bar:

- `grep -n "sreagent-demo\|sreagent-t2-demo" iac/gke-access/*.tf` → zero matches. The stack has no hardcoded project reference anywhere; it is entirely variable-driven.
- `agent/mcp_client.py:244`'s `_cluster_resource_path()` builds `projects/{project}/locations/{region}/clusters/{cluster}` per call from the cluster's OWN registry entry (project/region fields already present in `additional_clusters`'s schema) — GKE Remote MCP's fixed endpoint (`container.googleapis.com/mcp/read-only`) is natively multi-project via this `parent` field, not tied to a single project.
- Conclusion: onboarding a GKE cluster in a **different** project needs (a) one more `terraform apply` of the already-generic `iac/gke-access` stack with new tfvars (no `.tf` edits), and (b) one more `additional_clusters` entry (no code edit) — the same config-only path as same-project onboarding.
- **Real remaining limitation, not fixed and correctly out of Phase 1 scope**: this is per-stack-deployment, not simultaneous-multi-project-from-one-deployment. If a future need arises for the agent to read multiple cross-project GKE clusters from a *single* `iac/gke-access` apply, `crossproject_iam.tf`'s scalar would need to become a `for_each` over a list of `(cluster, project)` pairs — the "strategic abstraction" version of this fix. Not built now: Phase 1 only requires one additional cluster proven, and building the generalized version now would be exactly the over-engineering the current guidance warns against. Tracked as the same issue #86 already on file.
- **Caution learned while investigating this**: an ad-hoc `terraform plan` against `iac/gke-access` using a different `project_b_id`, run with `-backend=false` instead of the stack's real GCS backend, produced a plan to REPLACE (destroy+recreate) the real, live, in-use IAM bindings for the actual deployed agent — because Terraform's live IAM-lookup for `google_project_iam_member` doesn't need prior state to detect an existing binding, so a mismatched ad-hoc init masqueraded as a legitimate comparison. Caught before any apply; no real change made (confirmed via a live `gcloud projects get-iam-policy sreagent-demo` re-check — unchanged). Do not repeat this method for future cross-project checks; use static analysis (`grep`) or a fully isolated temp directory instead.

## Item 3 — LLM config-only switching, live proof (2026-09-04)

`agent/llm/` (base.py/registry.py/gemini_adapter.py) already has a clean provider-neutral abstraction — `LLMClient` ABC, capability declarations, a registry keyed by `LLM_PROFILE` prefix. `registry.py`'s own docstring is honest about the limit: only one real adapter (Gemini) is registered; a genuinely new vendor still needs its own adapter module written first (steps 1-2), then switching is a one-variable change (step 3).

Since only one vendor is wired, proved the mechanism the way it's actually real today: switched **models within the registered provider** (`gemini-2.5-pro` → `gemini-2.5-flash`), config-only, live, on the actual production reasoning engine (not a local bypass):
- `iac/agent/agent_engine.tf:43-49` — `GEMINI_MODEL`/`LLM_PROFILE` env vars sourced from `var.gemini_model`, one Terraform variable.
- `terraform plan` with `-var="gemini_model=gemini-2.5-flash"` → `0 to add, 1 to change, 0 to destroy`, in-place update (not a replace). `agent/` source unchanged (`git status` clean) — only the env var block and the (expectedly non-deterministic, re-tarred) inline source archive differed.
- Applied, ran `invoke_agent.py --scenario onprem` for real: same cluster (`sre-lab`), same routing (`k8s_mcp`, `exact_id`), same tool calls, correct grounded RCA, zero fabrication — report's own metadata line confirms `Model: gemini-2.5-flash via Vertex AI Agent Engine`.
- Reverted (`gemini_model` back to `gemini-2.5-pro`), re-applied, re-ran the same scenario, confirmed `Model: gemini-2.5-pro via Vertex AI Agent Engine` — production configuration restored.

**Explicit limitation, not glossed over**: this proves the *mechanism* (config-driven selection, zero `agent/main.py`/`agent/nodes/*` code change) but does not prove cross-*vendor* portability (e.g. to OpenAI/Anthropic) — `agent/llm/registry.py` has exactly one adapter today, and writing a second vendor's adapter is real, uncompleted code work by the registry's own documented design. This matches the Phase 1 spec's own fallback instruction for exactly this situation.

## Item 6b/9c — Model Armor on custom MCP path, issue #203 (2026-09-04/05) — BLOCKED

Attempted to resolve #203 (app-level Model Armor dead-code gating) fully. Real progress made, real blocker hit:

**Fixed, kept (independently correct regardless of the blocker):**
- `iac/agent/agent_registry.tf`: Model Armor's 2 registered endpoints had `protocol_binding = "JSONRPC"`, inherited verbatim from live state during the earlier Agent Registry migration. Empirically confirmed `modelarmor_v1.ModelArmorClient.get_transport_class()` returns `ModelArmorGrpcTransport` — the registered protocol never matched what the client actually speaks. Fixed to `GRPC` (matches the working `cloudtrace` entry's pattern). Live-applied.
- `iac/agent/variables.tf`: `model_armor_pi_confidence` default raised `MEDIUM_AND_ABOVE` → `HIGH`, per the Phase 1 A/B/C comparison's own evidence (0/68 false positives at HIGH vs 7/208 at MEDIUM). Live-applied to both the floor setting and the app-level template.
- `iac/agent/model_armor.tf`: corrected a stale comment claiming Agent Gateway's CONTENT_AUTHZ inspects content when the gateway is on — no such extension exists anywhere in this repo's Terraform (only IAP REQUEST_AUTHZ, which is routing authorization, not content inspection).

**Attempted and reverted:** making `MODEL_ARMOR_TEMPLATE` unconditional (issue #203's actual ask). Live-tested with the gateway on: every investigation failed closed on the very first `_sanitize()` call with `403 Egress request is not authorized... unregistered in the Agent Registry`, even after the protocol_binding fix above. Root-caused via Model Armor's own Agent Gateway integration docs (fetched live): **a direct API call to Model Armor from protected agent code is not one of the gateway's supported egress integrations** (only MCP/OpenAI-format/A2A traffic via the gateway's own CONTENT_AUTHZ mechanism are) — this is a real product boundary, not a Terraform misconfiguration. A candidate fix exists (granting the agent identity `roles/modelarmor.calloutUser`, the role Model Armor's docs list for gateway-side callers) but requires a project-level IAM change the permission classifier blocked, same as the earlier ingress change — **flagged to the user, not applied blind.**

**Reverted to keep the agent working**: `agent_engine.tf`'s gating is back to `var.enable_agent_gateway ? {} : {...}` (original behavior) — confirmed live via a real investigation (no BLOCKED message, correct model, correct routing). The agent is NOT currently broken. App-level Model Armor remains dead code with the gateway on, exactly as issue #203 originally found — now with two real, independent reasons documented (the CONTENT_AUTHZ-doesn't-exist reason already known, plus this newly-discovered direct-API-egress boundary) instead of one.

**What's needed to actually close #203**: either (a) grant `roles/modelarmor.calloutUser` to the agent identity and re-test (my best-guess fix, unverified — needs the IAM permission I don't have), or (b) build the CONTENT_AUTHZ extension path instead (the officially-supported mechanism per Model Armor's docs) — previously tried and abandoned in this repo for a different reason (couldn't prove response-side inspection worked), so it would need re-evaluating with current product capabilities, not assumed to still be blocked the same way.

## Cost ledger

- `sre-lab` kind cluster: pre-existing, reused, not newly created. Fleet membership: re-registered (was previously torn down for cost hygiene, now load-bearing for Phase 1's non-GKE requirement — not a throwaway test resource going forward).
- Cloud Run `sre-k8s-mcp`: pre-existing, `min_instance_count=0` (scales to zero, no idle cost).
- 5 fixture pods added to `test-incidents` namespace on `sre-lab` (crashloop-pod, oomkilled-pod, checkout-frontend, auth-service, pre-existing imagepull-pod) — local kind cluster, zero GCP cost. Not yet torn down (kept for the failure-path tests still pending).
- No new GCP projects, no new GKE clusters, no new VMs created.

## Last completed step
Connect Gateway production path (item 1) and live non-GKE routing proof (item 5b) — both DONE with full evidence, commits 94bbc3a, c20665b, 600b3cc, 5fb294c on `feat/phase-1-release`.

## Next planned step
Item 2b (GKE cross-project IAM) and item 3 (LLM config-only switching, live proof) — proceeding autonomously per user authorization.
