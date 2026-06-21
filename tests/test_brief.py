"""repo_brief (ROADMAP v2, Step 5) — anchor selection, hard verified-vs-guess
separation, and the low-confidence WITHHOLD that makes a confident-wrong brief
impossible. Offline (FakeEmbedder), so fully deterministic.

Also locks the two integrity bugs the adversarial review caught: an exact-name COLLISION
must not read as a confident anchor, and a COMPOUND (snake/camel) query term must still be
covered after splitting (else the gate false-withholds a correct anchor)."""

from __future__ import annotations

from pandemonium.brief import render_brief, repo_brief
from support import make_retriever, make_settings, reindex


def _brief(settings, task):
    retr = make_retriever(settings)
    try:
        return repo_brief(settings, task, retriever=retr)
    finally:
        retr.close()  # passed-in retriever is the caller's to close


def test_brief_anchors_on_symbol_and_hard_separates(indexed):
    b = _brief(indexed, "add two numbers")
    assert b["anchored"] is True
    assert b["anchor"].endswith("Calculator.add")
    assert b["anchor_confidence"] in ("high", "medium")

    out = render_brief(b)
    # Both blocks present, and the heuristic (guess) block precedes the verified one —
    # leading with the guess keeps the conditionality salient.
    h_at = out.index("## ⚠ Heuristic")
    v_at = out.index("## ✓ Verified")
    assert 0 <= h_at < v_at
    assert "GUESSES" in out and "NOT proof it's the right target" in out


def test_brief_verified_block_holds_only_confident_graph_facts(indexed):
    b = _brief(indexed, "add two numbers")
    v = b["verified"]
    assert v is not None
    # test_add() calls Calculator().add — a confident caller living in a test file, so it
    # is a VERIFIED related test (not a name-match guess).
    assert any("test_calculator" in t and "::" in t for t in v["verified_tests"])
    # The verified dict never smuggles in the unverified "possible" caller channel, and
    # name-matched tests are NOT inside the verified block — they live at the top level.
    assert "possible" not in v
    assert "name_matched_tests" not in v
    # The verified test FILE must not also appear as an unverified name-matched test
    # (the ref-vs-path partition bug). Compare at file granularity.
    vfiles = {t.split("::", 1)[0] for t in v["verified_tests"]}
    ufiles = {t.split("::", 1)[0].split(":", 1)[0] for t in b["unverified_tests"]}
    assert not (vfiles & ufiles)
    # Everything in the verified test bucket is actually a confident caller of the anchor.
    callers = set(v["callers_production"]) | set(v["callers_test"]) \
        | set(v["callers_transitive"])
    assert set(v["verified_tests"]) <= callers
    # And the unverified tests render OUTSIDE the verified block (own ⚠ section).
    out = render_brief(b)
    if b["unverified_tests"]:
        assert out.index("## ⚠ Unverified — name-matched tests") > out.index("## ✓ Verified")


def test_brief_exact_symbol_is_high_confidence(indexed):
    b = _brief(indexed, "multiply")  # a bare identifier -> exact short-circuit, unique name
    assert b["anchor_confidence"] == "high"
    assert b["anchored"] is True
    assert b["anchor"].endswith("multiply")
    assert "the query is an exact symbol name" in " ".join(b["anchor_confidence_reasons"])


def test_brief_exact_name_collision_is_not_confident(tmp_path):
    # Two distinct symbols share the exact name `process`. An exact match is then AMBIGUOUS,
    # not a confident anchor — otherwise an arbitrary one gets a confident "Edit-ready".
    (tmp_path / "a.py").write_text("def process():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def process():\n    return 2\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    b = _brief(settings, "process")
    assert b["anchor_confidence"] == "medium"          # demoted from high
    assert "share this exact name" in " ".join(b["anchor_confidence_reasons"])
    refs = [t["ref"] for t in b["heuristic"]["likely_targets"]]
    assert sum(r.endswith("::process") for r in refs) >= 2  # both candidates surfaced


def test_brief_compound_query_term_is_covered_after_split(indexed):
    # A snake_case query term must be split to match the anchor's split terms, or coverage
    # would read 0 and the gate would false-WITHHOLD a correct anchor (tokenizer asymmetry
    # regression — the failure hits C++ CamelCase/snake identifiers hardest).
    b = _brief(indexed, "fix send_vendor_email")
    assert b["anchored"] is True
    assert b["anchor"].endswith("send_vendor_email")


def test_brief_stale_anchor_file_caps_confidence(indexed):
    # Clean HIGH must never be claimed over an index whose anchor file changed since
    # indexing — the verified facts are computed against a stale index. (M1 lock: integrity
    # behaviors get a standing regression check so a future change can't regress them.)
    calc = indexed.repo_root / "pkg" / "calculator.py"
    calc.write_text(calc.read_text(encoding="utf-8") + "\n# drift since indexing\n",
                    encoding="utf-8")
    b = _brief(indexed, "add two numbers")
    assert b["anchored"] is True                     # still anchors (resolves from the index)
    assert b["anchor_confidence"] != "high"          # but never clean HIGH over a stale file
    out = render_brief(b)
    assert "STALE index" in out                       # the caveat renders under the anchor
    assert "repo_reindex_changed" in b["suggested_action"]


def test_brief_low_confidence_withholds_the_verified_block(indexed):
    # Nothing in the fixture is about this; no symbol can carry the query's terms.
    b = _brief(indexed, "quantum blockchain teleporter device")
    assert b["anchored"] is False
    assert b["verified"] is None
    assert b["anchor_confidence"] in ("low", "none")

    out = render_brief(b)
    assert "WITHHELD" in out                       # the refusal is rendered
    assert "## ✓ Verified" in out                  # the block header still exists (structure)
    assert "Impact — confidently-resolved callers" not in out  # but no authoritative facts
    action = b["suggested_action"].lower()
    assert "disambiguate" in action or "candidate" in action or "rephras" in action
    assert "edit-ready" not in action


def test_brief_render_marks_call_flow_arrows_as_graph_resolved(indexed):
    # The call-flow node SET is a guess; the arrows are graph edges — the render must say so
    # and the section must live in the heuristic block. Assert the precondition so the test
    # can't silently degrade to a no-op if call_flow ever empties.
    b = _brief(indexed, "calculator add subtract multiply arithmetic")
    assert b["heuristic"]["call_flow"], "expected at least one intra-result call edge"
    out = render_brief(b)
    assert "arrows are graph-resolved" in out
    assert out.index("Likely call flow") < out.index("## ✓ Verified")
