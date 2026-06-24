"""Code-aware chunking, scope-aware (Phase 4; cAST subchunking — Improvements4 #3).

Emitters, gated by `scopes`:
  symbol : one chunk per function/method (the FULL span — never line-split) + a class-header
           chunk (chunk_type = class|method|function, scope=symbol). A large NON-class symbol
           ALSO gets block-complete `ast_block` children (cAST) for precise search; delivery
           auto-upgrades a child hit to its complete parent symbol.
  code   : line-window chunks (chunk_type=window for parsed files; chunk_type=block
           is ALWAYS emitted for files with no symbols, as mandatory coverage).
  file   : a single file-scope card (chunk_type=file) per file.

The descriptor (what we embed) is built later from summary+tags; chunk.content is the
stored raw code used for display / repo_get fallback only.
"""

from __future__ import annotations

from typing import Iterator, List, Sequence, Tuple

from pandemonium.indexer.tree_sitter_parser import extract_symbol_blocks
from pandemonium.models import Chunk, Symbol
from pandemonium.util import chunk_id_for, sha256_text

# Runtime always passes scopes from settings (default ["symbol"], settings.py). The Phase-4
# bake-off settled symbol-primary; file/code scopes are opt-in. (Was ("symbol","file","code").)
DEFAULT_SCOPES = ("symbol",)
_FILE_PREVIEW_LINES = 40


def _slice(lines: List[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1:end])  # 1-based inclusive


def _windows(start: int, end: int, window: int, overlap: int) -> Iterator[Tuple[int, int]]:
    if end < start:
        return
    step = max(1, window - overlap)
    s = start
    while s <= end:
        e = min(end, s + window - 1)
        yield (s, e)
        if e >= end:
            break
        s += step


def _symbol_chunks(repo_id, file_id, path, language, lines, source_bytes, symbols,
                   subchunk_min_lines) -> List[Chunk]:
    chunks: List[Chunk] = []
    for sym in symbols:
        if sym.symbol_type == "class":
            members = [s for s in symbols if s.id != sym.id
                       and s.start_line > sym.start_line and s.end_line <= sym.end_line]
            header_end = (max(sym.start_line, min(m.start_line for m in members) - 1)
                          if members else sym.end_line)
            start, end = sym.start_line, header_end
        else:
            start, end = sym.start_line, sym.end_line
        # The full symbol (or class header) as ONE complete unit — no line-window splitting.
        # The parent symbol is always the reading/delivery target.
        content = _slice(lines, start, end)
        if content.strip():
            cid = chunk_id_for(file_id, start, end, sym.symbol_type)
            chunks.append(Chunk(cid, repo_id, file_id, sym.id, sym.symbol_type, language,
                                path, start, end, content, None, sha256_text(content)))
        # cAST: a large non-class symbol ALSO gets block-complete `ast_block` children so a
        # query can hit a precise sub-block; index_runner stamps parent_ref for auto-upgrade.
        if sym.symbol_type != "class":
            pqn = sym.qualified_name or sym.name
            for blk in extract_symbol_blocks(source_bytes, language, start, end,
                                             min_lines=subchunk_min_lines):
                bcontent = _slice(lines, blk.start_line, blk.end_line)
                if not bcontent.strip():
                    continue
                bcid = chunk_id_for(file_id, blk.start_line, blk.end_line, "ast_block")
                # Carry the block slug forward as a human-readable label (e.g.
                # `OrderService.approve#block:validate`); the resolvable ref stays line-based.
                chunks.append(Chunk(bcid, repo_id, file_id, sym.id, "ast_block", language,
                                    path, blk.start_line, blk.end_line, bcontent, None,
                                    sha256_text(bcontent),
                                    qualified_name=f"{pqn}#block:{blk.slug}", parent=pqn))
    return chunks


def _window_chunks(repo_id, file_id, path, language, lines, window_lines, overlap,
                   chunk_type) -> List[Chunk]:
    total = len(lines)
    chunks: List[Chunk] = []
    for ws, we in _windows(1, max(total, 1), window_lines, overlap):
        content = _slice(lines, ws, we)
        if not content.strip():
            continue
        cid = chunk_id_for(file_id, ws, we, chunk_type)
        chunks.append(Chunk(cid, repo_id, file_id, None, chunk_type, language, path,
                            ws, we, content, None, sha256_text(content)))
    return chunks


def _complement_chunks(repo_id, file_id, path, language, lines, symbols,
                       min_lines) -> List[Chunk]:
    """One chunk per non-trivial line-gap NOT covered by any symbol span — the between-symbol
    residue (imports, app wiring, DI/Startup registration, argparse/CLI blocks, top-level
    statements) that symbol-only mode leaves findable by nothing. Covers ONLY the gaps (a class
    span swallows its method gaps), so it never overlaps a symbol and cannot recreate the
    Phase-4 'code'-scope overlap that lost the bake-off."""
    total = len(lines)
    if total == 0 or not symbols:
        return []
    # Merge symbol spans (overlapping / nested — a class swallows its methods) into a disjoint
    # covered set, then take the complement over [1..total].
    covered: List[Tuple[int, int]] = []
    for s, e in sorted((sym.start_line, sym.end_line) for sym in symbols):
        if covered and s <= covered[-1][1]:
            covered[-1] = (covered[-1][0], max(covered[-1][1], e))
        else:
            covered.append((s, e))
    gaps: List[Tuple[int, int]] = []
    prev_end = 0
    for cs, ce in covered:
        if cs - 1 >= prev_end + 1:
            gaps.append((prev_end + 1, cs - 1))
        prev_end = max(prev_end, ce)
    if prev_end < total:
        gaps.append((prev_end + 1, total))

    chunks: List[Chunk] = []
    for gs, ge in gaps:
        content = _slice(lines, gs, ge)
        if sum(1 for ln in content.splitlines() if ln.strip()) < min_lines:
            continue  # blank / comment-only / trivial `if __name__: main()` gaps — no card spam
        cid = chunk_id_for(file_id, gs, ge, "module_body")
        chunks.append(Chunk(cid, repo_id, file_id, None, "module_body", language, path,
                            gs, ge, content, None, sha256_text(content)))
    return chunks


def _file_card(repo_id, file_id, path, language, lines) -> Chunk:
    total = len(lines)
    preview = _slice(lines, 1, min(total, _FILE_PREVIEW_LINES))
    cid = chunk_id_for(file_id, 1, max(total, 1), "file")
    return Chunk(cid, repo_id, file_id, None, "file", language, path, 1, max(total, 1),
                 preview, None, sha256_text("\n".join(lines)))


def build_chunks(repo_id: str, file_id: str, path: str, language: str, text: str,
                 symbols: List[Symbol], scopes: Sequence[str] = DEFAULT_SCOPES,
                 window_lines: int = 60, overlap: int = 10,
                 subchunk_min_lines: int = 60, complement: bool = False,
                 complement_min_lines: int = 4) -> List[Chunk]:
    lines = text.splitlines()
    chunks: List[Chunk] = []

    if symbols:
        if "symbol" in scopes:
            source_bytes = text.encode("utf-8", "replace")
            chunks += _symbol_chunks(repo_id, file_id, path, language, lines, source_bytes,
                                     symbols, subchunk_min_lines)
        if "code" in scopes:  # supplementary line-windows for parsed files (bake-off)
            chunks += _window_chunks(repo_id, file_id, path, language, lines,
                                     window_lines, overlap, "window")
        if complement:  # residue between symbol spans (gated; default off)
            chunks += _complement_chunks(repo_id, file_id, path, language, lines,
                                         symbols, complement_min_lines)
    else:
        # Files with no symbols: line-window 'block' chunks are mandatory coverage so
        # docs/config/unsupported langs stay findable regardless of scope config.
        chunks += _window_chunks(repo_id, file_id, path, language, lines,
                                 window_lines, overlap, "block")

    if "file" in scopes and text.strip():
        chunks.append(_file_card(repo_id, file_id, path, language, lines))

    if not chunks and text.strip():  # guarantee at least one chunk
        chunks.append(_file_card(repo_id, file_id, path, language, lines))
    return chunks
