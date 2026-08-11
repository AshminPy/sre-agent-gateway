"""
security.py — hardening primitives for the custom read-only K8s MCP server.

Covers PRODUCTION-LAUNCH-PLAN.md Priority 4's "Missing hardening" list:
  - request validation           (validate_namespace / validate_name)
  - namespace/cluster scope limits (enforce_namespace_scope)
  - response trimming            (trim_list / trim_text)
  - secret redaction             (redact)
  - timeouts                     (REQUEST_TIMEOUT, used by callers as _request_timeout)
  - response-size limits         (MAX_LIST_ITEMS / MAX_TEXT_CHARS)
  - rate limiting                (RateLimiter)
  - audit logging                (audit_log)

Deliberately dependency-light (stdlib only) so it can run inside the same
minimal Cloud Run image as server.py without growing requirements.txt.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import re
import threading
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger("sre-mcp")
audit_logger = logging.getLogger("sre-mcp.audit")


# ── Request validation ──────────────────────────────────────────────
# Kubernetes uses TWO different name-shape rules (apimachinery
# validation.go) and conflating them is a real bug, not a style choice —
# confirmed live against sre-lab: the kubelet-injected ConfigMap
# "kube-root-ca.crt" (present in every namespace) was rejected by an
# earlier, too-strict single regex here before this was caught in P4
# integration testing.
#   - Namespace names: DNS-1123 LABEL — single segment, no dots, max 63.
#   - Most other object names (Pod, Deployment, Service, ConfigMap, ...):
#     DNS-1123 SUBDOMAIN — dot-separated labels allowed, max 253.
# Rejecting anything outside these shapes before it reaches the Kubernetes
# API blocks injection via the K8s API path (e.g. "../", null bytes, shell
# metacharacters) at the MCP boundary.
_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_DNS_SUBDOMAIN_RE = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)
MAX_NAME_LEN = 253
MAX_NAMESPACE_LEN = 63


class ValidationError(ValueError):
    """Raised when a tool argument fails validation. Caught by server.py and
    turned into a structured {"error": ...} response — never an unhandled 500."""


def validate_namespace(namespace: str) -> str:
    if not namespace or not isinstance(namespace, str):
        raise ValidationError("namespace is required and must be a non-empty string")
    if len(namespace) > MAX_NAMESPACE_LEN:
        raise ValidationError(f"namespace exceeds max length {MAX_NAMESPACE_LEN}")
    if not _DNS_LABEL_RE.match(namespace):
        raise ValidationError(
            f"namespace '{namespace}' is not a valid Kubernetes namespace "
            "(lowercase alphanumeric and '-' only, RFC 1123 label)"
        )
    return namespace


def validate_name(name: str, field: str = "name") -> str:
    if not name or not isinstance(name, str):
        raise ValidationError(f"{field} is required and must be a non-empty string")
    if len(name) > MAX_NAME_LEN:
        raise ValidationError(f"{field} exceeds max length {MAX_NAME_LEN}")
    if not _DNS_SUBDOMAIN_RE.match(name):
        raise ValidationError(
            f"{field} '{name}' is not a valid Kubernetes resource name "
            "(lowercase alphanumeric, '-' and '.' only, RFC 1123 subdomain)"
        )
    return name


def validate_optional_name(name: str | None, field: str = "name") -> str | None:
    if name is None:
        return None
    return validate_name(name, field)


def validate_container(container: str | None) -> str | None:
    """Container names are DNS-1123 LABELs (like namespaces) — no dots, unlike
    most other object names."""
    if container is None:
        return None
    if not isinstance(container, str) or not container:
        raise ValidationError("container must be a non-empty string")
    if len(container) > MAX_NAMESPACE_LEN:
        raise ValidationError(f"container exceeds max length {MAX_NAMESPACE_LEN}")
    if not _DNS_LABEL_RE.match(container):
        raise ValidationError(
            f"container '{container}' is not a valid Kubernetes container name "
            "(lowercase alphanumeric and '-' only, RFC 1123 label)"
        )
    return container


def validate_tail_lines(tail_lines: int) -> int:
    if not isinstance(tail_lines, int) or isinstance(tail_lines, bool):
        raise ValidationError("tail_lines must be an integer")
    if tail_lines < 1:
        raise ValidationError("tail_lines must be >= 1")
    return min(tail_lines, MAX_TAIL_LINES)


# ── Namespace/cluster scope limits ──────────────────────────────────
# K8S_MCP_ALLOWED_NAMESPACES: comma-separated allowlist. Empty/unset = no
# namespace restriction beyond what the underlying RBAC (view ClusterRole,
# see docs/connect-gateway-onprem.md) already enforces server-side.
#
# Cluster scope is enforced structurally, not by an argument check: no tool
# in this server accepts a cluster/endpoint/context argument — the target
# cluster is fixed at process start by get_k8s_clients() (env-configured
# once, cached via lru_cache). There is no code path by which a tool call's
# arguments can redirect this process to a different cluster.
def _allowed_namespaces() -> set[str] | None:
    raw = os.environ.get("K8S_MCP_ALLOWED_NAMESPACES", "").strip()
    if not raw:
        return None
    return {ns.strip() for ns in raw.split(",") if ns.strip()}


def enforce_namespace_scope(namespace: str) -> None:
    allowed = _allowed_namespaces()
    if allowed is not None and namespace not in allowed:
        raise ValidationError(
            f"namespace '{namespace}' is outside this server's allowed scope "
            f"(K8S_MCP_ALLOWED_NAMESPACES={sorted(allowed)})"
        )


# ── Timeouts ─────────────────────────────────────────────────────────
# Passed as kubernetes-client's `_request_timeout=(connect, read)` on every
# API call so a hung/unreachable apiserver (e.g. Connect Gateway tunnel
# down — see docs/connect-gateway-onprem.md §8) fails fast instead of
# hanging the MCP request indefinitely.
REQUEST_CONNECT_TIMEOUT_S = float(os.environ.get("K8S_MCP_CONNECT_TIMEOUT_S", "5"))
REQUEST_READ_TIMEOUT_S = float(os.environ.get("K8S_MCP_READ_TIMEOUT_S", "15"))
REQUEST_TIMEOUT = (REQUEST_CONNECT_TIMEOUT_S, REQUEST_READ_TIMEOUT_S)


# ── Response-size limits / trimming ─────────────────────────────────
MAX_LIST_ITEMS = int(os.environ.get("K8S_MCP_MAX_LIST_ITEMS", "200"))
MAX_TEXT_CHARS = int(os.environ.get("K8S_MCP_MAX_TEXT_CHARS", "20000"))
MAX_TAIL_LINES = int(os.environ.get("K8S_MCP_MAX_TAIL_LINES", "2000"))


def trim_list(items: list, max_items: int = MAX_LIST_ITEMS) -> tuple[list, bool]:
    """Returns (possibly-truncated list, truncated flag)."""
    if len(items) <= max_items:
        return items, False
    return items[:max_items], True


def trim_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    if text is None:
        return text
    if len(text) <= max_chars:
        return text
    head = text[: max_chars // 2]
    tail = text[-(max_chars // 2):]
    return f"{head}\n...[truncated {len(text) - max_chars} chars]...\n{tail}"


# ── Secret redaction ─────────────────────────────────────────────────
# Applied to every tool response before it leaves the process — defense in
# depth even though this server has no get_secret/list_secrets tool and the
# `view` ClusterRole already excludes Secret objects server-side (RBAC).
# This catches secret-shaped VALUES that leak through other resources:
# ConfigMap data, env values baked into command/args, log lines, annotations.
_REDACT_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*")
_REDACT_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def redact(value: Any) -> Any:
    """
    Redact secret-shaped values from an arbitrary JSON-serializable tool
    response. Same technique as agent/gcs_client.py's redact() (serialize,
    regex over the string form, deserialize) but duplicated here on purpose
    — this module has no dependency on agent/, so the MCP image stays
    deployable standalone.
    """
    try:
        data_str = json.dumps(value, default=str)
    except Exception:
        return value

    data_str = _REDACT_EMAIL_RE.sub("[REDACTED_EMAIL]", data_str)
    data_str = _REDACT_BEARER_RE.sub("Bearer [REDACTED_TOKEN]", data_str)
    # Substring match on the JSON key (not exact-match) — real-world ConfigMap/env
    # keys are rarely the bare word "password"; they're "DB_PASSWORD",
    # "database-password", "stripe-api-key", etc. Matches the key name, redacts
    # only that key's value.
    data_str = re.sub(
        r'"([a-zA-Z0-9_.-]*(?:password|passwd|pwd|token|secret|api[_-]?key|apikey|'
        r'credential|private[_-]?key|access[_-]?key|client[_-]?secret|auth)'
        r'[a-zA-Z0-9_.-]*)"\s*:\s*"[^"]*"',
        r'"\1": "[REDACTED]"',
        data_str,
        flags=re.IGNORECASE,
    )

    try:
        return json.loads(data_str)
    except Exception:
        return value


# ── Rate limiting ────────────────────────────────────────────────────
# Simple in-process sliding-window limiter. Cloud Run runs this server
# stateless_http=True but the Python PROCESS persists across requests
# within an instance's lifetime, so in-memory state here is meaningful
# per-instance (min_instance_count=0/max=3 per cloudrun_mcp.tf — a global
# distributed limiter would need Redis/Memorystore, out of scope for a
# single-tenant internal read-only tool).
class RateLimiter:
    def __init__(self, max_calls: int, window_s: float):
        self.max_calls = max_calls
        self.window_s = window_s
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> tuple[bool, float]:
        """Returns (allowed, retry_after_s)."""
        now = time.monotonic()
        with self._lock:
            while self._calls and now - self._calls[0] > self.window_s:
                self._calls.popleft()
            if len(self._calls) >= self.max_calls:
                retry_after = round(self.window_s - (now - self._calls[0]), 2)
                return False, max(retry_after, 0.1)
            self._calls.append(now)
            return True, 0.0


RATE_LIMIT_MAX_CALLS = int(os.environ.get("K8S_MCP_RATE_LIMIT_MAX_CALLS", "60"))
RATE_LIMIT_WINDOW_S = float(os.environ.get("K8S_MCP_RATE_LIMIT_WINDOW_S", "60"))
_rate_limiter = RateLimiter(RATE_LIMIT_MAX_CALLS, RATE_LIMIT_WINDOW_S)


def check_rate_limit() -> None:
    allowed, retry_after = _rate_limiter.allow()
    if not allowed:
        raise RateLimitError(
            f"rate limit exceeded ({RATE_LIMIT_MAX_CALLS} calls / {RATE_LIMIT_WINDOW_S}s); "
            f"retry after {retry_after}s"
        )


class RateLimitError(RuntimeError):
    pass


# ── Audit logging ────────────────────────────────────────────────────
def audit_log(
    tool: str,
    arguments: dict,
    ok: bool,
    duration_s: float,
    error: str | None = None,
) -> None:
    """
    One structured log line per tool invocation — every call, success or
    failure, blocked or not. Uses standard `logging` so it lands in Cloud
    Run's stdout → Cloud Logging automatically (no extra IAM/API needed,
    unlike the google-cloud-logging client used for tool_executor's
    failure-only audit trail in agent/nodes/tool_executor.py).
    """
    safe_args = redact(arguments)
    audit_logger.info(
        json.dumps(
            {
                "event": "mcp_tool_call",
                "tool": tool,
                "arguments": safe_args,
                "ok": ok,
                "duration_s": round(duration_s, 3),
                "error": error,
            }
        )
    )


def guarded(namespace_fields: tuple[str, ...] = (), name_fields: tuple[str, ...] = ()) -> Callable:
    """
    Decorator applied to every @mcp.tool() function in server.py.

    Order of operations: rate limit → validate → scope-check → call →
    trim/redact → audit log. Any ValidationError/RateLimitError/unexpected
    exception is caught and turned into a structured {"error": ...} dict —
    a tool call NEVER raises out of this server, so one bad argument can't
    take down the whole MCP process.
    """
    namespace_fields = namespace_fields or ()
    name_fields = name_fields or ()

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.time()
            try:
                check_rate_limit()

                for field in namespace_fields:
                    if field in kwargs and kwargs[field] is not None:
                        ns = validate_namespace(kwargs[field])
                        enforce_namespace_scope(ns)
                for field in name_fields:
                    if field in kwargs and kwargs[field] is not None:
                        validate_name(kwargs[field], field)
                if "container" in kwargs:
                    validate_container(kwargs["container"])
                if "tail_lines" in kwargs and kwargs["tail_lines"] is not None:
                    kwargs["tail_lines"] = validate_tail_lines(kwargs["tail_lines"])

                result = fn(*args, **kwargs)
                result = _postprocess(result)

                duration = time.time() - start
                audit_log(fn.__name__, kwargs, ok=True, duration_s=duration)
                return result

            except (ValidationError, RateLimitError) as e:
                duration = time.time() - start
                audit_log(fn.__name__, kwargs, ok=False, duration_s=duration, error=str(e))
                log.warning("guarded tool=%s rejected: %s", fn.__name__, e)
                return {"error": str(e), "ok": False}

            except Exception as e:  # noqa: BLE001 — last line of defense, never crash the server
                duration = time.time() - start
                audit_log(fn.__name__, kwargs, ok=False, duration_s=duration, error=str(e))
                log.error("guarded tool=%s failed: %s", fn.__name__, e)
                return {"error": str(e), "ok": False}

        return wrapper

    return decorator


def _postprocess(result: Any) -> Any:
    """Applies redaction + response trimming to a tool's return value."""
    result = redact(result)
    if isinstance(result, dict):
        for key in ("logs",):
            if key in result and isinstance(result[key], str):
                result[key] = trim_text(result[key])
        for key, val in list(result.items()):
            if isinstance(val, list) and len(val) > MAX_LIST_ITEMS:
                trimmed, truncated = trim_list(val)
                result[key] = trimmed
                result["truncated"] = truncated
    return result
# path-filter test: mcp/ touch, expect mcp filter=true
