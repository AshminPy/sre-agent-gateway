# RCA / Test Report — Terraform 1.4.7 + network/DNS grant removal

**Date:** 2026-08-26
**Repo:** `AshminPy/sre-agent-gateway`
**PR:** [#198](https://github.com/AshminPy/sre-agent-gateway/pull/198) — merged `2026-08-26T20:46:31Z`
**Project under test:** `sreagent-t2-demo` (project number `327234009108`)
**Status:** **PARTIAL** — both changes applied and proven safe; CI smoke test is red for an unrelated, pre-existing reason.

---

## 1. Why this was done

`sre-agent-gateway` (personal) and `sre-agent-app-infra` (company) must stay identical apart
from variables. Two things blocked that:

1. This stack required Terraform `>= 1.9.0`. Company Spacelift is pinned to **1.4.7**.
2. This stack granted three project-level network/DNS roles to Google-managed service agents.
   Those are broad, and the company environment is a Shared VPC where they need justification.

The changes were made and validated **here first**, deliberately, before being copied to the
company repo.

---

## 2. What changed

### 2a. Terraform 1.4.7

The `>= 1.9.0` floor existed for exactly one reason: `var.additional_clusters`' collision guard
was a **cross-variable `validation` block** referencing `var.gke_cluster_name`, which requires
Terraform 1.9+.

That guard moved to a **`lifecycle.precondition`** on
`google_storage_bucket_object.clusters_json` (`iac/agent/buckets.tf`). Preconditions are
available from Terraform 1.2.0
([HashiCorp docs](https://developer.hashicorp.com/terraform/language/expressions/custom-conditions)).
The condition text is unchanged, `trimspace()` on both sides included.

A `check` block was **not** used — it only ever warns, never fails plan/apply. That was already
rejected in an earlier review of this same guard.

CI repinned `1.9.0` → `1.4.7` in `terraform-plan.yml`, `terraform-apply.yml`,
`claude-merge-gate.yml`.

### 2b. Network/DNS service-agent grants removed

| Identity | Roles removed |
|---|---|
| `service-327234009108@gcp-sa-aiplatform.iam.gserviceaccount.com` | `compute.networkAdmin`, `dns.peer` |
| `service-327234009108@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | `compute.networkAdmin`, `dns.peer` |
| `service-327234009108@gcp-sa-agentgateway.iam.gserviceaccount.com` | `dns.admin` |

Reasons, all checkable:

1. They existed to program a **Private Service Connect** data plane this stack no longer
   creates — the PSC-I `network_attachment` and its dedicated subnet were removed 2026-08-07.
2. [Google's docs](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-vpc-connectivity)
   scope these to the Agent Gateway service agent on the **Shared VPC host project**, name
   `roles/compute.networkUser` for the network attachment with `compute.networkAdmin` only as a
   broader alternative, do not mention the aiplatform service agents in that context, and list
   `dns.peer` rather than `dns.admin`.
3. Both roles are broad and project-level.

`roles/aiplatform.serviceAgent` was **deliberately left in place** — the Agent Engine cannot be
created without it. It also served as the control for this test.

---

## 3. Test results

### Test 1 — precondition still enforces on 1.4.7 (pre-commit, local)

Standalone `terraform plan` at `TFENV_TERRAFORM_VERSION=1.4.7`, three cases:

| Case | Expected | Actual |
|---|---|---|
| no collision | plan succeeds | plan succeeded |
| exact collision | plan fails | `Error: Resource precondition failed` + original message |
| key `" sre-test-cluster "` (padded) | plan fails | same failure — `trimspace()` still applies |

**Proves:** moving from `validation` to `precondition` loses no enforcement on 1.4.7.
**Does not prove:** anything about the real stack — see Test 2.

### Test 2 — CI plan against live state

- Run: [33011836159](https://github.com/AshminPy/sre-agent-gateway/actions/runs/33011836159), conclusion `success`
- `setup-terraform` step: `terraform_version: 1.4.7`
- `Plan: 0 to add, 2 to change, 5 to destroy.`
- The 5 destroys were exactly the 5 bindings above. The 2 in-place changes were
  `google_storage_bucket_object.clusters_json` and `.eval_dataset` — pre-existing content drift,
  not caused by this PR (adding a `lifecycle` block does not modify a resource). **Not further
  investigated.**

**Proves:** the whole stack initialises, resolves providers and plans on 1.4.7 against real
remote state, and the change set matches intent exactly.

### Test 3 — live IAM before/after

Command both times: `gcloud projects get-iam-policy sreagent-t2-demo --format=json`, filtered to
the four roles on those service agents.

**Before** (captured immediately before merge):
```
roles/aiplatform.serviceAgent   service-327234009108@gcp-sa-aiplatform...
roles/compute.networkAdmin      service-327234009108@gcp-sa-aiplatform-re...
roles/compute.networkAdmin      service-327234009108@gcp-sa-aiplatform...
roles/dns.admin                 service-327234009108@gcp-sa-agentgateway...
roles/dns.peer                  service-327234009108@gcp-sa-aiplatform-re...
roles/dns.peer                  service-327234009108@gcp-sa-aiplatform...
```

**After:**
```
roles/aiplatform.serviceAgent   service-327234009108@gcp-sa-aiplatform...
```

**Proves:** all five target bindings are gone from the live policy, and the control grant
survived. The reduction is real, not just planned.

### Test 4 — apply

- Run: [33012090786](https://github.com/AshminPy/sre-agent-gateway/actions/runs/33012090786)
- `terraform_version: 1.4.7`
- `Apply complete! Resources: 0 added, 2 changed, 5 destroyed.`
- **Run conclusion: `failure`** — see Test 5. The apply step itself succeeded.

### Test 5 — post-apply smoke test (RED, unrelated cause)

Failing step: `Smoke test — verify the agent responds through the gateway`.
Failure line: `SMOKE TEST FAILED — no well-formed RCA in the response`.

The agent ran and produced `run_id: run_20260826_204843_vczp`, with:

```
"identity_confirmed": 1.0
"routing_confirmed":  1.0
"tool_success":       1.0
"freshness":          1.0
"evidence_domains_present": ["kubernetes_events", "kubernetes_status"]
"evidence_ids": ["ev_001", "ev_002", "ev_003"]
"confidence_band": "escalate"
"confidence": 0.5
"working_theory": "The pod 'imagepull-pod' does not exist. It was likely deleted or never
                   successfully created."
```

**Root cause of the red: the test fixture is missing from the cluster, not an IAM problem.**

Verified directly:
```
kubectl --context gke_sreagent-demo_us-central1_sre-test-cluster get pods -A | grep imagepull
  -> no scenario pods found in any namespace
kubectl --context ... -n test-incidents get pods
  -> No resources found in test-incidents namespace.
```

The agent behaved **correctly**: it authenticated, routed, called tools successfully, read real
Kubernetes events and status, found no such pod, and escalated instead of inventing a cause.

**Why this rules out the IAM removal as the cause:** if the removed network/DNS grants were
required for the gateway data path, `tool_success` would be `0` and there would be **no**
`kubernetes_events` / `kubernetes_status` evidence at all. Both are present at full score,
*after* the grants were destroyed.

**Secondary defect noticed, not fixed:** `scripts/smoke_test.sh` line 22 emits
`echo: write error: Broken pipe` while printing the response. It does not change the verdict
here, but it can truncate diagnostic output.

### Test 6 — full agent run against the deployed engine (PASS)

The CI smoke test could not be made green at the time: `test-incidents` had no pods, and the
cluster had **zero nodes** (`FailedScaleUp: GCE quota exceeded`). The fixture pod
`k8s/imagepull-pod.yaml` was applied and sat `Pending`. The autoscaler later succeeded, one node
came up, and the pod scheduled.

The same invocation the smoke test performs was then run directly against the deployed engine:

```
cd ~/projects/sre-agent-gateway
PROJECT_ID=sreagent-t2-demo REGION=us-central1 \
REASONING_ENGINE_ID=7801582006105538560 \
.venv/bin/python invoke_agent.py --scenario imagepull --verbose
```

Result — `sre-rca-20260826-211251-rlwe`, 2026-08-26T21:13:42Z:

```
"status": "done"                       <- smoke_test.sh's own pass condition
rca_report: full structured RCA present
tool_success:       1.0
identity_confirmed: 1.0
routing_confirmed:  1.0
freshness:          1.0
investigation_completeness: 1.00 (band: complete)
error_count: 0.0   errors: []
tool_calls: 3      mcp_latency_s: 4.53
evidence_domains_present: ["kubernetes_events", "kubernetes_status", "unknown"]
```

**Proves:** with the five network/DNS grants destroyed, the agent still authenticates as
AGENT_IDENTITY, resolves and routes to `sre-test-cluster`, calls GKE MCP tools with zero errors,
reads real Kubernetes events and status, and produces a well-formed RCA. This is the direct
end-to-end evidence the reduction is safe.

**Separate defect found during this run — NOT caused by this change.** The agent named the image
as `nginx:1.14.2-nonexistent`, and its evidence chain also cited
`gcr.io/google-samples/gb-frontend:v5`. The pod's real image is
`gcr.io/google-containers/nonexistent-image:v99.9.9`, confirmed with
`kubectl -n test-incidents get pod imagepull-pod -o jsonpath='{.spec.containers[0].image}'`.

The agent self-reported the problem: root-cause confidence `0.74`, band `review_required`, with
reasons *"Evidence ev_002 and ev_003 provided conflicting image names"* and *"3 supporting
evidence item(s) reference a different namespace/pod than the resolved investigation target"*.
The run also logged `Memory Bank: Prior context recalled (636 chars)`. Most likely cause is
Memory Bank contamination from earlier investigations of a different pod. Needs its own issue.

---

## 4. Conclusion

| Claim | Status |
|---|---|
| Stack plans and applies on Terraform 1.4.7 | **Proven** (Tests 2, 4) |
| Collision guard still fails plan/apply after the move | **Proven** (Test 1) |
| The five network/DNS bindings are gone from the live project | **Proven** (Test 3) |
| The gateway data path still works without them | **Proven** (Tests 5 and 6 — identity, routing and tool calls all succeeded, real k8s evidence returned, full RCA produced) |
| CI is green end to end | **NOT proven** — the smoke test has not been re-run in CI since the fixture pod scheduled |

---

## 5. Remaining risks / not covered

- The smoke test has not been seen green **in CI** since this change. The fixture pod is now
  deployed and scheduled, and the same invocation passes locally (Test 6), so a re-run of the
  `terraform-apply` workflow on `main` should now go green. Not yet done.
- The wrong-image / Memory Bank contamination defect from Test 6 is unfiled.
- The two in-place bucket-object changes were not investigated.
- Only the `imagepull` scenario was exercised. Other scenarios and the custom-MCP fallback path
  were not re-tested after the IAM change.
- Nothing here proves behaviour in the **company** Shared VPC, which is a different network
  topology. This lowers the risk of copying the reduction across; it does not eliminate it.

## 6. Rollback

Restore only the identity and role an actual error names. Prefer `roles/compute.networkUser`
over `compute.networkAdmin`, and `roles/dns.peer` over `dns.admin`. One PR, one apply.
