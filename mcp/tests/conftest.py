"""
mcp/tests/conftest.py — makes mcp/*.py and mcp/tools/*.py importable the
same way server.py imports them (top-level `from security import ...`,
`from tools.pods import ...`), by putting mcp/ on sys.path before collection.

Run with:  cd mcp && pytest tests/ -v
(mcp/ is a self-contained deployable unit with its own requirements.txt —
kept out of the repo-root tests/ + pyproject.toml pytest config on purpose,
same reasoning as mcp/Dockerfile being separate from the agent's.)
"""
import os
import sys

_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MCP_DIR not in sys.path:
    sys.path.insert(0, _MCP_DIR)
