"""Retrieval eval harness — baseline + before/after comparison.

Runs the labeled query set (gold.py) against the live index and reports:
  precision@1/3/5, MRR (file-level), symbol precision@5,
  one-shot pack tokens vs cards-only tokens (the token-savings story),
  and a same-path-repeat rate (duplicate proxy; matters once scopes land).

`fetches-to-resolution` is recorded as N/A for the current one-shot system — it
becomes measurable once repo_get + the cards loop exist (Phase 1+).

Usage:
  python evals/run_eval.py                 # print summary
  python evals/run_eval.py --save baseline # also write evals/baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # make `gold` importable

from gold import CPP_FIXTURE_GOLD, CPP_MERGE_GOLD, GOLD, IMPACT_GOLD  # noqa: E402

from pandemonium import service  # noqa: E402
from pandemonium.config import Settings  # noqa: E402
from pandemonium.retrieval.context_packer import ContextPacker  # noqa: E402
from pandemonium.retrieval.hybrid_search import Retriever  # noqa: E402
from pandemonium.tokens.counter import TokenCounter  # noqa: E402

TOP_K = 10
MAX_FETCHES = 5  # cards an agent fetches (repo_get) before giving up on a query


def _bare(qname) -> str:
    return (qname or "").split(".")[-1]


def _impact_fp_fn(settings, impact_gold=None) -> dict:
    """Compare repo_impact's direct callers to hand-authored truth (gold.IMPACT_GOLD, or an
    external task file's `impact` list). FP = claimed-but-not-real; FN = real-but-missed."""
    impact_gold = IMPACT_GOLD if impact_gold is None else impact_gold
    fp = fn = pred = gold = 0
    for item in impact_gold:
        imp = service.impact_for(settings, item["ref"]) or {}
        got = set(imp.get("direct", []))
        want = set(item["true_direct"])
        fp += len(got - want)
        fn += len(want - got)
        pred += len(got)
        gold += len(want)
    return {"impact_fp_rate": round(fp / max(pred, 1), 3),
            "impact_fn_rate": round(fn / max(gold, 1), 3),
            "impact_cases": len(impact_gold)}


def _first_rank(results, needles, attr):
    """0-based rank of the first result whose `attr` matches any needle; else None."""
    for i, r in enumerate(results):
        value = getattr(r, attr) or ""
        if attr == "path":
            if any(n in value for n in needles):
                return i
        else:
            if any(n == value for n in needles):
                return i
    return None


def _card_line(r) -> str:
    sym = r.symbol_name or r.chunk_type or ""
    return f"- {r.path}::{sym} (L{r.start_line}-{r.end_line}) — {r.summary or ''}"


def load_tasks(path: str):
    """Load an EXTERNAL retrieval task set so the harness can run against ANY indexed repo —
    the large-repo / cross-file task set (Improvements3 #9's `tasks.yaml`, made real and
    repo-agnostic). Returns (queries, impact).

    JSON by default; YAML when the path ends .yaml/.yml AND PyYAML is installed (no hard dep).
    Schema: a top-level LIST of query items, OR a dict with `queries` (required) + optional
    `impact`. Each query: {q, files:[POSIX path substrings], symbols?:[bare names]}. Each
    impact: {ref: "path::Qualified.Name", true_direct:[refs]}. Extra keys (e.g. `_doc`) are
    ignored, so a template can document itself inline."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml  # optional — only imported for YAML task files
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if isinstance(data, list):
        queries, impact = data, []
    else:
        queries, impact = data.get("queries", []), data.get("impact", [])
    if not queries:
        raise SystemExit(f"no queries in task file: {path}")
    for i, item in enumerate(queries):
        if "q" not in item or "files" not in item:
            raise SystemExit(f"task[{i}] needs at least 'q' and 'files': {item!r}")
    return queries, impact


def run(repo: str = ".", gold=None, impact_gold=None) -> dict:
    gold = gold or GOLD
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    packer = ContextPacker(settings, retriever=retriever)
    counter = TokenCounter(settings.section("context_pack").get("tokenizer", "cl100k_base"))

    n = len(gold)
    file_hits = {1: 0, 3: 0, 5: 0}
    sym_hit5 = 0
    mrr_sum = 0.0
    pack_tok_sum = 0
    cards_tok_sum = 0
    dup_repeat = 0
    dup_total = 0
    dup_card_repeat = 0          # same symbol (path+qualified_name) re-surfaced
    dup_card_total = 0
    ftr_counts = []              # fetches-to-resolution, per resolved query
    ftr_miss = 0
    amb_refs = 0                 # ambiguous refs among fetched cards
    amb_checked = 0
    same_name_cases = 0          # gold symbols whose name collides in the repo
    wrong_symbol = 0
    rows = []

    for item in gold:
        q = item["q"]
        results = retriever.search(q, top_k=TOP_K)
        f_rank = _first_rank(results, item["files"], "path")
        s_rank = _first_rank(results, item.get("symbols", []), "symbol_name")

        for k in (1, 3, 5):
            if f_rank is not None and f_rank < k:
                file_hits[k] += 1
        if s_rank is not None and s_rank < 5:
            sym_hit5 += 1
        mrr_sum += (1.0 / (f_rank + 1)) if f_rank is not None else 0.0

        pack = packer.build(q, token_budget=4000)
        pack_tok = counter.count(pack)
        cards = "\n".join(_card_line(r) for r in results[:5])
        cards_tok = counter.count(cards)
        pack_tok_sum += pack_tok
        cards_tok_sum += cards_tok

        seen_paths: set = set()
        seen_cards: set = set()
        for r in results:
            dup_total += 1
            if r.path in seen_paths:
                dup_repeat += 1
            seen_paths.add(r.path)
            dup_card_total += 1
            ckey = (r.path, r.qualified_name) if r.qualified_name else r.ref
            if ckey in seen_cards:
                dup_card_repeat += 1
            seen_cards.add(ckey)

        # Agentic loop: fetch each card by ref (real repo_get) until one resolves to the
        # gold target. Counts fetches-to-resolution + ambiguous refs seen along the way.
        gold_files, gold_syms = item["files"], item.get("symbols", [])
        fetched, resolved_ok = 0, False
        for r in results[:MAX_FETCHES]:
            if not r.ref:
                continue
            rc = service.get(settings, r.ref)
            if rc is None:
                continue
            fetched += 1
            amb_checked += 1
            if rc.ambiguous:
                amb_refs += 1
            path_ok = any(gf in rc.path for gf in gold_files)
            sym_ok = (not gold_syms) or any(_bare(rc.qualified_name) == s for s in gold_syms)
            if path_ok and sym_ok:
                resolved_ok = True
                break
        if resolved_ok:
            ftr_counts.append(fetched)
        else:
            ftr_miss += 1

        # Same-name disambiguation: when a gold symbol name has an EXACT-name collision
        # (>1 symbol literally named `s` — not prefix/substring), does the top card
        # bearing that name point at the gold file?
        for s in gold_syms:
            exact = [m for m in service.symbol(settings, s) if m["name"] == s]
            if len(exact) <= 1:
                continue
            same_name_cases += 1
            top = next((r for r in results if r.symbol_name == s), None)
            if top is None or not any(gf in top.path for gf in gold_files):
                wrong_symbol += 1

        rows.append({"q": q, "file_rank": f_rank, "sym_rank": s_rank,
                     "pack_tokens": pack_tok, "cards_tokens": cards_tok,
                     "fetches": fetched if resolved_ok else None})

    retriever.close()

    summary = {
        "queries": n,
        "precision_at_1": round(file_hits[1] / n, 3),
        "precision_at_3": round(file_hits[3] / n, 3),
        "precision_at_5": round(file_hits[5] / n, 3),
        "symbol_precision_at_5": round(sym_hit5 / n, 3),
        "mrr": round(mrr_sum / n, 3),
        "avg_pack_tokens": round(pack_tok_sum / n, 1),
        "avg_cards_tokens": round(cards_tok_sum / n, 1),
        "token_savings_cards_vs_pack": round(1 - cards_tok_sum / max(pack_tok_sum, 1), 3),
        "same_path_repeat_rate": round(dup_repeat / max(dup_total, 1), 3),
        "duplicate_card_rate": round(dup_card_repeat / max(dup_card_total, 1), 3),
        # Real fetches-to-resolution (repo_get exists now): mean repo_get calls before a
        # card resolves to the gold symbol, plus how often resolution happened at all.
        "fetches_to_resolution": (round(sum(ftr_counts) / len(ftr_counts), 2)
                                  if ftr_counts else None),
        "resolution_rate": round(len(ftr_counts) / n, 3),
        "ambiguous_ref_rate": round(amb_refs / max(amb_checked, 1), 3),
        "wrong_symbol_same_name_rate": (round(wrong_symbol / same_name_cases, 3)
                                        if same_name_cases else 0.0),
        "same_name_cases": same_name_cases,
    }
    summary.update(_impact_fp_fn(settings, impact_gold))
    return {"summary": summary, "rows": rows}


def _print(result: dict) -> None:
    s = result["summary"]
    print("=== Retrieval eval ===")
    for key in ("queries", "precision_at_1", "precision_at_3", "precision_at_5",
                "symbol_precision_at_5", "mrr", "avg_pack_tokens", "avg_cards_tokens",
                "token_savings_cards_vs_pack", "same_path_repeat_rate",
                "duplicate_card_rate", "fetches_to_resolution", "resolution_rate",
                "ambiguous_ref_rate", "wrong_symbol_same_name_rate", "same_name_cases",
                "impact_fp_rate", "impact_fn_rate", "impact_cases"):
        print(f"  {key:30s} {s[key]}")
    misses = [r["q"] for r in result["rows"] if r["file_rank"] is None]
    if misses:
        print("  misses (no gold file in top-10):")
        for q in misses:
            print(f"    - {q}")


# ---------------------------------------------------------------------------
# Acceptance gate (ROADMAP.md v2 — M1 lock + M3 acceptance rule).
#
# M1: "lock it so a future protocol update can't regress [the caller graph] unnoticed."
# M3: a synthesis feature "ships only when the eval matrix shows fewer total task tokens +
#     lower error rate than the baseline."
#
# So `--gate LABEL` re-runs the eval and compares to evals/<LABEL>.json. HARD gates (graph
# correctness + retrieval quality) FAIL the run on ANY adverse move vs the baseline — a
# regression exits non-zero (CI-catchable). SOFT gates (token cost) only warn. To accept a
# deliberate tradeoff, re-save the baseline; the gate forces that to be an explicit act.
# ---------------------------------------------------------------------------
_EPS = 1e-9
# (metric, direction, hard) — direction "up" = higher is better, "down" = lower is better.
_GATE_SPEC = [
    ("impact_fp_rate", "down", True),
    ("impact_fn_rate", "down", True),
    ("wrong_symbol_same_name_rate", "down", True),
    ("duplicate_card_rate", "down", True),
    ("ambiguous_ref_rate", "down", True),
    ("precision_at_1", "up", True),
    ("precision_at_3", "up", True),
    ("precision_at_5", "up", True),
    ("mrr", "up", True),
    ("resolution_rate", "up", True),
    ("avg_cards_tokens", "down", False),   # token budget is the edge (#17) — warn, don't block
    ("avg_pack_tokens", "down", False),
]


def gate(summary: dict, baseline: dict) -> bool:
    """Compare a fresh summary to a baseline summary. Return True if no HARD gate
    regressed. Prints a per-metric verdict table."""
    print("=== Acceptance gate (vs baseline) ===")
    print(f"  {'metric':30s} {'base':>9} {'now':>9} {'delta':>9}  verdict")
    hard_ok = True
    for metric, direction, hard in _GATE_SPEC:
        cur, base = summary.get(metric), baseline.get(metric)
        if cur is None or base is None:
            continue
        delta = cur - base
        adverse = (delta < -_EPS) if direction == "up" else (delta > _EPS)
        improved = (delta > _EPS) if direction == "up" else (delta < -_EPS)
        if adverse and hard:
            verdict, hard_ok = "FAIL", False
        elif adverse:
            verdict = "warn"
        elif improved:
            verdict = "improved"
        else:
            verdict = "ok"
        print(f"  {metric:30s} {base:9.3f} {cur:9.3f} {delta:+9.3f}  {verdict}")
    print(f"\n  GATE: {'PASS' if hard_ok else 'FAIL — a hard metric regressed vs baseline'}")
    return hard_ok


# ---------------------------------------------------------------------------
# Phase 4 scope bake-off: index once with all scopes, filter at eval time.
# ---------------------------------------------------------------------------
BASE = {"class", "method", "function", "block"}  # == Phase 3 emission -> repro gate
SWEEP_CONFIGS = {
    "base(sym+block)": BASE,
    "+file": BASE | {"file"},
    "+window": BASE | {"window"},
    "all": BASE | {"file", "window"},
}


def _overlap_count(results) -> int:
    from pandemonium.retrieval.hybrid_search import _overlaps
    redundant, kept = 0, []
    for r in results:
        if any(_overlaps(k, r) for k in kept):
            redundant += 1
        else:
            kept.append(r)
    return redundant


def _config_metrics(retriever, chunk_types) -> dict:
    n = len(GOLD)
    fh = {1: 0, 3: 0, 5: 0}
    sym5 = 0
    mrr = 0.0
    ranks = []
    pre_overlap = pre_total = 0
    for item in GOLD:
        res = retriever.search(item["q"], top_k=10, chunk_types=chunk_types)
        f = _first_rank(res, item["files"], "path")
        s = _first_rank(res, item.get("symbols", []), "symbol_name")
        ranks.append(f)
        for k in (1, 3, 5):
            if f is not None and f < k:
                fh[k] += 1
        if s is not None and s < 5:
            sym5 += 1
        mrr += (1.0 / (f + 1)) if f is not None else 0.0
        raw = retriever.search(item["q"], top_k=10, chunk_types=chunk_types, dedup=False)
        pre_total += len(raw)
        pre_overlap += _overlap_count(raw)
    return {"p1": fh[1] / n, "p3": fh[3] / n, "p5": fh[5] / n, "symP5": sym5 / n,
            "mrr": mrr / n, "pre_dedup_overlap": pre_overlap / max(pre_total, 1),
            "misses": sum(1 for r in ranks if r is None), "ranks": ranks}


def sweep(repo: str = ".") -> dict:
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    metrics = {name: _config_metrics(retriever, ct) for name, ct in SWEEP_CONFIGS.items()}
    retriever.close()

    print("=== Scope bake-off (index-once, filter-at-eval) ===")
    print(f"{'config':16s} {'P@1':>5} {'P@3':>5} {'P@5':>5} {'symP5':>6} "
          f"{'MRR':>5} {'preDupOv':>8} {'miss':>4}")
    for name, m in metrics.items():
        print(f"{name:16s} {m['p1']:5.3f} {m['p3']:5.3f} {m['p5']:5.3f} {m['symP5']:6.3f} "
              f"{m['mrr']:5.3f} {m['pre_dedup_overlap']:8.3f} {m['misses']:4d}")

    base_ranks = metrics["base(sym+block)"]["ranks"]
    print("\n=== Paired per-query deltas vs base (n=15) ===")
    for other in ("+file", "+window", "all"):
        imp = wor = same = 0
        for b, o in zip(base_ranks, metrics[other]["ranks"]):
            bb = b if b is not None else 99
            oo = o if o is not None else 99
            imp += oo < bb
            wor += oo > bb
            same += oo == bb
        print(f"  {other:8s}: improved {imp}  worsened {wor}  unchanged {same}")
    return metrics


# ---------------------------------------------------------------------------
# repo_brief A/B (ROADMAP v2, Step 5 + M3 acceptance rule).
#
# A brand-new synthesis tool can't be judged by `run()` (which never calls it), and the
# brief deliberately BUNDLES MORE than a context pack — so it will cost MORE tokens per
# call, not fewer. The honest, static-harness-measurable question is therefore NOT "fewer
# tokens" but: does the brief point at the RIGHT target (anchor, or top-3 likely targets)
# at least as reliably as `repo_context_pack` surfaces the right file, and at what token
# cost? The real M3 win — fewer wrong edits / re-searches across a whole task — is a
# task-level claim the static gold set cannot measure; it is stated as a hypothesis, the
# same honesty pattern as the Step-2 note. This mode is SEPARATE: it never touches `run()`'s
# summary dict, so the acceptance baseline the gate compares against is unmoved.
# ---------------------------------------------------------------------------
def _ref_path(ref: str) -> str:
    return (ref or "").split("::", 1)[0].split(":", 1)[0]


def brief_eval(repo: str = ".") -> dict:
    from pandemonium.brief import render_brief, repo_brief

    settings = Settings.load(repo)
    retriever = Retriever(settings)
    packer = ContextPacker(settings, retriever=retriever)
    counter = TokenCounter(settings.section("context_pack").get("tokenizer", "cl100k_base"))

    n = len(GOLD)
    anchor_hit = top3_hit = anchored = pack_hit = 0
    committed_hit = withheld_wouldhit = withheld = 0
    brief_tok_sum = pack_tok_sum = 0
    conf = {"high": 0, "medium": 0, "low": 0, "none": 0}
    rows = []
    for item in GOLD:
        q, gold_files = item["q"], item["files"]
        b = repo_brief(settings, q, retriever=retriever)
        conf[b["anchor_confidence"]] = conf.get(b["anchor_confidence"], 0) + 1
        anchored += int(b["anchored"])
        # a_hit: did the PICKED symbol hit the gold file (set even when withheld, so we can
        # ask whether the gate suppressed would-be-wrong anchors).
        a_hit = bool(b["anchor"]) and any(gf in _ref_path(b["anchor"]) for gf in gold_files)
        top3 = [t["ref"] for t in b["heuristic"]["likely_targets"][:3]]
        t_hit = any(any(gf in _ref_path(r) for gf in gold_files) for r in top3)
        anchor_hit += int(a_hit)
        top3_hit += int(t_hit)
        if b["anchored"]:
            committed_hit += int(a_hit)            # committed AND correct
        else:
            withheld += 1
            withheld_wouldhit += int(a_hit)        # withheld but would have been correct
        brief_tok_sum += counter.count(render_brief(b))

        pack = packer.build(q, token_budget=4000)
        p_hit = any(gf in pack for gf in gold_files)
        pack_hit += int(p_hit)
        pack_tok_sum += counter.count(pack)
        rows.append({"q": q, "anchor_hit": a_hit, "top3_hit": t_hit,
                     "conf": b["anchor_confidence"], "anchored": b["anchored"],
                     "pack_hit": p_hit})
    retriever.close()

    # The integrity question, not a recall race: when the brief COMMITS to an anchor, is it
    # right; and when it WITHHOLDS, was it right to (few correct anchors suppressed)?
    committed = anchored
    summary = {
        "queries": n,
        "brief_top3_hit_rate": round(top3_hit / n, 3),        # == retrieval precision@3
        "pack_file_hit_rate": round(pack_hit / n, 3),         # any gold file in the pack
        "anchored_rate": round(committed / n, 3),
        "anchor_precision_when_committed": round(committed_hit / committed, 3) if committed else None,
        "withheld_would_have_hit": f"{withheld_wouldhit}/{withheld}" if withheld else "0/0",
        "avg_brief_tokens": round(brief_tok_sum / n, 1),
        "avg_pack_tokens": round(pack_tok_sum / n, 1),
        "confidence_dist": conf,
    }
    print("=== repo_brief A/B (vs context_pack) ===")
    for k in ("queries", "brief_top3_hit_rate", "pack_file_hit_rate", "anchored_rate",
              "anchor_precision_when_committed", "withheld_would_have_hit",
              "avg_brief_tokens", "avg_pack_tokens"):
        print(f"  {k:32s} {summary[k]}")
    print(f"  {'confidence_dist':32s} {conf}")
    print("  READ: brief_top3_hit == retrieval precision@3 (the brief's likely-targets ARE "
          "the search hits — it adds no retrieval magic and claims none). The brief-specific "
          "metric is anchor_precision_when_committed + how few correct anchors were withheld. "
          "The M3 task-token win (fewer wrong edits / re-searches) is a task-level hypothesis "
          "the static gold set can't measure.")
    return {"summary": summary, "rows": rows}


# ---------------------------------------------------------------------------
# C++ retrieval fixture (ROADMAP v2 Step 2 — fan-out measured against its REAL target).
#
# Step 2's confidence-gate + fan-out were built for ONE measured failure: a compound query
# ("cell size") whose true target is buried while a `.size()` family collapses to the top
# under the real bge embedding. The Python gold + FakeEmbedder cannot reproduce that (a
# bag-of-words fake embedder doesn't collapse cell→size), so the win stayed UNMEASURED. This
# mode indexes a tiny C++ fixture with the REAL model and asserts categorical CORRECTNESS
# (not a drifting metric): the buried target is recovered to the top, and a genuine .size()
# control query does NOT over-fire. It exits non-zero on regression, but it is a RUNNABLE
# check, not a standing lock — it needs the real model, so it can't live in pytest or --gate;
# run it ALONGSIDE --gate at the end of each step. It asserts "collapse-THEN-recovers" (base
# buries the target, fan-out promotes it), so a green run also confirms the failure still
# reproduces — a future model that stopped collapsing would report a (benign) regression.
# ---------------------------------------------------------------------------
def _rank_of(results, target: str):
    """Rank of the first result matching `target` — by ref suffix (`Class.method`) OR by
    bare symbol name (its last dotted part), since dedup can keep a containing struct (a
    line-range ref) as the representative of an inner method."""
    bare = target.split(".")[-1]
    for i, r in enumerate(results):
        if (r.ref or "").endswith(target) or r.symbol_name == bare:
            return i
    return None


def cpp_eval(repo: str = ".") -> bool:
    from pandemonium.retrieval.hybrid_search import Retriever

    fix = Path(__file__).parent / "fixtures" / "cpp_grid"
    settings = Settings.load(fix)
    stats = service.index(settings, incremental=False)  # real model, fresh
    r = Retriever(settings)
    ok = True
    print("=== C++ retrieval fixture (Step 2 — fan-out vs its real target) ===")
    print(f"indexed: files={stats.indexed} symbols={stats.symbols}")
    print(f"  {'query':20s} {'base':>5} {'final':>6} {'fanout':>7}  verdict")
    for case in CPP_FIXTURE_GOLD:
        q, target, expect_fanout = case["q"], case["target"], case["expect_fanout"]
        base_rank = _rank_of(r._base_search(q, 10, None, True), target)
        final, a = r.search_assessed(q, top_k=10)
        final_rank = _rank_of(final, target)
        fanned = bool(a.get("fanned_out"))
        if expect_fanout:
            # The detector must fire AND fan-out must recover the buried target to the top.
            passed = (fanned and final_rank is not None and final_rank <= 2
                      and (base_rank is None or final_rank < base_rank))
        else:
            # Control: no spurious fan-out, and the target is found directly near the top.
            passed = (not fanned and final_rank is not None and final_rank <= 2)
        ok = ok and passed
        br = "miss" if base_rank is None else base_rank
        fr = "miss" if final_rank is None else final_rank
        print(f"  {q:20s} {str(br):>5} {str(fr):>6} {str(fanned):>7}  "
              f"{'PASS' if passed else 'FAIL'}")
    r.close()
    verdict = "PASS" if ok else "FAIL — Step-2 C++ fan-out recovery regressed"
    print(f"\n  CPP GATE: {verdict}")
    return ok


# ---------------------------------------------------------------------------
# C++ header->cpp doc merge (ROADMAP v2 Step 8 — measured against its real target).
#
# A method DECLARED with its Doxygen doc in a header but DEFINED out-of-line in a .cpp has,
# without the merge, a descriptor that is just its signature — so a query on the doc's words
# can't reach it and name-only matchers bury it. This indexes the cpp_header_merge fixture
# with the REAL model twice — merge OFF then ON — and asserts the merge categorically LIFTS
# the buried definition (top ON, and strictly above its OFF rank). Like --cpp it needs the
# real model, so it can't live in pytest/--gate; run it ALONGSIDE --gate at the end of a
# step. The deterministic mechanism (the doc actually lands on the def's summary + decl_ref)
# is locked offline in tests/test_cpp_header_merge.py — THAT is the real guarantee; THIS is a
# corroborating real-model measurement with a deliberately THIN margin (the OFF rank is ~2, not
# deeply buried), so a future model that ranks the bare signature differently could report a
# BENIGN failure here — treat that as model drift to investigate, not a code regression.
# ---------------------------------------------------------------------------
def cppmerge_eval(repo: str = ".") -> bool:
    from pandemonium.retrieval.hybrid_search import Retriever

    fix = Path(__file__).parent / "fixtures" / "cpp_header_merge"
    ok = True
    print("=== C++ header->cpp doc merge (Step 8 — doc lifts the buried out-of-line def) ===")
    print(f"  {'query':44s} {'off':>5} {'on':>5}  verdict")
    for case in CPP_MERGE_GOLD:
        q, target = case["q"], case["target"]
        rank = {}
        for label, merge in (("off", False), ("on", True)):
            settings = Settings.load(fix)
            settings.data["indexing"]["cpp_header_merge"] = merge
            service.index(settings, incremental=False)  # real model, fresh
            r = Retriever(settings)
            rank[label] = _rank_of(r.search(q, top_k=10), target)
            r.close()
        off, on = rank["off"], rank["on"]
        # The merge must MOVE the target up: near the top ON, strictly better than OFF.
        passed = on is not None and on <= 2 and (off is None or on < off)
        ok = ok and passed
        print(f"  {q:44s} {str('miss' if off is None else off):>5} "
              f"{str('miss' if on is None else on):>5}  {'PASS' if passed else 'FAIL'}")
    verdict = "PASS" if ok else "FAIL — Step-8 header doc merge did not lift the buried def"
    print(f"\n  CPPMERGE GATE: {verdict}")
    return ok


# ---------------------------------------------------------------------------
# Context modes (ROADMAP v2 Step 6) — HONEST default-sensitivity report, NOT validation.
#
# A mode is only proven by DIFFERENTIAL (crossover) performance across query types. The gold
# is 100% discovery-shaped ("where/how is X"), so this can ONLY show how each weight preset
# moves DISCOVERY retrieval — it cannot validate impact/bugfix (whose target query types
# aren't represented; that needs the #11 multi-type/multi-repo matrix). Read it as: (a) does
# the `discovery` preset beat the default on discovery queries (a single-type signal that may
# argue the GLOBAL DEFAULT is mistuned), and (b) impact/bugfix here are OFF-TARGET — a
# non-improvement is expected, not a failure. Presets ship as labelled hypotheses regardless.
# ---------------------------------------------------------------------------
def modes_eval(repo: str = ".") -> dict:
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    n = len(GOLD)
    out: dict = {}
    for mode in (None, "impact", "discovery", "bugfix"):
        fh = {1: 0, 3: 0, 5: 0}
        mrr = 0.0
        ranks = []
        for item in GOLD:
            res = retriever.search(item["q"], top_k=10, mode=mode)
            f = _first_rank(res, item["files"], "path")
            ranks.append(f)
            for k in (1, 3, 5):
                if f is not None and f < k:
                    fh[k] += 1
            mrr += (1.0 / (f + 1)) if f is not None else 0.0
        out[mode or "default"] = {"p1": fh[1] / n, "p3": fh[3] / n, "p5": fh[5] / n,
                                  "mrr": mrr / n, "ranks": ranks}
    retriever.close()

    print("=== Context modes vs default (DISCOVERY-shaped gold; NOT mode validation) ===")
    print(f"  {'mode':10s} {'P@1':>6} {'P@3':>6} {'P@5':>6} {'MRR':>6}")
    for name, m in out.items():
        print(f"  {name:10s} {m['p1']:6.3f} {m['p3']:6.3f} {m['p5']:6.3f} {m['mrr']:6.3f}")
    base = out["default"]["ranks"]
    print("\n  paired vs default (n=15):")
    for name in ("impact", "discovery", "bugfix"):
        imp = wor = 0
        for b, o in zip(base, out[name]["ranks"]):
            bb = 99 if b is None else b
            oo = 99 if o is None else o
            imp += oo < bb
            wor += oo > bb
        tag = "" if name != "discovery" else "  <- the only on-target row"
        print(f"    {name:10s} improved {imp}  worsened {wor}{tag}")
    print("\n  READ: only `discovery` is on-target here (gold is discovery-shaped). impact/"
          "bugfix are OFF-TARGET — judge them on the #11 matrix, not this. A discovery win "
          "argues the GLOBAL DEFAULT may be mistuned, NOT that a 'mode' is validated.")
    return out


# ---------------------------------------------------------------------------
# Channel-isolation baselines (#9 — "is hybrid earning its complexity?").
#
# The 0/4 retrieval-baseline gap: the harness compared the tool to its own prior snapshot,
# never to single-channel retrievers. This runs the gold set under hybrid vs symbol-only vs
# keyword-only(BM25) vs vector-only (Retriever.search(..., channels_only=...)) — a faithful
# single-channel ranking, not weight-zeroing. SCOPE OF THE CLAIM (honest): this measures
# RETRIEVAL RANKING only. It does NOT measure the token/cost-at-scale win — that is
# AGENT-level (evals/ab_runner.py: cost/tokens/tool-calls solving seeded bugs) and needs
# LARGE-REPO seeded-bug tasks, the explicit remaining half (see RESULTS.md "#9"). On a
# discovery-shaped gold a vector-heavy arm may match or beat hybrid (cf. the `discovery`
# preset in --modes) — that is a FINDING consistent with prior evidence, not a harness bug.
# ---------------------------------------------------------------------------
_ARMS = [("hybrid", None), ("symbol-only", {"symbol"}),
         ("keyword(BM25)", {"keyword"}), ("vector-only", {"vector"})]


def _rank_metrics(retriever, gold, search_fn) -> dict:
    n = len(gold)
    fh = {1: 0, 3: 0, 5: 0}
    sym5 = 0
    mrr = 0.0
    ranks = []
    for item in gold:
        res = search_fn(item["q"])
        f = _first_rank(res, item["files"], "path")
        s = _first_rank(res, item.get("symbols", []), "symbol_name")
        ranks.append(f)
        for k in (1, 3, 5):
            if f is not None and f < k:
                fh[k] += 1
        if s is not None and s < 5:
            sym5 += 1
        mrr += (1.0 / (f + 1)) if f is not None else 0.0
    return {"p1": fh[1] / n, "p3": fh[3] / n, "p5": fh[5] / n, "symP5": sym5 / n,
            "mrr": mrr / n, "misses": sum(1 for r in ranks if r is None), "ranks": ranks}


def baselines(repo: str = ".", gold=None) -> dict:
    gold = gold or GOLD
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    metrics = {
        name: _rank_metrics(retriever, gold,
                            lambda q, only=only: retriever.search(q, top_k=10,
                                                                  channels_only=only))
        for name, only in _ARMS
    }
    retriever.close()

    print("=== Channel-isolation baselines (RETRIEVAL RANKING — does hybrid earn it?) ===")
    print(f"  n={len(gold)} queries")
    print(f"  {'arm':14s} {'P@1':>6} {'P@3':>6} {'P@5':>6} {'symP5':>6} {'MRR':>6} {'miss':>5}")
    for name, _ in _ARMS:
        m = metrics[name]
        print(f"  {name:14s} {m['p1']:6.3f} {m['p3']:6.3f} {m['p5']:6.3f} "
              f"{m['symP5']:6.3f} {m['mrr']:6.3f} {m['misses']:5d}")
    base = metrics["hybrid"]["ranks"]
    print(f"\n  paired vs hybrid (n={len(gold)}):")
    for name, _ in _ARMS[1:]:
        imp = wor = 0
        for b, o in zip(base, metrics[name]["ranks"]):
            bb = 99 if b is None else b
            oo = 99 if o is None else o
            imp += oo < bb
            wor += oo > bb
        print(f"    {name:14s} better {imp}  worse {wor}")
    print("\n  READ: RETRIEVAL-RANKING ONLY — 'is hybrid worth its complexity over a single "
          "channel here'. It does NOT measure the token/cost-at-scale win (agent-level, "
          "ab_runner.py; needs large-repo seeded-bug tasks — the named remaining half). A "
          "vector-heavy arm matching hybrid on this discovery gold is a finding, not a bug.")
    return metrics


def perquery(repo: str = ".", gold=None) -> None:
    """Per-query rank + top-1 ref — for diffing two index states (e.g. heuristic vs
    enriched) to see exactly which query moved."""
    gold = gold or GOLD
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    for i, item in enumerate(gold):
        res = retriever.search(item["q"], top_k=10)
        rank = _first_rank(res, item["files"], "path")
        top1 = res[0].ref if res else "-"
        print(f"[{i:2d}] rank={'-' if rank is None else rank}  {item['q'][:46]:46s} top1={top1}")
    retriever.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--save", default=None, help="label -> writes evals/<label>.json")
    ap.add_argument("--sweep", action="store_true", help="run the scope bake-off")
    ap.add_argument("--brief", action="store_true",
                    help="repo_brief A/B vs context_pack (Step 5; separate from --gate)")
    ap.add_argument("--cpp", action="store_true",
                    help="C++ fixture: lock Step-2 fan-out recovery on its real target (real model)")
    ap.add_argument("--cppmerge", action="store_true",
                    help="C++ fixture: lock Step-8 header->cpp doc merge lifting the def (real model)")
    ap.add_argument("--modes", action="store_true",
                    help="Step-6 context modes: default-sensitivity report (NOT validation)")
    ap.add_argument("--baselines", action="store_true",
                    help="#9 channel-isolation baselines: hybrid vs symbol/keyword/vector-only "
                         "(RETRIEVAL ranking only — not the token-at-scale win)")
    ap.add_argument("--tasks", default=None,
                    help="path to an external task set (JSON/YAML) for run/baselines against "
                         "ANY indexed repo; pair with --repo (the large-repo / cross-file set)")
    ap.add_argument("--perquery", action="store_true", help="per-query rank + top1 ref")
    ap.add_argument("--gate", default=None,
                    help="label -> compare to evals/<label>.json; exit 1 if a hard "
                         "metric regressed (M1 lock + M3 acceptance gate)")
    args = ap.parse_args()

    gold = impact_gold = None
    if args.tasks:
        gold, impact_gold = load_tasks(args.tasks)

    if args.perquery:
        perquery(args.repo, gold=gold)
    elif args.baselines:
        baselines(args.repo, gold=gold)
    elif args.brief:
        brief_eval(args.repo)
    elif args.cpp:
        if not cpp_eval(args.repo):
            sys.exit(1)
    elif args.cppmerge:
        if not cppmerge_eval(args.repo):
            sys.exit(1)
    elif args.modes:
        modes_eval(args.repo)
    elif args.sweep:
        sweep(args.repo)
    else:
        result = run(args.repo, gold=gold, impact_gold=impact_gold)
        _print(result)
        if args.save:
            out = Path(__file__).parent / f"{args.save}.json"
            out.write_text(json.dumps(result["summary"], indent=2), encoding="utf-8")
            print(f"\nwrote {out}")
        if args.gate:
            base_path = Path(__file__).parent / f"{args.gate}.json"
            if not base_path.exists():
                print(f"\ngate baseline not found: {base_path}")
                sys.exit(2)
            baseline = json.loads(base_path.read_text(encoding="utf-8"))
            print()
            if not gate(result["summary"], baseline):
                sys.exit(1)
