"""The eval acceptance-gate LOGIC (ROADMAP v2, Step 1 / M3).

Running the full eval is model- and index-bound (it loads bge-small + reads a built
`.pandemonium`), so that stays a manual/CI step — `python evals/run_eval.py --gate <label>`.
What we can pin offline and deterministically is the gate's *decision logic*: hard metrics
(graph correctness + retrieval quality) fail the run on any adverse move; token metrics only
warn. This locks M3's "ships only if it doesn't regress" so a future edit to the gate can't
quietly weaken it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "evals"))

import run_eval  # noqa: E402

_BASE = {
    "impact_fp_rate": 0.0, "impact_fn_rate": 0.0, "wrong_symbol_same_name_rate": 0.0,
    "duplicate_card_rate": 0.0, "ambiguous_ref_rate": 0.0,
    "precision_at_1": 0.6, "precision_at_3": 0.6, "precision_at_5": 0.8,
    "mrr": 0.67, "resolution_rate": 0.667,
    "avg_cards_tokens": 190.0, "avg_pack_tokens": 1700.0,
}


def test_identical_summary_passes():
    assert run_eval.gate(dict(_BASE), _BASE) is True


def test_improvement_passes():
    better = dict(_BASE, precision_at_1=0.7, avg_cards_tokens=150.0)
    assert run_eval.gate(better, _BASE) is True


def test_quality_regression_fails():
    worse = dict(_BASE, precision_at_3=0.5)  # one query down -> hard fail
    assert run_eval.gate(worse, _BASE) is False


def test_graph_correctness_regression_fails():
    fp = dict(_BASE, impact_fp_rate=0.1)  # a false caller appeared -> hard fail
    assert run_eval.gate(fp, _BASE) is False


def test_token_bloat_only_warns():
    """Token cost is a SOFT gate: a regression warns but doesn't block (M3 makes tokens a
    goal, not a hard fail — a quality win can be worth a few tokens)."""
    bloated = dict(_BASE, avg_cards_tokens=999.0)
    assert run_eval.gate(bloated, _BASE) is True
