"""Regression test for scripts/verify_trace_export.py's pagination handling.

Postmortem (issue #130): every manual Cloud Trace check made during that
investigation only ever inspected page 1 of the List Traces API response,
never following nextPageToken -- reporting "0 traces" for a 10-minute
propagation-delay window while the real trace sat on page 2 the entire
time. This test proves fetch_all_traces() cannot repeat that mistake: page 1
is empty (a nextPageToken but no traces, exactly the shape that produced the
false negative), and the real trace only appears on page 2. A pagination
loop that returns early on an empty page 1 fails this test immediately.
"""
from scripts import verify_trace_export


def test_fetch_all_traces_follows_pagination_to_a_second_page():
    page_1 = {"nextPageToken": "TOKEN_ABC"}  # empty traces list -- the exact issue #130 shape
    page_2 = {"traces": [{"projectId": "p", "traceId": "abc123"}]}
    calls = []

    def fake_fetch_page(url):
        calls.append(url)
        return page_2 if "pageToken=TOKEN_ABC" in url else page_1

    result = verify_trace_export.fetch_all_traces(
        fake_fetch_page, "p", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
    )

    assert len(calls) == 2, "must follow nextPageToken to a second request"
    assert result == [{"projectId": "p", "traceId": "abc123"}]


def test_fetch_all_traces_stops_when_no_next_page_token():
    page_1 = {"traces": [{"traceId": "only-one"}]}
    calls = []

    def fake_fetch_page(url):
        calls.append(url)
        return page_1

    result = verify_trace_export.fetch_all_traces(
        fake_fetch_page, "p", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
    )

    assert len(calls) == 1
    assert result == [{"traceId": "only-one"}]


def test_fetch_all_traces_follows_three_pages():
    pages = {
        None: {"traces": [{"traceId": "one"}], "nextPageToken": "T1"},
        "T1": {"traces": [{"traceId": "two"}], "nextPageToken": "T2"},
        "T2": {"traces": [{"traceId": "three"}]},
    }

    def fake_fetch_page(url):
        token = url.split("pageToken=")[-1] if "pageToken=" in url else None
        return pages[token]

    result = verify_trace_export.fetch_all_traces(
        fake_fetch_page, "p", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z",
    )

    assert [t["traceId"] for t in result] == ["one", "two", "three"]


def test_find_run_id_checks_every_summary_until_a_match():
    summaries = [{"traceId": "no-match"}, {"traceId": "has-match"}]

    def fake_fetch_trace(project_id, trace_id):
        if trace_id == "has-match":
            return {"traceId": "has-match", "spans": [{"labels": {"sre.run_id": "run_x"}}]}
        return {"traceId": trace_id, "spans": [{"labels": {"sre.run_id": "run_other"}}]}

    result = verify_trace_export.find_run_id(fake_fetch_trace, "p", summaries, "run_x")

    assert result is not None
    assert result["traceId"] == "has-match"


def test_find_run_id_returns_none_when_nothing_matches():
    summaries = [{"traceId": "no-match"}]

    def fake_fetch_trace(project_id, trace_id):
        return {"traceId": trace_id, "spans": []}

    result = verify_trace_export.find_run_id(fake_fetch_trace, "p", summaries, "run_x")

    assert result is None
