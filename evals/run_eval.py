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
import os
import sys
from pathlib import Path

# Local-first: point HF_HOME at the repo's bundled model cache (the A/B runners do the same in
# their subprocess env) so `python evals/run_eval.py` and --matrix load bge-small fully offline.
# Must run BEFORE huggingface_hub is imported (it resolves the cache path at import). The #11
# matrix fixtures ship no model of their own — they share this cache.
_HF_CACHE = Path(__file__).resolve().parent.parent / ".pandemonium" / "hf"
if _HF_CACHE.exists():
    os.environ.setdefault("HF_HOME", str(_HF_CACHE))

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
    external task file's `impact` list). FP = claimed-but-not-real; FN = real-but-missed.

    Also reports the conventional CALLER-edge precision/recall (Improvements5 "relation
    evals" — micro-averaged over all gold callers) and the per-symbol EXACT-match rate: the
    fraction of gold symbols for which the tool returned ALL and ONLY the real callers. That
    last one is the "did the tool find the correct edit site" signal — stricter than the
    micro rates, since one wrong/missed caller fails the whole symbol."""
    impact_gold = IMPACT_GOLD if impact_gold is None else impact_gold
    fp = fn = pred = gold = tp = exact = 0
    for item in impact_gold:
        imp = service.impact_for(settings, item["ref"]) or {}
        got = set(imp.get("direct", []))
        want = set(item["true_direct"])
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
        pred += len(got)
        gold += len(want)
        exact += int(got == want)
    n = max(len(impact_gold), 1)
    return {"impact_fp_rate": round(fp / max(pred, 1), 3),
            "impact_fn_rate": round(fn / max(gold, 1), 3),
            "edge_precision": round(tp / max(pred, 1), 3),
            "edge_recall": round(tp / max(gold, 1), 3),
            "impact_exact_case_rate": round(exact / n, 3),
            "impact_cases": len(impact_gold)}


def _impact_per_case(settings, impact_gold=None) -> list:
    """Per-symbol caller-edge detail for --perquery: what the tool got vs the gold truth,
    plus the missing (FN) and extra (FP) refs. The diffing tool for adjudicating whether a
    non-zero FP/FN is a real tool limitation or a stale gold entry."""
    impact_gold = IMPACT_GOLD if impact_gold is None else impact_gold
    rows = []
    for item in impact_gold:
        imp = service.impact_for(settings, item["ref"]) or {}
        got = set(imp.get("direct", []))
        want = set(item["true_direct"])
        rows.append({"ref": item["ref"], "exact": got == want,
                     "missing": sorted(want - got), "extra": sorted(got - want)})
    return rows


def _tests_pr(settings, tests_gold) -> dict:
    """Deterministic test-selection scoring (#11 'test selection' task type): find_tests(target)
    vs hand-authored expected test paths. An expected entry is hit if any of its POSIX substrings
    appears in a returned test path — the same path-substring convention used for `files`."""
    tp = fp = fn = pred = gold = exact = 0
    for item in tests_gold:
        got = list(service.tests(settings, item["target"], limit=10))
        subs = item["expected_tests"]
        want_hit = [w for w in subs if any(w in g for g in got)]
        matched = [g for g in got if any(w in g for w in subs)]
        tp += len(want_hit)
        fn += len(subs) - len(want_hit)
        fp += len(got) - len(matched)
        pred += len(got)
        gold += len(subs)
        exact += int(len(matched) == len(got) and len(want_hit) == len(subs))
    n = max(len(tests_gold), 1)
    return {"tests_precision": round(tp / max(pred, 1), 3),
            "tests_recall": round(tp / max(gold, 1), 3),
            "tests_exact_case_rate": round(exact / n, 3),
            "tests_cases": len(tests_gold)}


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


def _recall_at_k(results, expected, attr, k) -> float:
    """Fraction of DISTINCT `expected` needles matched within the top-k results. Path needles
    substring-match (like _first_rank); symbol needles exact-match. This is the coverage metric
    _first_rank can't give (it stops at the first hit) — load-bearing for the feature / API-
    refactor task types, whose gold lists MULTIPLE expected sites."""
    if not expected:
        return 0.0
    topk = results[:k]
    hit = 0
    for needle in expected:
        for r in topk:
            value = getattr(r, attr) or ""
            if (needle in value) if attr == "path" else (needle == value):
                hit += 1
                break
    return hit / len(expected)


def _card_line(r) -> str:
    sym = r.symbol_name or r.chunk_type or ""
    return f"- {r.path}::{sym} (L{r.start_line}-{r.end_line}) — {r.summary or ''}"


def load_tasks(path: str):
    """Load an EXTERNAL retrieval task set so the harness can run against ANY indexed repo —
    the large-repo / cross-file task set (Improvements3 #9's `tasks.yaml`, made real and
    repo-agnostic) and the #11 matrix fixtures. Returns (queries, impact, tests).

    JSON by default; YAML when the path ends .yaml/.yml AND PyYAML is installed (no hard dep).
    Schema: a top-level LIST of query items, OR a dict with `queries` (required) + optional
    `impact` + optional `tests`. Each query: {q, files:[POSIX path substrings], symbols?:[bare
    names], type?}. Each impact: {ref: "path::Qualified.Name", true_direct:[refs], type?}. Each
    tests: {target:[bare name], expected_tests:[path substrings], type?}. Extra keys (e.g. `_doc`,
    `type`) are ignored by the loader, so a template can document itself inline."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        import yaml  # optional — only imported for YAML task files
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if isinstance(data, list):
        queries, impact, tests = data, [], []
    else:
        queries = data.get("queries", [])
        impact = data.get("impact", [])
        tests = data.get("tests", [])
    if not queries:
        raise SystemExit(f"no queries in task file: {path}")
    for i, item in enumerate(queries):
        if "q" not in item or "files" not in item:
            raise SystemExit(f"task[{i}] needs at least 'q' and 'files': {item!r}")
    return queries, impact, tests


def _score_queries(retriever, packer, counter, settings, gold, impact_gold=None,
                   score_impact=True, mode=None) -> dict:
    """Score one query list against an ALREADY-OPEN retriever; return {summary, rows}. Factored
    out of run() so the #11 matrix can score per-(task-type) slices without re-opening the index.
    `score_impact=False` skips the caller-edge metrics (per-type slices, where impact is its own
    bucket). When score_impact=True, impact_gold=None falls back to gold.IMPACT_GOLD — exactly
    the dogfood run's long-standing behaviour (so run() stays byte-identical on existing metrics).
    Does NOT close the retriever; the caller owns its lifecycle."""
    n = len(gold)
    file_hits = {1: 0, 3: 0, 5: 0}
    sym_hit5 = 0
    mrr_sum = 0.0
    recall_sum = 0.0
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
        results = retriever.search(q, top_k=TOP_K, mode=mode)
        f_rank = _first_rank(results, item["files"], "path")
        s_rank = _first_rank(results, item.get("symbols", []), "symbol_name")

        for k in (1, 3, 5):
            if f_rank is not None and f_rank < k:
                file_hits[k] += 1
        if s_rank is not None and s_rank < 5:
            sym_hit5 += 1
        mrr_sum += (1.0 / (f_rank + 1)) if f_rank is not None else 0.0
        recall_sum += _recall_at_k(results, item["files"], "path", 5)

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

    summary = {
        "queries": n,
        "precision_at_1": round(file_hits[1] / n, 3),
        "precision_at_3": round(file_hits[3] / n, 3),
        "precision_at_5": round(file_hits[5] / n, 3),
        "symbol_precision_at_5": round(sym_hit5 / n, 3),
        "mrr": round(mrr_sum / n, 3),
        "recall_at_5": round(recall_sum / n, 3),
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
    if score_impact:
        summary.update(_impact_fp_fn(settings, impact_gold))
    return {"summary": summary, "rows": rows}


def run(repo: str = ".", gold=None, impact_gold=None, settings=None) -> dict:
    """Dogfood retrieval eval over one query list. Opens the index once, scores, closes.
    A thin wrapper over _score_queries — output is unchanged from before the matrix refactor
    (the summary now also carries `recall_at_5`; every pre-existing metric value is untouched)."""
    gold = gold or GOLD
    settings = settings or Settings.load(repo)
    retriever = Retriever(settings)
    packer = ContextPacker(settings, retriever=retriever)
    counter = TokenCounter(settings.section("context_pack").get("tokenizer", "cl100k_base"))
    result = _score_queries(retriever, packer, counter, settings, gold, impact_gold)
    retriever.close()
    return result


def _print(result: dict) -> None:
    s = result["summary"]
    print("=== Retrieval eval ===")
    for key in ("queries", "precision_at_1", "precision_at_3", "precision_at_5",
                "symbol_precision_at_5", "mrr", "recall_at_5", "avg_pack_tokens", "avg_cards_tokens",
                "token_savings_cards_vs_pack", "same_path_repeat_rate",
                "duplicate_card_rate", "fetches_to_resolution", "resolution_rate",
                "ambiguous_ref_rate", "wrong_symbol_same_name_rate", "same_name_cases",
                "impact_fp_rate", "impact_fn_rate", "edge_precision", "edge_recall",
                "impact_exact_case_rate", "impact_cases"):
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
    ("edge_precision", "up", True),          # caller-edge precision (1 - fp_rate)
    ("edge_recall", "up", True),             # caller-edge recall (1 - fn_rate)
    ("impact_exact_case_rate", "up", True),  # "found the correct edit site" per symbol
    ("tests_precision", "up", True),         # #11 test-selection precision (skipped if absent)
    ("tests_recall", "up", True),            # #11 test-selection recall
    ("wrong_symbol_same_name_rate", "down", True),
    ("duplicate_card_rate", "down", True),
    ("ambiguous_ref_rate", "down", True),
    ("precision_at_1", "up", True),
    ("precision_at_3", "up", True),
    ("precision_at_5", "up", True),
    ("mrr", "up", True),
    ("recall_at_5", "up", True),             # #11 feature / API-refactor multi-site coverage
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
# #11 eval matrix — {language × task-type} deterministic retrieval over vendored fixtures.
#
# ROADMAP.md:72-78. The matrix that "doesn't exist yet": run a TYPED task set per fixture repo,
# slice metrics by the 6 task types, and report a {language × type} table that the modes presets
# and the M3 gate can finally be proven against. Deterministic (fixed source + pinned model);
# each fixture is re-indexed fresh (.pandemonium is gitignored — nothing committed), the cpp_eval
# pattern. NOT an agent-level A/B — pure retrieval metrics, so it is gate-able.
# ---------------------------------------------------------------------------
# Headline proxy metric shown per task-type column (the cell value); the full sub-summary is in
# matrix_result.json. discovery/bugtrace = ranking; feature/refactor = multi-site coverage;
# impact = caller-edge recall; testsel = test-selection recall.
_MATRIX_COLS = ("discovery", "bugtrace", "feature", "refactor", "impact", "testsel")
_MATRIX_PROXY = {"discovery": "mrr", "bugtrace": "mrr", "feature": "recall_at_5",
                 "refactor": "recall_at_5", "impact": "edge_recall", "testsel": "tests_recall"}


def run_typed(repo: str, queries, impact_gold, tests_gold, settings=None, mode=None) -> dict:
    """Score one fixture's typed task set; return {overall, by_type:{type: sub_summary}}.
    The 4 search-shaped types (discovery/bugtrace/feature/refactor) are bucketed from `queries`
    and scored by ranking; known-symbol impact and test selection are mono-type buckets scored
    once by _impact_fp_fn / _tests_pr. The index is opened ONCE and reused across slices.
    `mode` selects a per-call ranking-weight preset for the search-shaped types (impact graph +
    find_tests are mode-independent)."""
    from collections import defaultdict
    settings = settings or Settings.load(repo)
    retriever = Retriever(settings)
    packer = ContextPacker(settings, retriever=retriever)
    counter = TokenCounter(settings.section("context_pack").get("tokenizer", "cl100k_base"))

    groups = defaultdict(list)
    for item in queries:
        groups[item.get("type", "untyped")].append(item)

    by_type = {}
    for t, qs in groups.items():
        by_type[t] = _score_queries(retriever, packer, counter, settings, qs,
                                    score_impact=False, mode=mode)["summary"]
    # `impact_gold or []` (never None) so the overall does NOT fall back to dogfood IMPACT_GOLD.
    overall = _score_queries(retriever, packer, counter, settings, queries,
                             impact_gold=impact_gold or [], mode=mode)["summary"]
    retriever.close()

    if impact_gold:
        by_type["impact"] = _impact_fp_fn(settings, impact_gold)
    if tests_gold:
        by_type["testsel"] = _tests_pr(settings, tests_gold)
    return {"overall": overall, "by_type": by_type}


def _render_matrix(out: dict) -> None:
    print("\n=== #11 eval matrix — {language × task-type} ===")
    print(f"  {'language':12s}" + "".join(f"{c[:9]:>10s}" for c in _MATRIX_COLS))
    for lang, block in out.items():
        cells = []
        for c in _MATRIX_COLS:
            summ, key = block["by_type"].get(c), _MATRIX_PROXY[c]
            cells.append(f"{summ[key]:>10.3f}" if summ and key in summ else f"{'-':>10s}")
        print(f"  {lang:12s}" + "".join(cells))
    print("  legend: discovery/bugtrace=MRR  feature/refactor=recall@5  impact=edge_recall  "
          "testsel=tests_recall")


def matrix_eval(manifest_path: str) -> dict:
    """Run the #11 matrix from a manifest of vendored fixtures. Each entry indexes its fixture
    fresh with the real model, then scores per task type. Writes matrix_result.json beside the
    manifest. Manifest: {"matrix": [{language, repo_path, tasks_file}]} with repo-root-relative
    paths (or a bare list of such entries)."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entries = manifest["matrix"] if isinstance(manifest, dict) else manifest
    root = Path(__file__).parent.parent  # repo root; manifest paths are repo-relative
    out = {}
    print("=== #11 eval matrix (deterministic retrieval; fresh real-model index per fixture) ===")
    for entry in entries:
        lang = entry["language"]
        repo_path = str((root / entry["repo_path"]).resolve())
        tasks_file = str((root / entry["tasks_file"]).resolve())
        queries, impact_gold, tests_gold = load_tasks(tasks_file)
        settings = Settings.load(repo_path)
        stats = service.index(settings, incremental=False)  # real model, fresh
        print(f"  [{lang}] indexed files={stats.indexed} symbols={stats.symbols}  {entry['repo_path']}")
        out[lang] = run_typed(repo_path, queries, impact_gold, tests_gold, settings=settings)
    _render_matrix(out)
    art = Path(manifest_path).parent / "matrix_result.json"
    art.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nwrote {art}")
    return out


def matrix_gate(result: dict, baseline: dict) -> bool:
    """Gate every per-language `overall` + per-task-type leaf against a saved matrix baseline,
    reusing gate() (which skips any metric absent from either side). FAIL if any HARD leaf
    regressed. A leaf missing from the baseline can't regress, so first-time leaves pass."""
    ok = True
    for lang, block in result.items():
        base = baseline.get(lang, {})
        print(f"\n--- matrix gate [{lang}] overall ---")
        ok = gate(block["overall"], base.get("overall", {})) and ok
        for t, summ in block["by_type"].items():
            print(f"--- matrix gate [{lang}] {t} ---")
            ok = gate(summ, base.get("by_type", {}).get(t, {})) and ok
    print(f"\n  MATRIX GATE: {'PASS' if ok else 'FAIL — a hard metric regressed in some leaf'}")
    return ok


# ---------------------------------------------------------------------------
# #11 matrix ARMS — run the matrix under config-override arms (the gated reranker signals; the
# per-call mode presets) over the SAME fresh index per fixture, and report each arm's delta vs
# base, per (language × task-type).
#
# DISCIPLINE: the vendored fixtures are small + self-authored (6 queries each). This is the
# does-it-HURT / no-regression gate and a DIRECTIONAL signal only — NOT a ship decision. A ship
# requires the external-repo crossover (crossover_eval / a large external --tasks set), never this
# gold. rerank/mode change SEARCH ranking only, so the impact (graph) and testsel (FTS) columns
# are mode/rerank-invariant by construction — expect '.' there.
# ---------------------------------------------------------------------------
_RERANK_ARMS = [
    ("base",           {},                                                               None),
    ("rerank:prose",   {"rerank": True, "rerank_prose": True,  "rerank_density": False}, None),
    ("rerank:density", {"rerank": True, "rerank_prose": False, "rerank_density": True},  None),
    ("rerank:both",    {"rerank": True, "rerank_prose": True,  "rerank_density": True},  None),
]
_MODE_ARMS = [
    ("base",           {}, None),
    ("mode:impact",    {}, "impact"),
    ("mode:discovery", {}, "discovery"),
    ("mode:bugfix",    {}, "bugfix"),
]


def _run_arm(repo_path, queries, impact_gold, tests_gold, override, mode):
    settings = Settings.load(repo_path)
    if override:
        settings.data["retrieval"].update(override)
    return run_typed(repo_path, queries, impact_gold, tests_gold, settings=settings, mode=mode)


def _render_arms(out: dict, labels) -> None:
    base_label = labels[0]
    langs = list(out[base_label].keys())
    for label in labels[1:]:
        print(f"\n  --- arm '{label}' vs '{base_label}' (delta of headline proxy per cell) ---")
        print(f"  {'language':12s}" + "".join(f"{c[:9]:>10s}" for c in _MATRIX_COLS))
        any_move = False
        for lang in langs:
            cells = []
            for c in _MATRIX_COLS:
                key = _MATRIX_PROXY[c]
                a, b = out[label][lang]["by_type"].get(c), out[base_label][lang]["by_type"].get(c)
                if a and b and key in a and key in b:
                    d = a[key] - b[key]
                    if abs(d) > _EPS:
                        any_move = True
                    cells.append(f"{d:>+10.3f}" if abs(d) > _EPS else f"{'.':>10s}")
                else:
                    cells.append(f"{'-':>10s}")
            print(f"  {lang:12s}" + "".join(cells))
        if not any_move:
            print("  (no cell moved vs base)")
    print("\n  legend: '.' = no change, +/- = delta vs base, '-' = absent. "
          "discovery/bugtrace=MRR  feature/refactor=recall@5  impact=edge_recall  testsel=tests_recall")


def matrix_arms_eval(manifest_path: str, arms, title: str) -> dict:
    """Run the #11 matrix under `arms` over ONE fresh index per fixture (rerank/mode are
    retrieval-time, so the index is built once and reused). Prints per-cell deltas vs the base
    arm. Returns {label: {language: run_typed_result}}. Directional / no-regression only."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    entries = manifest["matrix"] if isinstance(manifest, dict) else manifest
    root = Path(__file__).parent.parent
    out = {label: {} for label, _, _ in arms}
    print(f"\n=== #11 matrix ARMS — {title} "
          "(directional / does-it-hurt only; NOT a ship gate — ship needs external crossover) ===")
    for entry in entries:
        lang = entry["language"]
        repo_path = str((root / entry["repo_path"]).resolve())
        tasks_file = str((root / entry["tasks_file"]).resolve())
        queries, impact_gold, tests_gold = load_tasks(tasks_file)
        settings = Settings.load(repo_path)
        stats = service.index(settings, incremental=False)  # index once; arms reuse it
        print(f"  [{lang}] indexed files={stats.indexed} symbols={stats.symbols}")
        for label, override, mode in arms:
            out[label][lang] = _run_arm(repo_path, queries, impact_gold, tests_gold, override, mode)
    _render_arms(out, [a[0] for a in arms])
    return out


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


# ---------------------------------------------------------------------------
# cAST subchunking A/B (Improvements4 #3) — SEPARATE from --gate (a measurement, not a lock).
#
# Re-indexes the repo with the REAL model twice: subchunking OFF (whole-symbol cards only) vs
# ON (full symbol + block-complete `ast_block` children), and compares retrieval on the gold
# set. The design law is "search small, read complete": children add precise sub-block recall
# while the full symbol stays the delivered unit, so the bar is "cAST holds (>=) whole-symbol
# precision/MRR" — not a big precision jump on this symbol-shaped gold. The real win (a query
# whose TRUE region is a sub-block of a large function) needs a sub-block gold set + large-repo
# tasks; this arm reports the no-regression guardrail + how many ast_block children surface.
# ---------------------------------------------------------------------------
def cast_eval(repo: str = ".") -> dict:
    arms = {"whole-symbol": 10 ** 9, "cAST(+blocks)": 60}  # threshold: disabled vs default
    out: dict = {}
    for label, thr in arms.items():
        settings = Settings.load(repo)
        settings.data["indexing"]["subchunk_min_lines"] = thr
        service.index(settings, incremental=False)  # real model, fresh
        retriever = Retriever(settings)
        m = _rank_metrics(retriever, GOLD, lambda q: retriever.search(q, top_k=10))
        ab = sum(1 for item in GOLD for r in retriever.search(item["q"], top_k=10)
                 if r.chunk_type == "ast_block")
        retriever.close()
        out[label] = {**m, "ast_block_hits": ab}

    print("=== cAST subchunking A/B (whole-symbol vs full symbol + ast_block children) ===")
    print(f"  n={len(GOLD)} queries")
    print(f"  {'arm':16s} {'P@1':>6} {'P@3':>6} {'P@5':>6} {'symP5':>6} {'MRR':>6} {'astblk':>7}")
    for label in arms:
        m = out[label]
        print(f"  {label:16s} {m['p1']:6.3f} {m['p3']:6.3f} {m['p5']:6.3f} "
              f"{m['symP5']:6.3f} {m['mrr']:6.3f} {m['ast_block_hits']:7d}")
    base, cast = out["whole-symbol"], out["cAST(+blocks)"]
    held = all(cast[k] >= base[k] - _EPS for k in ("p1", "p3", "p5", "mrr"))
    print(f"\n  cAST holds whole-symbol precision/MRR: {held}")
    print("  READ: bar is no-regression here (symbol-shaped gold); the sub-block recall win "
          "needs a sub-block gold set + large-repo tasks (the named remaining half).")
    return out


def crossover_eval(repo: str = ".", tasks_path: str = None) -> dict:
    """Patch 6 — the OFF-DOGFOOD ship gate for the structural reranker. Indexes an external
    `--repo` fresh with the REAL model, loads a 2-class task file (`buried` = dispatcher/doc-
    buried queries the rerank should HELP; `control` = exact-symbol/entrypoint/doc-intent
    queries it must NOT hurt), and runs each class with the reranker OFF vs ON. PASS iff buried
    IMPROVES while control does NOT regress — the crossover a single-repo dogfood A/B can't show.
    THIS is what a rerank ships on, never `--rerank` on the 15-query gold."""
    data = json.loads(Path(tasks_path).read_text(encoding="utf-8"))
    classes = [("buried", data.get("buried", [])), ("control", data.get("control", []))]
    settings = Settings.load(repo)
    stats = service.index(settings, incremental=False)  # real model, fresh
    print("=== Patch 6 crossover (external repo — the rerank SHIP gate) ===")
    print(f"  repo={repo}  indexed files={stats.indexed} symbols={stats.symbols}")

    def measure(gold, on):
        s = Settings.load(repo)
        if on:
            s.data["retrieval"].update({"rerank": True, "rerank_prose": True, "rerank_density": True})
        retr = Retriever(s)
        m = _rank_metrics(retr, gold, lambda q: retr.search(q, top_k=10))
        retr.close()
        return m

    res = {}
    for name, gold in classes:
        if not gold:
            continue
        off, on = measure(gold, False), measure(gold, True)
        res[name] = (off, on)
        print(f"\n  [{name}] n={len(gold)}")
        print(f"    {'arm':6s}{'P@1':>7}{'P@3':>7}{'P@5':>7}{'MRR':>7}")
        for lbl, m in (("off", off), ("on", on)):
            print(f"    {lbl:6s}{m['p1']:7.3f}{m['p3']:7.3f}{m['p5']:7.3f}{m['mrr']:7.3f}")
    if "buried" in res and "control" in res:
        (bo, bn), (co, cn) = res["buried"], res["control"]
        improved = (bn["p5"] > bo["p5"] + 1e-9) or (bn["mrr"] > bo["mrr"] + 1e-9)
        regressed = (cn["p5"] < co["p5"] - 1e-9) or (cn["mrr"] < co["mrr"] - 1e-9)
        ok = improved and not regressed
        print(f"\n  CROSSOVER: {'PASS' if ok else 'FAIL'}  "
              f"(buried improved={improved}; control regressed={regressed})")
    print("  READ: the OFF-DOGFOOD ship gate. A rerank ships only on PASS here — never on the "
          "15-query dogfood A/B (--rerank).")
    return res


def rerank_eval(repo: str = ".", gold=None) -> dict:
    """Patch 4/5 ablation A/B (dogfood — NOT a ship gate). Runs the gold under the structural
    reranker OFF vs prose-only vs density-only vs both, on ONE fresh index, toggling only
    `retrieval.rerank*`. Prints aggregate + per-query rank moves so each signal's effect is
    visible. The default/gate stays OFF/unmoved; the real ship decision is the EXTERNAL --tasks
    crossover (never this dogfood set)."""
    arms = {
        "off":     {"rerank": False},
        "prose":   {"rerank": True, "rerank_prose": True,  "rerank_density": False},
        "density": {"rerank": True, "rerank_prose": False, "rerank_density": True},
        "both":    {"rerank": True, "rerank_prose": True,  "rerank_density": True},
    }
    out = {}
    for name, cfg in arms.items():
        s = Settings.load(repo)
        s.data["retrieval"].update(cfg)
        out[name] = run(repo, gold=gold, settings=s)
    keys = [("precision_at_1", "P@1"), ("precision_at_3", "P@3"), ("precision_at_5", "P@5"),
            ("mrr", "MRR"), ("resolution_rate", "resol"), ("wrong_symbol_same_name_rate", "wrongsym")]
    print("=== Patch 4/5 rerank A/B (dogfood; NOT a ship gate — external --tasks decides) ===")
    print("  " + f"{'arm':9s}" + "".join(f"{lbl:>9}" for _, lbl in keys))
    for name in arms:
        s = out[name]["summary"]
        print("  " + f"{name:9s}" + "".join(f"{s[k]:9.3f}" for k, _ in keys))
    base = {row["q"]: row["file_rank"] for row in out["off"]["rows"]}
    print("\n  per-query file_rank (off -> both); only moved queries:")
    for row in out["both"]["rows"]:
        o, n = base[row["q"]], row["file_rank"]
        if o != n:
            os_, ns_ = ("-" if o is None else str(o)), ("-" if n is None else str(n))
            tag = "better" if (n is not None and (o is None or n < o)) else "worse "
            print(f"    {tag} {os_:>3} -> {ns_:<3} {row['q'][:52]}")
    print("\n  READ: dogfood A/B only. SHIP requires the EXTERNAL crossover (win on dispatcher/"
          "doc-buried queries WHILE not losing on exact-symbol/control). Do NOT re-baseline on this.")
    return out


def signals_report(repo: str = ".", gold=None) -> None:
    """OBSERVE-ONLY (Patch 2): print the three structural rerank SIGNALS per top card so we can
    SEE where a delegator / code-vs-prose / constant-density rerank WOULD fire — BEFORE any of
    it touches scoring. Changes NO ranking: `run()` and `--gate` are untouched (verify by a
    flat `--gate` after this lands). Uses a wider top-50 pool so a buried implementation is
    visible (Patch 3 must rerank over a wide pool, then emit top-10)."""
    from pandemonium.graph import GraphIndex
    from pandemonium.retrieval import rerank_signals as sig

    gold = gold or GOLD
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    store, repo_id = retriever.sqlite, retriever.repo_id
    idx = GraphIndex(store, repo_id)  # built once; resolves wrapper -> impl chains
    print("=== Rerank signals (OBSERVE-ONLY — no ranking change) ===")
    print("  ct=content_type  vis=search_visibility  scores=sym/kw/vec->combined  "
          "deleg=delegator->leaf_implementation(s)")
    for i, item in enumerate(gold):
        res = retriever.search(item["q"], top_k=50)
        intent = sig.query_intent(item["q"])
        grank = _first_rank(res, item["files"], "path")
        print(f"\n[{i:2d}] intent={intent:10s} gold@{'-' if grank is None else grank:<3}"
              f"  {item['q'][:58]}")
        for rank, rr in enumerate(res[:6]):
            cs = rr.channel_scores or {}
            leaves = sig.delegator_leaves(rr, store, idx)
            deleg = ("YES->" + ",".join(sorted({l["name"] for l in leaves}))) if leaves else "-"
            anchor = rr.ref or f"{rr.path}::{rr.symbol_name or rr.chunk_type or ''}"
            print(f"   {rank:2d} {sig.content_type(rr):9s} {sig.search_visibility(rr):12s} "
                  f"{cs.get('symbol', 0.0):.2f}/{cs.get('keyword', 0.0):.2f}/"
                  f"{cs.get('vector', 0.0):.2f}->{rr.score:.3f} deleg={deleg:24s} {anchor}")
    retriever.close()
    print("\n  READ: observe-only. Look for (a) thin CLI/MCP wrappers flagged deleg=YES with the "
          "real implementation as their callee, (b) prose/data cards with ct!=code outranking "
          "code, (c) where query_intent mislabels (e.g. an 'mcp'/'cli' query that should still "
          "find the implementation, not the launcher) — those calibrate Patch 3/4/5.")


def perquery(repo: str = ".", gold=None, impact_gold=None) -> None:
    """Per-query rank + top-1 ref — for diffing two index states (e.g. heuristic vs
    enriched) to see exactly which query moved. Also prints per-symbol caller-edge detail
    (got vs gold, missing/extra) so a non-zero FP/FN can be adjudicated against the source."""
    gold = gold or GOLD
    settings = Settings.load(repo)
    retriever = Retriever(settings)
    for i, item in enumerate(gold):
        res = retriever.search(item["q"], top_k=10)
        rank = _first_rank(res, item["files"], "path")
        top1 = res[0].ref if res else "-"
        print(f"[{i:2d}] rank={'-' if rank is None else rank}  {item['q'][:46]:46s} top1={top1}")
    retriever.close()

    print("\n=== Caller-edge per-symbol (impact gold) ===")
    for row in _impact_per_case(settings, impact_gold):
        flag = "exact" if row["exact"] else "DIFF "
        print(f"  [{flag}] {row['ref']}")
        if row["missing"]:
            print(f"            missing (FN): {row['missing']}")
        if row["extra"]:
            print(f"            extra   (FP): {row['extra']}")


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
    ap.add_argument("--cast", action="store_true",
                    help="cAST subchunking A/B: whole-symbol vs +ast_block children (real "
                         "model; re-indexes the repo; separate from --gate)")
    ap.add_argument("--tasks", default=None,
                    help="path to an external task set (JSON/YAML) for run/baselines against "
                         "ANY indexed repo; pair with --repo (the large-repo / cross-file set)")
    ap.add_argument("--perquery", action="store_true", help="per-query rank + top1 ref")
    ap.add_argument("--signals", action="store_true",
                    help="OBSERVE-ONLY structural rerank signals per top card "
                         "(delegator/prose/density) — changes no ranking (Patch 2)")
    ap.add_argument("--rerank", action="store_true",
                    help="Patch 4/5 rerank A/B: off vs prose vs density vs both "
                         "(dogfood ablation; NOT a ship gate)")
    ap.add_argument("--crossover", default=None,
                    help="path to a 2-class task file {buried, control}; the OFF-DOGFOOD rerank "
                         "ship gate. Indexes --repo fresh (real model) — point it at an external "
                         "repo/fixture, not the main repo")
    ap.add_argument("--matrix", default=None,
                    help="path to evals/fixtures/matrix/manifest.json; run the #11 "
                         "per-(language x task-type) deterministic retrieval matrix over the "
                         "vendored fixtures (indexes each fresh). Pair with --save/--gate <label>")
    ap.add_argument("--matrix-arms", default=None,
                    help="path to the matrix manifest; run the matrix under config arms (the "
                         "gated reranker signals AND the mode presets) over one index per fixture "
                         "and print per-cell deltas vs base. DIRECTIONAL / no-regression only — "
                         "not a ship gate (ship needs the external crossover)")
    ap.add_argument("--gate", default=None,
                    help="label -> compare to evals/<label>.json; exit 1 if a hard "
                         "metric regressed (M1 lock + M3 acceptance gate)")
    args = ap.parse_args()

    gold = impact_gold = tests_gold = None
    if args.tasks:
        gold, impact_gold, tests_gold = load_tasks(args.tasks)

    if args.perquery:
        perquery(args.repo, gold=gold, impact_gold=impact_gold)
    elif args.signals:
        signals_report(args.repo, gold=gold)
    elif args.rerank:
        rerank_eval(args.repo, gold=gold)
    elif args.crossover:
        crossover_eval(args.repo, args.crossover)
    elif args.baselines:
        baselines(args.repo, gold=gold)
    elif args.cast:
        cast_eval(args.repo)
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
    elif args.matrix_arms:
        matrix_arms_eval(args.matrix_arms, _RERANK_ARMS, "gated structural reranker signals")
        matrix_arms_eval(args.matrix_arms, _MODE_ARMS, "per-call mode presets")
    elif args.matrix:
        result = matrix_eval(args.matrix)
        if args.save:
            out = Path(__file__).parent / f"{args.save}.json"
            out.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(f"\nwrote {out}")
        if args.gate:
            base_path = Path(__file__).parent / f"{args.gate}.json"
            if not base_path.exists():
                print(f"\ngate baseline not found: {base_path}")
                sys.exit(2)
            baseline = json.loads(base_path.read_text(encoding="utf-8"))
            if not matrix_gate(result, baseline):
                sys.exit(1)
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
