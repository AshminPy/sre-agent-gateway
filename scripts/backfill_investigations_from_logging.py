#!/usr/bin/env python3
"""Backfill the `sre_agent_investigations` BigQuery sink table from Cloud Logging.

Why this exists: a Cloud Logging sink only forwards entries written AFTER the
sink is created (2026-09-05 20:06 UTC for this project). Everything the agent
logged before that is still in Cloud Logging (default 30-day retention) but
never reached BigQuery, so the Investigation Dashboard started life with a
handful of runs while Logging held hundreds. This script copies the missing
entries across, once, with the same shape the sink itself would have written.

What it does, in order (every step is visible in the output):
  1. `gcloud logging read` the investigations log for the last N days.
  2. Rewrite each entry as newline-delimited JSON, truncating RFC 3339
     timestamps to microseconds (BigQuery rejects nanoseconds).
  3. Load into a STAGING table created from the live sink table's exact
     schema (unknown legacy jsonPayload keys are ignored, not failed).
  4. Report counts: rows, distinct run_ids, date span, and how many entries
     are already in the sink (matched by insertId).
  5. Only with --apply: INSERT the staging rows whose insertId is NOT already
     in the sink, then drop the staging table. Without --apply nothing is
     written to the sink and the staging table is left for inspection.

Re-running is safe: the insertId anti-join makes the INSERT idempotent.

Requires: gcloud + bq on PATH, authenticated as an identity with
roles/bigquery.dataEditor on the dataset and roles/logging.viewer.

    python3 scripts/backfill_investigations_from_logging.py --project sreagent-t2-demo
    python3 scripts/backfill_investigations_from_logging.py --project sreagent-t2-demo --apply
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

LOG_ID = "sre-agent-investigations"
DATASET = "sre_agent_investigations"
SINK_TABLE = "sre_agent_investigations"
STAGING_TABLE = "backfill_staging"

_NANOS = re.compile(r"(\.\d{6})\d+Z$")


def run(cmd: list[str], *, capture: bool = True) -> str:
    print("$", " ".join(cmd), flush=True)
    res = subprocess.run(cmd, check=True, text=True, capture_output=capture)
    return res.stdout if capture else ""


def to_micros(ts: str) -> str:
    return _NANOS.sub(r"\1Z", ts)


def export_entries(project: str, days: int) -> list[dict]:
    log_filter = (
        f'logName="projects/{project}/logs/{LOG_ID}" AND jsonPayload.run_id!=""'
    )
    out = run([
        "gcloud", "logging", "read", log_filter,
        f"--project={project}", f"--freshness={days}d",
        "--limit=100000", "--format=json",
    ])
    return json.loads(out or "[]")


def write_ndjson(entries: list[dict], path: Path) -> None:
    with path.open("w") as fh:
        for e in entries:
            for key in ("timestamp", "receiveTimestamp"):
                if key in e:
                    e[key] = to_micros(e[key])
            fh.write(json.dumps(e) + "\n")


def bq_query(project: str, sql: str) -> list[dict]:
    out = run([
        "bq", "query", "--use_legacy_sql=false", "--format=json",
        f"--project_id={project}", sql,
    ])
    return json.loads(out or "[]")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="GCP project that owns the log and the dataset")
    ap.add_argument("--days", type=int, default=30, help="How far back to read Cloud Logging (default 30)")
    ap.add_argument("--apply", action="store_true", help="Actually INSERT into the sink table (default: dry run, staging only)")
    args = ap.parse_args()

    sink = f"{args.project}:{DATASET}.{SINK_TABLE}"
    staging = f"{args.project}:{DATASET}.{STAGING_TABLE}"
    sink_q = f"`{args.project}.{DATASET}.{SINK_TABLE}`"
    staging_q = f"`{args.project}.{DATASET}.{STAGING_TABLE}`"

    entries = export_entries(args.project, args.days)
    print(f"Cloud Logging entries with a run_id in the last {args.days} days: {len(entries)}")
    if not entries:
        print("Nothing to backfill.")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.json"
        ndjson = Path(tmp) / "entries.ndjson"
        schema.write_text(run(["bq", "show", "--schema", "--format=prettyjson", sink]))
        write_ndjson(entries, ndjson)

        subprocess.run(["bq", "rm", "-f", "-t", staging], check=False, capture_output=True)
        run(["bq", "mk", "-t", "--time_partitioning_field=timestamp",
             "--time_partitioning_type=DAY", f"--schema={schema}", staging])
        run(["bq", "load", "--source_format=NEWLINE_DELIMITED_JSON",
             "--ignore_unknown_values", staging, str(ndjson)], capture=False)

    stats = bq_query(args.project, f"""
        SELECT COUNT(*) AS n_rows,
               COUNT(DISTINCT jsonPayload.run_id) AS runs,
               CAST(MIN(timestamp) AS STRING) AS first_ts,
               CAST(MAX(timestamp) AS STRING) AS last_ts,
               COUNTIF(insertId IN (SELECT insertId FROM {sink_q})) AS already_in_sink
        FROM {staging_q}""")[0]
    print("Staging:", json.dumps(stats, indent=2))
    new_rows = int(stats["n_rows"]) - int(stats["already_in_sink"])
    print(f"Rows that would be inserted (not yet in sink): {new_rows}")

    if not args.apply:
        print(f"Dry run. Staging table {staging} left in place for inspection; re-run with --apply to insert.")
        return 0

    before = bq_query(args.project, f"SELECT COUNT(*) AS n FROM {sink_q}")[0]["n"]
    run(["bq", "query", "--use_legacy_sql=false", f"--project_id={args.project}", f"""
        INSERT INTO {sink_q}
        SELECT s.* FROM {staging_q} s
        WHERE NOT EXISTS (SELECT 1 FROM {sink_q} t WHERE t.insertId = s.insertId)"""], capture=False)
    after = bq_query(args.project, f"SELECT COUNT(*) AS n FROM {sink_q}")[0]["n"]
    print(f"Sink rows before={before} after={after} inserted={int(after) - int(before)} (expected {new_rows})")
    if int(after) - int(before) != new_rows:
        print("WARNING: inserted count differs from expectation — inspect before trusting the dashboard.", file=sys.stderr)
        return 1
    run(["bq", "rm", "-f", "-t", staging])
    print("Staging table dropped. Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
