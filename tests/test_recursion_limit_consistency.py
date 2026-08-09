"""Regression guard for the 2026-08-09 recursion_limit drift bug.

What happened: agent/main.py's investigate() explicitly passed
config={"recursion_limit": 60} to graph.invoke(), independently hardcoded in two call
sites. agent/eval/run_eval.py's run_local() passed no config at all, so it silently
fell back to LangGraph's own built-in default of 25 — four golden cases failed with
"Recursion limit of 25 reached" that had nothing to do with real agent behavior.

The fix made GRAPH_RECURSION_LIMIT a single constant in agent/graph.py, imported by
both call sites. This test does not just check the two files' behavior matches today —
it checks BY CONSTRUCTION that neither file can silently hardcode its own number again:
every recursion_limit literal in both files must come from importing GRAPH_RECURSION_LIMIT,
not a bare int.
"""
from __future__ import annotations

import inspect
import re

import agent.main as main_mod
import agent.eval.run_eval as run_eval_mod
from agent.graph import GRAPH_RECURSION_LIMIT

# Matches "recursion_limit": <int literal> — a hardcoded value bypassing the shared
# constant. A correct call site writes "recursion_limit": GRAPH_RECURSION_LIMIT instead,
# which this pattern does not match (GRAPH_RECURSION_LIMIT is not all-digits).
_HARDCODED_LIMIT = re.compile(r'"recursion_limit"\s*:\s*\d+')


def test_main_py_recursion_limit_is_not_hardcoded():
    source = inspect.getsource(main_mod)
    assert not _HARDCODED_LIMIT.search(source), (
        "agent/main.py hardcodes a recursion_limit int literal — it must import and "
        "use agent.graph.GRAPH_RECURSION_LIMIT instead, or it can silently drift from "
        "the eval harness again."
    )
    assert "GRAPH_RECURSION_LIMIT" in source


def test_run_eval_py_recursion_limit_is_not_hardcoded():
    source = inspect.getsource(run_eval_mod)
    assert not _HARDCODED_LIMIT.search(source), (
        "agent/eval/run_eval.py hardcodes a recursion_limit int literal — it must "
        "import and use agent.graph.GRAPH_RECURSION_LIMIT instead, or local eval runs "
        "can silently drift from the deployed agent's setting again."
    )
    assert "GRAPH_RECURSION_LIMIT" in source


def test_graph_recursion_limit_is_reasonably_above_langgraph_default():
    # LangGraph's own built-in default is 25 (see agent/graph.py's comment on the
    # constant). The whole point of this constant is to be deliberately higher than
    # that default for this graph's normal shape (~4-5 nodes per loop iteration).
    assert GRAPH_RECURSION_LIMIT > 25
