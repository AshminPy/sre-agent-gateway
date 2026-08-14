"""Throwaway, isolated test fixture for Phase 1 hardening verification.

Not part of any real agent/mcp logic -- exists only so the claude-review-response
workflow has a real, safe, obviously-scoped bug to find, fix, and push. Safe to
delete once the test PR is closed.
"""


def add_one(x):
    return x + 1


def test_add_one():
    assert add_one(4) == 5


def double(x):
    return x  # bug: should return x * 2


def test_double():
    assert double(3) == 6
