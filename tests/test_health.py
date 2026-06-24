"""Protocol health / readiness (M4): ARMED vs NOT INDEXED, version surfaced, no side effects."""

from pathlib import Path

from pandemonium import __version__, service
from pandemonium.health import health_report, render_health
from tests.support import make_settings, reindex


def test_not_indexed_is_detectable(tmp_path):
    settings = make_settings(tmp_path)  # never indexed
    r = health_report(settings)
    assert r["status"] == "NOT INDEXED"
    assert r["counts"]["files"] == 0
    assert r["version"] == __version__
    # a read-only health check must not create an index
    assert not Path(settings.sqlite_path).exists()
    assert "MISSING" in render_health(r)


def test_armed_after_index(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    r = health_report(settings)
    assert r["status"] == "ARMED"
    assert r["index_present"] and r["counts"]["files"] >= 1 and r["counts"]["symbols"] >= 1
    text = render_health(r)
    assert "ARMED" in text and __version__ in text


def test_service_health_wrapper(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    reindex(settings, incremental=False)
    assert service.health(settings)["status"] == "ARMED"
