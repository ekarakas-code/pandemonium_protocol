# Response to the architecture review (2026-06)

An external review rated the architecture "very strong" and proposed ~15 refinements. We
ran a grounded, per-claim audit against the actual code (6 parallel readers) before acting.
**Most of the review is validated.** This doc records the verdict, the few push-backs, what
we shipped immediately, and the prioritized backlog.

## Verdict at a glance

| Review point | Audit verdict |
|---|---|
| `path::QualifiedName` is line-stable, not identity-stable | **Confirmed.** `resolve()` matches by qname and returns the *first* hit; no `ref_id` / `signature_hash`; duplicate qnames collapse silently. |
| Graph edges are Python-only; non-Python `repo_impact` is weak | **Confirmed + worse than stated:** non-Python impact rendered *identically* to a genuine no-callers result → silently misleading. **Fixed now.** |
| `affects` edges need staleness / evidence hashing | **Confirmed.** No `evidence_hash`, no `created_at`, no revalidation; `repo_changed` ignores them. |
| Outputs should tier Certain/Likely/Hypothesis + surface ambiguity | **Partly already done** (numeric confidence + an "Ambiguous calls" section + "(hypothesis)" labels already ship); named tiers and "tests are name-matched" labels were missing. |
| Session ledger should be graph-aware (confirmed/rejected/stale edges) | **Confirmed gap.** Graph tools never touch the ledger; the edge data is computed and discarded each call. |
| `repo_map` modes / new eval metrics / `repo_edit_plan` / impact skill | **Confirmed gaps** (impact skill file shipped now; rest planned). |

### Push-backs (the minority where the review overreached)

1. **"Surface `_CALL_CONF` / `CALLER_MIN_CONFIDENCE` to the agent"** — mischaracterization.
   The agent *already* receives numeric resolution confidence on every callee/caller/
   similar/affects edge. Those two constants are internal (an extraction weight and a gate);
   exposing them adds nothing.
2. **"Surface ambiguous callers"** — conflicts with a *measured* decision: `repo_impact` is
   conservative on purpose because error compounds at every BFS hop. We keep impact strict.
   (Compromise available: show a *count* of dropped-ambiguous callers without putting them
   in the BFS — see backlog.)
3. **"Symbol-only scope shouldn't be a universal truth"** — already addressed. `scopes` is a
   config key (`settings.py` `indexing.scopes: ["symbol"]`); symbol-only is the *default*,
   not hardcoded. `file` / `code` remain selectable.
4. **`affects` / `similar` should both read as "Hypothesis"** — `affects` already does.
   `similar` is a *measured* vector similarity, so it's framed differently on purpose; we
   added a "suggestive, not verified" caveat rather than calling it a hypothesis.

## Shipped in this pass (safe, additive, no schema/resolution change)

- **Honesty fix (the one real correctness bug):** `repo_graph` / `repo_impact` now carry
  `edges_available` and their renderers print an explicit "edges are Python-only; empty ≠
  no callers" notice on non-Python files, instead of the misleading "Conservative…" footer.
  (`pandemonium/graph.py`)
- **Output framing:** "Similar implementations (vector — suggestive, not verified)" and
  "Tests (name-matched — confirm relevance)" labels in graph + impact renders.
- **`impact-protocol.md`** new skill file (before-you-edit discipline + the Certain/Likely/
  Hypothesis tier guide), cross-linked from `SKILL.md`.
- **`retrieval-protocol.md`** tool table now lists `repo_graph` / `repo_impact` /
  `repo_logic_map` (they were missing) + a Python-only-graph caveat.
- **Dead code removed:** unused `SessionLedger.record_edit` (MCP is read-only; `edited_files`
  is populated via the manual note path).
- **`.mcp.json`** points at the venv executable so the server actually launches in Claude Code.

All 39 tests still pass.

## Shipped — reliability sequence (wave 2)

The review's #1 priority ("graph reliability"). Implemented as one schema + resolution
sequence; **44 tests pass** (5 new); repo fully reindexed. Adversarially verified by 3
skeptics — they found one real bug (below), now fixed + regression-tested.

- **Duplicate-qname ambiguity** — `resolve()` collects *all* qname matches; disambiguates by
  `signature_hash`, else picks nearest-by-line and sets `ambiguous=True`; `repo_get` surfaces
  it. Attacks the "confidently fetch the *wrong* same-named symbol" danger.
- **Durable identity discriminants** — `signature_hash` + `fingerprint` + `symbol_content_hash`
  columns on symbols/chunks. **Deviation from the audit:** we did *not* drop `start_line` from
  `symbol_id` — it's what keeps same-qname symbols from colliding on the PK; dropping it would
  silently lose a symbol. The discriminants live in their own columns instead.
- **Tiered resolution** — qname → signature → **fingerprint (survives a rename; body
  unchanged)** → line fallback, plus a **content-hash staleness confirm** (`stale` now means
  the body actually changed).
- **`affects` staleness** — `evidence_hash` + `created_at` on `relationships`; `repo_graph`
  flags `needs_revalidation` and renders stale hypotheses as `— STALE: re-run affects`.

**Bug found by the adversarial pass + fixed:** the staleness check compared the live *full*
symbol span against the *chunk* hash, which for class-with-members (header-only chunk) and
>60-line symbols (window chunk) is a different span — so every such ref falsely read stale.
Fixed by comparing against the symbol's full-span hash (`symbol_content_hash`).
*Known low-severity limitation:* a fingerprint match can resolve a coincidental
identical-body twin after the original is deleted — `repo_get` warns "name is outdated."

## Shipped — graph-aware ledger (wave 3)

The cheapest high-leverage backlog item, now done; **46 tests pass** (2 new). The ledger
stopped being search/fetch-only:

- **`confirmed_edges`** — `repo_graph` / `repo_impact` auto-record the confidently-resolved
  edges they compute (`"A -> B"` == A calls B), so a later/parallel call doesn't re-derive
  the graph. Verified live: one `repo_graph(service.get)` recorded 13 edges (callees +
  callers, resolved across files).
- **`stale_refs`** — `repo_get` (on a stale resolve) and `repo_changed` auto-record stale
  refs.
- **`rejected_edges`** — agent-asserted only (manual `repo_session` note); ambiguous edges
  are **never** auto-mapped to rejected.
- Batched writes (`_extend` saves once per call), bounded display.

*Honest scope:* the ledger is per-process (one file per MCP session). Cross-agent auto-merge
is **not** built — a sub-agent's edges land in its own ledger; merge by reporting back and
`note`-ing into `agent_findings` / `confirmed_edges`. Documented in the skill + the tool docstring.

## Shipped — repo_edit_plan (wave 4)

Composes the graph work into the agent's actual question: *"I'm about to change this — what
do I need first?"* **47 tests pass** (1 new); 14 MCP tools now.

- `edit_plan(ref)` ranks: **primary target** → **direct callers to keep compatible** →
  **tests to update** → **dependencies (callees) to read**, plus **coupling hypotheses**
  (affects, with STALE flags) and a **suggested fetch order** (target → callers → tests).
- **Risks** are derived, not guessed: high fan-in (≥5 callers), transitive reach, missing
  tests, side-effect tags (e.g. `database`), affects-hypothesis count, and target
  ambiguity/staleness.
- Surfaced as the `repo_edit_plan` MCP tool, a `pandemonium plan` CLI command, and
  `service.edit_plan`. Verified live on `SqliteStore.chunk_by_ref`: 5 real callers, the
  `database` side-effect risk, no-tests flag, target-first fetch order.

## Shipped — finishing pass (wave 5)

The remaining backlog, all three items, now landed. **56 tests pass** (+8); repo
reindexed. The hard part of multi-language was not the node-type table but resolution
*contamination*, called out before any code.

- **Multi-language graph edges** (the focus — C++ first, then C#/JS/TS). `extract_edges`
  is now a per-language `EDGE_SPECS` dispatch (call/import/inherit node-types + extractor
  callables); C++ handles `this->` / `Class::` / bare calls, `#include`, `base_class_clause`,
  **and out-of-line `Class::method` definitions** (qname normalized `::`→`.` for
  resolution). The load-bearing change: **`GraphIndex.resolve_call` is now language-scoped**
  — a call in language A can never resolve to a same-named symbol in language B (every
  fallback, incl. the single-match and ambiguous buckets, is keyed by the caller's
  language). `all_symbols` exposes `f.language` (one-line join, no migration);
  `_edges_available` is now per-language (`EDGE_LANGUAGES`), so the honesty notice fires
  only for languages still without a spec. Verified live + by new `test_multilang.py`
  tests, **including the cross-language non-resolution guarantee**.
- **`repo_map` modes**: `architecture | entrypoints | domains | tests | changed` (+
  `default`), threaded through `service.repo_map` → `mapping` → MCP tool + `pandemonium
  map --mode`. All on already-indexed data (tags JSON, `is_test_path`, `staleness`) — no
  new indexing. `changed` live-detected the in-flight edits this pass.
- **Eval metrics**: `duplicate_card_rate`, `ambiguous_ref_rate`,
  `wrong_symbol_same_name_rate`, **impact FP/FN** (vs hand-authored `gold.IMPACT_GOLD`),
  and a **real `fetches_to_resolution`** (repo_get-based; the old `None` placeholder is
  gone). Impact FP/FN came out **0.0/0.0** against independently-grepped caller truth.
  Honestly scoped: the gold set is Python-only, so these validate the Python reliability
  work — **not** the multi-language edges (those are covered by unit tests). See
  `evals/RESULTS.md` → "Reliability metrics".

Deferred sub-items (unchanged): `repo_changed`-reports-stale-affects (columns exist; only
surfacing left); the speculative cross-reindex stable `ref_id`.

## Production-readiness audit (wave 6) + the one correctness fix it found

A 6-dimension adversarial audit (correctness, operability, security/privacy, performance/
scale, testing, docs) found **no blockers** for the local single-user model, but one
**HIGH** correctness bug — now fixed:

- **Same-stem call-resolution collision (FIXED).** `resolve_call`'s bare and module-call
  branches keyed on file **stem** (`by_file_and_name`), so a call in one `util.py` could
  resolve — confidently, `ambiguous=False`, conf 0.8 — to a same-named symbol in a
  *different* `util.py`, then feed `repo_impact`/`edit_plan` as a false caller. Fixed:
  bare calls now resolve against the caller's **own file** (`by_file_id_and_name`, keyed
  on `file_id`); module-call stem collisions are flagged ambiguous like the other
  collision branches; `all_symbols` is now `ORDER BY`-deterministic. Regression test:
  `test_bare_call_same_stem_files_do_not_cross_resolve`. **57 tests pass; impact FP/FN
  still 0.0/0.0.**

The remaining audit findings are non-blocking hardening/polish (should-fix, not
ship-stoppers): repo_get path-traversal containment + a content-blind secret filter +
`init` should add `.pandemonium/` to `.gitignore`; friendlier errors for offline/uncached
model load and malformed YAML; declare/pin torch; cache `GraphIndex` on the MCP context
and column-project the single-vector LanceDB fetch (large-monorepo scale); and test gaps
(failure paths, binary/large-file ingest, a real-embedder smoke gate, CLI smoke, and
multi-language eval gold). Affirmed strong: SQL/FTS injection-safety, the read-only MCP
guarantee, and cross-platform path handling.
