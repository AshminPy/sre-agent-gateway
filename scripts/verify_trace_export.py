#!/usr/bin/env python3
"""
verify_trace_export.py -- Confirm a given investigation run's spans actually
landed in Cloud Trace.

Postmortem (issue #130, 2026-08-13): every ad-hoc check made during that
investigation only ever inspected the FIRST page of Cloud Trace's List
Traces API response and never followed nextPageToken. That reported "0
traces" for a full 10-minute propagation-delay window while the real trace
sat on page 2 the entire time -- a false negative caused by the check
itself, not by Cloud Trace ever actually failing to export. fetch_all_traces()
below is REQUIRED to follow pagination to exhaustion so that mistake can't
happen again; tests/test_verify_trace_export.py proves it does, using a
mocked 2-page response where page 1 is empty but carries a nextPageToken --
exactly the shape that produced the false negative.

Usage:
  python3 scripts/verify_trace_export.py --project sreagent-t2-demo \
      --run-id run_20260813_222248_ybag \
      --start-time 2026-08-13T22:15:00Z --end-time 2026-08-13T22:45:00Z
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request

TRACE_API_BASE = "https://cloudtrace.googleapis.com/v1"


def _access_token() -> str:
    return subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _http_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_all_traces(fetch_page, project_id: str, start_time: str, end_time: str,
                      view: str = "MINIMAL") -> list[dict]:
    """Return every trace summary in the time window, following
    nextPageToken to exhaustion.

    `fetch_page(url) -> dict` is injected so tests can mock the transport
    without a real network call -- this function's own logic (the pagination
    loop) is what issue #130's postmortem requires to be proven correct, not
    the HTTP transport itself.
    """
    traces: list[dict] = []
    base = f"{TRACE_API_BASE}/projects/{project_id}/traces"
    url = f"{base}?startTime={start_time}&endTime={end_time}&view={view}&pageSize=50"

    while url:
        page = fetch_page(url)
        traces.extend(page.get("traces", []))
        next_token = page.get("nextPageToken")
        if not next_token:
            break
        url = f"{base}?startTime={start_time}&endTime={end_time}&view={view}&pageSize=50&pageToken={next_token}"

    return traces


def find_run_id(fetch_trace, project_id: str, trace_summaries: list[dict],
                 run_id: str) -> dict | None:
    """Fetch each candidate trace's full spans and return the first one
    whose spans carry the given sre.run_id label, or None if none match."""
    for summary in trace_summaries:
        trace = fetch_trace(project_id, summary["traceId"])
        for span in trace.get("spans", []):
            if span.get("labels", {}).get("sre.run_id") == run_id:
                return trace
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--run-id", required=True, help="sre.run_id label to search for")
    parser.add_argument("--start-time", required=True, help="RFC3339, e.g. 2026-08-13T22:00:00Z")
    parser.add_argument("--end-time", required=True, help="RFC3339")
    args = parser.parse_args()

    token = _access_token()

    def fetch_page(url: str) -> dict:
        return _http_get(url, token)

    def fetch_trace(project_id: str, trace_id: str) -> dict:
        return _http_get(f"{TRACE_API_BASE}/projects/{project_id}/traces/{trace_id}", token)

    summaries = fetch_all_traces(fetch_page, args.project, args.start_time, args.end_time)
    print(f"Fetched {len(summaries)} trace summaries (all pages followed).")

    match = find_run_id(fetch_trace, args.project, summaries, args.run_id)
    if match is None:
        print(f"NOT FOUND: no trace carries sre.run_id={args.run_id}", file=sys.stderr)
        return 1

    span_count = len(match.get("spans", []))
    print(f"FOUND: trace {match['traceId']} -- {span_count} spans for run_id={args.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
