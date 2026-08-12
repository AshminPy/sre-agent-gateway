"""Unit tests for agent/mcp_client.py's _parse_response().

Covers GitHub issue #31: the SSE parser must not return an empty/notification
first frame as a false-positive success. It should keep scanning later
`data:` frames until it finds one with genuinely non-empty result/content,
and return None only if no frame ever has real content.
"""
from agent.mcp_client import _parse_response


def test_sse_empty_first_frame_then_real_content_returns_real_content():
    body = (
        'data: {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}\n'
        '\n'
        'data: {"jsonrpc": "2.0", "result": {}}\n'
        '\n'
        'data: {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "{\\"pods\\": 3}"}]}}\n'
    )
    assert _parse_response(body) == {"pods": 3}


def test_sse_all_frames_empty_returns_none():
    body = (
        'data: {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}\n'
        '\n'
        'data: {"jsonrpc": "2.0", "result": {}}\n'
        '\n'
        'data: {"jsonrpc": "2.0", "result": {"content": []}}\n'
    )
    assert _parse_response(body) is None


def test_sse_single_non_empty_frame_still_works():
    body = 'data: {"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "hello"}]}}\n'
    assert _parse_response(body) == "hello"


def test_sse_text_content_json_decoded_when_frame_is_later():
    body = (
        'data: {"jsonrpc": "2.0", "result": {}}\n'
        '\n'
        'data: {"jsonrpc": "2.0", "result": {"structuredContent": {"status": "ok"}}}\n'
    )
    assert _parse_response(body) == {"status": "ok"}


def test_direct_json_fallback_unchanged():
    body = '{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "42"}]}}'
    assert _parse_response(body) == 42


def test_direct_json_fallback_empty_result_returns_none():
    body = '{"jsonrpc": "2.0", "result": {}}'
    assert _parse_response(body) is None
