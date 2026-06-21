"""Retrieval confidence assessment + domain-coverage re-ranking (ROADMAP v2, Step 2).

The one measured worst failure (T2): the query ``"cell size"`` confidently returned
``.size()`` accessors — synthesis-of-garbage. The signature of that failure is structural,
not lexical: **the top hits cluster on one symbol family (`size`/`getSize`/…) while a
domain term from the query (`cell`) is covered by none of them.** A `repo_brief` built on
that layer would just dress the wrong answer in authoritative prose, so the fix is a
*trust primitive*: detect the pattern, and act on it.

This module is the detector + the re-ranker. It is deliberately **not a ban-list** — we
never hardcode "`size` is bad" (it is sometimes the real domain term). We measure, per
result set: which query terms the top hits actually *cover*, and whether they collapse onto
a single term/name. The caller (``Retriever.search_assessed``) fans out per-term sub-queries
when confidence is low and re-ranks the union by how many distinct query terms each result
covers — which is exactly what the manual T2 retry did to surface the rescale contract.

No LLM, no network: tokenization + set overlap only, so it is fully deterministic and
testable with the offline fake embedder.
"""

from __future__ import annotations

import re
from typing import List

from pandemonium.retrieval import symbol_search

# Split an identifier / phrase into lowercase word-parts, handling camelCase, snake_case,
# kebab, and digits: "cellSize" -> {cell, size}; "getSize" -> {get, size}; "HTTPServer" ->
# {http, server}. Parts shorter than 3 chars are dropped (noise like "id", "a", "x").
_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z][a-z]+|[a-z]+|[A-Z]+|[0-9]+")

# In the fan-out re-rank, domain-term **coverage leads** and retrieval score only modulates
# within/near a coverage tier. The score is the very signal that misled us (a strong
# `.size()` embedding match), so a result covering more of the query's domain terms must be
# able to overtake a higher-scoring single-term cluster — but a one-term coverage gap can
# still be outweighed by a much stronger score, so a coincidental summary match doesn't win.
SCORE_WEIGHT = 0.4
# A top-N is "clustered" when this fraction (or more) of it collapses onto one name/term.
CLUSTER_FRACTION = 0.6
TOP_N = 5  # how many leading results define "the top hits" for clustering/coverage


def terms_of(text: str) -> set:
    """The distinct lowercase word-parts in a string (camelCase/snake/space-split)."""
    out: set = set()
    if not text:
        return out
    for chunk in re.findall(r"[A-Za-z0-9]+", text):
        for w in _PART_RE.findall(chunk):
            if len(w) >= 3:
                out.add(w.lower())
    return out


def query_terms(query: str) -> List[str]:
    """The query's distinct content terms (stopwords + sub-3-char tokens removed). Reuses
    the symbol channel's tokenizer so the stopword list stays a single seed, not a fork."""
    return symbol_search._query_tokens(query or "")


def result_terms(r) -> set:
    """Every domain term a result *carries*: its symbol name, summary, and tag values —
    what an agent could tell the result is 'about' without fetching its code."""
    out = terms_of(r.symbol_name or "")
    out |= terms_of(r.summary or "")
    for vals in (r.tags or {}).values():
        if isinstance(vals, (list, tuple)):
            for v in vals:
                out |= terms_of(str(v))
    out |= terms_of((r.qualified_name or "").replace(".", " ").replace("::", " "))
    return out


def assess(query: str, results: List) -> dict:
    """Judge whether a result set credibly answers the query.

    Low confidence == the top hits **cluster on one symbol family** AND **a query domain
    term is covered by none of them** — the measured `.size()` failure. Single-term and
    bare-identifier queries are never low (handled by the symbol fast path). Returns a dict
    the caller surfaces and uses to decide whether to fan out.
    """
    qterms = query_terms(query)
    top = results[:TOP_N]
    base = {"confidence": "high", "query_terms": qterms, "covered_terms": qterms,
            "missing_terms": [], "clustered_on": None, "reason": "ok"}
    if len(qterms) < 2 or not top:
        return base  # nothing to be suspicious about

    qset = set(qterms)
    # Per-term: how many top hits carry it. A query term carried by NONE of the top hits is
    # a buried domain term — the distinctive word the result set dropped.
    rterms = [result_terms(r) for r in top]
    carried = {t: sum(1 for rt in rterms if t in rt) for t in qset}
    covered = sorted(t for t, c in carried.items() if c > 0)
    missing = sorted(t for t, c in carried.items() if c == 0)

    # The diagnostic signal is a NAME cluster: the top hits collapse onto one bare symbol
    # name (the `.size()` accessors across many classes). This is rare in a healthy result
    # set (whose names are diverse), so it does not over-fire on ordinary multi-term
    # queries — a common-word "term cluster" alone is NOT enough (it flagged ~60% of a
    # healthy gold set). Low confidence == a name cluster AND a buried domain term.
    names = [(r.symbol_name or "").lower() for r in top if r.symbol_name]
    name_cluster = None
    if len(names) >= 2:
        dom, cnt = max(((n, names.count(n)) for n in set(names)), key=lambda kv: kv[1])
        if cnt >= 2 and cnt / len(top) >= CLUSTER_FRACTION:
            name_cluster = dom

    base["covered_terms"] = covered
    base["missing_terms"] = missing
    if name_cluster and missing:
        return {"confidence": "low", "query_terms": qterms, "covered_terms": covered,
                "missing_terms": missing, "clustered_on": name_cluster,
                "reason": (f"top results cluster on the symbol '{name_cluster}'; query "
                           f"term(s) not covered by any: {', '.join(missing)}")}
    return base


def rerank_by_coverage(qterms: List[str], candidates: List) -> List:
    """Re-rank a fan-out union by blending normalized retrieval score with domain-term
    coverage, so a result covering *both* `cell` and `size` overtakes a strong-but-narrow
    `size` cluster. Collapses duplicates (same symbol/ref across sub-queries) keeping the
    best score. Deterministic (chunk_id tiebreak)."""
    qset = set(qterms)
    qn = max(len(qset), 1)
    best: dict = {}
    for r in candidates:
        key = (r.symbol_id and ("sym", r.symbol_id)) or (r.ref and ("ref", r.ref)) or \
            ("cid", r.chunk_id)
        cur = best.get(key)
        if cur is None or r.score > cur.score:
            best[key] = r
    uniq = list(best.values())
    scores = [r.score for r in uniq]
    lo, hi = (min(scores), max(scores)) if scores else (0.0, 0.0)
    span = (hi - lo) or 1.0
    ranked = []
    for r in uniq:
        cov = len(qset & result_terms(r)) / qn
        blended = cov + SCORE_WEIGHT * (r.score - lo) / span
        ranked.append((blended, r))
    ranked.sort(key=lambda br: (-br[0], br[1].chunk_id))
    return [r for _, r in ranked]
