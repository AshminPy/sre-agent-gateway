"""
Enforces PRODUCTION-LAUNCH-PLAN.md P4 acceptance: "no write/exec/port-forward
capability anywhere" — as a permanent regression test, not just a one-time
grep during code review. Fails CI if anyone ever adds a mutating call.
"""
import os
import re

_MCP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Actual kubernetes-client method name prefixes/patterns that mutate cluster
# state, execute in a container, or open a proxy/port-forward tunnel.
_MUTATING_PATTERNS = [
    r"\bcreate_namespaced_\w+\(",
    r"\bcreate_node\(",
    r"\bcreate_persistent_volume\(",
    r"\bdelete_namespaced_\w+\(",
    r"\bdelete_node\(",
    r"\bdelete_persistent_volume\(",
    r"\bdelete_collection_\w+\(",
    r"\bpatch_namespaced_\w+\(",
    r"\bpatch_node\w*\(",
    r"\breplace_namespaced_\w+\(",
    r"\breplace_node\w*\(",
    r"\breplace_persistent_volume\w*\(",
    r"\bconnect_\w*(exec|proxy|attach)\w*\(",
    r"\.exec\(",
    r"port_forward",
]
_MUTATING_RE = re.compile("|".join(_MUTATING_PATTERNS))


def _py_files():
    for root, dirs, files in os.walk(_MCP_DIR):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "tests")]
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


def test_no_mutating_kubernetes_api_calls_anywhere_in_mcp():
    violations = []
    for path in _py_files():
        with open(path, "r") as fh:
            for lineno, line in enumerate(fh, start=1):
                if _MUTATING_RE.search(line):
                    violations.append(f"{path}:{lineno}: {line.strip()}")
    assert not violations, (
        "Found mutating/exec/port-forward Kubernetes API call(s) in the "
        "read-only MCP server:\n" + "\n".join(violations)
    )


def test_no_secret_resource_tools_exposed():
    """No get_secret/list_secrets tool anywhere — Secrets stay out of this server
    entirely, on top of the `view` ClusterRole already excluding them server-side."""
    server_path = os.path.join(_MCP_DIR, "server.py")
    with open(server_path) as fh:
        content = fh.read()
    assert "def list_secrets" not in content
    assert "def get_secret" not in content
    assert "def describe_secret" not in content
    assert "read_namespaced_secret" not in content
