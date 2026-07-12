"""
invoke_agent.py — Test the GCP SRE Agent against real k8s incident scenarios.

Usage:
  python invoke_agent.py                           # run all scenarios
  python invoke_agent.py --scenario 3tier          # single scenario
  python invoke_agent.py --scenario liveness --verbose
  python invoke_agent.py --list                    # list all scenarios
"""

import argparse
import json
import os
import sys
import time

# All three come from the environment. PROJECT_ID and REASONING_ENGINE_ID are
# emitted by `terraform output` after deploying the agent stack — set them via
# `source scripts/init-env.sh` or export them manually before running.
PROJECT   = os.environ.get("PROJECT_ID", "")
REGION    = os.environ.get("REGION", "us-central1")
ENGINE_ID = os.environ.get("REASONING_ENGINE_ID", "")

if not PROJECT or not ENGINE_ID:
    sys.exit(
        "PROJECT_ID and REASONING_ENGINE_ID must be set.\n"
        "  export PROJECT_ID=$(terraform -chdir=iac/agent output -raw project_a_id)\n"
        "  export REASONING_ENGINE_ID=$(terraform -chdir=iac/agent output -raw reasoning_engine_id)\n"
        "or run:  source scripts/init-env.sh"
    )

# ── All test scenarios ──────────────────────────────────────────────────────
SCENARIOS = {

    # ── Simple (namespace: test-incidents) ───────────────────────────────
    "crashloop": {
        "namespace": "test-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "crashloop-pod",
        "severity":  "high",
        "query": "Pod crashloop-pod in test-incidents keeps crashing. Investigate and give root cause.",
    },
    "oomkilled": {
        "namespace": "test-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "oomkilled-pod",
        "severity":  "high",
        "query": "Pod oomkilled-pod in test-incidents is OOMKilled repeatedly. What is happening?",
    },
    "imagepull": {
        "namespace": "test-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "imagepull-pod",
        "severity":  "medium",
        "query": "Pod imagepull-pod in test-incidents cannot pull its image. Investigate.",
    },

    # ── Complex (namespace: test-incidents) ──────────────────────────────
    "rollout": {
        "namespace":  "test-incidents",
        "cluster":    "sre-test-cluster",
        "deployment": "payment-api",
        "severity":   "critical",
        "query": (
            "Deployment payment-api in test-incidents shows 0/1 AVAILABLE for 10 minutes. "
            "Pods are Running but not Ready. Investigate the rollout failure."
        ),
    },
    "init": {
        "namespace": "test-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "inventory-service",
        "severity":  "high",
        "query": (
            "Pod inventory-service in test-incidents has been Init:0/2 for 15 minutes. "
            "The app never started. What is blocking it?"
        ),
    },
    "configmap": {
        "namespace": "test-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "auth-service",
        "severity":  "critical",
        "query": (
            "Pod auth-service in test-incidents is stuck ContainerCreating for 8 minutes. "
            "Users cannot authenticate. Investigate the startup failure."
        ),
    },
    "selector": {
        "namespace": "test-incidents",
        "cluster":   "sre-test-cluster",
        "severity":  "high",
        "query": (
            "notification-svc in test-incidents shows no endpoints. "
            "Pods are Running. Users report 503 errors. "
            "Investigate why traffic is not reaching the pods."
        ),
    },
    "cascading": {
        "namespace":  "test-incidents",
        "cluster":    "sre-test-cluster",
        "deployment": "order-api",
        "severity":   "critical",
        "query": (
            "order-api in test-incidents is CrashLoopBackOff. "
            "Payment orders are failing. Identify the true root cause."
        ),
    },

    # ── Management Demo (namespace: demo-incidents) ───────────────────────
    "liveness": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "web-service",
        "severity":  "high",
        "query": (
            "web-service in demo-incidents restarts every 30 seconds. "
            "Users see intermittent 502 errors. The pod appears healthy but keeps restarting. "
            "What is causing the restarts?"
        ),
    },
    "secret": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "user-service",
        "severity":  "critical",
        "query": (
            "user-service in demo-incidents has been ContainerCreating for 10 minutes. "
            "All user authentication is broken. Investigate the startup failure."
        ),
    },
    "highrestart": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "pod":       "cache-service",
        "severity":  "high",
        "query": (
            "cache-service in demo-incidents has restarted 15 times in the last hour. "
            "The service works for a while then fails. Users see intermittent cache misses. "
            "What is the root cause?"
        ),
    },
    "job": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "severity":  "high",
        "query": (
            "data-export-job in demo-incidents shows BackoffLimitExceeded. "
            "Nightly data export has not run for 3 days. "
            "What is wrong and how do we fix it?"
        ),
    },
    "cronjob": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "severity":  "medium",
        "query": (
            "report-generator CronJob in demo-incidents has failed 3 times in a row. "
            "Management dashboards are not updating. "
            "Investigate why the job keeps failing."
        ),
    },
    "3tier": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "severity":  "critical",
        "query": (
            "The entire website is down. Users are getting 500 errors on the homepage. "
            "web-frontend deployment in demo-incidents is affected. "
            "Investigate the full stack and find the root cause."
        ),
    },
    "progress": {
        "namespace":  "demo-incidents",
        "cluster":    "sre-test-cluster",
        "deployment": "billing-api",
        "severity":   "critical",
        "query": (
            "billing-api deployment in demo-incidents was updated 5 minutes ago "
            "but the rollout has not completed. Billing is broken. "
            "Investigate the deployment failure."
        ),
    },
    "unreachable": {
        "namespace": "demo-incidents",
        "cluster":   "sre-test-cluster",
        "severity":  "high",
        "query": (
            "search-service in demo-incidents returns connection refused for all clients. "
            "The service was working yesterday. Investigate why it is unreachable."
        ),
    },
}

DESCRIPTIONS = {
    "crashloop":    "App exits immediately (exit code 1)",
    "oomkilled":    "Memory limit exceeded → OOMKilled",
    "imagepull":    "Wrong image tag → ImagePullBackOff",
    "rollout":      "Rollout stuck — readiness probe wrong port (9999 vs 8080)",
    "init":         "Init container waiting for missing DB service forever",
    "configmap":    "Pod stuck ContainerCreating — ConfigMap not found",
    "selector":     "Service 0 endpoints — selector doesn't match pod labels",
    "cascading":    "order-api crashes because order-db is OOMKilled (cascade)",
    "liveness":     "Liveness probe kills pod — /health returns HTTP 500",
    "secret":       "Pod stuck ContainerCreating — Secret not found",
    "highrestart":  "Slow memory leak → OOMKilled every 45s → 15+ restarts",
    "job":          "Batch job BackoffLimitExceeded — missing EXPORT_BUCKET env var",
    "cronjob":      "CronJob killed by deadline — external API context deadline exceeded",
    "3tier":        "[FLAGSHIP] web→api→postgres cascade — root cause is DB OOMKilled",
    "progress":     "Deployment ProgressDeadlineExceeded — missing PAYMENT_API_KEY",
    "unreachable":  "Service unreachable — deployment scaled to 0 replicas",
}


def get_agent():
    from google.cloud import aiplatform_v1beta1
    return aiplatform_v1beta1.ReasoningEngineExecutionServiceClient(
        client_options={"api_endpoint": f"{REGION}-aiplatform.googleapis.com"}
    )


def _resource_name() -> str:
    return f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/{ENGINE_ID}"


def create_session(agent) -> str:
    """Create a client-side session ID for grouping related queries.

    We use a UUID as the session identifier. The SREAgent receives this in
    the input payload and logs it for correlation. Memory Bank context from
    prior investigations in the same session is injected automatically.
    """
    import uuid
    return str(uuid.uuid4())[:8]


def run_session_interactive(agent, name: str, verbose: bool = False) -> None:
    """Run an investigation then open an interactive follow-up session.

    Use case: security researcher receives a PagerDuty alert (single-shot),
    then wants to ask follow-up questions without losing investigation context.

    The initial RCA is passed as memory_context in every follow-up query so
    the agent has full incident context without re-investigating from scratch.
    """
    session_id = create_session(agent)
    scenario = SCENARIOS[name]

    print(f"\n  [Session {session_id} starting — running initial investigation]")
    result = run_scenario(agent, name, verbose=verbose, session_id=session_id)

    if result["status"] == "error":
        print("  Initial investigation failed — session aborted.")
        return

    # Build session context from the initial RCA for follow-up queries
    rca_data   = result.get("result", {})
    session_ctx = ""
    if isinstance(rca_data, dict):
        if "rca_report" in rca_data:
            session_ctx = f"PREVIOUS INVESTIGATION (session {session_id}):\n{rca_data['rca_report'][:3000]}"
        elif "executive_summary" in rca_data:
            session_ctx = f"PREVIOUS INVESTIGATION SUMMARY:\n{rca_data['executive_summary']}"

    cluster   = scenario.get("cluster", "sre-test-cluster")
    namespace = scenario.get("namespace", "test-incidents")

    print(f"\n  [Session {session_id} — type follow-up questions, 'exit' to quit]")
    print("  Examples: 'Write the kubectl command to fix this'")
    print("            'What is the blast radius if this spreads?'")
    print("            'Draft a postmortem template for this incident'\n")

    while True:
        try:
            user_input = input("  Follow-up > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Session ended.")
            break

        if not user_input or user_input.lower() in ("exit", "quit", "q"):
            print("  Session ended.")
            break

        followup = {
            "cluster":        cluster,
            "namespace":      namespace,
            "query":          user_input,
            "session_id":     session_id,
            "memory_context": session_ctx,
            "source_type":    "session_followup",
        }

        start = time.time()
        try:
            response = agent.query_reasoning_engine(
                request={"name": _resource_name(), "input": followup}
            )
            from google.protobuf import json_format as _pjf
            res     = _pjf.MessageToDict(response._pb).get("output", {})
            elapsed = time.time() - start

            print(f"\n  Agent [{elapsed:.1f}s]:")
            if isinstance(res, dict):
                if res.get("status") == "blocked":
                    print(f"  Blocked: {res.get('error', '')}")
                elif verbose:
                    print(json.dumps(res, indent=2))
                elif "rca_report" in res:
                    print(res["rca_report"])
                    session_ctx = f"PREVIOUS INVESTIGATION:\n{res['rca_report'][:2000]}"
                elif "executive_summary" in res:
                    print(f"  {res['executive_summary']}")
                    session_ctx += f"\n\nFollow-up: {user_input}\nAnswer: {res.get('executive_summary', '')}"
                else:
                    print(str(res)[:500])
            else:
                print(str(res)[:500])

        except Exception as e:
            print(f"  Error: {e}")
        print()


def run_scenario(agent, name: str, verbose: bool = False, session_id: str = None) -> dict:
    scenario = dict(SCENARIOS[name])  # copy so we can add session_id
    if session_id:
        scenario["session_id"] = session_id

    print(f"\n{'='*65}")
    print(f"  SCENARIO : {name.upper()}")
    print(f"  What     : {DESCRIPTIONS.get(name, '')}")
    if session_id:
        print(f"  Session  : {session_id}")
    print(f"{'='*65}")
    print(f"  Query: {scenario['query'][:90]}...")

    start = time.time()
    try:
        response = agent.query_reasoning_engine(
            request={"name": _resource_name(), "input": scenario}
        )
        # Convert protobuf Struct → plain Python dict
        from google.protobuf import json_format as _pjf
        result = _pjf.MessageToDict(response._pb).get("output", {})
        elapsed = time.time() - start

        print(f"\n  Agent response [{elapsed:.1f}s]:")
        if isinstance(result, dict):
            if result.get("status") == "blocked":
                print(f"  ⚠ BLOCKED by Model Armor: {result.get('error', '')}")
            elif verbose:
                print(json.dumps(result, indent=2))
            else:
                if "rca_report" in result:
                    print()
                    print(result["rca_report"])
                else:
                    if "executive_summary" in result:
                        print(f"\n  SUMMARY: {result['executive_summary']}")
                    for key in ("status", "confidence_band", "tool_calls", "error", "session_id"):
                        if key in result:
                            val = str(result[key])
                            print(f"  {key}: {val[:200]}{'...' if len(val) > 200 else ''}")
        else:
            text = str(result)
            print(text[:500] + ("..." if len(text) > 500 else ""))

        return {"scenario": name, "status": "ok", "elapsed": elapsed, "result": result}

    except Exception as e:
        elapsed = time.time() - start
        print(f"\n  ERROR [{elapsed:.1f}s]: {e}")
        return {"scenario": name, "status": "error", "elapsed": elapsed, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Invoke SRE Agent against live k8s incident scenarios"
    )
    parser.add_argument("--scenario", choices=list(SCENARIOS.keys()), help="Run a single scenario")
    parser.add_argument("--verbose",  action="store_true", help="Print full JSON response")
    parser.add_argument("--list",     action="store_true", help="List all scenarios and exit")
    parser.add_argument("--session",  action="store_true", help="Create an Agent Engine session per scenario (multi-turn)")
    args = parser.parse_args()

    if args.list:
        print("\nAvailable scenarios:\n")
        simple = ["crashloop", "oomkilled", "imagepull"]
        complex_ = ["rollout", "init", "configmap", "selector", "cascading"]
        demo = ["liveness", "secret", "highrestart", "job", "cronjob", "3tier", "progress", "unreachable"]
        for group, names in [("Simple (test-incidents)", simple),
                              ("Complex (test-incidents)", complex_),
                              ("Management Demo (demo-incidents)", demo)]:
            print(f"  [{group}]")
            for n in names:
                print(f"    {n:14} — {DESCRIPTIONS.get(n, '')}")
            print()
        return

    print(f"Connecting to Agent Engine {ENGINE_ID} in {PROJECT}/{REGION}...")
    agent = get_agent()
    print("Connected.")

    # --session + single scenario → interactive REPL for security researchers
    if args.session and args.scenario:
        run_session_interactive(agent, args.scenario, verbose=args.verbose)
        return

    to_run = [args.scenario] if args.scenario else list(SCENARIOS.keys())
    results = []

    for name in to_run:
        sid = create_session(agent) if args.session else None
        r   = run_scenario(agent, name, verbose=args.verbose, session_id=sid)
        results.append(r)
        if len(to_run) > 1:
            time.sleep(1)

    if len(results) > 1:
        print(f"\n{'='*65}")
        print("  SUMMARY")
        print(f"{'='*65}")
        for r in results:
            mark = "✓" if r["status"] == "ok" else "✗"
            note = r.get("error", "")[:50] if r["status"] == "error" else ""
            print(f"  {mark} {r['scenario']:14} [{r['elapsed']:5.1f}s]  {note}")
        ok = sum(1 for r in results if r["status"] == "ok")
        print(f"\n  Passed: {ok}/{len(results)}")


if __name__ == "__main__":
    main()
