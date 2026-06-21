"""Step 7 — session resume with airtight staleness. The load-bearing property: resume
NEVER claims a fact is verified; an unchanged anchor is "believed-then, not re-verified",
a changed/missing anchor is "STALE — re-verify", no anchor is "unverifiable". Resume is
render-only (no copying facts forward). Offline."""

from __future__ import annotations

from pandemonium.indexer.hasher import read_file
from pandemonium.session import (SessionLedger, _revalidate, ledger_path,
                                 latest_prior_ledger, render_resume)
from support import make_settings


def _hash(settings, rel):
    return read_file(settings.repo_root / rel)[2]


def test_record_fact_anchors_and_coerces(indexed):
    led = SessionLedger.open(indexed, "mcp-prev")
    led.record_fact("confirmed_facts", "add() returns the sum",
                    ref="pkg/calculator.py::Calculator.add",
                    file_hash=_hash(indexed, "pkg/calculator.py"))
    led.add("confirmed_facts", "an unanchored belief")
    ents = led.entries("confirmed_facts")
    assert any(e["ref"] == "pkg/calculator.py::Calculator.add" and e["hash"] for e in ents)
    assert any(e["ref"] is None for e in ents)         # legacy string coerces to text-only
    assert "Calculator.add" in led.render()            # ref shown in render


def test_revalidate_four_states(indexed):
    h = _hash(indexed, "pkg/calculator.py")
    ref = "pkg/calculator.py::Calculator.add"
    assert _revalidate(indexed, {"text": "x", "ref": ref, "hash": h}) == "current"
    assert _revalidate(indexed, {"text": "x", "ref": ref, "hash": "different"}) == "changed"
    assert _revalidate(indexed, {"text": "x", "ref": "gone/x.py::Y", "hash": "h"}) == "missing"
    assert _revalidate(indexed, {"text": "x", "ref": None, "hash": None}) == "unanchored"
    # ref present but NO recorded baseline (read_file returned None at note time) -> there's
    # nothing to compare, so it must read as unverifiable, never as "as recorded".
    assert _revalidate(indexed, {"text": "x", "ref": ref, "hash": None}) == "unanchored"


def test_resume_unchanged_anchor_is_NOT_called_verified(indexed):
    # THE trap: a hash match shows only the anchor didn't drift — it does NOT verify the
    # fact. Resume must say "as recorded / NOT re-verified", never "verified" or "current".
    led = SessionLedger.open(indexed, "mcp-prev")
    led.record_fact("confirmed_facts", "add sums two numbers",
                    ref="pkg/calculator.py::Calculator.add",
                    file_hash=_hash(indexed, "pkg/calculator.py"))
    out = render_resume(indexed, led)
    assert "believed-then" in out                       # header sets the frame
    assert "as recorded" in out and "NOT re-verified" in out
    low = out.lower()
    assert "verified-now" not in low and "still current" not in low  # no positive over-claim


def test_resume_changed_anchor_is_flagged_stale(indexed):
    led = SessionLedger.open(indexed, "mcp-prev")
    led.record_fact("confirmed_facts", "add sums two numbers",
                    ref="pkg/calculator.py::Calculator.add",
                    file_hash=_hash(indexed, "pkg/calculator.py"))
    p = indexed.repo_root / "pkg" / "calculator.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n# edited after the fact\n", encoding="utf-8")
    out = render_resume(indexed, led)
    assert "STALE" in out and "re-verify" in out


def test_resume_unanchored_fact_is_unverifiable(indexed):
    led = SessionLedger.open(indexed, "mcp-prev")
    led.add("confirmed_facts", "a belief with no anchor")
    out = render_resume(indexed, led)
    assert "unverifiable" in out


def test_latest_prior_ledger_excludes_current_and_picks_recent(tmp_path):
    import os
    s = make_settings(tmp_path)
    SessionLedger.open(s, "mcp-1").record_query("old")
    SessionLedger.open(s, "mcp-2").record_query("newer")
    os.utime(ledger_path(s, "mcp-1"), (1000, 1000))      # deterministic mtimes
    os.utime(ledger_path(s, "mcp-2"), (2000, 2000))
    led, _ = latest_prior_ledger(s, "mcp-current")       # current absent -> newest prior
    assert led.session_id == "mcp-2"
    led2, _ = latest_prior_ledger(s, "mcp-2")            # exclude newest -> the older one
    assert led2.session_id == "mcp-1"


def test_resume_touched_nothing_does_not_fabricate_stale_files(indexed):
    # A session that touched no anchorable paths must claim NOTHING is stale — even if an
    # unrelated file changed. (Regression: service.staleness([]) scans the whole repo, which
    # would fabricate untouched files as "touched, reindex these" — confident-wrong.)
    from pandemonium.session import _touched_stale
    led = SessionLedger.open(indexed, "mcp-prev")
    led.add("confirmed_facts", "an unanchored belief")   # no refs, no fetches, no edits
    led.record_query("a search")
    p = indexed.repo_root / "pkg" / "email_service.py"
    p.write_text(p.read_text(encoding="utf-8") + "\n# unrelated change\n", encoding="utf-8")
    assert _touched_stale(indexed, led) == []
    assert "out of sync" not in render_resume(indexed, led)


def test_note_invalid_field_is_not_reported_as_recorded(indexed):
    # The tool must not report success for a write the ledger silently drops (unknown field).
    from pandemonium.mcp.tools import ToolContext
    ctx = ToolContext(indexed)
    msg = ctx.repo_session(action="note", field="not_a_field", value="x")
    assert "unknown field" in msg.lower() and "recorded to" not in msg.lower()
    assert not ctx.ledger.data.get("not_a_field")


def test_resume_is_render_only_no_copy_forward(indexed):
    from pandemonium.mcp.tools import ToolContext
    prev = SessionLedger.open(indexed, "mcp-prev")
    prev.record_fact("confirmed_facts", "prior belief",
                     ref="pkg/calculator.py::Calculator.add",
                     file_hash=_hash(indexed, "pkg/calculator.py"))
    ctx = ToolContext(indexed)                           # a different (current) session
    out = ctx.repo_session(action="resume")
    assert "Resume" in out and "prior belief" in out
    # the prior fact must NOT be laundered into the current ledger as freshly confirmed
    assert not ctx.ledger.data.get("confirmed_facts")
