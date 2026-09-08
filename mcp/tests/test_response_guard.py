"""
test_response_guard.py — unit tests for the central Model Armor response
interceptor (response_guard.py). Mocks the Model Armor client directly
(no live GCP call) — real-traffic proof lives in PHASE1_EVIDENCE_LOG.md /
the #203 follow-on investigation, not here.

Uses asyncio.run() directly rather than pytest-asyncio, matching the existing
async-test pattern in test_live_connect_gateway.py — pytest-asyncio is not a
repo dependency.
"""
import asyncio

import mcp_types as mt
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from response_guard import ModelArmorResponseGuard, UNINSPECTED_MARKER, _extract_text


def _make_result(text: str) -> ToolResult:
    return ToolResult(content=[mt.TextContent(type="text", text=text)])


def _make_context(tool_name: str = "get_pod_logs") -> MiddlewareContext:
    message = mt.CallToolRequestParams(name=tool_name, arguments={})
    return MiddlewareContext(message=message, method="tools/call", type="request")


class _FakeSanitizationResult:
    def __init__(self, match_state):
        self.filter_match_state = match_state


class _FakeResponse:
    def __init__(self, match_state):
        self.sanitization_result = _FakeSanitizationResult(match_state)


class _FakeClient:
    """Stand-in for modelarmor_v1.ModelArmorClient."""

    def __init__(self, match_state=None, raise_exc=None):
        self._match_state = match_state
        self._raise_exc = raise_exc
        self.calls = []

    def sanitize_model_response(self, request):
        self.calls.append(request)
        if self._raise_exc:
            raise self._raise_exc
        return _FakeResponse(self._match_state)


def _guard_not_configured() -> ModelArmorResponseGuard:
    guard = ModelArmorResponseGuard.__new__(ModelArmorResponseGuard)
    guard._client = None
    return guard


def _guard_with_client(client) -> ModelArmorResponseGuard:
    guard = ModelArmorResponseGuard.__new__(ModelArmorResponseGuard)
    guard._client = client
    return guard


def test_not_configured_passes_through_unchanged():
    guard = _guard_not_configured()
    original = _make_result("pod logs: everything is fine")

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())
    assert result is original


def test_allow_passes_through_unchanged():
    from google.cloud import modelarmor_v1

    client = _FakeClient(match_state=modelarmor_v1.FilterMatchState.NO_MATCH_FOUND)
    guard = _guard_with_client(client)
    original = _make_result("pod is Running, 0 restarts")

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())
    assert result is original
    assert result.is_error is False
    assert len(client.calls) == 1


def test_block_returns_controlled_error_not_original_content():
    from google.cloud import modelarmor_v1

    client = _FakeClient(match_state=modelarmor_v1.FilterMatchState.MATCH_FOUND)
    guard = _guard_with_client(client)
    original = _make_result("https://testsafebrowsing.appspot.com/s/malware.html")

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())
    assert result is not original
    assert result.is_error is True
    text = _extract_text(result)
    assert "testsafebrowsing" not in text
    assert "withheld" in text.lower()


def test_sanitize_error_fails_open_and_logs(caplog):
    client = _FakeClient(raise_exc=RuntimeError("regional endpoint unreachable"))
    guard = _guard_with_client(client)
    original = _make_result("pod events: nothing unusual")

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    with caplog.at_level("ERROR", logger="sre-mcp.response_guard"):
        result = asyncio.run(_run())

    # Section 8 (2026-09-08): fail-open still delivers the REAL content (never
    # withheld, unlike an actual block) -- but it is no longer indistinguishable
    # from a fully-inspected response. The original object is intentionally no
    # longer returned as-is; a marker is prepended so evidence_extractor.py can
    # tell this specific tool response bypassed Model Armor.
    assert result is not original
    assert any("model_armor_fail_open" in r.message for r in caplog.records)
    text = _extract_text(result)
    assert UNINSPECTED_MARKER in text
    assert "pod events: nothing unusual" in text  # real content still delivered, not withheld


def test_structured_only_content_is_still_inspected():
    """FastMCP JSON-serializes structured_content into a text block even when
    no explicit text content is given — this guard must inspect that
    serialized form too, not skip structured-only tool results."""
    from google.cloud import modelarmor_v1

    client = _FakeClient(match_state=modelarmor_v1.FilterMatchState.NO_MATCH_FOUND)
    guard = _guard_with_client(client)
    original = ToolResult(structured_content={"pods": []})

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())
    assert result is original
    assert len(client.calls) == 1


def test_init_failure_logs_alertable_error_not_a_warning(monkeypatch, caplog):
    """Section 8 (2026-09-08): a broken Model Armor client at construction time used
    to log a plain WARNING with no alertable signal, then silently and permanently
    disable sanitization for the rest of the process. Must now log at ERROR with the
    exact string the matching Terraform log-based metric filters on."""
    # MODEL_ARMOR_RESPONSE_TEMPLATE is read once at module import time (response_guard.py:40),
    # so the module-level attribute must be patched directly -- setenv alone has no effect
    # on an already-imported module-level constant.
    import response_guard
    monkeypatch.setattr(response_guard, "MODEL_ARMOR_RESPONSE_TEMPLATE", "projects/x/locations/us-central1/templates/y")

    def _boom(*a, **k):
        raise RuntimeError("permission denied")

    monkeypatch.setattr("google.cloud.modelarmor_v1.ModelArmorClient", _boom)

    with caplog.at_level("ERROR", logger="sre-mcp.response_guard"):
        guard = ModelArmorResponseGuard()

    assert guard._client is None
    assert any("model_armor_guard_init_failed" in r.message for r in caplog.records)
    assert all(r.levelname == "ERROR" for r in caplog.records if "model_armor_guard_init_failed" in r.message)


def test_fail_open_marks_real_json_tool_result_without_corrupting_it():
    """The realistic case -- every real tool in this server returns a JSON-serializable
    dict (mcp/security.py's _postprocess), never plain prose. Proves the fail-open marker
    round-trips through agent/mcp_client.py's OWN parsing shape (content[0], json.loads)
    without losing or altering a single real field -- this is the exact function that
    would have silently swallowed the real tool result if the marker had been prepended
    as a separate content block instead of injected into the existing JSON payload."""
    import json as _json

    client = _FakeClient(raise_exc=RuntimeError("regional endpoint unreachable"))
    guard = _guard_with_client(client)
    real_payload = {"pods": [{"name": "checkout-abc123", "phase": "Running"}], "count": 1}
    original = ToolResult(content=[mt.TextContent(type="text", text=_json.dumps(real_payload))])

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())

    # Simulates agent/mcp_client.py::_extract_content()'s exact real logic: read
    # content[0], json.loads its text.
    parsed = _json.loads(result.content[0].text)
    assert parsed["_uninspected"] is True
    assert parsed["pods"] == real_payload["pods"]  # real evidence intact, not replaced
    assert parsed["count"] == 1


def test_fail_open_marks_structured_content_too():
    client = _FakeClient(raise_exc=RuntimeError("regional endpoint unreachable"))
    guard = _guard_with_client(client)
    original = ToolResult(structured_content={"pods": [], "count": 0})

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())
    assert result.structured_content["_uninspected"] is True
    assert result.structured_content["count"] == 0
