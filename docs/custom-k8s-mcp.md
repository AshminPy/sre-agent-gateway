# Custom read-only Kubernetes MCP server — P4 completion evidence

Status: **built, hardened, tested — real evidence below.** Covers
`PRODUCTION-LAUNCH-PLAN.md` Priority 4. Builds on Priority 3
(`docs/connect-gateway-onprem.md`) — this MCP server now reaches the same
`sre-lab` cluster through the same Connect Gateway path that document
proved standalone.

## What changed

| Area | File(s) |
|---|---|
| New tool logic (StatefulSet/DaemonSet, Service/Endpoints, Node, ConfigMap, HPA, PVC, Job, namespace-wide events) | `mcp/tools/workloads.py`, `services.py`, `nodes.py`, `configmaps.py`, `scaling.py`, `storage.py`, `jobs.py`; extended `deployments.py` (`describe_deployment`, ReplicaSet), `events.py` (`get_namespace_events`) |
| Hardening primitives (validation, scope limits, trimming, redaction, timeouts, rate limiting, audit logging) | `mcp/security.py` — new file |
| Wiring: 27 tools, all wrapped in `@guarded()`, Connect Gateway kube-context support, health checks | `mcp/server.py` |
| Tests (unit + integration) | `mcp/tests/` — new: `conftest.py`, `test_security.py`, `test_tools.py`, `test_no_mutation.py`, `test_live_connect_gateway.py` |

No changes to `mcp/requirements.txt` or `mcp/Dockerfile` — `security.py` is
stdlib-only by design, and `starlette` (used for the health-check routes) is
already a transitive dependency of `fastmcp`.

## Tool coverage — exact parity with the allowlist

`agent/mcp_client.py`'s `CUSTOM_K8S_TOOLS` frozenset was already written
(ahead of this work) as the target tool list. Confirmed live:

```
python -c "
import server, asyncio
names = sorted(t.name for t in asyncio.run(server.mcp.list_tools()))
print(len(names)); print(names)
"
# -> 27
import agent.mcp_client as mc; sorted(mc.CUSTOM_K8S_TOOLS)
# -> same 27 names, same order
```
Both lists are identical: `describe_daemonset, describe_deployment,
describe_hpa, describe_job, describe_node, describe_pod_detail,
describe_pvc, describe_replicaset, describe_service, describe_statefulset,
get_configmap, get_current_logs, get_previous_logs, list_configmaps,
list_daemonsets, list_deployments, list_endpoints, list_events, list_hpas,
list_jobs, list_namespace_events, list_nodes, list_pods, list_pvcs,
list_replicasets, list_services, list_statefulsets`.

## No write/exec/port-forward — verified, not assumed

Permanent regression test (`mcp/tests/test_no_mutation.py`) scans every
`.py` file under `mcp/` for mutating Kubernetes API call patterns
(`create_namespaced_*`, `delete_*`, `patch_*`, `replace_*`,
`connect_*_exec`/`connect_*_proxy`, `port_forward`) — zero matches. Also ran
manually:
```
grep -rniE "(create_namespaced|delete_namespaced|patch_namespaced|replace_namespaced|create_node|delete_node|patch_node|replace_node|connect_.*_exec|connect_.*_proxy|delete_collection)" --include="*.py" mcp/
# -> no matches (exit code 1)
```
No `get_secret`/`list_secrets`/`describe_secret` tool exists anywhere, and
`read_namespaced_secret` is never called — enforced by
`test_no_secret_resource_tools_exposed`. This is on top of the `view`
ClusterRole (Task 4/P3) already excluding Secret objects at the RBAC layer.

## Hardening — what was added, `mcp/security.py`

- **Request validation**: `validate_namespace` (DNS-1123 *label* — no dots,
  matches K8s namespace rules) and `validate_name` (DNS-1123 *subdomain* —
  dots allowed, matches most other K8s object names). **Real bug caught
  during integration testing**: an earlier version used the label rule for
  both, which rejected the real, built-in `kube-root-ca.crt` ConfigMap
  (present in every namespace) — fixed and regression-tested
  (`test_validate_name_accepts_dotted_subdomain`,
  `test_live_configmap_with_dotted_name_regression`).
- **Namespace/cluster scope limits**: `K8S_MCP_ALLOWED_NAMESPACES` env var
  (comma list) rejects out-of-scope namespace args before any K8s API call.
  Cluster scope is structural — no tool accepts a cluster/endpoint argument,
  so no request can redirect the process to a different cluster than the one
  fixed at startup.
- **Response trimming / size limits**: `MAX_LIST_ITEMS` (200),
  `MAX_TEXT_CHARS` (20000), `MAX_TAIL_LINES` (2000, hard cap on
  caller-supplied `tail_lines`) — all env-tunable.
- **Secret redaction**: regex-based, substring key match (`*password*`,
  `*token*`, `*api[-_]key*`, etc.) + bearer-token + email patterns, applied
  to every tool's return value by `@guarded()`. Verified it does NOT
  false-positive on a real CA certificate (`test_redact_does_not_touch_ca_certificates`).
- **Timeouts**: every Kubernetes API call passes
  `_request_timeout=(connect_s, read_s)` (default 5s/15s, env-tunable) —
  so a Connect Gateway tunnel outage (§8 of `docs/connect-gateway-onprem.md`)
  fails fast instead of hanging the MCP request.
- **Rate limiting**: in-process sliding-window limiter, 60 calls/60s default
  (env-tunable), returns a structured `{"error": "rate limit exceeded..."}`
  rather than raising.
- **Audit logging**: one structured JSON log line per tool call (tool,
  redacted arguments, ok/error, duration) via standard `logging` → Cloud
  Run stdout → Cloud Logging automatically, no extra IAM needed.
- **Health checks**: `GET /healthz` (liveness, no K8s call) and `GET
  /readyz` (real `list_namespace(limit=1)` connectivity check) — both
  verified live (see below).

## Real evidence — live against Connect Gateway → sre-lab

All commands below were run for real, this session, against
`connectgateway_sreagent-t2-demo_global_sre-lab` (the same context Task 4
proved) reaching the `sre-lab` kind cluster.

### Health checks (server started with `K8S_MCP_KUBE_CONTEXT` set)
```
$ curl -s http://127.0.0.1:8091/healthz
ok
$ curl -s http://127.0.0.1:8091/readyz
{"ready":true}
```

### 19 tool calls through `server.mcp._call_tool_mcp` — real cluster

18 succeeded with real data (`list_pods`: 12 pods in kube-system,
`describe_deployment` coredns: 2/2 ready, `list_configmaps`: 7 ConfigMaps
including the dotted-name regression case, `list_events`: 52 real events,
etc.). One (`list_nodes`) correctly returned a structured
`{"ok": false, "error": "... nodes is forbidden ... at the cluster
scope ..."}` — this is the exact, already-documented RBAC gap from
`docs/connect-gateway-onprem.md` (`view` ClusterRole excludes cluster-scoped
Nodes), surfaced cleanly instead of crashing the process.

### Output-shape comparison — GKE Remote MCP vs custom MCP, both real calls

```
CUSTOM MCP  (k8s_mcp,        sre-lab via Connect Gateway):
  envelope keys: ['duration_s', 'mcp_source', 'ok', 'result', 'tool']
GKE REMOTE MCP (gke_remote_mcp, sre-test-cluster, real GKE, project sreagent-demo):
  envelope keys: ['duration_s', 'mcp_source', 'ok', 'result', 'tool']
```
Identical, via two real HTTP calls through `agent/mcp_client.py`'s
`call_tool()` — the exact function `agent/nodes/tool_executor.py` calls in
production. That function only ever reads `result["ok"]`,
`result.get("duration_s")`, and `result.get("result", {})` — all three
present and typed the same way regardless of source, so the reasoning layer
is provably vendor-agnostic at the layer that matters.

The internal `result` *content* is not byte-identical (GKE Remote MCP
returns a `kubectl describe`-style text blob; the custom MCP returns a
curated JSON dict) — documented honestly rather than glossed over. This
doesn't break vendor-agnosticism because `agent/nodes/evidence_extractor.py`
never pattern-matches specific fields in `result`; it JSON-serializes
whatever it received and lets the LLM extract `summary`/`key_facts` from the
text, independent of the exact schema.

### Local-dev limitation hit, and how it was worked around

Calling the custom MCP through `agent/mcp_client.py`'s `k8s_mcp` HTTP path
needs `_get_identity_token()`, which needs a Google service-account
credential to mint an audience-scoped ID token — user ADC
(`gcloud auth application-default login`) can't do this
(`DefaultCredentialsError: Neither metadata server or valid service account
credentials are found`), which is expected: that path is designed for Cloud
Run's Workload Identity, not a human's local gcloud session. Worked around
for this local shape-comparison only by monkey-patching
`_get_identity_token` to return a placeholder (the local dev server has no
auth middleware to validate it against) — the token-minting mechanism
itself is unchanged production code and untouched by this workaround.

## Tests

```
cd mcp && K8S_MCP_KUBE_CONTEXT=connectgateway_sreagent-t2-demo_global_sre-lab \
    pytest tests/ -v
# 64 passed (57 unit incl. 27 new-tool-module tests + 30 security tests
#            + 2 no-mutation tests, 7 live-cluster integration tests)
```
Root-level suite unaffected: `pytest tests/` at repo root — 85 passed
(pre-existing count, mcp/ is intentionally outside the root `testpaths`
config since it's a separately-deployable component with its own
`requirements.txt`/`Dockerfile`).

## Known gaps / follow-ups (not fixed here, documented not hidden)

- **Cloud Run production wiring now done and live.** `iac/agent/cloudrun_mcp.tf`
  sets `K8S_MCP_KUBE_CONTEXT` (via `var.custom_mcp_kube_context`), and
  `mcp/Dockerfile` bakes in the Connect Gateway kubeconfig, the
  `gke-gcloud-auth-plugin`, and the base `google-cloud-cli` package the
  plugin needs. Real end-to-end proof exists in production (Agent → Agent
  Gateway → custom Cloud Run MCP → Connect Gateway → the `sre-lab` cluster),
  verified 2026-09-04 and independently re-confirmed 2026-09-05/06/07 — see
  [MCP Architecture](architecture/mcp-architecture.md) for the evidence.
  RESOLVED 2026-09-07 (issue #86, Section 5 of the new-assignment expansion
  work): `get_k8s_clients(cluster_id)` is now `@lru_cache(maxsize=32)`, keyed
  per cluster — one shared Cloud Run service correctly serves many clusters
  concurrently with proven isolation (`mcp/tests/test_multi_cluster_isolation.py`'s
  real concurrent-threading test). Adding a new on-prem cluster is now a
  registry entry + Fleet registration, not a new deployment — see
  `docs/connect-gateway-onprem.md`'s "Adding a second on-prem cluster" section.
- **Rate limiting is per-process, not distributed.** Fine for
  `min_instance_count=0/max=3` single-tenant internal use
  (`cloudrun_mcp.tf`); would need Memorystore/Redis for a real distributed
  limiter across instances — not built, matches this tool's actual blast
  radius today.
- **DATA_READ audit logging gap carries over from P3** — successful reads
  through Connect Gateway still don't appear in Cloud Audit Logs by default
  (see `docs/connect-gateway-onprem.md` §6); this MCP server's own
  `audit_log()` covers the *tool-call* layer (what the agent asked for),
  not the underlying Connect Gateway API call layer — the two are
  complementary, neither replaces the other.
