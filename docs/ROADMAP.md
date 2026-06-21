# PandemoniumProtocol — Implementation Roadmap (canonical implementation list)

> **SUPERSEDED (v1, historical).** The current canonical roadmap is the root
> [`ROADMAP.md`](../ROADMAP.md) (v2, trust-first); `IMPROVEMENTS.md` is its v1 predecessor.
> This file is the original MVP plan, kept for history — its status lines (e.g. "15 tests")
> are from the MVP era; the live suite is now 146. Do not treat this as current.

This is the single source of truth for what we build next. It integrates the
external review in [`docs/Improvements.txt`](./Improvements.txt) with the design
agreed in chat. The MVP (index → hybrid search → context pack → CLI + MCP, Python)
is **already shipped and verified** (15 tests, dogfood passes); everything below is
the "intelligence upgrade" layered on top.

## Guiding principle (from the review)

> PandemoniumProtocol is **not a search feature — it is an attention-management
> system for coding agents.** Search returns cheap, tagged **cards**; exact code is
> fetched by **stable reference** only when needed; a **session ledger** prevents
> rediscovery; parallel agents coordinate through **compact evidence capsules**.

Corollary that governs the data model: **summaries guide retrieval; `repo_get`
confirms reality.** Descriptors are retrieval hints; the stored code is the only
source of truth.

---

## Locked decisions (mine + review-confirmed)

| Decision | Resolution |
|---|---|
| Descriptor strategy | **Embed a clean descriptor; store raw code separately, fetched on demand.** Raw code stays a bake-off variant, not the default. |
| Stable references | Priority: **stable id → `path::qualified_name` (re-resolved) → `path:lines` (fallback)**. |
| Scope priority | **Symbol-scope is the primary unit.** File-scope = orientation. Code-chunk = fallback when symbol extraction fails. Eval decides whether code-scope earns its cost. |
| Enrichment default | **`heuristic` (local, zero-cost) is default. `claude` is opt-in, first-class. `local_llm` (Ollama) is the privacy middle ground.** Never silently send code out. |
| Enrichment model | **Haiku-class for bulk** symbol summaries; stronger model only selectively (complex file/architecture summaries). |
| Tag schema | **Fixed structured schema** (below). Free-form tags + normalization deferred. |
| `repo_get` expansion | Ship **`exact`, `neighbors`, `file`, `parent`** early; `callers`/`callees` later (needs the symbol graph). |

---

## The card model

Each indexed unit is a **card**. We embed `descriptor(card)`; we store `code`.

```
ref            stable anchor (see Reference rules)
scope          symbol | file | code
language
path
qualified_name e.g. Retriever.search
parent         e.g. Retriever
signature
summary        heuristic- or LLM-generated   ← embedded
tags           fixed schema (below)          ← embedded + filterable
code           stored, NOT embedded          ← returned only via repo_get
```

**Storage:** new columns on `chunks` (SQLite) — `ref`, `scope`, `qualified_name`,
`parent`, `tags` (JSON). The **filterable** subset (`scope`, `language`,
`qualified_name`, `ref`, flattened tags) is mirrored to the LanceDB `code_chunks`
table so vector search can be scope/tag-filtered.

**Descriptor format embedded (labeled, NL-first):**

```
Language: Python
Scope: symbol
Qualified name: Retriever.search
Signature: search(query: str, top_k: int | None) -> list[SearchResult]
Summary: Runs hybrid (symbol+keyword+vector) search, normalizes & merges channels, dedups, returns ranked cards.
Responsibilities: retrieval, ranking, dedup
Depends on: SqliteStore, LanceStore, LocalEmbedder
Domain: repository search, context packing
Search terms: find relevant code, semantic search, hybrid merge
```

**Result dedup/grouping (required):** multi-scope retrieval will surface near-dupes
(file + symbol + chunk for the same code). Group results by file → symbols → chunk
rather than emitting three competing cards.

---

## Reference rules

- **Stable anchor = `path + qualified_name`**, re-resolved to current lines at fetch.
  Current `chunk_id`/`symbol_id` embed `start_line` → not edit-stable; the ref layer
  fixes this.
- **Resolver** accepts stable id → `path::qualified_name` → `path:lines`.
- **Staleness:** `repo_changed` reports whether a fetched ref's file changed since
  indexing; never assume a symbol is current after a post-index edit.
- **Acceptance test:** edit a file (shift lines), confirm a pre-edit ref still resolves
  to the right symbol.

---

## Tag schema (fixed)

```json
{
  "responsibilities": [],
  "depends_on": [],
  "domain": [],
  "search_terms": [],
  "side_effects": [],   // "writes disk", "mutates state", "network call", ...
  "entrypoints": []     // CLI command, API route, handler, worker, scheduled job, hook
}
```

A `raw_tags → normalized_tags` layer (to collapse `auth/authentication/login/identity`)
is **deferred** until after baseline measurement; flagged as a cross-provider
consistency risk.

---

## Evaluation metrics (eval harness — Phase 0, baseline before any change)

| Metric | Why |
|---|---|
| `precision@k` / `MRR` | Right symbol/file ranks near the top |
| `tokens-to-right-code` | The new loop actually saves context |
| **`fetches-to-resolution`** | How many `repo_get` calls before the agent finds the right code (cheap cards that force 5 fetches aren't a win) |
| **`duplicate-result-rate`** | Multi-scope shouldn't flood the pack with near-dupes |

---

## Tool surface (phased — don't expose too many verbs at once)

**First:** `repo_search` (returns cards), `repo_get` (exact/neighbors/file/parent),
`repo_context_pack`, `repo_session`.
**Later:** `repo_graph` (callers/callees), `repo_impact`, `repo_changed`,
`repo_tests`, `repo_explain`.

---

## Risks / guards (from the review)

1. **LLM summaries can hallucinate** → enricher prompted to ground strictly in the
   code; summaries are hints, code is truth.
2. **Symbol-extraction quality becomes critical** once symbol-scope is central → a
   robustness test matrix (nested classes/functions, overloads, decorators, anonymous
   functions, partial classes, duplicate qualified names, generated files, large mixed
   files) — especially for C++/C#/JS.
3. **Tag inconsistency across providers** → normalization layer (deferred).
4. **Multi-scope duplicate hits** → group/dedup in the result pack.

---

## Phased implementation list

Each phase is **measured against the Phase-0 baseline** and reported at its boundary.

### Phase 0 — Evaluation baseline
Labeled query set over this repo; capture `precision@k`, `MRR`, `tokens-to-right-code`,
`fetches-to-resolution`, `duplicate-result-rate` for the **current** system.
*(harness started: `evals/gold.py`.)*

### Phase 1 — Card model + stable refs + `repo_get`
Card schema (SQLite cols + LanceDB mirror); stable-ref resolver; `repo_search` returns
**cards, not code**; `repo_get(ref, expand=exact|neighbors|file|parent)`. Gives the
agentic search→fetch loop even before LLM enrichment. *(Python only.)*

### Phase 2 — Descriptor-based embedding
Switch the embedded input from raw-code chunks to the labeled **heuristic descriptor**
+ result dedup/grouping. Isolates the descriptor effect before LLMs enter.

### Phase 3 — Claude / LLM enrichment (opt-in)
`Enricher` provider abstraction (`heuristic|claude|local_llm`); structured summaries +
6-field tags via structured outputs; cache by `content_hash`; Batch API; prompt-cache
the instruction; audit-log every external call; grounding instructions. Bake off
**heuristic descriptor vs claude descriptor**.

### Phase 4 — Scope bake-off
Compare `symbol-only` / `file+symbol` / `symbol+code` / `all`. Adopt what wins on
precision **and** duplicate-rate — don't assume multi-scope is better.

### Phase 5 — Embedding-model bake-off
`bge-small` vs `bge-base` (768-d) vs a code model; auto-detect dim from the model.
Run **after** the descriptor format is stable (don't compare models on a moving target).

### Phase 6 — Multi-language expansion
C++, C#, JS/TS, Markdown headings, config (YAML/JSON/TOML) — each with a per-language
node extractor + the **symbol-extraction robustness test matrix**. After the
architecture is proven in Python.

### Phase 7 — Session memory + staleness
`repo_session` ledger: `searched_queries, returned_refs, fetched_refs, edited_files,
confirmed_facts, open_questions, agent_findings, invalidated_assumptions` (keyed by
session). `repo_changed` staleness check. "Check the ledger before searching broadly;
don't re-fetch unless the file changed."

### Phase 8 — Claude Skill (thin) + parallel-agent protocol + extended tools
A **thin** `pandemonium` skill that teaches the discipline, **not** the project data.
Package split:

```
pandemonium/
  SKILL.md                      # short: activation + main workflow
  retrieval-protocol.md         # search-cards-first / get-exact / context-pack rules
  parallel-agent-protocol.md    # task capsules, roles, merge
  session-ledger-protocol.md    # preserve discoveries across the session
  ref-rules.md                  # stable refs, fallback, invalidation
  eval-rubric.md                # did retrieval actually help?
```

- **Workflow it enforces:** classify task → search **cards** first (never raw code) →
  fetch exact refs only → build compact working context → edit → verify impact
  (callers/tests/config/docs).
- **Parallel-agent task capsules** (objective, retrieval limits, structured return)
  and roles (architecture / implementation / dependency / test / risk / docs), gated
  by task size (small=none, medium=2–3, large=4–6).
- **Retrieval budget:** ~10–20 cards initial, 1–3 initial fetches, ≤5 fetched refs
  before edit, whole-file only with reason, ≤3–5 refs per subagent.
- **Prohibitions:** don't dump raw search results; don't default to whole-file fetch;
  don't let agents overlap; don't edit before identifying ownership; don't trust
  summaries as truth; don't repeat ledgered searches; don't use line numbers as
  identity; don't assume a symbol is current after a post-index edit.
- **Invocation:** `pandemonium` auto+manual; destructive variants (refactor/commit/
  batch-agents) manual-only via `disable-model-invocation: true`.
- **Remaining verbs:** `repo_graph`, `repo_impact`, `repo_tests`, `repo_explain`.

---

## Queued track — Relationship graph (from `docs/Improvements2.txt`)

**To implement after the current phase run.** Turns the card index into a
code-intelligence graph: **nodes = refs, edges = relationships**. Reuses the
generalized SQLite `relationships` table (`source_type/source_id/relationship_type/
target_*/confidence`) already created in the MVP, and expands `scope` from
`file|symbol|code` toward `file|class|function|method|route|test|config|doc|data|command`.

### Phase 9 — Static AST graph
Per-language extraction of **static** edges (truth-like): `contains, imports, calls,
inherits, implements, depends_on, reads, writes, tested_by, handles`. Stored with
`origin=static, confidence=1.0`. Python first (tree-sitter call/import walk), then
C#/JS/C++.

### Phase 10 — Impact analyzer + graph tools
`repo_graph(ref, depth)` (callers / callees / imports / tests / configs) and
`repo_impact(ref)` (directly + indirectly affected refs, recommended fetches, risk
areas). Agents start from known refs, not blind search — big token + dedup win for
the parallel-agent protocol.

### Phase 11 — LLM logic graph + repo_logic_map
LLM-**inferred** edges (`affects`, `owned_by`, `similar_to`) stored with
`origin=llm_inferred`, a confidence score, and `evidence_refs`. Principle: *static
edges are truth-like; logic edges are hypotheses with evidence.* `repo_logic_map(topic)`
returns the conceptual flow across files/classes/functions.

---

## What this changes vs. the current code

- New: `ref`/`scope`/`tags` columns (SQLite + LanceDB), ref resolver, `repo_get`,
  descriptor builder, enricher provider abstraction, session store, the Skill package.
- Changed: embedding input (descriptor, not raw code), `repo_search` returns cards,
  retrieval dedups/groups, parser gains multi-language extractors + robustness tests.
- Reused: existing storage/indexer/retrieval/MCP scaffolding — this is additive.
