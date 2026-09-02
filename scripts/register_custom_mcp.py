#!/usr/bin/env python3
"""
Register the custom Cloud Run MCP server with the Agent Registry so Agent
Gateway allows egress to it — without this, every call gets:
  HTTP 403: Egress request is not authorized. The endpoint is either
  incorrect or unregistered in the Agent Registry.

Only matters when enable_custom_mcp=true (the Cloud Run service exists).
No-op (prints a message, exits 0) otherwise, matching
attach_gateway_to_engine.sh's own "no-op when disabled" convention.

Registers with --mcp-server-spec-type=tool-spec (the real MCP tool list, not
--endpoint-spec-type=no-spec) — this is the pattern Google's own codelab uses
for external/custom MCP servers specifically:
  https://codelabs.developers.google.com/agw-cuj-arun-egress-emcp
(--endpoint-spec-type=no-spec, used elsewhere in this repo by
register_endpoints.py, is for plain Google API passthrough endpoints, not MCP
servers — verified against the codelab before writing this, not assumed.)

Content is capped at 10KB by the API (--mcp-server-spec-content docs) — tool
descriptions/schemas are included but trimmed to name/description/inputSchema
only (no outputSchema/meta) to stay under that limit with 27 tools.

Usage:
  python3 scripts/register_custom_mcp.py --project=X --region=Y --url=Z
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_ID = "sre-k8s-mcp"


def build_tool_spec() -> dict:
    """Import mcp/server.py and produce a trimmed, MCP-tools/list-shaped spec."""
    sys.path.insert(0, str(REPO_ROOT / "mcp"))
    import server  # noqa: E402 — must import after sys.path is set

    tools = asyncio.run(server.mcp.list_tools())
    trimmed = []
    for t in tools:
        # by_alias=True is required: fastmcp's Tool model exposes these fields as
        # snake_case (input_schema/output_schema) with camelCase aliases. Without it,
        # model_dump() returns the snake_case names and full["inputSchema"] KeyErrors
        # on every tool (issue #228) -- confirmed by reproducing locally against the
        # installed fastmcp 4.0.1.
        full = t.to_mcp_tool().model_dump(exclude_none=True, by_alias=True)
        trimmed.append({
            "name": full["name"],
            "description": full["description"],
            "inputSchema": full["inputSchema"],
        })
    return {"tools": trimmed}


def service_exists(service_id: str, project: str, region: str) -> bool:
    result = subprocess.run(
        ["gcloud", "alpha", "agent-registry", "services", "describe", service_id,
         f"--project={project}", f"--location={region}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--url", required=True, help="Cloud Run MCP service URL")
    parser.add_argument("--enabled", default="true", help="Set to 'false' to skip (no-op)")
    args = parser.parse_args()

    if args.enabled.lower() != "true":
        print("enable_custom_mcp is false — skipping Agent Registry registration (no-op).")
        return

    spec = build_tool_spec()
    content = json.dumps(spec, separators=(",", ":"))
    content_bytes = len(content.encode("utf-8"))
    print(f"Built tool-spec: {len(spec['tools'])} tools, {content_bytes} bytes "
          f"({'OK' if content_bytes <= 10_000 else 'EXCEEDS 10KB LIMIT'})")
    if content_bytes > 10_000:
        print("ERROR: tool-spec content exceeds the Agent Registry's documented 10KB "
              "limit — trim tool descriptions/schemas before retrying.", file=sys.stderr)
        sys.exit(1)

    exists = service_exists(SERVICE_ID, args.project, args.region)
    action = "update" if exists else "create"
    print(f"{'Updating' if exists else 'Creating'} Agent Registry service '{SERVICE_ID}' "
          f"in {args.project}/{args.region}...")

    cmd = [
        "gcloud", "alpha", "agent-registry", "services", action, SERVICE_ID,
        f"--project={args.project}",
        f"--location={args.region}",
        "--display-name=Custom SRE Kubernetes MCP",
        "--mcp-server-spec-type=tool-spec",
        f"--mcp-server-spec-content={content}",
        f"--interfaces=url={args.url},protocolBinding=JSONRPC",
    ]
    if not exists:
        cmd.insert(6, "--description=Read-only custom Kubernetes MCP server (Cloud Run) "
                       "- fallback to GKE Remote MCP, on-prem/non-GKE clusters via Connect Gateway")

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    print(f"Registered '{SERVICE_ID}' -> {args.url}")


if __name__ == "__main__":
    main()
