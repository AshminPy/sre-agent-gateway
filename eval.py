"""
eval.py — Evaluate SRE Agent accuracy against ground-truth scenarios.

Usage:
  python eval.py --project your-gcp-project-id --region us-central1 --engine-id YOUR_REASONING_ENGINE_ID
  python eval.py --project your-gcp-project-id --region us-central1 --engine-id YOUR_REASONING_ENGINE_ID --upload

Outputs:
  - Console table: per-scenario pass/fail + scores
  - results/eval-<timestamp>.json (local)
  - gs://<eval-bucket>/results/eval-<timestamp>.json (if --upload)

Scoring (simple keyword matching):
  - root_cause_hit  : key terms from reference appear in agent response
  - has_fix         : response contains an actionable fix
  - latency_ok      : investigation completed within 60 seconds
  - overall_score   : average of the three above
"""

import argparse
import json
import os
import time

PROJECT   = os.environ.get("PROJECT_ID", "your-gcp-project-id")
REGION    = os.environ.get("REGION", "us-central1")
EVAL_BUCKET = os.environ.get("EVAL_BUCKET", "")

DATASET_PATH = os.path.join(os.path.dirname(__file__), "eval", "dataset.jsonl")

LATENCY_OK_SECONDS = 60


def load_dataset(path: str) -> list:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def get_agent(project: str, region: str, engine_id: str):
    """Return low-level execution client + resource name.
    SDK 1.158.0 removed AgentEngine.query() — must use ReasoningEngineExecutionServiceClient."""
    import vertexai
    vertexai.init(project=project, location=region)
    from google.cloud import aiplatform_v1beta1
    client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )
    resource_name = f"projects/{project}/locations/{region}/reasoningEngines/{engine_id}"
    return client, resource_name


def init_experiment(project: str, region: str) -> bool:
    """Create or resume the sre-agent-eval experiment in Vertex AI.
    Returns True if experiment tracking is active, False if unavailable."""
    try:
        from google.cloud import aiplatform
        aiplatform.init(project=project, location=region, experiment="sre-agent-eval")
        return True
    except Exception as exc:
        print(f"  [warn] Vertex AI Experiments unavailable: {exc}")
        return False


def extract_text(result) -> str:
    """Pull readable text out of whatever the agent returned."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        parts = []
        for key in ("summary", "root_cause", "recommendation", "working_theory", "status"):
            v = result.get(key)
            if v:
                if isinstance(v, dict):
                    parts.append(json.dumps(v))
                else:
                    parts.append(str(v))
        return " ".join(parts)
    return str(result)


def score_response(response_text: str, reference: str) -> dict:
    """
    Simple keyword-based scoring.
    Does NOT call an LLM — deterministic and free.
    """
    resp_lower = response_text.lower()
    ref_lower  = reference.lower()

    # Split reference into key phrases (split on ',', ';', '.')
    import re
    key_phrases = [p.strip() for p in re.split(r"[,;.]", ref_lower) if p.strip()]

    # root_cause_hit: what % of reference key phrases appear in the response
    if key_phrases:
        hits = sum(1 for p in key_phrases if any(w in resp_lower for w in p.split() if len(w) > 4))
        root_cause_hit = hits / len(key_phrases)
    else:
        root_cause_hit = 0.0

    # has_fix: response contains fix-related keywords
    fix_keywords = ["fix", "set ", "update ", "delete ", "scale ", "restart ", "check ", "change ", "apply "]
    has_fix = float(any(kw in resp_lower for kw in fix_keywords))

    return {
        "root_cause_hit": round(root_cause_hit, 2),
        "has_fix":        round(has_fix, 2),
    }


def run_eval(project: str, region: str, engine_id: str, upload: bool = False) -> dict:
    print(f"\nLoading eval dataset: {DATASET_PATH}")
    dataset = load_dataset(DATASET_PATH)
    print(f"  {len(dataset)} scenarios loaded")

    print(f"\nConnecting to Agent Engine {engine_id}...")
    agent_client, agent_resource = get_agent(project, region, engine_id)
    print("  Connected.\n")

    ts = int(time.time())

    # Start Vertex AI Experiment run — populates the Experiments tab in console
    experiment_active = init_experiment(project, region)
    exp_run = None
    if experiment_active:
        try:
            from google.cloud import aiplatform
            exp_run = aiplatform.start_run(f"eval-{ts}")
            exp_run.log_params({
                "engine_id":  engine_id,
                "dataset":    "eval/dataset.jsonl",
                "total":      len(dataset),
            })
            print(f"  Vertex AI Experiment run started: eval-{ts}")
        except Exception as exc:
            print(f"  [warn] Could not start experiment run: {exc}")
            exp_run = None

    results = []
    passed = 0

    header = f"{'ID':20} {'Root-cause':12} {'Has-fix':8} {'Latency':10} {'Pass'}"
    print(header)
    print("-" * len(header))

    for entry in dataset:
        entry_id  = entry.get("id", "?")
        request   = entry.get("request", {})
        reference = entry.get("reference", "")

        start = time.time()
        try:
            from google.protobuf import json_format as _pjf
            raw = agent_client.query_reasoning_engine(
                request={"name": agent_resource, "input": request}
            )
            result  = _pjf.MessageToDict(raw._pb).get("output", {})
            elapsed = time.time() - start
            text    = extract_text(result)
            scores  = score_response(text, reference)
            latency_ok = float(elapsed <= LATENCY_OK_SECONDS)

            overall = (scores["root_cause_hit"] + scores["has_fix"] + latency_ok) / 3
            ok = overall >= 0.4  # pass threshold
            if ok:
                passed += 1

            row = {
                "id":             entry_id,
                "root_cause_hit": scores["root_cause_hit"],
                "has_fix":        scores["has_fix"],
                "latency_s":      round(elapsed, 1),
                "latency_ok":     latency_ok,
                "overall":        round(overall, 2),
                "pass":           ok,
                "raw_response":   text[:500],
            }
            results.append(row)

            mark = "PASS" if ok else "FAIL"
            print(
                f"  {entry_id:18} {scores['root_cause_hit']:12.0%}"
                f" {scores['has_fix']:8.0%} {elapsed:8.1f}s   {mark}"
            )

            # Log per-scenario metrics to Vertex AI Experiments
            if exp_run:
                try:
                    exp_run.log_metrics({
                        f"{entry_id}.root_cause_hit": scores["root_cause_hit"],
                        f"{entry_id}.has_fix":        scores["has_fix"],
                        f"{entry_id}.latency_s":      round(elapsed, 1),
                        f"{entry_id}.overall":        round(overall, 2),
                        f"{entry_id}.pass":           float(ok),
                    })
                except Exception:
                    pass

        except Exception as exc:
            elapsed = time.time() - start
            results.append({
                "id":       entry_id,
                "error":    str(exc),
                "pass":     False,
                "latency_s": round(elapsed, 1),
            })
            print(f"  {entry_id:18} ERROR: {str(exc)[:40]}")

        time.sleep(0.5)  # be kind to the quota

    total    = len(results)
    pass_pct = passed / total if total else 0

    print(f"\n{'─'*55}")
    print(f"  Result: {passed}/{total} passed  ({pass_pct:.0%})")
    print(f"{'─'*55}\n")

    # Log overall summary to Vertex AI Experiments and close the run
    if exp_run:
        try:
            exp_run.log_metrics({
                "pass_rate": round(pass_pct, 2),
                "passed":    float(passed),
                "total":     float(total),
                "failed":    float(total - passed),
            })
            exp_run.end_run()
            print(f"  Vertex AI Experiment run completed: eval-{ts}")
        except Exception as exc:
            print(f"  [warn] Could not close experiment run: {exc}")

    summary = {
        "project":       project,
        "region":        region,
        "engine_id":     engine_id,
        "dataset":       DATASET_PATH,
        "total":         total,
        "passed":        passed,
        "pass_rate":     round(pass_pct, 2),
        "scenarios":     results,
    }

    # Save local copy
    os.makedirs("results", exist_ok=True)
    local_out = f"results/eval-{ts}.json"
    with open(local_out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Results saved → {local_out}")

    # Upload to GCS eval bucket
    if upload:
        bucket = EVAL_BUCKET.removeprefix("gs://") if EVAL_BUCKET else f"{project}-eval"
        gcs_key = f"results/eval-{ts}.json"
        try:
            from google.cloud import storage
            client = storage.Client(project=project)
            blob   = client.bucket(bucket).blob(gcs_key)
            blob.upload_from_string(json.dumps(summary, indent=2), content_type="application/json")
            print(f"  Uploaded → gs://{bucket}/{gcs_key}")
        except Exception as exc:
            print(f"  GCS upload failed: {exc}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Evaluate SRE Agent against ground-truth dataset")
    parser.add_argument("--project",   default=PROJECT,   help="GCP project ID")
    parser.add_argument("--region",    default=REGION,    help="GCP region")
    parser.add_argument("--engine-id", required=True,     help="Agent Engine ID or full resource name")
    parser.add_argument("--upload",    action="store_true", help="Upload results to GCS eval bucket")
    args = parser.parse_args()

    # Accept short ID or full resource name
    engine_id = args.engine_id
    if engine_id.startswith("projects/"):
        engine_id = engine_id.rsplit("/", 1)[-1]

    run_eval(
        project=args.project,
        region=args.region,
        engine_id=engine_id,
        upload=args.upload,
    )


if __name__ == "__main__":
    main()
