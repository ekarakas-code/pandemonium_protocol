"""Stable references (anchors) + the resolver behind `repo_get`.

A ref points at an exact code location in a way that survives edits:

    path                          -> file scope     (whole file)
    path::Qualified.Name          -> symbol scope   (re-found by name, edit-stable)
    path:start-end                -> code scope     (line fallback)

The symbol form is the durable anchor: `resolve()` re-parses the *current* file and
locates the symbol by qualified_name, so line shifts after indexing don't break it
(the advisor's edit-stability requirement). Line refs are the last-resort fallback.

`repo_get` calls `resolve(ref, expand=..., view=...)`:
    expand widens the span:
        exact     -> just the target span
        neighbors -> target ± a few lines (imports/local context)
        file      -> the whole file
        parent    -> the containing class/module (or the file if top-level)
    view narrows the resolved span (orthogonal to expand — applied last):
        full       -> the whole span (default)
        signature  -> just the declaration head (through the first '{' or def ':')
        head:N     -> the first N lines of the span
        lines:a-b  -> file lines a..b, clamped within the span
The narrowing views answer "I only need to confirm the shape" without paying for the
whole body — the token-efficiency lever (a body is often 10× the signature an agent
actually needs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from pandemonium.indexer.language_detector import detect, is_parseable
from pandemonium.indexer.tree_sitter_parser import ParsedSymbol, parse_symbols
from pandemonium.util import fingerprint_for, sha256_text, signature_hash_for

_LINE_RE = re.compile(r"^(?P<path>.+):(?P<start>\d+)-(?P<end>\d+)$")
_BLOCK_RE = re.compile(r"#block:(?P<slug>[A-Za-z0-9._\-]+)$")  # cAST child-block suffix
EXPAND_MODES = ("exact", "neighbors", "file", "parent", "block")  # "block" handled pre-resolve
VIEW_FULL = "full"
_VIEW_LINES_RE = re.compile(r"^lines:(\d+)-(\d+)$")
_VIEW_HEAD_RE = re.compile(r"^head:(\d+)$")


@dataclass
class ResolvedCode:
    ref: str
    path: str
    scope: str  # symbol | file | code
    qualified_name: Optional[str]
    start_line: int
    end_line: int
    code: str
    expand: str
    stale: bool = False  # True if we couldn't confirm against the current file
    ambiguous: bool = False  # >1 symbol shares this qualified_name; pick is best-effort
    resolved_by: str = "qname"  # qname | signature | fingerprint | line | file | code
    view: str = VIEW_FULL  # full | signature | head:N | lines:a-b (narrowing, post-expand)
    decl_ref: Optional[str] = None  # C++ header declaration site (Step 8), if merged
    # cAST completeness (Improvements4 #7): so repo_get never silently serves a partial unit.
    unit_kind: Optional[str] = None
    is_complete_unit: bool = True
    safe_for_reasoning: bool = True  # derived: is_complete_unit
    parent_ref: Optional[str] = None
    note: Optional[str] = None  # e.g. auto-upgrade explanation
    truncated: int = 0  # lines dropped by the max_lines safety clamp (0 = nothing clamped)


def build_ref(path: str, scope: str, qualified_name: Optional[str] = None,
              start_line: Optional[int] = None, end_line: Optional[int] = None,
              block_name: Optional[str] = None) -> str:
    if scope == "symbol" and qualified_name:
        base = f"{path}::{qualified_name}"
        return f"{base}#block:{block_name}" if block_name else base  # cAST child label
    if scope == "file":
        return path
    return f"{path}:{start_line}-{end_line}"


def parse_ref(ref: str) -> Tuple[str, Optional[str], Optional[Tuple[int, int]], Optional[str]]:
    """Return (path, qualified_name | None, (start, end) | None, block_name | None).

    cAST: a `#block:<slug>` suffix is stripped FIRST (before the `::`/line parsing — otherwise
    it would corrupt the qualified name). A `path::Qual.fn#block:slug` ref therefore parses to
    the PARENT symbol `Qual.fn` plus the slug, so resolve() degrades it to the complete parent
    (which is the auto-upgrade target anyway). The block slug is informational here."""
    ref = ref.strip()
    block_name = None
    bm = _BLOCK_RE.search(ref)
    if bm:
        block_name = bm.group("slug")
        ref = ref[:bm.start()]
    if "::" in ref:
        path, qname = ref.split("::", 1)
        return path, qname, None, block_name
    m = _LINE_RE.match(ref)
    if m:
        return m.group("path"), None, (int(m.group("start")), int(m.group("end"))), block_name
    return ref, None, None, block_name  # whole-file ref


def _read_lines(repo_root: Path, rel_path: str) -> Optional[List[str]]:
    try:
        root = repo_root.resolve()
        target = (root / rel_path).resolve()
        # Path-traversal containment: a ref is agent-supplied, so an absolute path or a
        # `../`-escape ("../../etc/passwd", "C:\\Windows\\...") must never read outside the
        # indexed repo. .resolve() also collapses any symlink that would escape.
        if target != root and root not in target.parents:
            return None
        return target.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return None


def _find_symbols(symbols: List[ParsedSymbol], qname: str) -> List[ParsedSymbol]:
    return [s for s in symbols if s.qualified_name == qname]


def _find_symbol(symbols: List[ParsedSymbol], qname: str) -> Optional[ParsedSymbol]:
    matches = _find_symbols(symbols, qname)
    return matches[0] if matches else None


def _disambiguate(candidates: List[ParsedSymbol], signature_hash: Optional[str],
                  near: Optional[Tuple[int, int]]) -> Tuple[ParsedSymbol, bool]:
    """Choose among same-qualified-name candidates. Prefer a unique signature_hash
    match (overloads/redefs differ by shape); else the one nearest the hint line. Returns
    (chosen, ambiguous) — ambiguous is True when the choice was a best-effort guess."""
    if signature_hash:
        sig = [c for c in candidates if signature_hash_for(c.signature) == signature_hash]
        if len(sig) == 1:
            return sig[0], False
        if len(sig) > 1:
            candidates = sig  # narrowed, but still not unique
    if near:
        target = near[0]
        return min(candidates, key=lambda c: abs(c.start_line - target)), True
    return candidates[0], True


def _match_by_fingerprint(symbols: List[ParsedSymbol], lines: List[str],
                          fingerprint: str) -> Optional[ParsedSymbol]:
    """Find a symbol whose body matches `fingerprint` (a rename: name changed, body
    didn't). Only returns a match when it is *unambiguous* — never guesses."""
    matches = [s for s in symbols
               if fingerprint_for("\n".join(lines[s.start_line - 1:s.end_line])) == fingerprint]
    return matches[0] if len(matches) == 1 else None


def _apply_expand(expand: str, start: int, end: int, total: int,
                  neighbor_lines: int) -> Tuple[int, int]:
    if expand == "neighbors":
        return max(1, start - neighbor_lines), min(total, end + neighbor_lines)
    if expand == "file":
        return 1, total
    return start, end  # exact (parent handled before this call)


def _signature_end(lines: List[str], start: int, end: int) -> int:
    """Last line of a symbol's declaration head: through the first line that opens a body
    (`{` for C-like / brace languages) or a definition line ending in `:` (Python `def`/
    `class`). Falls back to the first line when neither is found within the span."""
    for i in range(start, end + 1):
        line = lines[i - 1]
        if "{" in line:
            return i
        if line.rstrip().endswith(":"):
            return i
    return start


def _apply_view(view: Optional[str], start: int, end: int,
                lines: List[str]) -> Tuple[int, int]:
    """Narrow an already-resolved [start, end] span (1-based, inclusive). Orthogonal to
    expand and applied after it. Unknown/blank views are a no-op (full)."""
    if not view or view == VIEW_FULL:
        return start, end
    if view == "signature":
        return start, _signature_end(lines, start, end)
    m = _VIEW_HEAD_RE.match(view)
    if m:
        n = max(1, int(m.group(1)))
        return start, min(end, start + n - 1)
    m = _VIEW_LINES_RE.match(view)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if a > b:
            a, b = b, a
        return max(start, min(a, end)), max(start, min(b, end))
    return start, end  # unrecognized -> full span (no surprise truncation)


def resolve(repo_root, ref: str, expand: str = "exact", neighbor_lines: int = 8,
            max_lines: int = 1200, fallback_lines: Optional[Tuple[int, int]] = None,
            signature_hash: Optional[str] = None, content_hash: Optional[str] = None,
            fingerprint: Optional[str] = None,
            view: str = VIEW_FULL) -> Optional[ResolvedCode]:
    """Resolve a ref to a code span, tiered for reliability:
    qname-exact -> signature-disambiguated (same-name collisions) -> fingerprint
    (survives a rename) -> line fallback. When `content_hash` is supplied (the indexed
    span's hash), the result's `stale` reflects an actual content change, not just
    name-presence. The hash args come from the stored card via `chunk_by_ref`."""
    repo_root = Path(repo_root)
    path, qname, line_range, _block = parse_ref(ref)
    lines = _read_lines(repo_root, path)
    if lines is None:
        return None  # file gone / unreadable
    total = len(lines)
    if expand not in EXPAND_MODES:
        expand = "exact"

    scope = "file"
    start, end = 1, total
    stale = False
    ambiguous = False
    resolved_by = "file"

    if qname is not None:
        scope = "symbol"
        resolved_by = "qname"
        language = detect(path)
        sym = None
        if is_parseable(language):
            parsed = parse_symbols("\n".join(lines).encode("utf-8", "replace"), language)
            candidates = _find_symbols(parsed, qname)
            if len(candidates) == 1:
                sym = candidates[0]
            elif len(candidates) > 1:
                sym, ambiguous = _disambiguate(candidates, signature_hash,
                                               fallback_lines or line_range)
                resolved_by = "qname" if ambiguous else "signature"
            elif fingerprint:
                sym = _match_by_fingerprint(parsed, lines, fingerprint)
                if sym is not None:
                    resolved_by = "fingerprint"  # re-found after a likely rename
        if sym is not None:
            start, end = sym.start_line, sym.end_line
            # Confirm against the indexed hash — more accurate than "found by name".
            # Skip for a fingerprint match: the name changed but the body is the proof.
            if content_hash is not None and resolved_by != "fingerprint":
                live = sha256_text("\n".join(lines[start - 1:end]))
                stale = live != content_hash
            if expand == "parent":
                start, end = _resolve_parent(lines, language, qname, start, end)
                expand = "exact"
        elif fallback_lines or line_range:
            start, end = fallback_lines or line_range  # type: ignore[assignment]
            stale = True  # couldn't re-find by name; lines may have shifted
            resolved_by = "line"
        else:
            return None  # symbol no longer exists and no fallback
    elif line_range is not None:
        scope = "code"
        resolved_by = "code"
        start, end = line_range
        if expand == "parent":
            expand = "exact"

    start = max(1, min(start, total))
    end = max(start, min(end, total))
    start, end = _apply_expand(expand, start, end, total, neighbor_lines)
    # view narrows the (post-expand) span — the token-efficiency lever — before the
    # max_lines safety clamp, so a 'signature'/'head:N' view is always honored in full.
    start, end = _apply_view(view, start, end, lines)
    truncated = 0
    if end - start + 1 > max_lines:
        truncated = (end - start + 1) - max_lines
        end = start + max_lines - 1

    code = "\n".join(lines[start - 1:end])
    return ResolvedCode(ref=ref, path=path, scope=scope, qualified_name=qname,
                        start_line=start, end_line=end, code=code, expand=expand,
                        stale=stale, ambiguous=ambiguous, resolved_by=resolved_by,
                        view=view or VIEW_FULL, truncated=truncated)


def resolve_from_row(repo_root, ref: str, row, expand: str = "exact",
                     view: str = VIEW_FULL) -> Optional[ResolvedCode]:
    """Resolve `ref` using identity hints (signature/content/fingerprint hashes + line
    fallback) from a stored card row (a `chunk_by_ref` sqlite3.Row, or None)."""
    def col(name):
        return row[name] if row is not None and name in row.keys() else None
    fb = (row["start_line"], row["end_line"]) if row is not None else None
    # Staleness compares against the symbol's FULL-span hash, not the chunk's own
    # content_hash (which may be just a class header or one window of a large symbol).
    resolved = resolve(repo_root, ref, expand=expand, view=view, fallback_lines=fb,
                       signature_hash=col("signature_hash"),
                       content_hash=col("symbol_content_hash"),
                       fingerprint=col("fingerprint"))
    # decl_ref (C++ header↔cpp merge) is a cross-file fact established at index time, so it
    # can't be re-derived by resolve()'s single-file re-parse — carry it from the stored row.
    if resolved is not None:
        resolved.decl_ref = col("decl_ref")
        _apply_completeness(resolved, row)
    return resolved


def _apply_completeness(resolved: ResolvedCode, row) -> None:
    """Copy the cAST completeness columns from the stored card onto the resolved code, and
    derive safe_for_reasoning (a complete unit is safe; a partial child/header/preview is not).
    Defaults keep legacy rows (no columns) complete + safe."""
    def col(name):
        return row[name] if row is not None and name in row.keys() else None
    ic = col("is_complete_unit")
    resolved.is_complete_unit = True if ic is None else bool(ic)
    resolved.unit_kind = col("unit_kind")
    resolved.parent_ref = col("parent_ref")
    resolved.safe_for_reasoning = resolved.is_complete_unit


def resolve_with_upgrade(repo_root, ref: str, row, fetch_row, expand: str = "exact",
                         view: str = VIEW_FULL) -> Optional[ResolvedCode]:
    """The delivery contract (Improvements4 #4): resolve `ref`, but if it lands on a partial
    cAST child (an `ast_block`, is_complete_unit=False) auto-upgrade to its COMPLETE parent so
    the agent never reasons from half a unit. `fetch_row(parent_ref) -> row` is the storage
    callback (keeps this storage-agnostic). `expand="block"` opts OUT and returns the raw child
    (still flagged unsafe). Other expand modes pass through unchanged."""
    raw = expand == "block"
    resolved = resolve_from_row(repo_root, ref, row, expand=("exact" if raw else expand),
                                view=view)
    if resolved is None:
        return None
    if not raw and not resolved.is_complete_unit and resolved.parent_ref:
        prow = fetch_row(resolved.parent_ref)
        parent = resolve_from_row(repo_root, resolved.parent_ref, prow, expand="exact",
                                  view=view)
        if parent is not None:
            parent.note = (f"auto-expanded from partial block {ref} to complete unit "
                           f"{resolved.parent_ref}")
            return parent
    if not resolved.is_complete_unit:
        tail = f" (parent: {resolved.parent_ref})" if resolved.parent_ref else ""
        resolved.note = f"partial unit ({resolved.unit_kind}) — not safe to reason from alone{tail}"
    return resolved


def _resolve_parent(lines: List[str], language: Optional[str], qname: str,
                    default_start: int, default_end: int) -> Tuple[int, int]:
    """Span of the containing class/module; whole file if top-level."""
    if "." not in qname:
        return 1, len(lines)  # top-level symbol -> the file is its parent
    parent_qname = qname.rsplit(".", 1)[0]
    if is_parseable(language):
        source = "\n".join(lines).encode("utf-8", "replace")
        parent = _find_symbol(parse_symbols(source, language), parent_qname)
        if parent is not None:
            return parent.start_line, parent.end_line
    return default_start, default_end
