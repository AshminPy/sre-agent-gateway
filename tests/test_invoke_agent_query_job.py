"""Mock-only tests for invoke_agent.py's async transport path (issue #103, Slice 1).

Proves the caller-side polling/parsing logic against a fake AgentEngines client --
no real GCP calls, no real sleeping (time.sleep/time.time are monkeypatched). This
is transport-only: it does not exercise SAFETY_BUDGET_SECONDS, max_duration_seconds,
or any agent-side behavior, since Slice 1 changes none of those.
"""
import json
import os

import pytest

# invoke_agent.py sys.exit()s at import time if these are unset (its own guard for
# real usage) -- set harmless test values before importing, same spirit as other
# env-dependent module tests in this repo (tests/test_loop_controller_safety_budget.py).
os.environ.setdefault("PROJECT_ID", "test-project")
os.environ.setdefault("REASONING_ENGINE_ID", "test-engine-id")

import invoke_agent  # noqa: E402 -- must follow the env var setup above


class _FakeJob:
    def __init__(self, job_name, output_gcs_uri):
        self.job_name = job_name
        self.output_gcs_uri = output_gcs_uri


class _FakeCheck:
    def __init__(self, status, result=None):
        self.status = status
        self.result = result


class _FakeAgentEngines:
    """Records run_query_job's config and replays a scripted check_query_job sequence."""

    def __init__(self, check_sequence):
        self.run_query_job_calls = []
        self.check_query_job_calls = []
        self._check_sequence = list(check_sequence)

    def run_query_job(self, *, name, config):
        self.run_query_job_calls.append({"name": name, "config": config})
        return _FakeJob(job_name="projects/p/locations/l/operations/op123",
                         output_gcs_uri=config["output_gcs_uri"])

    def check_query_job(self, *, name, config=None):
        self.check_query_job_calls.append({"name": name, "config": config})
        return self._check_sequence.pop(0)


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # E: no real sleeping in these tests -- mock the clock/sleep, same spirit as
    # tests/test_loop_controller_safety_budget.py's env-controlled approach.
    monkeypatch.setattr(invoke_agent.time, "sleep", lambda *_: None)


def _fake_clock(monkeypatch, values):
    # Repeats the last value forever once `values` is exhausted, so tests don't need
    # to hand-count run_scenario_async()'s exact number of time.time() calls -- only
    # the values that matter for the assertion need to be explicit.
    import itertools

    it = itertools.chain(values, itertools.repeat(values[-1]))
    monkeypatch.setattr(invoke_agent.time, "time", lambda: next(it))


# ── A: exact request payload shape ──────────────────────────────────────────

def test_run_query_job_receives_input_wrapped_payload(monkeypatch):
    _fake_clock(monkeypatch, [0.0, 1.0, 1.0])
    client = _FakeAgentEngines([_FakeCheck("SUCCESS", result=json.dumps({"status": "ok"}))])

    invoke_agent.run_scenario_async(client, "crashloop")

    assert len(client.run_query_job_calls) == 1
    sent_query = client.run_query_job_calls[0]["config"]["query"]
    scenario = dict(invoke_agent.SCENARIOS["crashloop"])
    assert sent_query == json.dumps({"input": scenario})
    assert sent_query != json.dumps(scenario)  # explicitly not the raw/unwrapped form


# ── B: RUNNING -> RUNNING -> SUCCESS ────────────────────────────────────────

def test_running_running_success(monkeypatch):
    _fake_clock(monkeypatch, [0.0, 1.0, 2.0, 3.0])
    client = _FakeAgentEngines([
        _FakeCheck("RUNNING"),
        _FakeCheck("RUNNING"),
        _FakeCheck("SUCCESS", result=json.dumps({"rca": "root cause found"})),
    ])

    result = invoke_agent.run_scenario_async(client, "crashloop")

    assert result["status"] == "ok"
    assert result["result"] == {"rca": "root cause found"}
    assert len(client.check_query_job_calls) == 3


# ── C: RUNNING -> FAILED ─────────────────────────────────────────────────────

def test_running_failed(monkeypatch):
    _fake_clock(monkeypatch, [0.0, 1.0, 2.0])
    client = _FakeAgentEngines([
        _FakeCheck("RUNNING"),
        _FakeCheck("FAILED", result="backend error: quota exceeded"),
    ])

    result = invoke_agent.run_scenario_async(client, "crashloop")

    assert result["status"] == "error"
    assert "quota exceeded" in result["error"]


# ── D: polling timeout does not lose the job identifier ────────────────────

def test_poll_timeout_preserves_job_identifier(monkeypatch):
    # call1=start=0.0, call2=deadline-base=0.0 (deadline=600.0), then two small
    # in-bound values (RUNNING iterations), then 700.0 repeating forever -- guarantees
    # the while-loop condition eventually and permanently evaluates false.
    _fake_clock(monkeypatch, [0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 700.0])
    client = _FakeAgentEngines([_FakeCheck("RUNNING")] * 5)

    with pytest.raises(invoke_agent.QueryJobTimeout) as exc_info:
        invoke_agent.run_scenario_async(client, "crashloop", timeout_s=600.0)

    err = exc_info.value
    assert err.job_name == "projects/p/locations/l/operations/op123"
    assert err.output_gcs_uri.startswith("gs://")
    assert "RUNNING" in str(err)
    assert "not a platform limit" in str(err).lower()


# ── F: result unwrap matches sync run_scenario()'s shape either way ────────

@pytest.mark.parametrize("blob_content", [
    {"output": {"rca_report": "root cause: OOMKilled"}},
    {"rca_report": "root cause: OOMKilled"},
])
def test_result_unwrap_matches_sync_shape(monkeypatch, blob_content):
    _fake_clock(monkeypatch, [0.0, 1.0])
    client = _FakeAgentEngines([_FakeCheck("SUCCESS", result=json.dumps(blob_content))])

    result = invoke_agent.run_scenario_async(client, "crashloop")

    assert result["result"] == {"rca_report": "root cause: OOMKilled"}
