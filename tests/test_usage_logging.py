"""Tests for per-call usage logging + stats (pandemonium/usage.py)."""

from __future__ import annotations

import json

import pytest

from pandemonium import usage
from pandemonium.usage import UsageLogger, _extract_inputs
from pandemonium.util import repo_id_for

from support import FakeEmbedder  # noqa: E402  (conftest puts tests/ on sys.path)


def _logger(settings, surface="cli", session="cli-test", **cfg):
    if cfg:
        settings.data.setdefault("usage_logging", {}).update(cfg)
    return UsageLogger(settings, surface, session, repo_id_for(settings.repo_root))


def test_record_inserts_a_well_formed_row(settings):
    _logger(settings).record("repo_search", "where is rerank computed", {"top_k": 10},
                             "1. hybrid_search.py::_rerank  [symbol]\n   ...", 12.5, ok=True)
    rows = usage.read_calls(settings)
    assert len(rows) == 1
    r = rows[0]
    assert r["tool"] == "repo_search"
    assert r["question"] == "where is rerank computed"
    assert r["surface"] == "cli"
    assert r["session_id"] == "cli-test"
    assert r["repo_id"] == repo_id_for(settings.repo_root)
    assert r["repo"] == str(settings.repo_root)
    assert r["req_tokens"] > 0
    assert r["resp_tokens"] > 0
    assert r["resp_chars"] > 0
    assert r["ok"] == 1
    assert json.loads(r["inputs_json"]) == {"top_k": 10}


@pytest.mark.parametrize("mode,expect", [
    ("preview", lambda p: p is not None and len(p) == 10),
    ("full", lambda p: p == "X" * 50),
    ("none", lambda p: p is None),
])
def test_capture_response_modes(settings, mode, expect):
    _logger(settings, capture_response=mode, preview_chars=10).record(
        "repo_get", "pkg/x.py::Foo", {}, "X" * 50, 1.0)
    r = usage.read_calls(settings)[0]
    assert expect(r["resp_preview"])
    # Token counts and size are recorded regardless of capture mode.
    assert r["resp_tokens"] > 0
    assert r["resp_chars"] == 50


def test_disabled_is_a_noop(settings):
    _logger(settings, enabled=False).record("repo_search", "q", {}, "answer", 1.0)
    assert usage.read_calls(settings) == []


def test_logging_is_best_effort_never_raises(settings):
    # Point the DB at a directory so sqlite cannot open it: record must swallow the error.
    settings.data["storage"]["sqlite_path"] = str(settings.repo_root)
    _logger(settings).record("repo_search", "q", {}, "answer", 1.0)  # must not raise
    assert usage.read_calls(settings) == []  # read also swallows


def test_question_extraction_binds_the_wrapped_signature():
    class Fake:
        def repo_search(self, query, top_k=10, mode=""):
            return "x"

    raw = Fake().repo_search  # bound method -> signature excludes self
    question, inputs = _extract_inputs(raw, ("where is rerank",), {"top_k": 5})
    assert question == "where is rerank"
    assert inputs == {"top_k": 5, "mode": ""}  # question key dropped, defaults applied


def test_record_call_mcp_path(settings):
    class Fake:
        def repo_get(self, ref, expand="exact", view="full"):
            return "code body"

    log = UsageLogger(settings, "mcp", "mcp-123", repo_id_for(settings.repo_root))
    log.record_call("repo_get", Fake().repo_get, ("pkg/x.py::Foo",),
                    {"view": "signature"}, "code body", 3.0, ok=True)
    r = usage.read_calls(settings, surface="mcp")[0]
    assert r["tool"] == "repo_get"
    assert r["question"] == "pkg/x.py::Foo"
    assert json.loads(r["inputs_json"]) == {"expand": "exact", "view": "signature"}
    assert r["resp_preview"] == "code body"  # default preview mode keeps the (short) answer


def test_aggregate_rolls_up_per_tool():
    rows = [
        {"tool": "repo_search", "ok": 1, "ms": 10.0, "req_tokens": 5, "resp_tokens": 100,
         "session_id": "s1", "repo": "r", "ts": "t1"},
        {"tool": "repo_search", "ok": 0, "ms": 30.0, "req_tokens": 7, "resp_tokens": 200,
         "session_id": "s1", "repo": "r", "ts": "t2"},
        {"tool": "repo_get", "ok": 1, "ms": 5.0, "req_tokens": 3, "resp_tokens": 50,
         "session_id": "s2", "repo": "r", "ts": "t3"},
    ]
    agg = usage.aggregate(rows)
    s = agg["summary"]
    assert s["total_calls"] == 3
    assert s["total_errors"] == 1
    assert s["total_resp_tokens"] == 350
    assert set(s["sessions"]) == {"s1", "s2"}
    tools = {t["tool"]: t for t in agg["tools"]}
    assert tools["repo_search"]["calls"] == 2
    assert tools["repo_search"]["errors"] == 1
    assert tools["repo_search"]["resp_tokens"] == 300
    assert tools["repo_search"]["ms_avg"] == 20.0
    assert usage.render_stats(agg)  # renders without error


def test_toolcontext_logs_tool_calls(indexed):
    """A real ToolContext call goes through the tracing wrapper and lands a usage row."""
    from pandemonium.mcp.tools import ToolContext

    ctx = ToolContext(indexed, embedder=FakeEmbedder())
    try:
        out = ctx.repo_map()  # repo_map is SQL-only (no embedder/LanceDB needed)
        assert out
    finally:
        ctx._reset()
    rows = usage.read_calls(indexed, surface="mcp")
    assert rows, "expected at least one mcp usage row"
    r = next(r for r in rows if r["tool"] == "repo_map")
    assert r["session_id"].startswith("mcp-")
    assert r["repo_id"] == repo_id_for(indexed.repo_root)
    assert r["ok"] == 1


def test_cli_stats_and_logs_commands(settings):
    from typer.testing import CliRunner

    from pandemonium.cli.main import app

    _logger(settings).record("repo_search", "hello world", {}, "some answer text", 5.0)
    runner = CliRunner()
    repo = str(settings.repo_root)

    logs = runner.invoke(app, ["logs", "--repo", repo])
    assert logs.exit_code == 0
    assert "repo_search" in logs.output

    stats = runner.invoke(app, ["stats", "--repo", repo])
    assert stats.exit_code == 0
    assert "repo_search" in stats.output

    as_json = runner.invoke(app, ["stats", "--repo", repo, "--json"])
    assert as_json.exit_code == 0
    assert '"total_calls"' in as_json.output
