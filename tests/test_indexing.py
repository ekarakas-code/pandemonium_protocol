"""Indexing: symbol extraction, incremental skip, change + deletion handling."""

from __future__ import annotations

from support import make_settings, reindex

from pandemonium import service
from pandemonium.indexer.ignore import IgnoreMatcher
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for


def _store(settings):
    s = SqliteStore(settings.sqlite_path)
    s.create_schema()
    return s


def test_index_populates_stores(indexed):
    store = _store(indexed)
    try:
        counts = store.counts(repo_id_for(indexed.repo_root))
    finally:
        store.close()
    assert counts["files"] >= 3
    assert counts["symbols"] >= 4  # Calculator, add, subtract, multiply, send_vendor_email
    assert counts["chunks"] > 0


def test_symbol_extraction_types_and_lines(indexed):
    store = _store(indexed)
    repo_id = repo_id_for(indexed.repo_root)
    try:
        multiply = store.symbols_by_name(repo_id, "multiply")
        add = store.symbols_by_name(repo_id, "add")
        cls = store.symbols_by_name(repo_id, "Calculator")
    finally:
        store.close()
    assert multiply and multiply[0]["symbol_type"] == "function"
    assert multiply[0]["start_line"] > 0 and multiply[0]["end_line"] >= multiply[0]["start_line"]
    assert add and add[0]["symbol_type"] == "method"  # inside the class
    assert cls and cls[0]["symbol_type"] == "class"


def test_incremental_skip(indexed):
    stats = reindex(indexed, incremental=True)
    assert stats.indexed == 0
    assert stats.skipped >= 3


def test_change_detection_reindexes_only_changed(settings):
    reindex(settings, incremental=False)
    target = settings.repo_root / "pkg" / "calculator.py"
    target.write_text(target.read_text(encoding="utf-8")
                      + "\n\ndef divide(a, b):\n    return a / b\n", encoding="utf-8")
    stats = reindex(settings, incremental=True)
    assert stats.indexed == 1
    assert stats.skipped >= 2

    store = _store(settings)
    try:
        assert store.symbols_by_name(repo_id_for(settings.repo_root), "divide")
    finally:
        store.close()


def test_oversized_files_are_reported_not_silently_dropped(tmp_path):
    """Phase D: a file over max_file_bytes is skipped but SURFACED (stats + changed list),
    not silently dropped — so an agent never reads its absence as 'not in the repo'."""
    (tmp_path / "small.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (tmp_path / "big.py").write_text("x = 1  # padding\n" * 2000, encoding="utf-8")
    settings = make_settings(tmp_path)
    settings.data["indexing"]["max_file_bytes"] = 500  # force big.py over the limit
    stats = reindex(settings, incremental=False)
    assert stats.skipped_too_large == 1
    assert stats.indexed == 1  # only small.py
    ch = service.detect_changes(settings)
    assert any("big.py" in p for p in ch["skipped_too_large"])
    assert not any("big.py" in p for p in ch["new"] + ch["unchanged"])


def test_gitignore_used_when_no_pandemoniumignore(tmp_path):
    """Phase D: with no .pandemoniumignore, .gitignore excludes are honored out of the box."""
    (tmp_path / "keep.py").write_text("def k():\n    return 1\n", encoding="utf-8")
    (tmp_path / "skip.py").write_text("def s():\n    return 2\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("skip.py\n", encoding="utf-8")
    m = IgnoreMatcher.load(tmp_path)
    assert m.matches("skip.py") and not m.matches("keep.py")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    ch = service.detect_changes(settings)
    assert any("keep.py" in p for p in ch["new"] + ch["unchanged"])
    assert not any("skip.py" in p for p in ch["new"] + ch["changed"] + ch["unchanged"])


def test_deletion_cascades(settings):
    reindex(settings, incremental=False)
    (settings.repo_root / "pkg" / "email_service.py").unlink()
    stats = reindex(settings, incremental=True)
    assert stats.deleted >= 1

    store = _store(settings)
    try:
        assert not store.symbols_by_name(repo_id_for(settings.repo_root), "send_vendor_email")
    finally:
        store.close()
