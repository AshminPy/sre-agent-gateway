"""Regression test for issue #32: the output-side Model Armor `blocked` verdict was
discarded entirely -- since _sanitize() never rewrites text (it only blocks-or-passes,
confirmed from its own docstring/comment), the old `sanitized_out != summary_text` check
could NEVER be true, so a real MATCH_FOUND on agent output had zero effect: no redaction,
no block, no requires_human_review change, no field in the response.
"""
import agent.main as main_mod
from agent.main import SREAgent


def _stub_common(monkeypatch, investigate_result: dict):
    """Stub out everything query() touches besides the output-sanitization step under
    test, so this stays a focused test of that one code path."""
    monkeypatch.setattr(main_mod, "investigate", lambda payload: dict(investigate_result))
    monkeypatch.setattr(SREAgent, "_mb_recall", classmethod(lambda cls, c, n: ""))
    monkeypatch.setattr(SREAgent, "_recall_memory", classmethod(lambda cls, c, n: ""))
    monkeypatch.setattr(SREAgent, "_save_to_gcs", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(SREAgent, "_mb_store", classmethod(lambda cls, *a, **k: None))
    monkeypatch.setattr(SREAgent, "_save_memory", classmethod(lambda cls, *a, **k: None))


def _base_investigate_result():
    return {
        "run_id": "run_test_armor",
        "status": "done",
        "summary": {"likely_root_cause": "Real root cause from a real evidence chain", "confidence_score": 0.8},
        "confidence": 0.8,
        "confidence_band": "auto",
        "requires_human_review": False,
    }


def test_blocked_output_withholds_the_summary_and_forces_human_review(monkeypatch):
    _stub_common(monkeypatch, _base_investigate_result())

    def fake_sanitize(cls, text, is_output=False):
        if is_output:
            return text, True  # Model Armor MATCH_FOUND on the output
        return text, False  # input sanitization passes clean

    monkeypatch.setattr(SREAgent, "_sanitize", classmethod(fake_sanitize))

    result = SREAgent.query(query="Pod x is crashing", cluster="sre-test-cluster", namespace="test-incidents")

    assert result["content_flagged"] is True
    assert result["requires_human_review"] is True
    assert result["summary"]["content_flagged"] is True
    assert result["summary"]["requires_human_review"] is True
    # The real root cause text must NOT leak into the response once blocked.
    assert "Real root cause from a real evidence chain" not in str(result["summary"])


def test_clean_output_is_unaffected_no_regression(monkeypatch):
    """Sanity check: a normal, unflagged output must pass through unchanged -- proves the
    fix didn't accidentally start withholding every response."""
    base = _base_investigate_result()
    _stub_common(monkeypatch, base)

    def fake_sanitize(cls, text, is_output=False):
        return text, False  # never blocked

    monkeypatch.setattr(SREAgent, "_sanitize", classmethod(fake_sanitize))

    result = SREAgent.query(query="Pod x is crashing", cluster="sre-test-cluster", namespace="test-incidents")

    assert "content_flagged" not in result
    assert result["requires_human_review"] is False
    assert result["summary"]["likely_root_cause"] == "Real root cause from a real evidence chain"
