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

from response_guard import ModelArmorResponseGuard, _extract_text


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

    assert result is original  # fail open: real content still delivered
    assert any("model_armor_fail_open" in r.message for r in caplog.records)


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
