"""Section 9 (2026-09-08): agent/llm/registry.py caches ONE GeminiAdapter instance
process-wide, reused by every investigation a warm Agent Engine/Cloud Run instance
handles. Before this fix, session/usage counters were plain instance attributes --
two concurrent investigations sharing that one instance could have investigation
B's reset_session() zero investigation A's in-flight counters, and both
investigations' usage sum into one shared, misattributed total.

This test fires REAL concurrent threads against the REAL shared adapter instance
(only the network call itself is mocked) and proves each thread's own
reset_session()/accumulate/get_session_usage() sequence sees ONLY its own counts --
matching this session's established "prove it under genuine concurrency" standard
(see mcp/tests/test_multi_cluster_isolation.py's own concurrent test for the same
technique applied to a different subsystem).
"""
import threading
from types import SimpleNamespace

from agent.llm.gemini_adapter import GeminiAdapter


def _fake_response(input_tokens: int):
    return SimpleNamespace(
        text="{}",
        usage_metadata=SimpleNamespace(
            prompt_token_count=input_tokens,
            cached_content_token_count=0,
            candidates_token_count=1,
            thoughts_token_count=0,
            tool_use_prompt_token_count=0,
            total_token_count=input_tokens + 1,
        ),
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="STOP"))],
    )


def test_concurrent_investigations_never_cross_contaminate_session_usage():
    # ONE shared adapter instance AND one shared fake client, both set up ONCE
    # before threads start (never reassigned mid-test) -- exactly what
    # agent.llm.registry's module-level singleton does in production, where the
    # underlying genai.Client is intentionally shared across concurrent calls.
    # Only the SESSION COUNTERS need per-investigation isolation, not the client
    # itself; reassigning adapter._client per-thread would race on an attribute
    # this fix was never meant to isolate and prove nothing real.
    adapter = GeminiAdapter(model="gemini-2.5-flash")

    class _FakeModels:
        def generate_content(self, **kwargs):
            # Discriminate by which investigation is calling via the prompt text
            # itself -- the one piece of per-call data _call_model() actually
            # threads through to the client, safe to read concurrently since
            # each thread passes its own distinct string.
            prompt = kwargs["contents"]
            input_tokens = 10 if "investigation_a" in prompt else 1000
            return _fake_response(input_tokens)

    adapter._client = SimpleNamespace(models=_FakeModels())

    results = {}
    errors = []
    barrier = threading.Barrier(2)

    def worker(name: str, num_calls: int):
        try:
            adapter.reset_session()
            barrier.wait(timeout=5)  # maximize actual interleaving with the other thread
            for _ in range(num_calls):
                adapter._call_model(f"prompt for {name}", max_tokens=100)
            results[name] = adapter.get_session_usage()
        except Exception as exc:  # pragma: no cover - failure path surfaced via errors
            errors.append((name, exc))

    t_a = threading.Thread(target=worker, args=("investigation_a", 5))
    t_b = threading.Thread(target=worker, args=("investigation_b", 3))
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not errors, f"worker threads raised: {errors}"
    assert results["investigation_a"]["session_calls"] == 5
    assert results["investigation_a"]["session_tokens_input"] == 10 * 5
    assert results["investigation_b"]["session_calls"] == 3
    assert results["investigation_b"]["session_tokens_input"] == 1000 * 3
