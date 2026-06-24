"""Post-edit breakage check: removed/renamed callees, changed signatures, the flag gate, and
the honesty floor that must accompany every result. The analyzer is called DIRECTLY (not via the
MCP path) so the on-disk edit delta survives (the MCP auto-reindexer would absorb it)."""

from pandemonium import service
from pandemonium.breakage import breakage_check, render_breakage
from tests.support import make_settings, reindex


def _index(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    return settings


CALLER = "from util import parse\n\ndef go():\n    return parse(1)\n"


def test_removed_callee_dangles_its_callers(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x\n", "run.py": CALLER})
    # genuinely gone: different name AND body, so it's not a fingerprint rename
    (tmp_path / "util.py").write_text("def other(y):\n    return y * 99 + 7\n", encoding="utf-8")
    result = breakage_check(settings)
    assert result["status"] == "ok"
    rec = next(r for r in result["removed"] if "parse" in r["ref"])
    assert rec["fate"] == "removed"
    callers = [c["ref"] for c in rec["callers"]] + [c["ref"] for c in rec["callers_possible"]]
    assert any("go" in c for c in callers)


def test_renamed_callee_is_labelled(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x + 1\n", "run.py": CALLER})
    # same body, new name => fingerprint re-find
    (tmp_path / "util.py").write_text("def parse2(x):\n    return x + 1\n", encoding="utf-8")
    result = breakage_check(settings)
    rec = next(r for r in result["removed"] if "parse" in r["ref"])
    assert rec["fate"].startswith("renamed ->")


def test_signature_change_flags_callsites_to_verify(tmp_path):
    settings = _index(tmp_path, {"util.py": "def fetch(url):\n    return url\n",
                                 "run.py": "from util import fetch\n\ndef go():\n    return fetch(1)\n"})
    (tmp_path / "util.py").write_text("def fetch(url, retries):\n    return url\n", encoding="utf-8")
    result = breakage_check(settings)
    rec = next(r for r in result["signature_changed"] if "fetch" in r["ref"])
    assert "retries" in rec["new_sig"]
    callers = [c["ref"] for c in rec["callers"]] + [c["ref"] for c in rec["callers_possible"]]
    assert any("go" in c for c in callers)


def test_dangling_import_of_removed_symbol(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x\n", "run.py": CALLER})
    (tmp_path / "util.py").write_text("def other(y):\n    return y + 9\n", encoding="utf-8")
    result = breakage_check(settings)
    importers = [d["importer_path"] for d in result["dangling_imports"]]
    assert any("run.py" in p for p in importers)  # `from util import parse` now dangles


def test_flag_off_is_noop(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x\n"})
    # default: retrieval.breakage_check is False
    assert service.breakage(settings)["status"] == "disabled"
    assert "disabled" in render_breakage(service.breakage(settings)).lower()


def test_flag_on_runs(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x\n", "run.py": CALLER})
    settings.data["retrieval"]["breakage_check"] = True
    (tmp_path / "util.py").write_text("def other(x):\n    return x\n", encoding="utf-8")
    assert service.breakage(settings)["status"] == "ok"


def test_empty_delta_is_honest_not_clean(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x\n"})
    result = breakage_check(settings)  # nothing changed on disk
    assert result["status"] == "empty"
    text = render_breakage(result)
    assert "no breakage" not in text.lower()  # never a bare green pass
    assert "auto_reindex" in text  # explains why it may have nothing to see


def test_clean_result_still_carries_the_floor(tmp_path):
    settings = _index(tmp_path, {"util.py": "def parse(x):\n    return x\n", "run.py": CALLER})
    # add a NEW function; `parse` stays byte-identical, so nothing it owns breaks
    (tmp_path / "util.py").write_text("def parse(x):\n    return x\n\n\ndef helper():\n    return 0\n",
                                      encoding="utf-8")
    result = breakage_check(settings)
    assert result["status"] == "ok" and not result["removed"] and not result["signature_changed"]
    text = render_breakage(result)
    assert "No static breakage found" in text
    assert "LOWER BOUND" in text  # the floor caveat is always present
