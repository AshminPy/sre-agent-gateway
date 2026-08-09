# End-to-End Honest Baseline Test — 2026-08-09

> **Purpose:** one real, unguided investigation by the deployed SRE Agent against a realistic,
> multi-symptom incident with a hidden root cause, run through the exact same path a real
> PagerDuty-triggered investigation would use. No agent code was changed before or during this
> test — this is an honest baseline, not a demo tuned to pass.
> **Result: PARTIAL PASS.** Real, well-evidenced tool use and reasoning; wrong final root cause,
> for a precise, reproducible reason documented below (Section 10).

---

## 1. Test scenario and hidden root cause

**Workload:** `notification-relay` — a 2-replica Kubernetes `Deployment` + `Service`, deployed
fresh into the live `test-incidents` namespace on `sre-test-cluster` (GKE Autopilot).

**Why this scenario, and why it's not a basic case:**
- Not OOMKilled, not ImagePullBackOff, not CrashLoopBackOff. The container is genuinely
  healthy — it starts, keeps running, and (after one cold-start hiccup, see Section 2) never
  restarts again. Pods stay `Running` the entire test.
- **More than one plausible cause was deliberately built in:**
  1. **The real, hidden root cause:** the `readinessProbe` targets port `8080`, but the
     container's actual HTTP server only listens on port `9090` (matching the `livenessProbe`,
     which is correctly configured on `9090` — this is why the container is never restarted).
     Readiness can never succeed. The `Service`'s `targetPort` is *also* misconfigured to
     `8080` — a realistic, common mistake (someone changed the app's real listening port and
     forgot to update both the probe and the Service).
  2. **A deliberate red herring:** the container prints a misleading startup log line —
     `WARN: cache backend unreachable at redis-cache:6379, falling back to local in-memory
     cache -- this is expected in this environment` — with no real `redis-cache` resource
     anywhere in the cluster. A shallow investigation could plausibly blame this.
- **Symptoms an on-call engineer would actually see:** the Service has zero endpoints, so all
  traffic to it fails; pods report `Running` in `kubectl get pods` (easy to misread as
  "healthy"); no crash loop, no OOM, no image-pull error — genuinely non-obvious.

**Full manifest:** reproduced in Appendix A below.

## 2. Complete test flow

| Step | What happened | Evidence |
|---|---|---|
| 1 | Confirmed `test-incidents` namespace was empty before starting | `kubectl get pods,svc -n test-incidents` → `No resources found` |
| 2 | Applied the manifest (2 replicas) | `kubectl apply -f notification-relay.yaml` → `deployment.apps/notification-relay created`, `service/notification-relay created` |
| 3 | **First attempt blocked by real infrastructure, unrelated to the agent** — both pods stuck `Pending` for 4+ minutes: `Warning FailedScaleUp ... GCE quota exceeded` in both `us-central1-a` and `us-central1-b`. Checked the project's actual regional CPU quota directly (`gcloud compute regions describe`) — 2 of 32 CPUs in use, plenty of headroom — so this was a transient zonal capacity issue, not a real account quota limit. Cleaned up the stuck pods and retried. | See Appendix B |
| 4 | Retry succeeded — pods scheduled immediately | `kubectl get pods` → both `Running` |
| 5 | One unintended cold-start hiccup: both pods failed their **liveness** probe once (`connection refused` on port 9090) ~2 minutes after container start and were restarted once (`RESTARTS: 1`). This was a container-startup-timing artifact of my own test manifest, not part of the intended design. Confirmed the restart count did **not** climb further — pods stabilized at `RESTARTS: 1`, `Running`, `0/1 Ready` for the remainder of the test. This is a genuine limitation of this specific test run, disclosed here rather than hidden. | See Appendix B |
| 6 | Confirmed target broken state: `0/1 Ready` on both pods, zero restarts-in-progress, `Service` endpoints list empty, readiness-probe-failure events accumulating on port 8080 | See Appendix B |
| 7 | Captured independent ground-truth evidence (`kubectl describe`, full events, pod logs) **before** invoking the agent | Full text in Appendix B |
| 8 | Triggered the deployed agent via the same real API path `invoke_agent.py`/the smoke test use (`aiplatform_v1beta1.ReasoningEngineExecutionServiceClient.query_reasoning_engine`) against the live Reasoning Engine (`87056581908234240`) — **not** a local/simulated run | See Appendix C for the exact query payload |
| 9 | Captured the complete, unedited raw JSON response | `docs/testing/e2e-test-2026-08-09-raw-response.json` (co-located with this report) |
| 10 | Pulled the full evidence objects from the real evidence GCS bucket (`gs://sreagent-t2-demo-evidence/run_20260809_221019_rijj/`) to see exact tool arguments, not just IDs | Section 4 |
| 11 | Deleted the Deployment and Service | `kubectl delete -f notification-relay.yaml` → both deleted |
| 12 | Confirmed `test-incidents` namespace fully empty again | `kubectl get pods,svc,deploy -n test-incidents` → `No resources found` |

**The query sent to the agent** (symptom-only, no cause hinted, matching how a real on-call
engineer would phrase a page):

> "The notification-relay service in test-incidents is not receiving any traffic. Users report
> notifications are not being delivered. The pods appear to be up but something seems wrong.
> Please investigate and identify the root cause."

## 3. Agent routing and tools called

| Field | Value | Evidence |
|---|---|---|
| Cluster routing method | `exact_id` — `'sre-test-cluster' matched a registered cluster id exactly` | `investigation_context.cluster_routing_reason` |
| MCP source selected | `gke_remote_mcp` (correct — `sre-test-cluster` is a GKE cluster) | `investigation_context.primary_mcp_source` |
| Total tool calls | **3** | `observability.tools_called` |
| Loop iterations | 0 (single pass, no re-planning loop) | `rca_report`, `observability.loop_exit_reason: "confidence_sufficient"` |
| Investigation latency | 97.9s total (92.7s model latency, 3.28s MCP latency) | `observability` |
| Tokens / cost | 8,453 tokens ($0.0022) | `observability` |

**Exact tool calls made** (pulled from the real evidence store, not the summary text):

| # | Tool | Arguments used | Result |
|---|---|---|---|
| 1 | `list_k8s_events` | `namespace=test-incidents`, **`name=notification-relay`, `resourceType=pod`** | Empty event list |
| 2 | `get_k8s_resource` | `resourceType=pod`, `name=notification-relay`, `namespace=test-incidents` | `Error from server (NotFound): Pod "notification-relay" not found` |
| 3 | `describe_k8s_resource` | `resourceType=replicaset`, `name=notification-relay`, `namespace=test-incidents` | `Error from server (NotFound): ReplicaSet "notification-relay" not found` |

All 3 calls used the **exact literal name** `notification-relay` — never the real, generated pod
name (`notification-relay-5fb84bdbd4-dmxg9`/`-lqq2b`). This is the central finding of this test —
see Section 10 for the full analysis.

## 4. Evidence collected

Pulled directly from `gs://sreagent-t2-demo-evidence/run_20260809_221019_rijj/` (real GCS
objects, not the local response):

```json
// ev_001 -- list_k8s_events, scoped to name=notification-relay, resourceType=pod
{
  "tool": "list_k8s_events",
  "sanitized": { "events": "LAST SEEN   TYPE   REASON   OBJECT   MESSAGE\n" }  // empty
}

// ev_002 -- get_k8s_resource
{
  "tool": "get_k8s_resource",
  "sanitized": { "output": "\nError from server (NotFound): Pod \"notification-relay\" not found" }
}

// ev_003 -- describe_k8s_resource
{
  "tool": "describe_k8s_resource",
  "sanitized": { "description": "\nError from server (NotFound): ReplicaSet \"notification-relay\" not found" }
}
```

**What the agent never collected:** any event, log, or resource query using the pod's *real*
name; a `Deployment`-level lookup (`get_k8s_resource(resourceType=deployment,
name=notification-relay)` — which **would have succeeded**, since Deployment objects keep the
exact caller-given name, unlike Pods/ReplicaSets); a namespace-wide, unfiltered
`list_k8s_events` call (the tool supports this — see Section 10); the Service's endpoint list;
or the container logs (which contained the red-herring cache warning — the agent never got far
enough to encounter it).

## 5. Exact, unedited RCA produced by the agent

Full raw JSON: `docs/testing/e2e-test-2026-08-09-raw-response.json` (saved alongside this
report, byte-for-byte as returned by the API). The `rca_report` field, verbatim:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SRE INCIDENT ROOT CAUSE ANALYSIS REPORT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  INCIDENT DETAILS
    Report ID     : sre-rca-20260809-221019-rijj
    Generated At  : 2026-08-09T22:11:57Z
    Investigation : 97.9s  |  Tool calls: 3  |  Loops: 0
    Cluster       : sre-test-cluster
    Namespace     : test-incidents
    Workload      : notification-relay
    Severity      : HIGH
    Incident Type : Unknown

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1.  EXECUTIVE SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Incident type: Unknown. Root cause: The 'notification-relay' pod and its
  associated ReplicaSet do not exist in the 'test-incidents' namespace,
  indicating the deployment was either deleted or never created (ev_002,
  ev_003). Confidence: REVIEW. Outcome: PROBABLE. Recommended action:
  Verify the 'notification-relay' Deployment is missing by running:
  `kubectl get deployment notification-relay -n test-incidents
  --context=sre-test-cluster`. If the Deployment is missing, re-apply the
  correct Kubernetes manifest for the 'notification-relay' service to the
  'sre-test-cluster'. Human review recommended before actioning.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  2.  ROOT CAUSE ANALYSIS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Primary Cause:
    The 'notification-relay' pod and its associated ReplicaSet do not
    exist in the 'test-incidents' namespace, indicating the deployment
    was either deleted or never created (ev_002, ev_003).

  Reasoning trace (verbatim, from working_theory/reasoning_trace):
    1. The initial report stated the service was not receiving traffic,
       but pods 'appeared' to be up. This suggested a subtle failure.
    2. First, I checked for the pod 'notification-relay' directly. The
       API server returned 'NotFound' (ev_002), contradicting the initial
       report and indicating the pod does not exist.
    3. To understand why the pod was missing, I checked for its
       controlling ReplicaSet, also named 'notification-relay'. The API
       server again returned 'NotFound' (ev_003).
    4. The absence of both the pod and its ReplicaSet rules out transient
       pod issues (like a crash loop) and points to a missing
       higher-level controller, likely the Deployment.
    5. The conclusion is that the entire application is not deployed,
       which is why no traffic is being received.

  Hypotheses considered:
    hyp_001 [ELIMINATED]: "The pod exists but is in a crash loop or error
      state, preventing it from serving traffic." (contradicted by ev_002)
    hyp_002 [WEAKENED, not eliminated]: "The pod exists but has a
      different name, managed by a deployment named 'notification-relay'."
      Missing evidence noted by the agent itself: "A 'kubectl get
      deployment notification-relay' command would confirm or deny the
      existence of the parent deployment." (contradicted by ev_003, but
      ev_003 only checked the ReplicaSet, not the Deployment)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  3.  CONFIDENCE BREAKDOWN
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Outcome                    : PROBABLE
  Investigation Completeness : 0.60  (complete_with_gaps)
  Root-Cause Confidence      : 0.65  (review_required)
  Completeness gaps:
    • Missing required evidence domain(s): kubernetes_status
  Root-cause reasons:
    • 1/3 claim(s) are inference or hypothesis, not directly observed
    • Only 1.0 independently-weighted evidence source(s) behind the claim
    • Missing required evidence for this incident type: kubernetes_status
  Policy version              : 1.0.0-uncalibrated

  AI Confidence   : 65%  (Band: REVIEW)
  Human Review    : REVIEW_RECOMMENDED
  Model           : Gemini 2.5 Flash via Vertex AI Agent Engine
  Tokens          : 8453  (est. cost: $0.002176)

  INVESTIGATION COMPLETE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Full claim-level detail** (from `summary.claims`, all marked `grounding_status: "grounded"`):

| Claim | Type | Support |
|---|---|---|
| "The pod named 'notification-relay' was not found in the 'test-incidents' namespace" | observed_fact | ev_002 |
| "The ReplicaSet for 'notification-relay' was not found in the 'test-incidents' namespace" | observed_fact | ev_003 |
| "The absence of both the pod and its ReplicaSet strongly suggests the entire deployment for 'notification-relay' is missing, not just a transient pod issue" | supported_inference | ev_002, ev_003 |

## 6. Expected RCA (ground truth, from my own independent kubectl investigation)

The real root cause: **`notification-relay`'s Deployment exists and its 2 pods are genuinely
`Running` and healthy** (liveness probe on port 9090 passing). The **readiness probe is
misconfigured to check port 8080**, which nothing listens on — so the pods can never become
`Ready`, the Service (also misconfigured to `targetPort: 8080`) has zero endpoints, and all
traffic to `notification-relay` fails even though the application itself is up. Confirmed
directly:

```
$ kubectl get events -n test-incidents --field-selector involvedObject.name=<real-pod-name>
Warning  Unhealthy  Readiness probe failed: Get "http://10.1.0.19:8080/healthz":
  dial tcp 10.1.0.19:8080: connect: connection refused
```

A correct investigation would have: found the 2 real pods (via label selector or Deployment
lookup), seen `0/1 Ready` with `0` recent restarts (ruling out crash/OOM), pulled the pod events
and found the repeated readiness-probe connection-refused messages on port 8080, checked the
container's actual listening port (9090, visible in logs or via `describe pod`'s container
port spec), and concluded: readiness probe (and Service `targetPort`) point at the wrong port.

## 7. Comparison between actual and expected results

| Dimension | Expected | Actual | Match? |
|---|---|---|---|
| Cluster/MCP routing | `sre-test-cluster` → `gke_remote_mcp` | Exactly this | ✅ |
| Workload located | Found via label/Deployment lookup | Never found — exact pod/ReplicaSet name lookup failed | ❌ |
| Root cause | Readiness probe / Service `targetPort` misconfigured to wrong port | "The deployment doesn't exist / was never created" | ❌ Wrong |
| Reasoning quality | N/A | Coherent, correctly eliminated the crash-loop hypothesis from real evidence (ev_002), correctly generated (but then wrongly abandoned) the "different pod name" hypothesis | 🟡 Partial credit |
| Evidence grounding | N/A | All 3 claims correctly grounded in real evidence IDs — no hallucinated facts | ✅ |
| Confidence calibration | Should be low/uncertain given the wrong conclusion | 65%, band `REVIEW` — correctly flagged for human review, did not overclaim certainty | ✅ Honest about uncertainty |
| Outcome label | Should not be "resolved" | `PROBABLE` (not `CONFIRMED`) | ✅ Appropriately hedged |
| Recommended action | Investigate probe/port config | "Re-apply the missing Deployment manifest" | ❌ Wrong (would be a no-op — the Deployment already exists) |

## 8. Confidence and completeness scores

**Root Cause Confidence: 0.65 (band: `review_required`)**
| Component | Value |
|---|---|
| `direct_support` | 0.667 |
| `independent_corroboration` | 0.5 |
| `missing_evidence_penalty` | 0.1 |
| `alternative_hypothesis_penalty` | 0.0 |
| `contradiction_penalty` | 0.0 |
| `claim_grounding` | 1.0 |
| `time_correlation` | 1.0 |
| `resource_identity_match` | 1.0 |

**Investigation Completeness: 0.60 (band: `complete_with_gaps`)**
| Component | Value |
|---|---|
| `routing_confirmed` | 1.0 |
| `freshness` | 1.0 |
| `tool_success` | 1.0 |
| `iteration_budget` | 1.0 |
| `required_evidence_coverage` | **0.0** |
| `identity_confirmed` | 1.0 |
| Missing required domain | `kubernetes_status` |

Both scores are self-consistent with the agent's own actual behavior — `required_evidence_coverage
= 0.0` and `missing_required_domains: [kubernetes_status]` accurately reflect that it never
actually retrieved live pod/container status data (every attempt returned `NotFound`). The
scoring system did **not** overclaim confidence given what it actually found — this part worked
correctly. See [Confidence Scoring](../architecture/confidence.md) for how these are computed.

## 9. Pass, partial pass, or fail

**PARTIAL PASS.**

- **PASS** on: correct cluster/MCP routing, zero hallucinated evidence, honest confidence
  scoring (didn't overclaim), appropriately hedged outcome label (`PROBABLE`, not
  `CONFIRMED`), didn't get misled by the planted red-herring log line (never had the chance to
  see it, which is itself informative — see Section 10).
- **FAIL** on: the actual root cause. The recommended action ("re-apply the missing Deployment
  manifest") would have been actively unhelpful in a real incident — the Deployment was never
  missing, and following that advice would waste on-call time before anyone re-checked reality.

## 10. Gaps or incorrect behaviour found

**Root cause of the wrong conclusion, precisely traced through the real code, not guessed:**

1. **The initial `pod` hint I supplied (`"pod": "notification-relay"`, matching the Deployment's
   name) was threaded unmodified into every subsequent tool call**, including the *one* tool
   that could have gone broader. Confirmed in `agent/mcp_client.py:_build_gke_args()`:
   `list_k8s_events` only adds a `name`/`resourceType` filter `if pod_name:` — if no pod name
   filter is applied, `list_k8s_events` returns **all** events in the namespace. Because a
   `pod_name` was present (from my hint, never verified against reality), the very first tool
   call was scoped to `name=notification-relay`, silently missing every real event (including
   the readiness-probe failures) that existed under the real, differently-named pods.
2. **`get_k8s_resource`/`describe_k8s_resource` genuinely require an exact resource name** —
   confirmed in the same function, marked `(req)` in Google's own MCP API — there is no
   wildcard or label-selector mode for these two tools. This part is a real, documented
   platform constraint, not an agent mistake.
3. **The agent correctly generated the right hypothesis** ("the pod exists but has a different
   name, managed by a deployment named 'notification-relay'") **and correctly identified the
   exact missing evidence that would resolve it** — its own `evidence_gaps` field says almost
   verbatim: *"A `kubectl get deployment notification-relay` command would confirm or deny the
   existence of the parent deployment."* **It never made that call.** `describe_k8s_resource`/
   `get_k8s_resource` both support an overridable `resourceType` argument (defaults to `pod`,
   but accepts `deployment`) — and a `Deployment` object keeps the exact name the caller gave
   it (unlike Pods/ReplicaSets, which get a generated suffix). This call was available, would
   have succeeded, and the agent identified it as necessary — but stopped anyway.
4. **The loop stopped with `loop_exit_reason: "confidence_sufficient"`** at 0.65 confidence,
   despite the agent's own `evidence_gaps` explicitly naming an unresolved, resolvable gap.
   This is a real confidence-threshold/stopping-policy question, not a tool-availability
   problem — the investigation had budget left (0 of its iteration budget used, single-pass)
   and a concrete, cheap, available next step it had already identified.
5. **The red-herring log line was never encountered** — the agent never got far enough to call
   `get_k8s_logs`. This means the "does it chase a red herring" part of this test's design is
   genuinely **unproven, not passed** — a fair, honest gap to disclose rather than claim a false
   win.

**None of this is a fabrication or hallucination problem** — every claim the agent made was
accurately grounded in the real (if incomplete) evidence it gathered. This is a **stopping-too-
early / under-using-available-tools** problem, not a truthfulness problem.

## 11. Recommended improvements

*(Not implemented — reporting only, per instruction.)*

1. **Don't propagate an unverified caller-supplied `pod`/resource hint directly into tool-call
   name filters without first confirming it resolves.** At minimum, `list_k8s_events` should be
   tried unscoped (namespace-wide) if a name-scoped call returns empty/NotFound, before
   concluding anything about the resource's existence.
2. **When a Pod and ReplicaSet lookup both return NotFound for a name that came from a
   Deployment-shaped hint, check the Deployment itself before concluding "doesn't exist."** The
   agent already reasons its way to this exact idea (see `hyp_002` and `evidence_gaps` above) —
   this is a case of the agent's own stated next step not being executed, not a missing
   capability.
3. **Revisit whether `loop_exit_reason: "confidence_sufficient"` should trigger at 0.65/`review`
   band when the agent's own `evidence_gaps` field is non-empty and names a concrete, cheap,
   available next action.** This is exactly the kind of real data point the confidence-
   calibration work (backlog item #3, not started per standing instruction) should be informed
   by — flagging it here as evidence for that future discussion, not starting that work now.
4. **Re-run this same scenario after any fix**, plus a second scenario that actually reaches the
   log-reading stage, to close the untested "does it get misled by a red herring in logs"
   question this run left open.

## 12. Cleanup confirmation

```
$ kubectl delete -f notification-relay.yaml
deployment.apps "notification-relay" deleted
service "notification-relay" deleted

$ kubectl get pods,svc,deploy -n test-incidents
No resources found in test-incidents namespace.
```

Confirmed empty. No test resources, no orphaned pods, no lingering Services left in the cluster.

---

## Appendix A — full manifest used

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: notification-relay
  namespace: test-incidents
  labels:
    app: notification-relay
    incident-type: e2e-honest-baseline-test
spec:
  replicas: 2
  selector:
    matchLabels:
      app: notification-relay
  template:
    metadata:
      labels:
        app: notification-relay
    spec:
      containers:
        - name: notification-relay
          image: python:3.11-slim
          command: ["python3", "-c"]
          args:
            - |
              import http.server, socketserver, sys, time
              print("notification-relay starting up...", flush=True)
              print("WARN: cache backend unreachable at redis-cache:6379, falling back to local in-memory cache -- this is expected in this environment", flush=True)
              time.sleep(2)
              print("notification-relay: local HTTP server ready on port 9090", flush=True)
              class H(http.server.SimpleHTTPRequestHandler):
                  def do_GET(self):
                      self.send_response(200)
                      self.end_headers()
                      self.wfile.write(b'{"status":"ok"}')
                  def log_message(self, fmt, *args):
                      pass
              with socketserver.TCPServer(("", 9090), H) as httpd:
                  httpd.serve_forever()
          ports:
            - containerPort: 9090
          resources:
            requests: {memory: "64Mi", cpu: "50m"}
            limits: {memory: "128Mi", cpu: "200m"}
          readinessProbe:
            httpGet: {path: /healthz, port: 8080}
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 3
          livenessProbe:
            httpGet: {path: /healthz, port: 9090}
            initialDelaySeconds: 10
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: notification-relay
  namespace: test-incidents
spec:
  selector: {app: notification-relay}
  ports:
    - port: 80
      targetPort: 8080
```

## Appendix B — ground-truth evidence (captured before invoking the agent)

**Pods:**
```
NAME                                  READY   STATUS    RESTARTS       AGE
notification-relay-5fb84bdbd4-dmxg9   0/1     Running   1 (4m5s ago)   5m16s
notification-relay-5fb84bdbd4-lqq2b   0/1     Running   1 (4m5s ago)   5m16s
```

**Service endpoints:**
```
NAME                 ENDPOINTS   AGE
notification-relay               5m16s
```
(empty — zero endpoints)

**Key events (chronological):**
```
Warning  FailedScaleUp     Node scale up in zones us-central1-b failed: GCE quota exceeded.  [FIRST ATTEMPT, retried]
Warning  FailedScaleUp     Node scale up in zones us-central1-a failed: GCE quota exceeded.  [FIRST ATTEMPT, retried]
Normal   Killing           Container notification-relay failed liveness probe, will be restarted
Warning  Unhealthy         Liveness probe failed: Get "http://10.1.0.19:9090/healthz": dial tcp 10.1.0.19:9090: connect: connection refused
Warning  Unhealthy         Readiness probe failed: Get "http://10.1.0.19:8080/healthz": dial tcp 10.1.0.19:8080: connect: connection refused
Warning  Unhealthy         Readiness probe failed: Get "http://10.1.0.18:8080/healthz": dial tcp 10.1.0.18:8080: connect: connection refused
```

**Pod logs (both replicas identical):**
```
notification-relay starting up...
WARN: cache backend unreachable at redis-cache:6379, falling back to local in-memory cache -- this is expected in this environment
notification-relay: local HTTP server ready on port 9090
```

Full untruncated capture (`kubectl describe` for deployment/pods/service, complete event list,
complete logs): `docs/testing/e2e-test-2026-08-09-ground-truth.txt` (saved alongside this
report).

## Appendix C — exact agent invocation

```python
payload = {
    "namespace": "test-incidents",
    "cluster": "sre-test-cluster",
    "pod": "notification-relay",
    "severity": "high",
    "query": "The notification-relay service in test-incidents is not receiving any traffic. "
             "Users report notifications are not being delivered. The pods appear to be up but "
             "something seems wrong. Please investigate and identify the root cause.",
}
client.query_reasoning_engine(
    request={
        "name": "projects/sreagent-t2-demo/locations/us-central1/reasoningEngines/87056581908234240",
        "input": payload,
    }
)
```
Same real API (`aiplatform_v1beta1.ReasoningEngineExecutionServiceClient`) used by
`invoke_agent.py` and `scripts/smoke_test.sh` — the actual production invocation path, not a
local/simulated LangGraph run.
