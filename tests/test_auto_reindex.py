"""Auto-indexer (server self-heal) tests — see pandemonium/indexer/auto_reindex.py."""

from __future__ import annotations

from pathlib import Path

from support import FakeEmbedder, make_retriever, reindex

from pandemonium.indexer.auto_reindex import AutoReindexer
from pandemonium.retrieval.symbol_search import lookup_symbol


def test_auto_reindex_detects_edit_and_makes_new_symbol_searchable(settings):
    reindex(settings, incremental=False)
    auto = AutoReindexer(settings, embedder=FakeEmbedder(), min_interval=0.0)
    auto.prime()

    # No change -> no reindex.
    assert auto.maybe_refresh() is None

    # Edit a file: add a new top-level function.
    calc = Path(settings.repo_root) / "pkg" / "calculator.py"
    calc.write_text(calc.read_text(encoding="utf-8")
                    + "\n\ndef divide(a, b):\n    \"\"\"Divide a by b.\"\"\"\n    return a / b\n",
                    encoding="utf-8")

    stats = auto.maybe_refresh()
    assert stats is not None and stats.indexed >= 1

    # The new symbol is now resolvable without any manual reindex call.
    retr = make_retriever(settings)
    assert any(m["name"] == "divide"
               for m in lookup_symbol(retr.sqlite, retr.repo_id, "divide"))

    # A second call with nothing changed is a no-op again.
    assert auto.maybe_refresh() is None


def test_auto_reindex_debounce_then_force(settings):
    reindex(settings, incremental=False)
    auto = AutoReindexer(settings, embedder=FakeEmbedder(), min_interval=10_000.0)
    auto.prime()

    (Path(settings.repo_root) / "pkg" / "calculator.py").write_text(
        "def renamed():\n    return 1\n", encoding="utf-8")

    # Inside the debounce window the scan is skipped entirely.
    assert auto.maybe_refresh() is None
    # force=True bypasses the debounce and picks up the change.
    assert auto.maybe_refresh(force=True) is not None


def test_auto_reindex_detects_deleted_file(settings):
    reindex(settings, incremental=False)
    auto = AutoReindexer(settings, embedder=FakeEmbedder(), min_interval=0.0)
    auto.prime()

    (Path(settings.repo_root) / "pkg" / "email_service.py").unlink()
    stats = auto.maybe_refresh()
    assert stats is not None and stats.deleted >= 1
