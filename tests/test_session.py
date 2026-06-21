"""Phase 7: session ledger + staleness."""

from __future__ import annotations

from support import make_settings, reindex

from pandemonium import service
from pandemonium.session import SessionLedger


def test_ledger_records_dedupes_and_persists(tmp_path):
    led = SessionLedger.open(make_settings(tmp_path), "s1")
    led.record_query("find auth")
    led.record_query("find auth")  # dedupe
    led.record_fetch("a.py::A.m")
    led.add("confirmed_facts", "token expiry handled in A.m")
    assert led.already_searched("find auth")
    assert led.already_fetched("a.py::A.m")
    assert led.data["searched_queries"] == ["find auth"]

    reopened = SessionLedger.open(make_settings(tmp_path), "s1")  # persisted to disk
    assert reopened.already_fetched("a.py::A.m")
    assert "token expiry" in reopened.render()


def test_ledger_records_edges_and_stale(tmp_path):
    led = SessionLedger.open(make_settings(tmp_path), "s2")
    led.record_edges(["a -> b", "a -> b", "c -> a"])  # batch + dedupe
    led.record_stale("x.py::foo")
    led.add("rejected_edges", "a -> z")  # manual path (field is in LEDGER_FIELDS)
    assert led.data["confirmed_edges"] == ["a -> b", "c -> a"]
    assert led.data["stale_refs"] == ["x.py::foo"]

    reopened = SessionLedger.open(make_settings(tmp_path), "s2")
    assert "a -> b" in reopened.render()
    assert reopened.data["rejected_edges"] == ["a -> z"]


def test_repo_graph_records_confirmed_edges(tmp_path):
    """repo_graph auto-records the confident edges it resolves into the session ledger."""
    from pandemonium.mcp.tools import ToolContext
    (tmp_path / "m.py").write_text(
        "def helper():\n    return 1\n\n\n"
        "class Engine:\n    def go(self):\n        return helper()\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    ctx = ToolContext(settings)
    ctx.repo_graph("m.py::Engine.go")
    edges = ctx.ledger.data.get("confirmed_edges", [])
    assert any("m.py::Engine.go -> m.py::helper" in e for e in edges)


def test_staleness_detects_edits(repo):
    settings = make_settings(repo)
    reindex(settings, incremental=False)
    assert all(not r["stale"] for r in service.staleness(settings))  # fresh -> all current

    (repo / "pkg" / "calculator.py").write_text("# changed\n", encoding="utf-8")
    rows = service.staleness(settings, ["pkg/calculator.py::multiply"])
    assert rows[0]["stale"] and rows[0]["state"] == "changed"
