"""Resident architectural skeleton (gated: indexing.emit_skeleton): depth-2 module grouping,
resolved-call direction, in-band honesty, idempotent marker-bounded write, and the service gate."""

from pandemonium import service
from pandemonium.models import IndexStats
from pandemonium.skeleton import (build_skeleton, module_key, render_skeleton,
                                  write_skeleton_into_claude_md)
from pandemonium.storage.sqlite_store import SqliteStore
from tests.support import make_settings, reindex


def test_module_key_is_depth_two():
    assert module_key("pandemonium/service.py") == "pandemonium"
    assert module_key("pandemonium/storage/sqlite_store.py") == "pandemonium/storage"
    assert module_key("top.py") == "(root)"


def _indexed(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    return settings


def test_build_skeleton_modules_and_direction(tmp_path):
    settings = _indexed(tmp_path, {
        "pkg_a/service.py": "from pkg_b.util import helper\n\ndef run():\n    return helper()\n",
        "pkg_b/util.py": "def helper():\n    return 1\n",
    })
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        model = build_skeleton(settings, store)
    finally:
        store.close()
    assert {"pkg_a", "pkg_b"} <= {m["module"] for m in model["modules"]}
    assert any(e["src"] == "pkg_a" and e["dst"] == "pkg_b" for e in model["edges"])

    text = render_skeleton(model)
    assert "believed-then" in text and "Believed as of" in text  # freshness + provenance
    assert "always-resident" in text  # the run-cost honesty line


def test_write_idempotent_and_preserves_user_content(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# My project\n\nUser notes here.\n", encoding="utf-8")
    body1 = "## Architectural skeleton (believed-then)\n_Believed as of T1 — x._\nstructure"
    assert write_skeleton_into_claude_md(tmp_path, body1) is True
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "User notes here." in text  # user content untouched
    assert text.count("pandemonium:skeleton:start") == 1
    # same structure, new stamp only => anti-churn skip (no write, no duplicate block)
    body2 = "## Architectural skeleton (believed-then)\n_Believed as of T2 — x._\nstructure"
    assert write_skeleton_into_claude_md(tmp_path, body2) is False
    assert text.count("pandemonium:skeleton:start") == 1


def test_write_bails_on_stray_marker(tmp_path):
    from pandemonium.skeleton import START
    # a lone START in user prose (no NOTICE/END) must NOT be treated as a managed block
    (tmp_path / "CLAUDE.md").write_text(f"# Docs\nUse `{START}` to mark.\nIMPORTANT user notes.\n",
                                        encoding="utf-8")
    body = "## Architectural skeleton (believed-then)\n_Believed as of T._\nstructure"
    assert write_skeleton_into_claude_md(tmp_path, body) is False  # bail, don't clobber/append
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "IMPORTANT user notes." in text  # user content preserved
    assert text.count("pandemonium:skeleton:start") == 1  # no duplication


def test_service_gate(tmp_path, monkeypatch):
    settings = _indexed(tmp_path, {"pkg/m.py": "def f():\n    return 1\n"})
    # skip the real re-index; we are exercising only the skeleton gate inside service.index
    monkeypatch.setattr(service, "run_index", lambda s, incremental=True: IndexStats())
    service.index(settings)  # emit_skeleton OFF (default)
    assert not (tmp_path / "CLAUDE.md").exists()
    settings.data["indexing"]["emit_skeleton"] = True
    service.index(settings)  # emit_skeleton ON
    claude = tmp_path / "CLAUDE.md"
    assert claude.exists() and "Architectural skeleton" in claude.read_text(encoding="utf-8")
