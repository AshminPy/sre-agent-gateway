"""Unit tests for security.py — validation, redaction, trimming, rate limiting, guarded()."""

import pytest

import security as sec


# ── Validation ────────────────────────────────────────────────────────

def test_validate_namespace_accepts_valid():
    assert sec.validate_namespace("test-incidents") == "test-incidents"


def test_validate_namespace_rejects_dots():
    """Namespaces are DNS-1123 LABELs — dots are not valid, unlike most object names."""
    with pytest.raises(sec.ValidationError):
        sec.validate_namespace("kube-root-ca.crt")


def test_validate_namespace_rejects_empty():
    with pytest.raises(sec.ValidationError):
        sec.validate_namespace("")


def test_validate_namespace_rejects_path_traversal():
    with pytest.raises(sec.ValidationError):
        sec.validate_namespace("../../etc")


def test_validate_name_accepts_dotted_subdomain():
    """Regression: kube-root-ca.crt is a REAL built-in ConfigMap name present in every
    namespace — confirmed live against sre-lab. A validator that rejects it is broken."""
    assert sec.validate_name("kube-root-ca.crt") == "kube-root-ca.crt"


def test_validate_name_rejects_shell_metacharacters():
    for bad in ["pod; rm -rf /", "pod$(whoami)", "pod`id`", "pod|cat", "pod name"]:
        with pytest.raises(sec.ValidationError):
            sec.validate_name(bad)


def test_validate_name_rejects_too_long():
    with pytest.raises(sec.ValidationError):
        sec.validate_name("a" * 300)


def test_validate_container_rejects_dots():
    """Container names ARE DNS-1123 labels (no dots), unlike most other K8s object names."""
    with pytest.raises(sec.ValidationError):
        sec.validate_container("my.container")


def test_validate_container_accepts_valid():
    assert sec.validate_container("app") == "app"
    assert sec.validate_container(None) is None


def test_validate_tail_lines_caps_at_max():
    assert sec.validate_tail_lines(10) == 10
    assert sec.validate_tail_lines(999999) == sec.MAX_TAIL_LINES


def test_validate_tail_lines_rejects_non_positive():
    with pytest.raises(sec.ValidationError):
        sec.validate_tail_lines(0)
    with pytest.raises(sec.ValidationError):
        sec.validate_tail_lines(-5)


def test_validate_tail_lines_rejects_bool():
    """bool is a subclass of int in Python — must be explicitly excluded."""
    with pytest.raises(sec.ValidationError):
        sec.validate_tail_lines(True)


# ── Namespace scope limits ───────────────────────────────────────────

def test_enforce_namespace_scope_no_restriction_by_default(monkeypatch):
    monkeypatch.delenv("K8S_MCP_ALLOWED_NAMESPACES", raising=False)
    sec.enforce_namespace_scope("anything")  # must not raise


def test_enforce_namespace_scope_rejects_out_of_scope(monkeypatch):
    monkeypatch.setenv("K8S_MCP_ALLOWED_NAMESPACES", "test-incidents,default")
    with pytest.raises(sec.ValidationError):
        sec.enforce_namespace_scope("kube-system")


def test_enforce_namespace_scope_allows_in_scope(monkeypatch):
    monkeypatch.setenv("K8S_MCP_ALLOWED_NAMESPACES", "test-incidents,default")
    sec.enforce_namespace_scope("default")  # must not raise


# ── Secret redaction ─────────────────────────────────────────────────

def test_redact_masks_secret_shaped_keys():
    raw = {"data": {"DB_PASSWORD": "hunter2", "stripe-api-key": "sk_live_abc"}}
    out = sec.redact(raw)
    assert out["data"]["DB_PASSWORD"] == "[REDACTED]"
    assert out["data"]["stripe-api-key"] == "[REDACTED]"


def test_redact_preserves_non_secret_values():
    raw = {"data": {"LOG_LEVEL": "debug", "note": "hello"}}
    out = sec.redact(raw)
    assert out == raw


def test_redact_masks_bearer_tokens_in_free_text():
    raw = {"logs": "auth header: Bearer abc123.def456-ghi"}
    out = sec.redact(raw)
    assert "abc123" not in out["logs"]
    assert "[REDACTED_TOKEN]" in out["logs"]


def test_redact_masks_emails():
    raw = {"annotations": {"owner": "someone@example.com"}}
    out = sec.redact(raw)
    assert out["annotations"]["owner"] == "[REDACTED_EMAIL]"


def test_redact_does_not_touch_ca_certificates():
    """A CA cert PEM is not a secret — must not be falsely redacted just because
    it's long/base64-shaped. Confirmed against the real kube-root-ca.crt ConfigMap."""
    raw = {"data": {"ca.crt": "-----BEGIN CERTIFICATE-----\nMIIDBTC...\n-----END CERTIFICATE-----\n"}}
    out = sec.redact(raw)
    assert out == raw


# ── Response trimming ─────────────────────────────────────────────────

def test_trim_text_under_limit_unchanged():
    assert sec.trim_text("short", max_chars=100) == "short"


def test_trim_text_over_limit_truncated():
    big = "x" * 1000
    out = sec.trim_text(big, max_chars=100)
    assert len(out) < len(big)
    assert "truncated" in out


def test_trim_list_under_limit_unchanged():
    items, truncated = sec.trim_list([1, 2, 3], max_items=10)
    assert items == [1, 2, 3]
    assert truncated is False


def test_trim_list_over_limit_truncated():
    items, truncated = sec.trim_list(list(range(500)), max_items=50)
    assert len(items) == 50
    assert truncated is True


# ── Rate limiting ─────────────────────────────────────────────────────

def test_rate_limiter_allows_up_to_max():
    rl = sec.RateLimiter(max_calls=3, window_s=60)
    assert [rl.allow()[0] for _ in range(3)] == [True, True, True]


def test_rate_limiter_blocks_over_max():
    rl = sec.RateLimiter(max_calls=3, window_s=60)
    for _ in range(3):
        rl.allow()
    allowed, retry_after = rl.allow()
    assert allowed is False
    assert retry_after > 0


def test_rate_limiter_window_expiry(monkeypatch):
    rl = sec.RateLimiter(max_calls=1, window_s=10)
    t = [1000.0]
    monkeypatch.setattr(sec.time, "monotonic", lambda: t[0])
    assert rl.allow()[0] is True
    assert rl.allow()[0] is False
    t[0] += 11  # past the window
    assert rl.allow()[0] is True


# ── guarded() decorator — integration of the above ───────────────────

def test_guarded_rejects_invalid_namespace():
    @sec.guarded(namespace_fields=("namespace",))
    def fn(namespace: str) -> dict:
        return {"ok": True}

    result = fn(namespace="bad namespace!")
    assert result["error"]
    assert result["ok"] is False


def test_guarded_passes_valid_args_through():
    # require_cluster_id=False: this test exercises generic arg passthrough, not
    # cluster scoping -- see the dedicated cluster_id tests below for that.
    @sec.guarded(namespace_fields=("namespace",), name_fields=("pod_name",), require_cluster_id=False)
    def fn(namespace: str, pod_name: str) -> dict:
        return {"namespace": namespace, "pod_name": pod_name}

    result = fn(namespace="test-incidents", pod_name="my-pod")
    assert result == {"namespace": "test-incidents", "pod_name": "my-pod"}


def test_guarded_never_raises_on_unexpected_exception():
    @sec.guarded(require_cluster_id=False)
    def fn() -> dict:
        raise RuntimeError("kaboom")

    result = fn()
    assert result == {"error": "kaboom", "ok": False}


def test_guarded_redacts_return_value():
    @sec.guarded(require_cluster_id=False)
    def fn() -> dict:
        return {"data": {"password": "hunter2"}}

    result = fn()
    assert result["data"]["password"] == "[REDACTED]"


def test_guarded_enforces_rate_limit(monkeypatch):
    limiter = sec.RateLimiter(max_calls=2, window_s=60)
    monkeypatch.setattr(sec, "_rate_limiter", limiter)

    @sec.guarded(require_cluster_id=False)
    def fn() -> dict:
        return {"ok": True}

    assert fn()["ok"] is True
    assert fn()["ok"] is True
    third = fn()
    assert third["ok"] is False
    assert "rate limit" in third["error"]


def test_guarded_preserves_function_metadata_for_fastmcp_introspection():
    """FastMCP builds the MCP tool schema from the wrapped function's signature via
    inspect.signature(), which follows __wrapped__ — functools.wraps must be used,
    not manual __name__/__doc__ copying, or every tool's argument schema breaks."""
    import inspect

    @sec.guarded(namespace_fields=("namespace",))
    def describe_thing(namespace: str, name: str) -> dict:
        """Docstring."""
        return {}

    sig = inspect.signature(describe_thing)
    assert list(sig.parameters.keys()) == ["namespace", "name"]
    assert describe_thing.__name__ == "describe_thing"
    assert describe_thing.__doc__ == "Docstring."
