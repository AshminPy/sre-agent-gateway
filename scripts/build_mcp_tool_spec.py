#!/usr/bin/env python3
"""Generate mcp/tool_spec.json -- the MCP tools/list spec Terraform registers in the
Agent Registry (iac/agent/agent_registry_mcp.tf reads it with file()).

Must run BEFORE `terraform plan`/`apply`, exactly like scripts/package_agent.sh does
for agent.tar.gz. Terraform cannot introspect a running Python MCP server, so the spec
is produced here and consumed as a plain file.

Extracted from the retired scripts/register_custom_mcp.py, which used to build this
same content and then register it with an imperative `gcloud alpha agent-registry
services create/update` call. Only the generation half survives; Terraform now owns
the registration itself, so a hand-edited or deleted registration is reconciled on the
next apply instead of silently persisting (issue #33).

Content is capped at 10KB by the Agent Registry API (--mcp-server-spec-content docs),
so tool entries are trimmed to name/description/inputSchema -- no outputSchema/meta.
Current real size: ~7.3KB for 27 tools.

Usage:  python3 scripts/build_mcp_tool_spec.py [--check]
        --check  verify the committed file matches what this script generates
                 (exit 1 if stale) without rewriting it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "mcp" / "tool_spec.json"
MAX_CONTENT_BYTES = 10_000


def build_tool_spec() -> dict:
    """Import mcp/server.py and produce a trimmed, MCP-tools/list-shaped spec."""
    sys.path.insert(0, str(REPO_ROOT / "mcp"))
    import server  # noqa: E402 -- must import after sys.path is set

    tools = asyncio.run(server.mcp.list_tools())
    trimmed = []
    for t in tools:
        # by_alias=True is required: fastmcp's Tool model exposes these fields as
        # snake_case (input_schema) with camelCase aliases. Without it, model_dump()
        # returns the snake_case names and full["inputSchema"] KeyErrors on every
        # tool (issue #228).
        full = t.to_mcp_tool().model_dump(exclude_none=True, by_alias=True)
        trimmed.append({
            "name": full["name"],
            "description": full["description"],
            "inputSchema": full["inputSchema"],
        })
    return {"tools": trimmed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="Verify the committed file is current; do not rewrite it.")
    args = parser.parse_args()

    spec = build_tool_spec()
    # Compact separators keep the payload under the API's 10KB ceiling. Sorted keys
    # make the output byte-stable across runs so an unchanged mcp/ never produces a
    # spurious Terraform diff.
    content = json.dumps(spec, separators=(",", ":"), sort_keys=True)
    size = len(content.encode("utf-8"))

    if size > MAX_CONTENT_BYTES:
        print(f"ERROR: tool-spec content is {size} bytes, over the Agent Registry's "
              f"documented {MAX_CONTENT_BYTES}-byte limit -- trim tool "
              f"descriptions/schemas before retrying.", file=sys.stderr)
        sys.exit(1)

    if args.check:
        if not OUT_PATH.is_file():
            print(f"ERROR: {OUT_PATH} does not exist -- run this script without "
                  f"--check to generate it.", file=sys.stderr)
            sys.exit(1)
        if OUT_PATH.read_text() != content:
            print(f"ERROR: {OUT_PATH} is stale -- mcp/ changed without regenerating "
                  f"it. Run: python3 scripts/build_mcp_tool_spec.py", file=sys.stderr)
            sys.exit(1)
        print(f"{OUT_PATH.relative_to(REPO_ROOT)} is current "
              f"({len(spec['tools'])} tools, {size} bytes)")
        return

    OUT_PATH.write_text(content)
    print(f"Wrote {OUT_PATH.relative_to(REPO_ROOT)} "
          f"({len(spec['tools'])} tools, {size} bytes, limit {MAX_CONTENT_BYTES})")


if __name__ == "__main__":
    main()
