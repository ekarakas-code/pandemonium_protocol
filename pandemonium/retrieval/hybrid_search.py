"""Hybrid retrieval: run all channels, normalize, weighted-merge, dedup, rank.

`hybrid_search` is the merge function: it min-max-normalizes the *uncalibrated* channels
(keyword bm25, vector cosine) to [0,1] — guarding the single-result / identical-score /
empty-channel degenerate cases so a lone hit is *not* forced to 1.0 and we never divide
by zero — then combines them with the configured weights. The symbol channel is already
calibrated (1.0 exact / 0.7 prefix / 0.4 substring) and is passed through by IDENTITY:
normalizing it would collapse a lone exact `1.0` to the degenerate 0.7 and let a strong
semantic near-match out-rank the exact symbol at top-1. In the MVP the note/relationship
channels are empty, so the effective weights are symbol/keyword/vector (docs Part 1).
"Reranking" here *is* this weighted linear merge — a learned reranker is a later phase.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

from pandemonium.embeddings.local_embedder import LocalEmbedder
from pandemonium.models import SearchResult
from pandemonium.refs import build_ref
from pandemonium.retrieval import confidence, keyword_search, symbol_search, vector_search
from pandemonium.storage.lancedb_store import LanceStore
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for

_DEGENERATE_SCORE = 0.7  # present-but-not-maximal for single/identical-score channels
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")  # a single identifier-shaped query
# Channels whose raw scores are already on a calibrated [0,1] scale and must NOT be
# min-max-normalized (which would relativize them away). The symbol channel scores
# exact=1.0 / prefix=0.7 / substring=0.4 — meaningful in absolute terms.
_IDENTITY_CHANNELS = frozenset({"symbol"})


def _row_get(row, key):
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _parse_tags(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


_SYMBOL_TYPES = {"class", "method", "function"}


def _where_clause(chunk_types) -> Optional[str]:
    if not chunk_types:
        return None
    vals = ",".join("'" + t.replace("'", "''") + "'" for t in chunk_types)
    return f"chunk_type IN ({vals})"


def _normalize(pairs: List[Tuple[str, float]]) -> Dict[str, float]:
    if not pairs:
        return {}
    scores = [s for _, s in pairs]
    lo, hi = min(scores), max(scores)
    if (hi - lo) < 1e-9:  # single result or all-equal -> don't force 1.0
        return {cid: _DEGENERATE_SCORE for cid, _ in pairs}
    span = hi - lo
    return {cid: (s - lo) / span for cid, s in pairs}


def _identity(pairs: List[Tuple[str, float]]) -> Dict[str, float]:
    """Pass a pre-calibrated channel through unchanged (clamped to [0,1]). On a key
    collision keep the higher score (the channel may list a chunk more than once)."""
    out: Dict[str, float] = {}
    for cid, s in pairs:
        out[cid] = max(out.get(cid, 0.0), max(0.0, min(1.0, s)))
    return out


def hybrid_search(channels: Dict[str, List[Tuple[str, float]]],
                  weights: Dict[str, float],
                  final_top_k: int) -> List[Tuple[str, float, Dict[str, float]]]:
    """Merge per-channel (chunk_id, score) lists into a ranked list of
    (chunk_id, combined_score, channel_scores)."""
    normalized = {ch: (_identity(pairs) if ch in _IDENTITY_CHANNELS else _normalize(pairs))
                  for ch, pairs in channels.items()}
    all_ids: set[str] = set()
    for d in normalized.values():
        all_ids.update(d.keys())

    merged: List[Tuple[str, float, Dict[str, float]]] = []
    for cid in all_ids:
        contributions: Dict[str, float] = {}
        total = 0.0
        for channel, weight in weights.items():
            value = normalized.get(channel, {}).get(cid, 0.0)
            if value:
                contributions[channel] = round(value, 4)
            total += weight * value
        merged.append((cid, total, contributions))

    # Deterministic tie-break by chunk_id. Without it, equal combined scores keep their
    # `all_ids` set-iteration order, which is PYTHONHASHSEED-dependent — making ranking
    # (and therefore the eval gate that tunes everything else) non-reproducible across
    # processes. chunk_id is stable and unique, so ties now resolve identically every run.
    merged.sort(key=lambda x: (-x[1], x[0]))
    return merged[:final_top_k]


def _overlaps(a: SearchResult, b: SearchResult) -> bool:
    if a.path != b.path:
        return False
    if (a.scope == "file") != (b.scope == "file"):
        return False  # a whole-file card never collapses against a symbol/code hit
    return not (a.end_line < b.start_line or b.end_line < a.start_line)


def _ref_key(r: SearchResult):
    """Stable identity for the first-pass collapse: a symbol_id, else a non-empty ref.
    Returns None for code/file chunks with no stable anchor — those fall through to the
    line-overlap pass untouched."""
    if r.symbol_id:
        return ("sym", r.symbol_id)
    if r.ref:
        return ("ref", r.ref)
    return None


def _collapse_by_ref(results: List[SearchResult]) -> List[SearchResult]:
    """Collapse every result that shares a symbol_id / ref into ONE card.

    A large symbol is chunked into several overlapping windows that all share one ref
    (`build_ref` drops the window line range) and one symbol_id; the symbol channel then
    expands a match to *all* of them (`chunk_ids_for_symbols`), so the same symbol can
    appear 3× in the top-k. Results arrive score-desc, so the first occurrence is the
    highest score; on a score tie we upgrade the representative to the symbol-scope window
    anchored at the smallest start_line (the one carrying the signature). Ref-less code/
    file chunks are preserved for the subsequent line-overlap dedup."""
    out: List[SearchResult] = []
    pos: Dict[tuple, int] = {}
    for r in results:
        key = _ref_key(r)
        if key is None:
            out.append(r)
            continue
        if key not in pos:
            pos[key] = len(out)
            out.append(r)
            continue
        kept = out[pos[key]]
        if r.score < kept.score - 1e-9:
            continue  # strictly lower score -> the kept card wins
        # On a score tie, prefer the COMPLETE unit (the full symbol) over a partial cAST
        # `ast_block` child sharing its symbol_id, so the card the agent sees is whole.
        better_complete = r.is_complete_unit and not kept.is_complete_unit
        better_scope = (r.scope == "symbol") and (kept.scope != "symbol")
        earlier = (r.scope == kept.scope) and (r.start_line < kept.start_line)
        if better_complete or better_scope or earlier:
            out[pos[key]] = r
    return out


def _dedup(results: List[SearchResult]) -> List[SearchResult]:
    kept: List[SearchResult] = []
    for r in _collapse_by_ref(results):  # collapse same-ref/symbol windows first
        if not any(_overlaps(k, r) for k in kept):
            kept.append(r)
    return kept


def _complete(row) -> bool:
    """cAST is_complete_unit from a chunk row; legacy rows (no column) read as complete."""
    ic = _row_get(row, "is_complete_unit")
    return True if ic is None else bool(ic)


def _reason(r: SearchResult) -> str:
    cs = r.channel_scores or {}
    sym = max(cs.get("symbol", 0.0), 0.0)
    kw = cs.get("keyword", 0.0)
    if sym >= 0.6 and r.symbol_name:
        return f"exact symbol match: {r.symbol_name}"
    if sym > 0 and r.symbol_name:
        return f"name match: {r.symbol_name}"
    if kw >= max(cs.get("vector", 0.0), 0.0):
        return "keyword match"
    return "semantically related"


class Retriever:
    """Holds the (read-only) stores and runs hybrid retrieval."""

    def __init__(self, settings, sqlite: Optional[SqliteStore] = None,
                 lance: Optional[LanceStore] = None,
                 embedder: Optional[LocalEmbedder] = None, read_only: bool = True):
        self.settings = settings
        self.repo_id = repo_id_for(settings.repo_root)
        self.sqlite = sqlite or SqliteStore(settings.sqlite_path)
        self.sqlite.create_schema()
        self._read_only = read_only
        # The vector store + embedding model load LAZILY (on first vector-channel use), so
        # the exact-identifier short-circuit — which skips the vector channel — never pays
        # the ~1-2s model load. An injected lance/embedder (tests) is used as-is.
        self._lance = lance
        self._embedder = embedder
        # The confidence verdict for the most recent search (surfaced by repo_search).
        self.last_assessment: Optional[dict] = None

    @property
    def lance(self) -> LanceStore:
        if self._lance is None:
            dim = self.settings.section("embedding").get("dim", 384)
            self._lance = LanceStore(self.settings.lancedb_path, dim=dim,
                                     read_only=self._read_only)
        return self._lance

    @property
    def embedder(self) -> LocalEmbedder:
        if self._embedder is None:
            self._embedder = LocalEmbedder.from_settings(self.settings)
        return self._embedder

    def _exact_short_circuit(self, query: str, top_k: int) -> Optional[List[SearchResult]]:
        """#7: when the whole query is ONE identifier-shaped token (len>=3, not a stopword)
        that names an EXACT symbol, return one card per distinct exact symbol — carrying
        signature + line range — and skip the keyword/vector channels entirely (so the
        embedding model never loads). Returns None to fall through to full hybrid search.
        Gated strictly so semantic / multi-word queries are untouched; config-toggleable
        via retrieval.exact_short_circuit."""
        if not self.settings.section("retrieval").get("exact_short_circuit", True):
            return None
        raw = (query or "").strip()
        tokens = symbol_search._query_tokens(raw)
        if len(tokens) != 1 or _IDENT_RE.fullmatch(raw) is None:
            return None  # multi-word / non-identifier / too-short / stopword query
        rows = [row for row in self.sqlite.symbols_by_name(self.repo_id, tokens[0],
                                                           limit=max(top_k, 10))
                if row["match_rank"] == 3]  # exact name match only
        if not rows:
            return None
        results: List[SearchResult] = []
        seen: set = set()
        for row in rows:
            sid = row["id"]
            if sid in seen:
                continue
            seen.add(sid)
            frow = self.sqlite.conn.execute(
                "SELECT path, language FROM files WHERE id=?", (row["file_id"],)).fetchone()
            path = frow["path"] if frow else ""
            qn = row["qualified_name"] or row["name"]
            # Pull the body from the symbol's HEAD chunk (the signature-anchored window) so a
            # short-circuited context-pack still gets a code excerpt — parity with the full
            # search path, which carries chunk content. Keep the symbol's FULL line range.
            chunks = self.sqlite.get_chunks(self.sqlite.chunk_ids_for_symbols([sid]))
            head = min(chunks.values(), key=lambda c: c["start_line"], default=None)
            res = SearchResult(
                chunk_id=head["id"] if head is not None else sid, path=path,
                start_line=row["start_line"], end_line=row["end_line"], score=1.0,
                symbol_id=sid, symbol_name=row["name"], chunk_type=row["symbol_type"],
                language=frow["language"] if frow else None,
                content=head["content"] if head is not None else None,
                summary=row["summary"], channel_scores={"symbol": 1.0},
                ref=build_ref(path, "symbol", qn), scope="symbol", qualified_name=qn)
            res.reason = f"exact symbol match: {row['name']}"
            results.append(res)
            if len(results) >= top_k:
                break
        return results

    def _weights_for(self, mode: Optional[str]) -> Dict[str, float]:
        """Ranking weights for a context mode (Step 6). Unknown/empty mode -> the tuned
        default; a known mode -> its preset MERGED ONTO the default, so a partial preset
        (e.g. only {symbol: 0.55}) overrides one channel instead of silently zeroing the
        others (the merge keeps an under-specified preset from mis-ranking by omission).
        Weights ONLY (Phase 4 settled scope)."""
        r = self.settings.section("retrieval")
        default = r.get("weights", {"symbol": 0.40, "keyword": 0.30, "vector": 0.30})
        if not mode:
            return dict(default)
        preset = ((r.get("modes", {}) or {}).get(mode, {}) or {}).get("weights")
        return {**default, **preset} if preset else dict(default)

    def search(self, query: str, top_k: Optional[int] = None,
               chunk_types: Optional[set] = None,
               dedup: bool = True, mode: Optional[str] = None,
               channels_only: Optional[set] = None) -> List[SearchResult]:
        """Ranked cards for a query. Thin wrapper over `search_assessed` that drops the
        confidence verdict — kept for the many callers that only want the results."""
        results, _ = self.search_assessed(query, top_k=top_k, chunk_types=chunk_types,
                                           dedup=dedup, mode=mode,
                                           channels_only=channels_only)
        return results

    def search_assessed(self, query: str, top_k: Optional[int] = None,
                        chunk_types: Optional[set] = None,
                        dedup: bool = True, mode: Optional[str] = None,
                        channels_only: Optional[set] = None
                        ) -> Tuple[List[SearchResult], dict]:
        """`search` + a confidence verdict (ROADMAP v2 Step 2). Runs the base hybrid
        search, then `confidence.assess`; on a LOW-confidence verdict (top hits clustered
        on one symbol family while a query domain term is uncovered — the measured
        `.size()` failure) it auto-fans-out per-term sub-queries and re-ranks the union by
        domain coverage. The exact-symbol fast path is high-confidence by construction and
        never fans out. Scope-filtered searches keep the simple path.

        `channels_only` (eval channel-isolation baselines, e.g. {"vector"}) runs ONLY those
        channels and bypasses both the short-circuit and the fan-out — those are hybrid
        behaviours, so a single-channel baseline must measure the channel alone."""
        r = self.settings.section("retrieval")
        top_k = top_k or r.get("final_top_k", 10)
        # Bare-identifier exact-symbol queries short-circuit the vector model load.
        # Only when no scope filter / channel isolation is active (those need the full path).
        if chunk_types is None and channels_only is None:
            shorted = self._exact_short_circuit(query, top_k)
            if shorted:
                a = {"confidence": "high", "reason": "exact symbol match",
                     "query_terms": [], "covered_terms": [], "missing_terms": [],
                     "clustered_on": None}
                self.last_assessment = a
                return shorted, a

        results = self._base_search(query, top_k, chunk_types, dedup, mode, channels_only)
        assessment = confidence.assess(query, results)
        if (assessment["confidence"] == "low" and chunk_types is None
                and channels_only is None and r.get("auto_fanout", True)):
            results, assessment = self._fanout(query, top_k, dedup, results, assessment, mode)
        self.last_assessment = assessment
        return results, assessment

    def _fanout(self, query: str, top_k: int, dedup: bool,
                base_results: List[SearchResult], assessment: dict,
                mode: Optional[str] = None) -> Tuple[List[SearchResult], dict]:
        """Low-confidence recovery: search each distinct query term on its own, union with
        the full-query hits, and re-rank by how many query terms each result covers — so a
        result covering the buried domain term overtakes the single-term cluster. This is
        the manual T2 retry, automated; no LLM, no ban-list."""
        r = self.settings.section("retrieval")
        max_sub = int(r.get("fanout_max_subqueries", 4))
        subqueries = assessment["query_terms"][:max_sub]
        union = list(base_results)
        for sq in subqueries:
            union.extend(self._base_search(sq, top_k, None, dedup=False, mode=mode))
        reranked = confidence.rerank_by_coverage(assessment["query_terms"], union)
        reranked = (_dedup(reranked) if dedup else reranked)[:top_k]
        for res in reranked:
            res.reason = _reason(res)
        final = confidence.assess(query, reranked)
        final["fanned_out"] = subqueries
        final["base_reason"] = assessment["reason"]
        if final["confidence"] == "low":
            final["reason"] = (f"fanned out to {subqueries} but {final['reason']} — "
                               "still low confidence, verify manually (grep a distinctive term)")
        else:
            final["reason"] = (f"recovered by fanning out to {subqueries} and re-ranking "
                               f"by domain coverage (was: {assessment['reason']})")
        return reranked, final

    def _base_search(self, query: str, top_k: int,
                     chunk_types: Optional[set], dedup: bool,
                     mode: Optional[str] = None,
                     channels_only: Optional[set] = None) -> List[SearchResult]:
        """One hybrid pass: run channels, scope-filter, merge, build cards, dedup. No
        short-circuit, no confidence assessment — the reusable unit fan-out calls per
        sub-query. `chunk_types` restricts scopes per-channel BEFORE the merge (vector uses
        a LanceDB prefilter), so normalization is faithful to the subset. `mode` selects a
        ranking-weight preset (Step 6); default weights when None.

        `channels_only` restricts which channels run AT ALL (eval channel-isolation
        baselines). None => all three (byte-identical to before). A symbol-only/keyword-only
        baseline never touches `self.lance`/`self.embedder`, so it pays no model load. With a
        single channel present the merge ranks by that channel alone (absent channels
        contribute 0), and `_normalize`/`_identity` are monotonic — so the arm preserves that
        channel's own ranking (a faithful vector-only / BM25-only / symbol-only baseline)."""
        r = self.settings.section("retrieval")
        where = _where_clause(chunk_types)
        want = channels_only or {"symbol", "keyword", "vector"}
        run_symbol = chunk_types is None or bool(_SYMBOL_TYPES & set(chunk_types))
        channels: Dict[str, List[Tuple[str, float]]] = {}
        if "symbol" in want:
            channels["symbol"] = (symbol_search.search(self.sqlite, self.repo_id, query,
                                                       r.get("symbol_top_k", 10))
                                  if run_symbol else [])
        if "keyword" in want:
            channels["keyword"] = keyword_search.search(self.sqlite, query,
                                                        r.get("keyword_top_k", 20))
        if "vector" in want:  # property access here is the lazy model load — keep it gated
            channels["vector"] = vector_search.search(self.lance, self.embedder, query,
                                                      r.get("vector_top_k", 20), where=where)

        # One metadata fetch for every candidate — used for scope filtering AND building.
        cand = {cid for ch in channels.values() for cid, _ in ch}
        meta = self.sqlite.get_chunks(list(cand))
        if chunk_types is not None:
            allowed = set(chunk_types)
            for ch in channels:
                channels[ch] = [(cid, s) for cid, s in channels[ch]
                                if meta.get(cid) is not None
                                and meta[cid]["chunk_type"] in allowed]

        weights = self._weights_for(mode)
        ranked = hybrid_search(channels, weights, max(top_k * 4, top_k))

        results: List[SearchResult] = []
        for cid, score, contributions in ranked:
            row = meta.get(cid)
            if row is None:
                continue
            results.append(SearchResult(
                chunk_id=cid, path=row["path"],
                start_line=row["start_line"], end_line=row["end_line"],
                score=round(score, 4), symbol_id=row["symbol_id"],
                symbol_name=row["symbol_name"], chunk_type=row["chunk_type"],
                language=row["language"], content=row["content"],
                summary=row["summary"], channel_scores=contributions,
                ref=_row_get(row, "ref"), scope=_row_get(row, "scope"),
                qualified_name=_row_get(row, "qualified_name"),
                tags=_parse_tags(_row_get(row, "tags")),
                unit_kind=_row_get(row, "unit_kind"),
                is_complete_unit=_complete(row), safe_for_reasoning=_complete(row),
                parent_ref=_row_get(row, "parent_ref")))

        # Patch 4/5 structural rerank (gated, DEFAULT OFF). Applied to the wide pool BEFORE
        # dedup/truncate. Bypassed for channel-isolation baselines + scope-filtered searches so
        # those stay byte-identical; the exact-symbol short-circuit already returned earlier.
        if r.get("rerank", False) and channels_only is None and chunk_types is None:
            from pandemonium.retrieval import rerank_signals
            results = rerank_signals.apply_penalties(
                results, query, prose=r.get("rerank_prose", True),
                density=r.get("rerank_density", True))

        results = (_dedup(results) if dedup else results)[:top_k]
        for res in results:
            res.reason = _reason(res)
        return results

    def close(self) -> None:
        self.sqlite.close()


def search(settings, query: str, top_k: Optional[int] = None,
           mode: Optional[str] = None) -> List[SearchResult]:
    retriever = Retriever(settings)
    try:
        return retriever.search(query, top_k=top_k, mode=mode)
    finally:
        retriever.close()
