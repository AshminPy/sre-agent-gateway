"""
eval_native.py — Agent Platform native evaluation.

Shows results in: Agent Platform → Agents → Evaluation → Experiments tab.
Uses AutoRater metrics:
  - question_answering_quality : Did the agent answer the SRE question correctly?
  - groundedness               : Is the answer grounded in the evidence collected?

This is separate from eval.py (which uses keyword scoring → Vertex AI Experiments tab).
Both can run independently. eval.py is fast + free; this one uses AutoRaters.

Usage:
  python eval_native.py \\
    --project your-gcp-project-id \\
    --region us-central1 \\
    --engine-id YOUR_REASONING_ENGINE_ID

  # Limit to first 3 scenarios (saves cost during testing):
  python eval_native.py ... --limit 3

Requirements (local only, not inside the deployed agent):
  pip install google-cloud-aiplatform[evaluation] pandas
"""

import argparse
import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

PROJECT      = os.environ.get("PROJECT_ID", "your-gcp-project-id")
REGION       = os.environ.get("REGION", "us-central1")
EVAL_BUCKET  = os.environ.get("EVAL_BUCKET", "your-gcp-project-id-eval")
DATASET_PATH = Path(__file__).parent / "eval" / "dataset.jsonl"


def load_dataset(path: str) -> list:
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def run_inference_manually(resource_name: str, region: str, dataset: list) -> "pd.DataFrame":
    """Call the agent via query_reasoning_engine() for each scenario.

    Uses the v1beta1 REST client — the same method invoke_agent.py uses.
    Bypasses run_inference() which requires stream_query() or google.adk.
    """
    import pandas as pd
    from google.cloud import aiplatform_v1beta1
    from google.protobuf import json_format as pjf

    client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    rows = []
    total = len(dataset)
    for i, entry in enumerate(dataset, 1):
        # Natural language question — AutoRater needs readable text, not JSON
        query_text   = entry["request"].get("query", "")
        prompt_json  = json.dumps(entry["request"])
        print(f"  [{i}/{total}] {query_text[:70]}...")
        try:
            resp   = client.query_reasoning_engine(
                request={"name": resource_name, "input": {"prompt": prompt_json}}
            )
            result = pjf.MessageToDict(resp._pb).get("output", {})
            # Extract the human-readable incident summary for the AutoRater
            summary = result.get("summary", result)
            response = (
                summary.get("incident_summary")
                or summary.get("root_cause")
                or summary.get("rca")
                or json.dumps(summary)
            ) if isinstance(summary, dict) else str(summary)
        except Exception as e:
            response = f"ERROR: {e}"
            print(f"         Error: {e}")
        rows.append({
            "prompt":    query_text,   # clean natural language question
            "response":  response,     # clean natural language answer
            "reference": entry.get("reference", ""),
        })

    return pd.DataFrame(rows)


def run_native_eval(project: str, region: str, engine_id: str, limit: int = 0) -> None:
    from vertexai import Client
    from vertexai._genai.types.common import EvaluationDataset, EvaluationRunMetric
    import vertexai

    ts            = int(time.time())
    resource_name = f"projects/{project}/locations/{region}/reasoningEngines/{engine_id}"
    dest          = f"gs://{EVAL_BUCKET}/native-runs/{ts}/"

    print(f"\nLoading eval dataset: {DATASET_PATH}")
    dataset = load_dataset(str(DATASET_PATH))
    if limit and limit < len(dataset):
        dataset = dataset[:limit]
        print(f"  Running {limit} of {limit + (16 - limit)} scenarios (--limit {limit})")
    else:
        print(f"  {len(dataset)} scenarios")

    vertexai.init(project=project, location=region)

    print(f"\nRunning inference via Agent Engine {engine_id} ...")
    print("  Note: each scenario may take 30-120s — expect a long run.\n")
    df = run_inference_manually(resource_name, region, dataset)
    print("\n  Inference complete.")

    client = Client(project=project, location=region)

    print("\nCreating Agent Platform evaluation run ...")
    eval_run = client.evals.create_evaluation_run(
        dataset=EvaluationDataset(eval_dataset_df=df),
        dest=dest,
        metrics=[
            EvaluationRunMetric(metric="question_answering_quality"),
            EvaluationRunMetric(metric="groundedness"),
        ],
        display_name=f"sre-agent-eval-{ts}",
        agent=resource_name,
    )

    print(f"\n  Run name  : {eval_run.name}")
    print(f"  State     : {eval_run.state}")
    print(f"  Results   : {dest}")
    print()
    print("  View in GCP console:")
    print("  Agent Platform → Agents → Evaluation → Experiments")


def main():
    parser = argparse.ArgumentParser(
        description="Agent Platform native evaluation — shows in Evaluation tab"
    )
    parser.add_argument("--project",   default=PROJECT,   help="GCP project ID")
    parser.add_argument("--region",    default=REGION,    help="GCP region")
    parser.add_argument("--engine-id", required=True,     help="Agent Engine ID")
    parser.add_argument("--limit",     type=int, default=0, help="Max scenarios (0 = all)")
    args = parser.parse_args()

    engine_id = args.engine_id
    if engine_id.startswith("projects/"):
        engine_id = engine_id.rsplit("/", 1)[-1]

    run_native_eval(
        project=args.project,
        region=args.region,
        engine_id=engine_id,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()
