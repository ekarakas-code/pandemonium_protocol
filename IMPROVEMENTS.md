# PandemoniumProtocol — Improvement Roadmap

**Generated:** 2026-06-19
**Method:** Empirical A/B benchmarks (baseline `grep`/`Read` vs the pandemonium CLI, blind-judged
against an oracle answer key) **plus** a 5-analyst source teardown, run against a real **546-file /
3,190-symbol C++ codebase** (`SomeStrategyGame`). Every claim below was checked against the source
and, where noted, against the live index DB — not inferred.

---

## TL;DR — the root cause

Pandemonium **lost both A/B benchmarks to plain `grep`** on impact-mapping tasks. The teardown traced
it to a small number of concrete, fixable defects — and **disproved the obvious theory**:

> The losses were **NOT** "the graph is blind to header `constexpr`/inline functions." That symbol
> (`computeStepFromVelocity`) **is** indexed as a real symbol — it just had **0 call edges despite 13
> call sites**. The real bug is in **`graph.py::_callee_cpp`**, which **drops the call edge entirely**
> for **nested-namespace calls** (`a::b::c::fn()`) and **template calls** (`fn<T>()`).

Because `rts::sim::systems::fn(...)` is the *universal* call idiom in C++ codebases, this single
extraction gap makes `impact`/`graph` miss the real production callers across the board. A
uniquely-named member call (`world.queueDeath()`) *does* resolve (via the `by_name` fallback), which
is why the second benchmark's `impact` found its callers — yet pandemonium still lost there on
**token cost, exact line-number precision, and latency**.

The fixes are mostly **small and query-time** (ship with no reindex); the one structural recall fix
and the extraction fix **batch into a single reindex**.

---

## Benchmark evidence

Two tasks, identical for both arms, same model, blind 3-judge scoring vs an unconstrained oracle key.

### Benchmark 1 — impact map of `computeStepFromVelocity` (a unique header `constexpr`)

| Metric | Baseline (grep/read) | Pandemonium CLI | Winner |
|---|---|---|---|
| Source lines into context | ~620 | ~1,350 | **grep (2.2× leaner)** |
| Wall-clock | 265 s | 448 s | **grep** (CLI reloads the model per `search`) |
| Completeness / Correctness / Specificity (avg /10) | 8.3 / 9.3 / 9.3 | 7.3 / 7.7 / 7.7 | **grep** |
| Judges | 2× baseline, 1× tie | 0 | **grep** |

### Benchmark 2 — impact map of `World::queueDeath` (a real multi-caller `.cpp` method)

| Metric | Baseline (grep/read) | Pandemonium CLI | Winner |
|---|---|---|---|
| Source lines into context | ~720 | ~900 | **grep** |
| Wall-clock | 200 s | 460 s | **grep** |
| Completeness / Correctness / Specificity (avg /10) | 10 / 10 / 10 | 7.7 / 6.0 / 5.0 | **grep** |
| Judges | 3× baseline | 0 | **grep** |

Here `impact`/`graph` **did** find the caller functions (unique-name member call → resolved). It lost
because: (a) it answers at **function granularity**, so the agent had to *guess* exact call-site line
numbers and got them **systematically wrong** (the judges' decisive complaint); `grep` returns
`file:line` for free; (b) **ref-form friction** — `path:line` and hand-built namespace refs were
rejected, costing retries; (c) the per-`search` **model reload**; (d) more context pulled.

> **Structural takeaway that survives all fixes:** for *"enumerate every call site with exact line
> numbers,"* `grep` is structurally better (line granularity). Pandemonium's edge is *"which
> functions/subsystems are involved"* + **semantic discovery in unfamiliar code** — a regime not yet
> benchmarked.

---

## Ranked roadmap

| # | Fix | Effort | Reindex? | Payoff |
|---|---|---|---|---|
| 1 | `_callee_cpp`: extract nested-namespace + template calls | S | yes | Recovers real C++ callers — the impact/graph fix |
| 2 | `repo_get` `--signature` / `--head N` / `--lines a-b` | S | no | ~2–10× fewer tokens per lookup (all 5 analysts) |
| 3 | Stop min-max-normalizing the **symbol** channel | S | no | Confirmed bug: exact match lost top-1 to a semantic near-match |
| 4 | Dedup search results by **ref/symbol_id** | S | no | Kills the measured "same ref 3×" |
| 5 | Capture **C/Doxygen comments** in summaries | S | yes | Funds the embedding descriptor (empty for non-Python today) |
| 6 | "Possible callers" tier + **prod/test split** + harden `is_test_path` | M | no | Honest, grep-able residual + real blast radius |
| 7 | **Exact-identifier short-circuit** (skip vector + model load) | M | no | Unique-symbol query cheaper than grep, no model load |
| 8 | Summarize the ambiguous-callee collision dump | S | no | Large token cut on `graph` output |

### 1 — Fix `_callee_cpp` nested-namespace + template extraction `[S, reindex]`
**Problem:** `pandemonium/graph.py::_callee_cpp` (~lines 122–146) requires the call's `name` child to
be `identifier`/`field_identifier` (~line 141). A multi-level `qualified_identifier`
(`rts::sim::systems::runSeparation`) has a *nested* `qualified_identifier` as its `name` child → the
check fails → returns `(None,'bare','')` → `extract_edges` (~line 366, `if name:`) drops the edge.
Template calls parse as node type `template_function` → unhandled → also dropped. **DB-verified:**
`computeStepFromVelocity` = 0 edges (13 call sites); `runSeparation` = 4 edges, all from *bare* test
calls (`using …; runSeparation(w,jobs)`), production's qualified call dropped.
**Proposal:** (a) For `qualified_identifier`, take the full node text, split on `::`,
`name=parts[-1]`, `recv=parts[-2]` — composes with the existing `by_qual_suffix` index (verified:
`rts::sim::systems.runSeparation` → `parts[-2]='systems'` → 1 hit → 0.8 confidence → `World::tick`
surfaces as a confident caller). (b) Before type dispatch, if `fn.type in
('template_function','template_method')`, descend to `child_by_field_name('function')/('name')` and
recurse. Add unit cases for `a::b::c::fn()` and `fn<T>()`.
**Risk:** Low; pure extraction widening, resolution already supports the qualified path, no schema
change. Re-index to repopulate edges.

### 2 — Narrowing modes for `repo_get`: `--signature` / `--head N` / `--lines a-b` `[S, no reindex]`
**Problem:** `refs.py::resolve` only supports `exact|neighbors|file|parent` (`EXPAND_MODES` ~line 32)
— all return the **whole symbol span** (`lines[start-1:end]`, ~line 193). There is no narrowing mode.
This is *the* reason grep won on tokens (grep returns 1–2 matching lines).
**Proposal:** Add an orthogonal `view` (default `full`): `signature` → emit the stored
`ParsedSymbol.signature` (already populated at index time) or lines up to the first `{`/`:`;
`head:N`; `lines:a-b` (clamped within the span). Thread through `service.py::get`, `cli/main.py::get`,
`mcp/tools.py::repo_get`. Zero extra parse — `resolve` already re-parses for symbol refs.
**Risk:** Low; purely additive, default unchanged.

### 3 — Stop min-max-normalizing the symbol channel `[S, no reindex]`
**Problem:** `hybrid_search.py::hybrid_search` (~line 63) runs `_normalize` (~line 52) on **every**
channel. The symbol channel's scores are already calibrated (`1.0` exact / `0.7` prefix / `0.4`
substring via `symbol_search._RANK_SCORE`). When an exact match is the lone symbol hit, the
degenerate guard collapses its `1.0` → `_DEGENERATE_SCORE=0.7` → combined `0.40*0.7 = 0.28`, which
**loses** to any strong vector hit (`0.30*1.0 = 0.30`). A semantic near-match out-ranks the exact
symbol at top-1.
**Proposal:** Normalize only the uncalibrated channels (keyword bm25, vector cosine); pass the symbol
channel through **identity**. Update the module docstring that claims it normalizes "each channel."
**Risk:** Low; pure ranking change. One merge unit test may need its expected order updated.

### 4 — Dedup search results by ref/symbol_id before line-overlap `[S, no reindex]`
**Problem:** `build_ref` (`refs.py:50`) encodes only path + qualified_name (drops the window line
range), so every window-chunk of a large symbol shares one ref. `hybrid_search._dedup` (~88–101)
collapses only **line-range-overlapping** hits, and the symbol channel expands a match to *all* its
chunks (`chunk_ids_for_symbols`). Non-overlapping windows survive → "same ref 3× in top-3."
**Proposal:** Add a first-pass collapse keyed by `(symbol_id or non-empty ref)`: keep the
highest-scoring result per ref (prefer `scope=='symbol'` and the smallest `start_line`, which carries
the signature); run the existing overlap pass on ref-less survivors.
**Risk:** Low; guarded on symbol_id/ref presence so code/file chunks are untouched.

### 5 — Capture C-style / doc comments in symbol summaries `[S, reindex]`
**Problem:** `summaries/summarizer.py::extract_docstring`'s `_DOCSTRING_RE` matches **only Python
triple-quotes**. Every C++/C#/JS/TS symbol's summary collapses to the bare signature line, so
`descriptor.build_descriptor` embeds a near-meaningless descriptor (Path + qname + signature) — the
structural reason the vector channel contributed nothing for `computeStepFromVelocity`. The
descriptor module calls embedding the descriptor "the central bet"; that bet is **unfunded for every
non-Python language**.
**Proposal:** Add a language-aware leading-comment extractor (`//`, `///`, `/** */`, `#`) preferring
the block immediately above the symbol (Doxygen/JSDoc); thread `language` + a few preceding lines
(already in scope at `index_runner._index_file` ~line 118) into `summarize_symbol`. Keep the Python
path unchanged.
**Risk:** Low; index-time only, bounded by a sentence-length cap.

### 6 — "Possible callers" tier + production/test split `[M, no reindex]`
**Problem:** `graph.py::_callers_of` (~553–566) keeps a caller only if it resolves to exactly the
target id **and** not ambiguous **and** conf ≥ 0.6 — discarding a known-real caller whenever the
target is merely *among* an ambiguous set, with no prod/test separation. Also `tests_finder.is_test_path`
(line 18, `'test' in base`) misclassifies `contest.cpp`/`latest.cpp`.
**Proposal:** Build two lists — confident `callers` (unchanged) + `callers_possible` (ambiguous or
`0.4 ≤ conf < 0.6`), rendered under a labeled *"Possible callers (unverified — grep to confirm)"*
section (cap ~15). Split each tier into Production vs Test; lead with Production in `repo_impact`.
Harden `is_test_path` to a token/boundary check.
**Risk:** Low; additive tiers, confident set unchanged.

### 7 — Exact-identifier short-circuit `[M, no reindex]`
**Problem:** A bare-identifier query still runs all three channels and (CLI one-shot path) pays the
~1–2 s model load via `embed_query`, then ranks the exact hit among semantic neighbors that dilute
it. Yet `symbols_by_name` already computes `match_rank==3` (exact) and `lookup_symbol` returns
path+lines+signature with no model and no body.
**Proposal:** In `Retriever.search` (~line 131), before building channels: if the query is a single
identifier-shaped token (len ≥ 3, not a stopword) **and** `symbols_by_name` returns ≥1 row with
`match_rank==3`, return one card per distinct exact symbol (carrying signature + line range) and
return early — skipping keyword+vector (add a `vector=False` switch so the model never loads).
Config-gate (`retrieval.exact_short_circuit`).
**Risk:** Medium; gate strictly on single-token + `match_rank==3` so semantic/multi-word queries are
untouched. Common-word symbol names mitigated by the stopword + len gate.

### 8 — Demote/summarize the ambiguous-callee collision dump `[S, no reindex]`
**Problem:** `callees_ambiguous` is a large bucket of `by_name` collisions on ultra-common method
names (`.size()`, `.counters()`, `world.method()`) at 0.35 confidence; `render_graph` dumps the first
15 verbatim — pure token cost, near-zero signal. Root cause: `resolve_call` has **no `expr` branch**,
so every `obj.method()` falls to `by_name` and collides.
**Proposal:** Group `callees_ambiguous` by target_name; suppress any name whose `by_name` candidate
count exceeds a **data-driven** threshold (from `GraphIndex.by_name` sizes, not a hardcoded denylist),
collapsing to one summary line (`+142 high-collision calls suppressed (e.g. size, begin, counters)`).
Keep low-collision ambiguous callees fully listed and keep the count.
**Risk:** Low; thresholded on collision count so rare names stay visible.

---

## Sequencing

1. **Ship now (query-time, no reindex):** #2, #3, #4, #8 — plus the FTS keyword filter/column-weight
   and the oversized-file surfacing (below). These flip the token + precision losses immediately.
2. **One batched reindex:** #1 (`_callee_cpp`) + #5 (comment capture) + the identifier-gloss line in
   `descriptor.build_descriptor` + token-aware chunking. Do them together — each forces a reindex.
3. **Bigger bets (after the above prove out):** see below.

### Bigger bets
- **Header-decl → .cpp-def linking `[L]`** — **DONE (2026-06-20, ROADMAP v2 Step 8).** Shipped as a
  doc-comment MERGE onto the `.cpp` definition keyed by canonical qualified_name (`::`→`.`), with the
  header attached as an alternate decl-site (`Symbol.decl_ref`, surfaced by `repo_get`). Declarations
  are NEVER indexed as separate symbols — only their docs travel — so `by_name`/`by_qname`/`resolve_call`
  stay unchanged (constraint honored). The incremental-reindex invariant is handled by reading the
  sibling header off disk at `.cpp`-index time (self-contained per `.cpp`); the residual is a SILENT
  cross-file staleness when ONLY the header changes (documented in `evals/RESULTS.md` "Step 8" — a
  header→cpp dependency reindex is deliberately out of scope). Measured (`run_eval.py --cppmerge`) +
  locked offline (`tests/test_cpp_header_merge.py`).
- **`expr` receiver-type resolution `[M–L]`** — cheap single-file local type inference for
  `obj.method()` (scan the caller body + class fields for the receiver's declared type), resolve via
  the existing `by_qual_suffix`; only *upgrade* confidence on a unique hit. Converts much of the
  ambiguous-callee bucket into confident, typed callees.
- **Token-aware, symbol-atomic chunking `[M, reindex]`** — wire up the **dead**
  `chunk_max_tokens=512`/`chunk_min_lines=5` config (never read; real chunking is hardcoded
  `window_lines=60`/`overlap=10` in `index_runner._index_file` ~line 127). Split only when a symbol
  exceeds the token budget, anchor the head window at the signature, and embed **one vector per
  symbol** (extra windows FTS-only) to stop identical-summary collisions.
- **Persistent CLI search daemon `[M]`** — wrap the existing warm `mcp/tools.py::ToolContext` over a
  localhost socket / named pipe so CLI `search`/`context` load the model **once per session** instead
  of per call. Keep the synchronous one-shot fallback as default.
- **Token-budget `get` + context-pack rework `[M]`**; **file-skeleton expand** (`get path
  --skeleton` via the existing `symbols_in_file` query — one SQL, no body read; 10–50× cheaper than a
  full-file read for "what's in this file").

### Config foot-guns (cheap correctness/observability)
- `indexing.languages` defaults to `['python']` → a C++ repo indexes ~0 symbols until set. Auto-detect
  by extension in `init`, and/or print a `0 symbols — check indexing.languages` hint after `index`.
- `max_file_bytes` (default 1 MB) **silently drops** larger files (`scanner.scan` ~line 44, no log).
  Add `IndexStats.skipped_too_large`, warn in the `index`/`changed` summary, and raise the default.
- The scanner reads **only** `.pandemoniumignore`, never `.gitignore` — consider merging `.gitignore`
  when `.pandemoniumignore` is absent.

---

## Summaries

**Token efficiency:** grep won because `repo_get` always returns the whole span and the same symbol
appears up to 3×. #2 (signature mode) + #4 (ref dedup) + #7 (exact short-circuit, no model load) +
#8 (collapse the ambiguous dump) directly close this — bodies become signatures, dupes collapse,
bare-identifier queries skip embedding. The agent stops paying for whole bodies it only needs to
confirm and stops re-reading the same symbol.

**Result quality:** #1 recovers the real C++ callers (the headline fix); #3 stops exact matches losing
top-1 to a semantic near-match; #5 gives the C++ vector channel real meaning (today it embeds ~just
the signature); #6 makes the residual ambiguous callers honest and grep-able. The
header-decl→cpp-def merge is correctly a *bigger bet*, not a benchmark cause.

---

## Methodology caveats (so the numbers are read honestly)

- **Output-token figures from Benchmark 2 were discarded.** Two budget-measuring workflows were run
  concurrently; the Workflow token meter is a *shared pool*, so per-arm output-token deltas were
  contaminated. The clean signals used were per-agent self-reported tool-calls + lines-ingested +
  wall-clock, and the blind-judge quality scores. (Lesson: serialize budget-measured runs.)
- **CLI model-reload tax** inflated pandemonium's wall-clock in both runs (the MCP server keeps the
  model warm; the CLI reloads it per `search`). Time was weighted lightly as a result; the daemon
  bigger-bet addresses it.
- **N = 2**, both reference-finding tasks (grep-favorable by nature). The semantic-discovery regime —
  pandemonium's predicted strength — was **not** benchmarked.
