# Retrieval protocol — tool by tool

The tools (MCP), in the order you typically use them:

| Tool | Returns | Use it to |
|---|---|---|
| `repo_session(action="get")` | the session ledger | see what you already searched/fetched/found — check FIRST |
| `repo_map()` | stack + folders + entry points | orient in an unfamiliar repo |
| `repo_search(query, top_k=10)` | **cards** (ref + summary + tags, no code) | find code + related code by intent |
| `repo_symbol(name)` | a symbol's file + line range | jump to a known symbol by name |
| `repo_get(ref, expand)` | exact code | read the code for a chosen ref |
| `repo_find_tests(target)` | test files | find tests before you edit |
| `repo_graph(ref)` | callers, callees, imports, inheritance, members, tests (+ similar / affects) | pull *related* code for a ref without reading files |
| `repo_impact(ref)` | transitive callers + affected files + tests | check what breaks before editing a central symbol — see `impact-protocol.md` |
| `repo_edit_plan(ref)` | ranked plan: target + direct callers + tests + deps + risks + fetch order | "I'm about to change this — what do I need first?" (the impact-first default) |
| `repo_logic_map(topic)` | symbols + domains + call flow for a concept | understand how a feature works across the codebase |
| `repo_brief(task)` | a pre-flight brief: **✓ verified** graph-facts vs **⚠ heuristic** guesses, hard-separated (withholds the verified block at low anchor confidence) | START a task — anchored impact/tests/risks + likely targets, each carrying its own confidence |
| `repo_context_pack(task, token_budget)` | one budgeted markdown pack | when you want everything-at-once instead of the card→fetch loop |
| `repo_changed(refs)` | which files are stale | confirm fetched code is still current |
| `repo_reindex_changed()` | reindex result | refresh the index after edits (the only write tool) |

> **Graph edges cover Python, C++, C#, and JS/TS.** Symbols resolve in every language;
> `repo_graph` / `repo_impact` extract call/import/inherit edges (language-scoped — a call
> never resolves across languages) for those four. For any *other* language an empty graph
> means "edges not extracted," not "no relationships" — the tools say so in their output.
> Don't treat an empty impact on an unsupported language as "safe to change."

## How to read a card

```
ref=pandemonium/retrieval/hybrid_search.py::Retriever.search [symbol] score=0.86
   Runs symbol, keyword, and vector channels, weight-merges them, dedups, returns ranked cards.
   side_effects=database | domain=retrieval,hybrid search,ranking
   (exact symbol match) → repo_get(ref) to read; repo_impact(ref) before editing it
```
- `ref` is the durable handle — quote it back to `repo_get`.
- `tags` (`side_effects`, `entrypoints`, `domain`, `search_terms`) tell you what it does
  *without* fetching — use them to decide relevance.
- The summary is a **hint**, not truth. If you're going to depend on the behavior,
  `repo_get` and read it.
- The `→` line is a **terse next move**, tailored to the card: symbol cards point at the
  impact-first default; everything points at `repo_get` to confirm before trusting.

**The low-confidence banner.** If `repo_search` prints a `⚠ Low-confidence retrieval` line,
the results clustered on one symbol family while a domain term from your query went
uncovered (the classic "I asked for *cell size* and got `.size()` accessors"). The tool has
already fanned out and re-ranked; treat these cards as leads to **verify**, not answers —
the per-card hint switches to "verify" and the suggested edit actions are withheld until you
confirm. Grep the distinctive term, or `repo_get` and read.

## `repo_get` expand modes

| `expand` | Returns | When |
|---|---|---|
| `exact` (default) | just the symbol | almost always start here |
| `neighbors` | symbol ± nearby lines (imports/locals) | the symbol references things you can't see |
| `parent` | the containing class/module | you need the class around a method |
| `file` | the whole file | last resort, with a reason |

## Two modes — pick deliberately

- **Card → fetch loop** (default, token-efficient): `repo_search` → read cards →
  `repo_get` the 1–3 that matter. Use for almost everything.
- **One-shot pack** (`repo_context_pack`): a single budgeted bundle *with* code. Use
  when you won't do the fetch dance (e.g. a quick scoped task) — but it costs more
  tokens than cards.

## Don't

- Don't paste an entire `repo_search` result into your reasoning — pick refs.
- Don't `expand="file"` by default — it's the thing this skill exists to avoid.
- Don't fetch the same ref twice in a session (the ledger tracks it).
