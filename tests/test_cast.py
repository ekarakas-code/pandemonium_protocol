"""cAST AST-block subchunking (Improvements4 #3): a large symbol becomes ONE complete parent
card plus block-complete `ast_block` children, and delivery auto-upgrades a child to its
complete parent so the agent never reasons from half a unit."""

from __future__ import annotations

from pathlib import Path

from support import make_retriever, make_settings, reindex

from pandemonium import service
from pandemonium.indexer.tree_sitter_parser import extract_symbol_blocks, parse_symbols
from pandemonium.mcp.tools import _format_results, _next_move
from pandemonium.models import SearchResult
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for

LARGE = '''\
"""Order workflow."""


def approve_order(order_id, actor, repo):
    """Approve an order; deliberately large so it splits into AST blocks."""
    if order_id is None:
        raise ValueError("order_id required")
    if actor is None:
        raise ValueError("actor required")
    total = 0
    discount = 0
    for line in repo.lines(order_id):
        total += line.price * line.qty
        if line.discounted:
            discount += line.discount
        total -= line.adjustment
    net = total - discount
    try:
        repo.save(order_id, net)
        repo.commit()
    except Exception:
        repo.rollback()
        raise
    repo.notify(actor, net)
    return net


def tiny(x):
    return x + 1
'''


def _make_repo(tmp_path: Path, min_lines: int = 8):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "orders.py").write_text(LARGE, encoding="utf-8")
    settings = make_settings(tmp_path)
    settings.data["indexing"]["subchunk_min_lines"] = min_lines
    return settings


def _chunks(settings) -> list[dict]:
    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    try:
        rows = store.conn.execute(
            "SELECT * FROM chunks WHERE repo_id=? AND path=? ORDER BY start_line",
            (repo_id_for(settings.repo_root), "pkg/orders.py")).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]
    finally:
        store.close()


# -- the extractor in isolation ------------------------------------------------
def test_extract_blocks_are_node_aligned():
    src = LARGE.encode()
    approve = next(s for s in parse_symbols(src, "python") if s.name == "approve_order")
    blocks = extract_symbol_blocks(src, "python", approve.start_line, approve.end_line,
                                   min_lines=8)
    assert len(blocks) >= 2
    prev_end = approve.start_line  # blocks are inside the body, ordered, non-overlapping
    for b in blocks:
        assert approve.start_line <= b.start_line <= b.end_line <= approve.end_line
        assert b.start_line > prev_end
        prev_end = b.end_line


def test_small_symbol_has_no_blocks():
    src = LARGE.encode()
    tiny = next(s for s in parse_symbols(src, "python") if s.name == "tiny")
    assert extract_symbol_blocks(src, "python", tiny.start_line, tiny.end_line,
                                 min_lines=8) == []


# -- indexing integration ------------------------------------------------------
def test_large_symbol_emits_complete_parent_plus_children(tmp_path):
    settings = _make_repo(tmp_path)
    reindex(settings, incremental=False)
    chunks = _chunks(settings)

    parents = [c for c in chunks
               if c["chunk_type"] == "function" and c["qualified_name"] == "approve_order"]
    assert len(parents) == 1
    parent = parents[0]
    assert parent["is_complete_unit"] == 1
    assert parent["unit_kind"] == "function"

    children = [c for c in chunks if c["chunk_type"] == "ast_block"]
    assert len(children) >= 2
    for c in children:
        assert c["is_complete_unit"] == 0
        assert c["parent_ref"] == "pkg/orders.py::approve_order"
        assert c["symbol_id"] == parent["symbol_id"]
        assert parent["start_line"] <= c["start_line"] <= c["end_line"] <= parent["end_line"]

    tinies = [c for c in chunks if c["chunk_type"] == "function" and c["qualified_name"] == "tiny"]
    assert len(tinies) == 1  # small symbol: one card, no children


def test_no_window_chunks_and_parent_is_full_function(tmp_path):
    settings = _make_repo(tmp_path)
    reindex(settings, incremental=False)
    chunks = _chunks(settings)
    assert not any(c["chunk_type"] == "window" for c in chunks)  # large symbol is NOT line-split
    parent = next(c for c in chunks if c["qualified_name"] == "approve_order")
    assert LARGE.splitlines()[parent["start_line"] - 1].startswith("def approve_order")
    assert "return net" in parent["content"]  # full span, not a 60-line slice


def test_child_ids_stable_on_reindex(tmp_path):
    settings = _make_repo(tmp_path)
    reindex(settings, incremental=False)
    ids1 = {c["id"] for c in _chunks(settings) if c["chunk_type"] == "ast_block"}
    reindex(settings, incremental=False)
    ids2 = {c["id"] for c in _chunks(settings) if c["chunk_type"] == "ast_block"}
    assert ids1 and ids1 == ids2


def test_incremental_reindex_purges_old_children(tmp_path):
    settings = _make_repo(tmp_path)
    reindex(settings, incremental=False)
    p = settings.repo_root / "pkg" / "orders.py"
    p.write_text(p.read_text(encoding="utf-8").replace(
        "    net = total - discount\n",
        "    net = total - discount\n    net = round(net, 2)\n"), encoding="utf-8")
    reindex(settings, incremental=True)
    inc = {c["id"] for c in _chunks(settings) if c["chunk_type"] == "ast_block"}
    # A fresh FULL index of the identical content must yield the SAME child set — no orphans.
    reindex(settings, incremental=False)
    full = {c["id"] for c in _chunks(settings) if c["chunk_type"] == "ast_block"}
    assert inc == full


# -- delivery contract ---------------------------------------------------------
def test_repo_get_child_autoupgrades_to_parent(tmp_path):
    settings = _make_repo(tmp_path)
    reindex(settings, incremental=False)
    chunks = _chunks(settings)
    child = next(c for c in chunks if c["chunk_type"] == "ast_block")
    parent = next(c for c in chunks if c["qualified_name"] == "approve_order")

    up = service.get(settings, child["ref"], expand="exact")  # default: auto-upgrade
    assert up is not None
    assert up.start_line == parent["start_line"] and up.end_line == parent["end_line"]
    assert "def approve_order" in up.code
    assert up.note and "auto-expanded" in up.note

    raw = service.get(settings, child["ref"], expand="block")  # opt-out: raw block, flagged
    assert raw is not None
    assert raw.start_line == child["start_line"] and raw.end_line == child["end_line"]
    assert raw.safe_for_reasoning is False


# -- cards ---------------------------------------------------------------------
def test_partial_card_is_labeled_and_routes_to_parent():
    card = SearchResult(chunk_id="c", path="p.py", start_line=5, end_line=9, score=0.9,
                        chunk_type="ast_block", ref="p.py:5-9", scope="code",
                        unit_kind="ast_block", is_complete_unit=False,
                        safe_for_reasoning=False, parent_ref="p.py::approve_order")
    card.reason = "keyword match"
    assert "auto-expands to complete unit p.py::approve_order" in _next_move(card, False)
    assert "partial" in _format_results([card])


def test_search_surfaces_parent_for_block_local_token(tmp_path):
    settings = _make_repo(tmp_path)
    reindex(settings, incremental=False)
    retr = make_retriever(settings)
    try:
        res = retr.search("rollback", top_k=10)  # 'rollback' lives only in the try/except block
    finally:
        retr.close()
    found = {(r.qualified_name or "") for r in res} | {(r.parent_ref or "") for r in res}
    assert any("approve_order" in x for x in found)
