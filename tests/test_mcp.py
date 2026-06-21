"""MCP surface: the expected repo_* tools register with input schemas."""

from __future__ import annotations

import asyncio

from pandemonium.mcp.server import build_server

EXPECTED = {
    "repo_map", "repo_search", "repo_symbol", "repo_get", "repo_context_pack",
    "repo_prompt_context", "repo_find_tests", "repo_reindex_changed",
    "repo_session", "repo_changed", "repo_graph", "repo_impact", "repo_logic_map",
    "repo_edit_plan", "repo_brief",
}


def test_tools_registered_with_schemas(settings):
    mcp = build_server(settings)  # ToolContext is lazy: no model load here
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED <= names
    assert all(t.inputSchema is not None for t in tools)


def test_repo_map_every_mode_renders(indexed):
    from pandemonium import mapping, service
    for mode in mapping.MODES:
        out = mapping.render_repo_map(service.repo_map(indexed, mode=mode))
        assert isinstance(out, str) and out.strip()


def test_repo_map_tests_mode_lists_test_file(indexed):
    from pandemonium import service
    m = service.repo_map(indexed, mode="tests")
    assert any("test_calculator.py" in p for p in m["test_files"])


def test_repo_map_architecture_has_areas(indexed):
    from pandemonium import service
    m = service.repo_map(indexed, mode="architecture")
    assert m["areas"] and all("area" in a and a["files"] >= 1 for a in m["areas"])


def test_repo_map_changed_mode_detects_drift(indexed):
    from pandemonium import service
    target = indexed.repo_root / "pkg" / "calculator.py"
    target.write_text(target.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    m = service.repo_map(indexed, mode="changed")
    assert any(c["path"].endswith("calculator.py") and c["state"] == "changed"
               for c in m["changed"])


def _card(name=None, scope="symbol", chunk_type="function", reason="exact symbol match"):
    from pandemonium.models import SearchResult
    return SearchResult(chunk_id=f"c_{name}", path=f"{name or 'x'}.py", start_line=1,
                        end_line=3, score=0.9, symbol_name=name, chunk_type=chunk_type,
                        scope=scope, ref=f"{name or 'x'}.py::{name}", reason=reason)


def test_next_move_hints_are_confidence_conditional():
    """Step 4 (#8): high-confidence symbol cards point at impact-first; low-confidence cards
    point at verification (and suppress the edit hint) — terse, ≤2 actions, never a menu."""
    from pandemonium.mcp.tools import _format_results
    sym = _card("run")
    filecard = _card("notes", scope="file", chunk_type="file", reason="keyword match")

    high = _format_results([sym, filecard], {"confidence": "high"})
    assert "repo_impact(ref) before editing it" in high   # symbol -> impact-first
    assert "repo_get(ref) to read" in high                 # file card -> just fetch

    low = _format_results([sym], {"confidence": "low", "reason": "cluster on 'size'",
                                  "missing_terms": ["cell"], "clustered_on": "size"})
    assert "Low-confidence retrieval" in low               # banner present
    assert "to verify" in low                              # verify nudge
    assert "repo_impact" not in low                        # edit hint suppressed when unsure


def test_high_confidence_prints_no_banner():
    """The trust banner costs tokens, so it appears ONLY when confidence is low."""
    from pandemonium.mcp.tools import _confidence_banner
    assert _confidence_banner({"confidence": "high"}) == ""
    assert _confidence_banner(None) == ""
    assert "Low-confidence" in _confidence_banner(
        {"confidence": "low", "reason": "x", "missing_terms": ["cell"]})
