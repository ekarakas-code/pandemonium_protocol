"""#5: language-aware doc-comment capture so non-Python summaries carry real meaning."""

from __future__ import annotations

from dataclasses import dataclass

from support import make_settings, reindex

from pandemonium import service
from pandemonium.summaries.summarizer import (HeuristicSummarizer,
                                              extract_leading_comment)


@dataclass
class _Sym:
    name: str
    signature: str


def test_extract_leading_comment_styles():
    assert "area of a circle" in extract_leading_comment(
        ["/// Computes the area of a circle from its radius."])
    assert "area of a circle" in extract_leading_comment(
        ["/**", " * Computes the area of a circle from its radius.", " */"])
    assert "Computes the area" in extract_leading_comment(
        ["/* Computes the area of a circle. */"])
    # blank lines between the comment and the symbol are skipped
    assert "Runs the tick" in extract_leading_comment(["// Runs the tick.", "", ""])
    # a preprocessor / non-comment line directly above is NOT a doc comment
    assert extract_leading_comment(['#include "x.h"']) == ""
    assert extract_leading_comment(["int previous = 0;"]) == ""
    assert extract_leading_comment([]) == ""


def test_summarize_symbol_uses_leading_comment_for_cpp():
    s = _Sym("area", "double area(double r)")
    summ = HeuristicSummarizer().summarize_symbol(
        s, "double area(double r) { return 3.14 * r * r; }", language="cpp",
        preceding=["/// Area of a circle from its radius."])
    assert "double area(double r)" in summ
    assert "Area of a circle" in summ


def test_summarize_symbol_python_path_unchanged():
    """Python keeps its in-body docstring path; a `#` comment above is NOT consumed."""
    s = _Sym("f", "def f():")
    body = 'def f():\n    """Does the thing."""\n    return 1'
    assert "Does the thing" in HeuristicSummarizer().summarize_symbol(
        s, body, language="python", preceding=["# a leading hash comment"])
    # no docstring + python -> bare signature, NOT the leading `#` comment
    bare = HeuristicSummarizer().summarize_symbol(
        _Sym("g", "def g():"), "def g():\n    return 1", language="python",
        preceding=["# explanatory comment"])
    assert bare == "def g():"


def test_cpp_doc_comment_lands_in_indexed_summary(tmp_path):
    """End-to-end: the Doxygen comment above a C++ function reaches the stored summary,
    so the embedded descriptor (the vector channel's bet) is no longer just the signature."""
    (tmp_path / "geo.cpp").write_text(
        "/// Computes the area of a circle from its radius.\n"
        "double area(double r) { return 3.14 * r * r; }\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    matches = service.symbol(settings, "area")
    assert matches and "area of a circle" in matches[0]["summary"]
