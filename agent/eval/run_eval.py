"""
SRE Agent evaluation runner.

Two modes:
  1. LOCAL  — runs agent code directly (no Agent Engine needed). Fast for dev.
  2. REMOTE — calls deployed Vertex AI Agent Engine. Matches production.

Both modes output trajectory metrics and keyword accuracy.
Optional: submit results to Vertex AI Gen AI Evaluation Service.

Usage:
  # Local mode (no GCP needed, just k8s cluster access):
  python -m agent.eval.run_eval --mode local --cases all

  # Remote mode (Agent Engine deployed):
  python -m agent.eval.run_eval --mode remote --engine-id <ENGINE_ID>

  # Submit to Vertex AI Gen AI Evaluation Service:
  python -m agent.eval.run_eval --mode remote --engine-id <ENGINE_ID> --vertex-eval

Reference: https://cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation-agents
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

log = logging.getLogger("sre-agent.eval")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Trajectory scoring ────────────────────────────────────────────

def trajectory_precision(predicted: list[str], expected: list[str]) -> float:
    """Fraction of predicted tools that appear in expected set."""
    if not predicted:
        return 0.0
    expected_set = set(expected)
    correct = sum(1 for t in predicted if t in expected_set)
    return correct / len(predicted)


def trajectory_recall(predicted: list[str], expected: list[str]) -> float:
    """Fraction of expected tools that were called (any order)."""
    if not expected:
        return 1.0
    predicted_set = set(predicted)
    found = sum(1 for t in expected if t in predicted_set)
    return found / len(expected)


def trajectory_in_order_match(predicted: list[str], expected: list[str]) -> bool:
    """True if all expected tools appear in predicted in the correct relative order."""
    if not expected:
        return True
    it = iter(predicted)
    return all(e in it for e in expected)


def keyword_accuracy(root_cause: str, keywords: list[str]) -> float:
    """Fraction of expected keywords found in the root cause string (case-insensitive)."""
    if not keywords:
        return 1.0
    root_lower = root_cause.lower()
    found = sum(1 for k in keywords if k.lower() in root_lower)
    return found / len(keywords)


def score_case(result: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Score one agent result against a golden case."""
    summary = result.get("final_summary", result)
    predicted_tools = summary.get("tools_called", [])
    root_cause = summary.get("likely_root_cause", "")
    confidence = summary.get("confidence_score", 0.0)
    band = summary.get("confidence_band", "escalate")

    expected_traj = case["expected_trajectory"]
    expected_kw   = case["expected_keywords"]

    prec   = trajectory_precision(predicted_tools, expected_traj)
    recall = trajectory_recall(predicted_tools, expected_traj)
    order  = trajectory_in_order_match(predicted_tools, expected_traj)
    kw_acc = keyword_accuracy(root_cause, expected_kw)
    conf_ok = confidence >= case.get("expected_confidence_min", 0.0)

    passed = recall >= 0.5 and kw_acc >= 0.5 and conf_ok

    return {
        "case_id":              case["id"],
        "passed":               passed,
        "trajectory_precision": round(prec, 3),
        "trajectory_recall":    round(recall, 3),
        "in_order_match":       order,
        "keyword_accuracy":     round(kw_acc, 3),
        "confidence":           confidence,
        "confidence_band":      band,
        "confidence_ok":        conf_ok,
        "predicted_tools":      predicted_tools,
        "expected_tools":       expected_traj,
        "root_cause":           root_cause[:200],
    }


# ── Local mode: run agent graph directly ─────────────────────────

def run_local(case: dict[str, Any]) -> dict[str, Any]:
    """Run agent locally using the LangGraph graph directly."""
    from agent.graph import build_graph
    from agent.state import get_initial_state

    payload = {
        "user_query":     case["payload"]["user_query"],
        "incident":       case["payload"].get("incident", {}),
        "resource_hints": case["payload"].get("resource_hints", {}),
    }

    graph = build_graph()
    initial_state = get_initial_state(payload)

    start = time.time()
    final_state = graph.invoke(initial_state)
    elapsed = round(time.time() - start, 2)

    summary = final_state.get("final_summary", {})
    summary["_latency_seconds"] = elapsed
    return {"final_summary": summary}


# ── Remote mode: call deployed Agent Engine ───────────────────────

def run_remote(case: dict[str, Any], engine_id: str) -> dict[str, Any]:
    """Call deployed Vertex AI Agent Engine via execution client (SDK 1.158+)."""
    from google.cloud import aiplatform_v1beta1
    from google.protobuf import json_format as _pjf

    project_id = os.environ.get("PROJECT_ID", "your-gcp-project-id")
    region     = os.environ.get("REGION", "us-central1")

    client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )
    resource_name = f"projects/{project_id}/locations/{region}/reasoningEngines/{engine_id}"

    hints = case["payload"].get("resource_hints", {})
    payload = {
        "query":     case["payload"]["user_query"],
        "cluster":   hints.get("cluster", ""),
        "namespace": hints.get("namespace", ""),
        "pod":       hints.get("pod", ""),
        "severity":  case["payload"].get("incident", {}).get("severity", "medium"),
    }

    start = time.time()
    response = client.query_reasoning_engine(
        request={"name": resource_name, "input": payload}
    )
    elapsed = round(time.time() - start, 2)

    result = _pjf.MessageToDict(response._pb).get("output", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {"likely_root_cause": result}

    result["_latency_seconds"] = elapsed
    return {"final_summary": result}


# ── Vertex AI Gen AI Evaluation Service ──────────────────────────

def run_vertex_eval(results: list[dict[str, Any]], cases: list[dict[str, Any]], experiment_name: str) -> None:
    """
    Submit results to Vertex AI Gen AI Evaluation Service.
    Results appear in Agent Platform → Evaluation → Experiments tab.

    Requires: pip install google-cloud-aiplatform[evaluation] pandas
    Status: Public Preview
    Ref: https://cloud.google.com/vertex-ai/generative-ai/docs/models/evaluation-agents

    Agent Platform predefined metrics used:
      - trajectory_precision          : fraction of predicted tools in expected set
      - trajectory_recall             : fraction of expected tools that were called
      - trajectory_in_order_match     : expected tools appear in correct order
      - trajectory_any_order_match    : all expected tools appear (any order)
    """
    try:
        import pandas as pd
        import vertexai
        from vertexai.preview.evaluation import EvalTask
    except ImportError:
        log.error("Gen AI Evaluation requires: pip install google-cloud-aiplatform[evaluation] pandas")
        return

    project_id = os.environ.get("PROJECT_ID", "your-gcp-project-id")
    region     = os.environ.get("REGION", "us-central1")
    vertexai.init(project=project_id, location=region)

    rows = []
    for result, case in zip(results, cases):
        summary = result.get("final_summary", result)
        # Merge tool_history into tools_called for compatibility
        tools_called = (
            summary.get("tools_called")
            or [t.get("tool") for t in summary.get("tool_history", []) if t.get("tool")]
        )
        rows.append({
            "prompt":               case["payload"]["user_query"],
            "reference_trajectory": json.dumps(case["expected_trajectory"]),
            "predicted_trajectory": json.dumps(tools_called),
            "response":             summary.get("likely_root_cause", summary.get("executive_summary", "")),
            "reference":            " ".join(case["expected_keywords"]),
        })

    dataset = pd.DataFrame(rows)
    exp_name = f"{experiment_name}-{int(time.time())}"

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

    log.info("Submitting to Vertex AI Gen AI Evaluation Service (experiment: %s) ...", exp_name)
    eval_result = eval_task.evaluate()

    print("\n=== Vertex AI Evaluation Results ===")
    print(f"  Experiment: {exp_name}")
    print("  View in Agent Platform → Evaluation → Experiments")
    print()
    print(eval_result.summary_metrics)
    if hasattr(eval_result, "metrics_table"):
        print(eval_result.metrics_table)


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SRE Agent evaluation runner")
    parser.add_argument("--mode",        choices=["local", "remote"], default="local")
    parser.add_argument("--engine-id",   default=os.environ.get("REASONING_ENGINE_ID", ""))
    parser.add_argument("--cases",       default="all", help="Case ID or 'all'")
    parser.add_argument("--list-cases",  action="store_true", help="List all golden case IDs and exit")
    parser.add_argument("--vertex-eval", action="store_true", help="Submit to Vertex AI Eval Service (results appear in Agent Platform → Evaluation tab)")
    parser.add_argument("--experiment",  default="sre-agent-eval", help="Experiment name for Agent Platform Evaluation tab")
    parser.add_argument("--output",      default="eval_results.json", help="JSON output file")
    args = parser.parse_args()

    from agent.eval.golden_cases import GOLDEN_CASES

    if args.list_cases:
        print("\nAvailable golden cases:\n")
        for c in GOLDEN_CASES:
            print(f"  {c['id']:25} — {c['payload']['user_query'][:60]}...")
        print()
        return

    if args.cases == "all":
        cases = GOLDEN_CASES
    else:
        cases = [c for c in GOLDEN_CASES if c["id"] == args.cases]
        if not cases:
            log.error("Unknown case ID: %s. Available: %s", args.cases, [c["id"] for c in GOLDEN_CASES])
            sys.exit(1)

    if args.mode == "remote" and not args.engine_id:
        log.error("--engine-id required for remote mode (or set REASONING_ENGINE_ID env var)")
        sys.exit(1)

    results = []
    scores  = []

    for case in cases:
        log.info("Running case: %s (mode=%s)", case["id"], args.mode)
        try:
            if args.mode == "local":
                result = run_local(case)
            else:
                result = run_remote(case, args.engine_id)

            score = score_case(result, case)
            results.append(result)
            scores.append(score)

            status = "PASS" if score["passed"] else "FAIL"
            log.info(
                "%s %s | precision=%.2f recall=%.2f kw=%.2f conf=%.2f(%s) tools=%s",
                status, case["id"],
                score["trajectory_precision"],
                score["trajectory_recall"],
                score["keyword_accuracy"],
                score["confidence"],
                score["confidence_band"],
                score["predicted_tools"],
            )

        except Exception as e:
            log.error("Case %s failed with exception: %s", case["id"], e)
            scores.append({"case_id": case["id"], "passed": False, "error": str(e)})

    # Summary
    total         = len(scores)
    passed        = sum(1 for s in scores if s.get("passed"))
    avg_precision = sum(s.get("trajectory_precision", 0) for s in scores) / max(total, 1)
    avg_recall    = sum(s.get("trajectory_recall", 0) for s in scores) / max(total, 1)
    avg_kw        = sum(s.get("keyword_accuracy", 0) for s in scores) / max(total, 1)

    print(f"\n{'='*60}")
    print(f"EVAL SUMMARY: {passed}/{total} passed")
    print(f"  avg trajectory_precision : {avg_precision:.3f}")
    print(f"  avg trajectory_recall    : {avg_recall:.3f}")
    print(f"  avg keyword_accuracy     : {avg_kw:.3f}")
    print(f"{'='*60}")

    failed = [s["case_id"] for s in scores if not s.get("passed")]
    if failed:
        print(f"FAILED cases: {failed}")

    with open(args.output, "w") as f:
        json.dump({"summary": {"passed": passed, "total": total}, "scores": scores}, f, indent=2)
    log.info("Results written to %s", args.output)

    if args.vertex_eval and results:
        run_vertex_eval(results, cases, args.experiment)

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
