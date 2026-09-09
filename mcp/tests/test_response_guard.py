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
    guard._degraded = False
    return guard


def _guard_with_client(client) -> ModelArmorResponseGuard:
    guard = ModelArmorResponseGuard.__new__(ModelArmorResponseGuard)
    guard._client = client
    guard._degraded = False
    return guard


def _guard_degraded() -> ModelArmorResponseGuard:
    """Section 8 correction (2026-09-08): a guard in the collapsed "not actually
    checking anything, but must say so" state -- reached either by a required
    deployment with a missing template, or a template that failed to construct
    a client. Both origins produce identical on_call_tool behavior by design;
    see response_guard.py's _degraded docstring for why that's intentional."""
    guard = ModelArmorResponseGuard.__new__(ModelArmorResponseGuard)
    guard._client = None
    guard._degraded = True
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
    assert guard._degraded is True
    assert any("model_armor_guard_init_failed" in r.message for r in caplog.records)
    assert all(r.levelname == "ERROR" for r in caplog.records if "model_armor_guard_init_failed" in r.message)


def test_required_but_template_missing_is_alertable_error_not_info(monkeypatch, caplog):
    """Section 8 correction (2026-09-08): the gap the first fix missed. A deployment
    that REQUIRES the guard (MODEL_ARMOR_RESPONSE_GUARD_REQUIRED=true) but has no
    template at all must be treated the same as an init failure -- ERROR level,
    same alertable log string, guard marked degraded. Must NOT log a plain INFO
    and silently pass through, the way the non-required (local/dev) case correctly
    does."""
    import response_guard
    monkeypatch.setattr(response_guard, "MODEL_ARMOR_RESPONSE_TEMPLATE", "")
    monkeypatch.setattr(response_guard, "MODEL_ARMOR_RESPONSE_GUARD_REQUIRED", True)

    with caplog.at_level("ERROR", logger="sre-mcp.response_guard"):
        guard = ModelArmorResponseGuard()

    assert guard._client is None
    assert guard._degraded is True
    assert any("model_armor_guard_init_failed" in r.message for r in caplog.records)
    assert all(r.levelname == "ERROR" for r in caplog.records if "model_armor_guard_init_failed" in r.message)


def test_not_required_and_template_missing_stays_intentional_passthrough(monkeypatch, caplog):
    """The other half of the same fork: required=false + missing template is the
    legitimate local/dev off-switch, not a failure -- must stay INFO, not ERROR,
    and must NOT set _degraded (regression guard so the fix above doesn't
    over-correct into flagging the intentional case too)."""
    import response_guard
    monkeypatch.setattr(response_guard, "MODEL_ARMOR_RESPONSE_TEMPLATE", "")
    monkeypatch.setattr(response_guard, "MODEL_ARMOR_RESPONSE_GUARD_REQUIRED", False)

    with caplog.at_level("INFO", logger="sre-mcp.response_guard"):
        guard = ModelArmorResponseGuard()

    assert guard._client is None
    assert guard._degraded is False
    assert not any(r.levelname == "ERROR" for r in caplog.records)


def test_required_but_template_missing_marks_every_result_uninspected():
    """The actual behavioral fix, not just the log level: on_call_tool must mark
    results in the required-but-missing state, exactly like a live sanitize
    failure -- this is the case the original _init_failed-only design left open."""
    guard = _guard_degraded()
    original = _make_result("pod is Running, 0 restarts")

    async def call_next(ctx):
        return original

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    result = asyncio.run(_run())
    assert result is not original
    assert UNINSPECTED_MARKER in _extract_text(result) or '"_uninspected": true' in _extract_text(result)


def test_init_failure_marks_every_subsequent_result_uninspected(monkeypatch):
    """Covers 'repeated calls after init exception' explicitly: a guard that failed
    to construct must mark EVERY tool result for the rest of its lifetime, not just
    the first one -- proves there is no one-shot/reset behavior hiding in on_call_tool."""
    import json as _json
    import response_guard
    monkeypatch.setattr(response_guard, "MODEL_ARMOR_RESPONSE_TEMPLATE", "projects/x/locations/us-central1/templates/y")
    monkeypatch.setattr("google.cloud.modelarmor_v1.ModelArmorClient", lambda **k: (_ for _ in ()).throw(RuntimeError("permission denied")))

    guard = ModelArmorResponseGuard()
    assert guard._degraded is True

    async def call_next(ctx):
        return ToolResult(content=[mt.TextContent(type="text", text=_json.dumps({"pods": []}))])

    async def _run():
        return await guard.on_call_tool(_make_context(), call_next)

    for _ in range(3):
        result = asyncio.run(_run())
        parsed = _json.loads(result.content[0].text)
        assert parsed["_uninspected"] is True


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
