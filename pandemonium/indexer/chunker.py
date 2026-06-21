"""Code-aware chunking, scope-aware (Phase 4).

Emitters, gated by `scopes`:
  symbol : one chunk per function/method + a class-header chunk (chunk_type =
           class|method|function, scope=symbol). Large spans split into windows.
  code   : line-window chunks (chunk_type=window for parsed files; chunk_type=block
           is ALWAYS emitted for files with no symbols, as mandatory coverage).
  file   : a single file-scope card (chunk_type=file) per file.

The descriptor (what we embed) is built later from summary+tags; chunk.content is the
stored raw code used for display / repo_get fallback only.
"""

from __future__ import annotations

from typing import Iterator, List, Sequence, Tuple

from pandemonium.models import Chunk, Symbol
from pandemonium.util import chunk_id_for, sha256_text

DEFAULT_SCOPES = ("symbol", "file", "code")
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


def _spans(start: int, end: int, window: int, overlap: int) -> List[Tuple[int, int]]:
    if end - start + 1 <= window:
        return [(start, end)]
    return list(_windows(start, end, window, overlap))


def _symbol_chunks(repo_id, file_id, path, language, lines, symbols, window_lines,
                   overlap) -> List[Chunk]:
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
        for ws, we in _spans(start, end, window_lines, overlap):
            content = _slice(lines, ws, we)
            if not content.strip():
                continue
            cid = chunk_id_for(file_id, ws, we, sym.symbol_type)
            chunks.append(Chunk(cid, repo_id, file_id, sym.id, sym.symbol_type, language,
                                path, ws, we, content, None, sha256_text(content)))
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


def _file_card(repo_id, file_id, path, language, lines) -> Chunk:
    total = len(lines)
    preview = _slice(lines, 1, min(total, _FILE_PREVIEW_LINES))
    cid = chunk_id_for(file_id, 1, max(total, 1), "file")
    return Chunk(cid, repo_id, file_id, None, "file", language, path, 1, max(total, 1),
                 preview, None, sha256_text("\n".join(lines)))


def build_chunks(repo_id: str, file_id: str, path: str, language: str, text: str,
                 symbols: List[Symbol], scopes: Sequence[str] = DEFAULT_SCOPES,
                 window_lines: int = 60, overlap: int = 10) -> List[Chunk]:
    lines = text.splitlines()
    chunks: List[Chunk] = []

    if symbols:
        if "symbol" in scopes:
            chunks += _symbol_chunks(repo_id, file_id, path, language, lines, symbols,
                                     window_lines, overlap)
        if "code" in scopes:  # supplementary line-windows for parsed files (bake-off)
            chunks += _window_chunks(repo_id, file_id, path, language, lines,
                                     window_lines, overlap, "window")
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
