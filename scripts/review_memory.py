#!/usr/bin/env python3
"""review_memory.py — the smallest supported human-review operation for Memory Bank
records (Section 8, 2026-09-08; implements what ADR-010 documented as "decided but
not built"). A CLI, not a new UI, per that section's explicit instruction.

agent/main.py's _mb_store() writes every RCA with status=pending_review encoded in
its fact string; _mb_recall() only returns status=approved memories to future
investigations. This script is how a pending record actually becomes approved,
rejected, or revoked.

The Vertex AI Memory Bank SDK (vertexai._genai.memories.Memories) exposes no public
update() — only a private _update() (unstable, not used here) and public
create()/delete()/get()/list()/retrieve(). "Update" a memory's status is therefore
delete-then-recreate, using only the public, supported surface — same technique
_mb_store()'s dedup check already relies on (parsing the fact string), not a new
untested SDK code path.

Usage:
    python3 scripts/review_memory.py list --cluster sre-lab --namespace test-incidents
    python3 scripts/review_memory.py approve --name <memory resource name>
    python3 scripts/review_memory.py reject  --name <memory resource name> --reason "..."
    python3 scripts/review_memory.py revoke  --name <memory resource name> --reason "..."

Requires the same env vars agent/main.py's Memory Bank client does
(PROJECT_ID, REGION, MEMORY_BANK_RESOURCE) — see scripts/init-env.sh.
"""
import argparse
import os
import re
import sys


def _parse_fact(fact: str) -> dict:
    """Same parser as agent/main.py's SREAgent._parse_fact() — kept as its own copy
    here rather than importing agent.main, since this script runs standalone against
    real Memory Bank and importing the full agent module would pull in unrelated
    Vertex AI Agent Engine / LangGraph dependencies this script doesn't need."""
    parsed = {}
    for token in fact.split():
        if "=" in token:
            k, _, v = token.partition("=")
            parsed[k] = v
    return parsed


def _rebuild_fact(fact: str, status: str, reason: str = "") -> str:
    """Replaces status=<old> with status=<new> in the fact string, appending a
    reviewed_by/reason note. Everything else (cluster, namespace, pod, incident_type,
    root_cause, confidence, run_id, policy_version) is preserved unchanged."""
    if "status=" in fact:
        fact = re.sub(r"status=\S+", f"status={status}", fact)
    else:
        fact = f"{fact} status={status}"
    reviewer = os.environ.get("USER", "unknown")
    fact = f"{fact} reviewed_by={reviewer}"
    if reason:
        # Reason is free text -- keep it at the end, space-safe by underscoring.
        fact = f"{fact} reason={reason.replace(' ', '_')[:200]}"
    return fact


def _client_and_resource():
    from vertexai import Client

    project = os.environ.get("PROJECT_ID")
    region = os.environ.get("REGION", "us-central1")
    resource = os.environ.get("MEMORY_BANK_RESOURCE")
    if not project or not resource:
        print(
            "ERROR: PROJECT_ID and MEMORY_BANK_RESOURCE must be set "
            "(source scripts/init-env.sh first).",
            file=sys.stderr,
        )
        sys.exit(1)
    return Client(project=project, location=region), resource


def cmd_list(args):
    client, resource = _client_and_resource()
    memories = list(client.agent_engines.memories.retrieve(
        name=resource,
        scope={"cluster": args.cluster, "namespace": args.namespace},
    ))
    if not memories:
        print(f"No memories found for cluster={args.cluster} namespace={args.namespace}")
        return
    status_filter = args.status
    shown = 0
    for m in memories:
        fields = _parse_fact(m.memory.fact)
        status = fields.get("status", "(no status — pre-Section-8 record)")
        if status_filter and status != status_filter:
            continue
        shown += 1
        print(f"name:        {m.memory.name}")
        print(f"status:      {status}")
        print(f"incident:    {fields.get('incident_type', '?')} pod={fields.get('pod', '?')}")
        print(f"root_cause:  {fields.get('root_cause', '?')[:150]}")
        print(f"confidence:  {fields.get('confidence', '?')}")
        print(f"run_id:      {fields.get('run_id', '(none — pre-Section-8 record)')}")
        print(f"policy_ver:  {fields.get('policy_version', '(none — pre-Section-8 record)')}")
        print("-" * 60)
    if shown == 0:
        print(f"No memories with status={status_filter} for this cluster/namespace.")


def _change_status(args, new_status: str):
    client, resource = _client_and_resource()
    memory = client.agent_engines.memories.get(name=args.name)
    old_fact = memory.fact
    new_fact = _rebuild_fact(old_fact, new_status, getattr(args, "reason", "") or "")

    client.agent_engines.memories.delete(name=args.name)
    result = client.agent_engines.memories.create(
        name=resource,
        fact=new_fact,
        scope=dict(memory.scope) if memory.scope else {},
    )
    print(f"Status changed to {new_status}.")
    print(f"Old fact: {old_fact}")
    print(f"New fact: {new_fact}")
    new_name = getattr(getattr(result, "response", None), "name", None)
    if new_name:
        print(f"New memory name: {new_name}")
    print(
        "NOTE: delete+recreate assigns a NEW resource name -- this is the smallest "
        "supported operation available (no public update() exists on this SDK's "
        "Memories client); any external reference to the OLD name is now stale."
    )


def cmd_approve(args):
    _change_status(args, "approved")


def cmd_reject(args):
    _change_status(args, "rejected")


def cmd_revoke(args):
    _change_status(args, "revoked")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List memories for a cluster/namespace")
    p_list.add_argument("--cluster", required=True)
    p_list.add_argument("--namespace", required=True)
    p_list.add_argument("--status", default="", help="Filter by status (e.g. pending_review)")
    p_list.set_defaults(func=cmd_list)

    p_approve = sub.add_parser("approve", help="Mark a memory approved — becomes recallable")
    p_approve.add_argument("--name", required=True, help="Memory resource name (from `list`)")
    p_approve.set_defaults(func=cmd_approve)

    p_reject = sub.add_parser("reject", help="Mark a memory rejected — never recalled")
    p_reject.add_argument("--name", required=True)
    p_reject.add_argument("--reason", default="")
    p_reject.set_defaults(func=cmd_reject)

    p_revoke = sub.add_parser("revoke", help="Revoke a previously-approved memory")
    p_revoke.add_argument("--name", required=True)
    p_revoke.add_argument("--reason", default="")
    p_revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
