#!/usr/bin/env python3
"""
run.py — local CLI test harness for the SRE Agent (GCP).

Mirrors the AWS run.py pattern exactly.

Usage:
  python run.py "imagepull-pod is in ImagePullBackOff" \
      --namespace test-incidents --pod imagepull-pod --severity high

  python run.py "oomkilled-pod keeps dying" \
      --namespace test-incidents --pod oomkilled-pod --severity critical

  python run.py "crashloop-pod is crashing" \
      --namespace test-incidents --pod crashloop-pod

Environment variables required (set in agent/.env):
  PROJECT_ID        your-gcp-project-id
  REGION            us-central1
  GEMINI_MODEL      gemini-2.5-flash
  K8S_MCP_URL       https://sre-k8s-mcp-<hash>.<region>.run.app  (from: terraform output -raw custom_mcp_url)
  EVIDENCE_BUCKET   your-gcp-project-id-evidence                      (from: terraform output -raw evidence_bucket_name)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

# ── Load .env ─────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "agent", ".env"))

# ── Validate env before importing agent ───────────────────────────
_REQUIRED_ENV = ["PROJECT_ID", "K8S_MCP_URL", "GEMINI_MODEL"]
_missing = [v for v in _REQUIRED_ENV if not os.getenv(v)]
if _missing:
    print(f"ERROR: Missing required environment variables: {', '.join(_missing)}")
    print("\nSet them in agent/.env or export them:")
    for v in _missing:
        print(f"  export {v}=<value>")
    sys.exit(1)

from agent.graph import compile_graph, get_initial_state

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sre-agent.run")


def _print_report(result: dict, duration: float) -> None:
    summary         = result.get("final_summary", {}) or {}
    tool_history    = result.get("tool_history", [])
    inv             = result.get("investigation", {})
    conf            = inv.get("confidence", 0.0)
    conf_label      = "HIGH" if conf >= 0.85 else "MEDIUM" if conf >= 0.65 else "LOW"
    conf_band       = summary.get("confidence_band", inv.get("confidence_band", "escalate"))
    run_id          = result.get("run_id", "unknown")
    evidence_ids    = result.get("evidence_ids", [])
    tokens_total    = inv.get("tokens_total", 0)
    cost_usd        = inv.get("estimated_cost_usd", 0.0)
    evidence_bucket = os.environ.get("EVIDENCE_BUCKET", "your-gcp-project-id-evidence")
    gcs_path        = f"gs://{evidence_bucket}/{run_id}/"

    print("\n" + "=" * 72)
    print("  SRE AGENT — INVESTIGATION REPORT (GCP)")
    print(f"  Run ID    : {run_id}")
    print(f"  Duration  : {duration:.1f}s  |  Tool calls: {len(tool_history)}")
    print(f"  Evidence  : {len(evidence_ids)} items → {gcs_path}")
    print(f"  Tokens    : {tokens_total:,} total  |  Est. cost: ${cost_usd:.6f}")
    print("=" * 72)

    print(f"\n⚪  INCIDENT")
    print(f"   {summary.get('incident_summary', 'unknown')}")

    icon = "✅" if conf >= 0.85 else "🟡" if conf >= 0.65 else "🔴"
    print(f"\n🎯  ROOT CAUSE  {icon} {conf_label} CONFIDENCE (band: {conf_band})")
    print(f"   {summary.get('likely_root_cause', 'undetermined')}")

    if evidence_ids:
        print(f"\n📦  EVIDENCE CHAIN ({len(evidence_ids)} items — stored in GCS)")
        for ev_id in evidence_ids:
            ev = result.get("evidence_store", {}).get(ev_id, {})
            print(f"   [{ev_id}] {ev.get('summary', '')[:120]}")
            for fact in ev.get("key_facts", [])[:3]:
                print(f"            • {fact}")
            print(f"            raw_ref: {ev.get('raw_ref', '')}")

    if tool_history:
        print(f"\n🔧  TOOLS CALLED ({len(tool_history)})")
        for i, h in enumerate(tool_history, 1):
            icon2   = "✅" if h.get("ok") else "❌"
            source  = h.get("mcp_source", "?")
            # Skip internal 'parent' arg from display — it's built automatically
            args    = {k: v for k, v in h.get("args", {}).items() if k != "parent"}
            arg_str = " ".join(f"{k}={v}" for k, v in args.items())
            print(f"   [{i}] {icon2} {source}.{h.get('tool','?')}  ({h.get('duration_s','?')}s)  {arg_str}")

    reasoning = summary.get("reasoning_trace", [])
    if reasoning:
        print(f"\n🧠  REASONING TRACE")
        for i, step in enumerate(reasoning, 1):
            print(f"   {i}. {step}")

    remediation = summary.get("suggested_remediation", [])
    if remediation:
        print(f"\n🩹  SUGGESTED REMEDIATION (human action required)")
        for i, r in enumerate(remediation, 1):
            print(f"   {i}. {r}")

    gaps = summary.get("evidence_gaps", [])
    if gaps:
        print(f"\n⚠️   EVIDENCE GAPS")
        for g in gaps:
            print(f"   • {g}")

    sources_skipped = summary.get("sources_skipped", [])
    if sources_skipped:
        print(f"\n⏭️   SOURCES SKIPPED")
        for s in sources_skipped:
            print(f"   • {s}")

    errors = result.get("errors", [])
    if errors:
        print(f"\n🚨  ERRORS")
        for e in errors:
            print(f"   • {e}")

    requires_review = summary.get("requires_human_review", True)
    review_icon = "🔴 REQUIRED" if requires_review else "✅ NOT REQUIRED"
    print(f"\n👤  HUMAN REVIEW: {review_icon}")

    print("\n" + "─" * 72)
    print("  MACHINE-READABLE OUTPUT (JSON)")
    print("─" * 72)
    print(json.dumps(summary, indent=2))
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SRE Agent — GCP local test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("query",        help="Incident description or alert text")
    parser.add_argument("--namespace",  default="test-incidents")
    parser.add_argument("--pod",        default="")
    parser.add_argument("--cluster",    default="sre-test-cluster")
    parser.add_argument("--deployment", default="")
    parser.add_argument("--severity",   default="high",
                        choices=["low", "medium", "high", "critical"])
    parser.add_argument("--max-steps",  type=int, default=5)
    args = parser.parse_args()

    log.info("starting investigation query=%s", args.query[:80])

    envelope = {
        "source_type":     "manual",
        "source_event_id": "",
        "user_query":      args.query,
        "resource_hints": {
            "cluster":    args.cluster,
            "namespace":  args.namespace,
            "pod":        args.pod,
            "deployment": args.deployment,
        },
        "incident": {
            "severity": args.severity,
        },
    }

    graph = compile_graph()
    state = get_initial_state(envelope)
    state["investigation"]["max_steps"] = args.max_steps

    started  = time.time()
    result   = graph.invoke(state)
    duration = round(time.time() - started, 1)

    inv        = result["investigation"]
    confidence = inv.get("confidence", 0.0)
    tokens     = inv.get("tokens_total", 0)
    cost       = inv.get("estimated_cost_usd", 0.0)

    log.info(
        "investigation complete run_id=%s status=%s confidence=%.2f duration=%ss tools=%d tokens=%d cost=$%.6f",
        result.get("run_id"), inv["status"], confidence,
        duration, len(result.get("tool_history", [])),
        tokens, cost,
    )

    _print_report(result, duration)


if __name__ == "__main__":
    main()
