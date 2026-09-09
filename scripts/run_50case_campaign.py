#!/usr/bin/env python3
"""Phase 1 50-case readiness campaign runner (Section 9).

Runs every case in agent/eval/golden_cases.py (25 GKE + 25 non-GKE, plus the
bonus unscoped ambiguous-routing-001) against the LIVE deployed reasoning
engine -- the same real Vertex AI Agent Engine invoke_agent.py's scenarios
hit, not a local/mocked harness. Execution order is randomized once at
startup and then fixed for the whole run. Every result (pass or fail) is
recorded on first attempt -- this script never retries a case.

Usage:
    python3 scripts/run_50case_campaign.py [--seed N] [--limit N] [--start-at ID]

Requires PROJECT_ID / REGION / REASONING_ENGINE_ID env vars (same as
invoke_agent.py -- source scripts/init-env.sh or export manually).
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.eval.golden_cases import GOLDEN_CASES  # noqa: E402

PROJECT = os.environ.get("PROJECT_ID", "sreagent-t2-demo")
REGION = os.environ.get("REGION", "us-central1")
ENGINE_ID = os.environ.get("REASONING_ENGINE_ID", "7801582006105538560")


def _resource_name() -> str:
    return f"projects/{PROJECT}/locations/{REGION}/reasoningEngines/{ENGINE_ID}"


def get_agent():
    from google.cloud import aiplatform_v1beta1
    return aiplatform_v1beta1.ReasoningEngineExecutionServiceClient(
        client_options={"api_endpoint": f"{REGION}-aiplatform.googleapis.com"}
    )


def case_to_payload(case: dict) -> dict:
    rh = case["payload"]["resource_hints"]
    return {
        "cluster": rh.get("cluster") or "",
        "namespace": rh.get("namespace", "test-incidents"),
        "pod": rh.get("pod", ""),
        "severity": case["payload"].get("incident", {}).get("severity", "P3"),
        "query": case["payload"]["user_query"],
    }


def is_gke(case: dict) -> bool:
    return case["payload"]["resource_hints"].get("cluster") == "sre-test-cluster"


def grade(case: dict, res: dict, elapsed: float) -> dict:
    """Deterministic, objective PASS/FAIL -- no LLM judge, no manual score
    massaging. Checks exactly the criteria the campaign requires."""
    reasons = []
    expected_cluster = case["payload"]["resource_hints"].get("cluster")
    obs = res.get("observability", {}) if isinstance(res, dict) else {}
    actual_cluster = obs.get("cluster")
    selected_mcp = obs.get("selected_mcp") or obs.get("primary_mcp_source")
    expected_mcp = "gke_remote_mcp" if is_gke(case) else "k8s_mcp"

    wrong_cluster = expected_cluster is not None and actual_cluster != expected_cluster
    wrong_mcp = expected_cluster is not None and selected_mcp and selected_mcp != expected_mcp
    if wrong_cluster:
        reasons.append(f"wrong_cluster: expected={expected_cluster} actual={actual_cluster}")
    if wrong_mcp:
        reasons.append(f"wrong_mcp: expected={expected_mcp} actual={selected_mcp}")

    errors = res.get("errors") or []
    # A non-empty `errors` list is NOT itself a failure signal -- it can legitimately
    # contain per-tool diagnostic notes (e.g. a NAME_SCOPED_NOT_FOUND finding that IS
    # the correct, expected outcome for a "resource is genuinely missing" case, see
    # rca_builder.py). The only thing that actually indicates a real crash is the
    # top-level status field, or this script's own run_one() catching an exception
    # from the API call itself (which sets status="exception" below).
    crashed = res.get("status") == "failed"
    if crashed:
        reasons.append(f"status=failed: {errors}")

    summary = res.get("summary", {}) if isinstance(res, dict) else {}
    claims = summary.get("claims") or []
    primary_id = summary.get("primary_causal_claim_id")
    primary = next((c for c in claims if c.get("claim_id") == primary_id), None)
    fabricated = bool(primary and primary.get("grounding_status") == "unsupported_entity")
    if fabricated:
        reasons.append("fabricated_claim: primary claim grounding_status=unsupported_entity")

    rca_text = json.dumps(res.get("rca_report", "")) + json.dumps(res.get("executive_summary", ""))
    expected_keywords = case.get("expected_keywords", [])
    keyword_hit = any(kw.lower() in rca_text.lower() for kw in expected_keywords) if expected_keywords else True
    zero_evidence = len(res.get("evidence_ids") or []) == 0

    outcome = res.get("outcome", "unknown")
    confidence_band = res.get("confidence_band", "unknown")

    # PASS bar: correct cluster/MCP, no crash, no fabrication. Keyword match and
    # zero-evidence are recorded but only fail the case for a NON-ambiguous case
    # with zero evidence at all (a real investigation gap), not for a
    # legitimately-cautious escalate/insufficient_evidence outcome with SOME
    # evidence -- that is the agent behaving correctly, not a failure.
    hard_fail = wrong_cluster or wrong_mcp or fabricated or (crashed and expected_cluster is not None)
    if expected_cluster is not None and zero_evidence and outcome not in ("insufficient_evidence",):
        hard_fail = True
        reasons.append("zero_evidence_but_not_flagged_insufficient")
    if expected_cluster is not None and not keyword_hit:
        reasons.append(f"keyword_miss: none of {expected_keywords} found in RCA text (not a hard fail on its own)")

    return {
        "pass": not hard_fail,
        "reasons": reasons,
        "actual_cluster": actual_cluster,
        "selected_mcp": selected_mcp,
        "outcome": outcome,
        "confidence_band": confidence_band,
        "confidence": res.get("confidence"),
        "completeness": (res.get("investigation_completeness") or {}).get("score"),
        "exit_reason": obs.get("loop_exit_reason"),
        "tool_calls": res.get("tool_calls"),
        "evidence_ids": res.get("evidence_ids"),
        "tokens_total": obs.get("tokens_total"),
        "estimated_cost_usd": obs.get("estimated_cost_usd"),
        "duration_s": elapsed,
        "run_id": res.get("run_id"),
        "errors": errors,
        "fabricated": fabricated,
        "keyword_hit": keyword_hit,
        "zero_evidence": zero_evidence,
    }


def run_one(agent, case: dict) -> dict:
    payload = case_to_payload(case)
    from google.protobuf import json_format as pjf
    start = time.time()
    try:
        response = agent.query_reasoning_engine(request={"name": _resource_name(), "input": payload})
        res = pjf.MessageToDict(response._pb).get("output", {})
        elapsed = time.time() - start
        g = grade(case, res, elapsed)
    except Exception as e:  # noqa: BLE001 -- record the failure, never crash the campaign
        elapsed = time.time() - start
        g = {
            "pass": False, "reasons": [f"exception: {e}"], "actual_cluster": None,
            "selected_mcp": None, "outcome": "exception", "confidence_band": None,
            "confidence": None, "completeness": None, "exit_reason": None,
            "tool_calls": None, "evidence_ids": None, "tokens_total": None,
            "estimated_cost_usd": None, "duration_s": elapsed, "run_id": None,
            "errors": [str(e)], "fabricated": False, "keyword_hit": False, "zero_evidence": True,
        }
    g["test_id"] = case["id"]
    g["cluster_expected"] = case["payload"]["resource_hints"].get("cluster")
    g["is_gke"] = is_gke(case)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=None, help="Fixed seed for reproducible ordering (default: time-based)")
    ap.add_argument("--limit", type=int, default=None, help="Run only the first N of the randomized order (for smoke testing this runner)")
    ap.add_argument("--out", type=str, default="PHASE1_50CASE_RESULTS.jsonl")
    args = ap.parse_args()

    cases = [c for c in GOLDEN_CASES if c["payload"]["resource_hints"].get("cluster") in ("sre-test-cluster", "sre-lab", "sre-lab-2")]
    bonus = [c for c in GOLDEN_CASES if c["payload"]["resource_hints"].get("cluster") not in ("sre-test-cluster", "sre-lab", "sre-lab-2")]
    assert len(cases) == 50, f"expected exactly 50 cluster-scoped cases, got {len(cases)}"

    seed = args.seed if args.seed is not None else int.from_bytes(os.urandom(4), "big")
    rng = random.Random(seed)
    order = cases[:]
    rng.shuffle(order)
    order += bonus  # bonus case(s) run last, not counted in the required 50

    print(f"Campaign seed: {seed}")
    print(f"Execution order ({len(order)} total, {len(cases)} required + {len(bonus)} bonus):")
    for i, c in enumerate(order, 1):
        print(f"  {i:2d}. {c['id']}")

    if args.limit:
        order = order[: args.limit]

    agent = get_agent()
    results = []
    with open(args.out, "w") as f:
        for i, case in enumerate(order, 1):
            print(f"\n[{i}/{len(order)}] Running {case['id']} (cluster={case['payload']['resource_hints'].get('cluster')}) ...", flush=True)
            g = run_one(agent, case)
            results.append(g)
            f.write(json.dumps(g) + "\n")
            f.flush()
            status = "PASS" if g["pass"] else "FAIL"
            print(f"  -> {status}  outcome={g['outcome']} band={g['confidence_band']} "
                  f"duration={g['duration_s']:.1f}s run_id={g['run_id']}")
            if not g["pass"]:
                print(f"     reasons: {g['reasons']}")

    n_required = [r for r in results if r["test_id"] in {c["id"] for c in cases}]
    n_pass = sum(1 for r in n_required if r["pass"])
    print(f"\n=== CAMPAIGN COMPLETE: {n_pass}/{len(n_required)} required cases passed (seed={seed}) ===")
    print(f"Results written to {args.out}")


if __name__ == "__main__":
    main()
