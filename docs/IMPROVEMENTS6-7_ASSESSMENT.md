# Improvements 6 & 7 — Alignment Assessment

**Date:** 2026-06-24 · **Scope:** `docs/Improvement6.txt` (general "what would help me" + a
chunking-tier list) and `docs/Improvement7.txt` (Claude-Code-specific). Assessed **jointly**
because the two are one review session and their top items collide/duplicate (6's "resident
architectural skeleton" **is** 7's "CLAUDE.md skeleton"; the meta-point spans both).

**Method:** every checkable claim verified against *actual code* (file:line) via four parallel
reads — chunking/indexer, `repo_get`/session/staleness, graph/impact/affects/determinism, and
the MCP surface/maps/context-pack — plus first-hand reads of the **whole skill**
(`.claude/skills/pandemonium/*`) and `docs/ARCHITECTURE.md`. Cross-checked against **`ROADMAP.md`
(v2, trust-first)** and the sibling precedent `docs/IMPROVEMENTS5_ASSESSMENT.md`. Statuses are
the post-verification verdict, not a read of the docs.

---

## Verdict (front-loaded)

**Right thesis, snapshot-stale plan — earned a third time.** The docs' governing instinct —
*"tell me the boundaries of what I know… calibration is the one thing I can't supply myself"* —
**is** PandemoniumProtocol's stated mission verbatim (ROADMAP v2: *"every synthesis output must
carry its own confidence and visually separate verified graph-facts from heuristic guesses; a
confident-wrong brief is worse than no brief"*). So, like Improvements 3 and 5, these are
**external reviews written without the project's internals in hand.** Mined selectively they
yield a tight, high-quality keeper list; followed naively they re-build shipped primitives.

> **The verification overturns six load-bearing *current-state* claims outright, and finds
> another five already substantially shipped.** What remains is **five genuinely net-new
> items** — and the single best one (a post-edit breakage check) the project has *not* yet
> built and is *not* on the roadmap.

**Six current-state claims the code refutes** (these were the docs' premises for action — they
are wrong, so the actions attached to them mostly evaporate):

| Doc claim (current state) | Reality | Evidence |
|---|---|---|
| 6·T1b — symbol span uses bare `function_definition`; **decorators/attributes excluded** | **FALSE** — span starts *at* the decorator | `tree_sitter_parser.py:353-363` (Python `decorated_definition`); C# `attribute_list` is a child of the decl, so the decl's `start_point` already covers `[HttpGet]` (`:396-398`); only the *signature string* strips it (`:244-248`) |
| 6·T4 — ast_block child descriptor **= parent's; child vectors are no-ops** | **FALSE** — children get a **distinct** descriptor | distinct `#block:` qname (`chunker.py:81`), per-block summary + content-derived tags (`index_runner.py:271, 295-308`) |
| 6·T5 / 6-part2 — affects read paths **render an empty "Affects:" header** | **FALSE** — every path guards or omits | `graph.py:1031` `if g.get("affects")`; `graph.py:1295` `if p.get("affects")`; `brief.py` has *no* affects section |
| 6-part2·#1 — `repo_impact` returns only resolved callers, **hides the recall gap** | **FALSE** — it surfaces it | `possible`/`possible_production`/`possible_test` in the return (`graph.py:1102-1107`); renders **"Possible callers (unverified — grep to confirm)"** + per-caller grep (`graph.py:1148-1153`); skill says "treat the caller list as a *floor*" (`impact-protocol.md:70`) |
| 6-part2·#5 — outputs may be **non-deterministic** | **FALSE** — engineered against | final sort stable + fully tie-broken on `chunk_id`, with an explicit anti-`PYTHONHASHSEED` comment (`hybrid_search.py:104-108`); indexer single-threaded; `chunk_id` is content/position-derived |
| 7·#6 — line-fetching gives no reason to leave native Read; **edit-stability isn't sold** | **FALSE** — it's the skill's exact framing | `ref-rules.md:11-16` "re-parses the current file and re-finds the symbol by name… survives edits"; SKILL hard rule "Don't use `path:line-range` as durable identity" (`SKILL.md:101-102`); `repo_get` re-resolution verified (`refs.py:214-233`) |

**Five asks that are already substantially shipped** (the reviewer asks for what exists):
7·#1 grep-only-for-a-*distinctive*-token (SKILL.md:63-71 = ROADMAP Step 0 #7) · 7·#3 ledger
re-hydration across restarts with staleness re-validation (`repo_session(resume)`, SKILL step 2,
`session-ledger-protocol.md`) · 7·#5 subagent delegation with task-capsules
(`parallel-agent-protocol.md`) · 6-part2·#7 affects-as-hypothesis labeling
(`impact-protocol.md` "Hypothesis… not facts") · and the **meta-point itself** (= the trust-first
ROADMAP).

---

## The keeper list — five net-new, valid items (everything else is shipped / validation / measure-first)

Ordered by value. Per the **M3 acceptance rule**, none may be called an "improvement" until the
eval matrix shows **fewer total task tokens + lower error rate** than baseline. Three of the five
touch trust or the resident budget and are flagged accordingly.

### 1. Module-body / complement card  *(6·T2)*  — **the real retrieval-quality change · GATE**
**Verified gap.** In symbol-only mode (the default), a file *with* symbols gets **no** coverage
of the lines *between* its symbols: the fallback block-windows and the backstop fire **only for
zero-symbol / zero-chunk files** (`chunker.py:114-132`). So app-wiring that isn't a def —
route-registration calls, `Startup.cs`/DI registration, argparse/CLI blocks, `if __name__`,
C# top-level statements — is findable by nothing. (Nuance: Python top-level *assignments* already
become symbols, `tree_sitter_parser.py:384-388`, so `ROUTES = [...]` is covered; the gap is
non-assignment top-level code.)
**Why it's safe where `code`-scope wasn't** (the bake-off objection the reviewer pre-empts, and
correctly): `code` windows hurt because they laid arbitrary 60-line slices *over already-symbol-
covered* code → redundant, low-coherence, rank-diluting. A complement card covers **only** the
residue between symbol spans — never overlaps a symbol, is a coherent "module wiring" unit. The
structural failure mode of `code` cannot recur. Bonus: macro/codegen functions tree-sitter
misses fall into the residue and become findable.
**Token economics:** net-saving — replaces the whole-file `repo_get(expand=file)` fallback an
agent uses today to find wiring. **Ship behind an A/B** (confirm the new cards don't crowd slots
on library-heavy corpora where the residue is thin) — the reviewer says the same.

### 2. Post-edit resolve / breakage check  *(6-part2·#2)*  — **the strongest forward item · net-new, not on the roadmap · GATE + must declare its own recall limit**
**Verified absent.** Nothing checks name-resolution breakage after an edit. `repo_changed` is
file-level mtime/size/hash only (`service.py:71-110`); the full tool set has no resolve check
(`mcp/server.py:79-143`). Yet the machinery to answer it — query-time receiver-aware
`resolve_call` / `by_name` — already exists. A primitive that, after an edit, reports *which refs
no longer resolve, which import is now dangling, which signature no longer matches its N call
sites* would catch the compiler-catchable class of mistakes the agent makes when it **can't run
the code** — the docs' "my expensive failures are write-side, not read-side."
**Trust requirement (non-negotiable, by the project's own rule):** the check must **declare its
recall limits**. A "no breakage" verdict that silently missed a dynamic-dispatch / reflection /
cross-language call site is a confident-wrong output — exactly what `repo_impact`'s "under-claims
on purpose; treat as a floor" discipline guards against. Report it as a floor, not a guarantee,
or it violates the very calibration principle that makes it valuable.

### 3. Resident architectural skeleton, emitted at index time into CLAUDE.md  *(6-part2·#3 ≡ 7·#4)*  — **build-cheap, run-cost · GATE + staleness discipline**
**Verified gap.** No resident skeleton exists. `repo_map` is small/stable/repo-level but
**call-on-demand** and gives folders + heuristic entry-point *paths* only — no module *roles*, no
*dependency direction* (`mapping.py:53,149`; entry points are path-substring hints, `:26`).
`repo_logic_map` is **per-topic/volatile** (`graph.py:1310-1341`). The CLI `index` command emits
only a stats line — no CLAUDE.md / module-map artifact anywhere (`cli/main.py:50-69`). CLAUDE.md
is the one always-resident, never-evicted channel on this platform, so emitting a compact
roles+dependency-direction map there is the platform-native way to make orientation free.
**Two honesty caveats the build must carry — do not list this as "cheap" without them:**
- **Run-cost, not free.** A resident skeleton spends the *same* per-turn budget that item 7·#2
  (below) wants to *reclaim*. The two must be **netted**: is a ~1–2k-token skeleton worth more
  per resident token than the redundant tool schemas it sits beside? Plausibly yes (orientation
  is high-value and currently absent), **but state it** — don't bank the build-cost saving and
  ignore the run-cost.
- **It goes stale and isn't re-resolved live.** Unlike a card (re-found by name on fetch), a map
  emitted at index time is a decoupled artifact — this is 7·#7's staleness hole applied to the
  map *itself*, and a stale skeleton silently misorients ("right edit, wrong layer" — the exact
  mistake it's meant to prevent). It must regenerate on index and carry a freshness stamp, and be
  treated as **believed-then** like the session-resume narrative. Inherits the eval gate.

### 4. Card size hint + visible truncation notice  *(6·T3)*  — **cheap, low-risk**
**Verified, with a correction.** No card carries `loc`/`est_tokens` (no such field on `Symbol`/
`Chunk`/`SearchResult`, `models.py`) — so a size hint is genuinely net-new and free at index time
(the span is already owned). But the doc's "`repo_get` dumps the whole thing, no cap" is **wrong**:
a cap exists (`refs.py:261-262`, `max_lines=1200`). It is just **silent** (no truncation notice —
`tools.py:250-261` has no such branch) and **high**. So the real work is: (a) add `loc`/`est_tokens`
to the card so the agent can choose cost-aware *pre-fetch*; (b) make over-threshold truncation
emit a "truncated — N more lines, `view=full` to expand" notice instead of clamping silently.
Sharpest on minified/generated JS-TS, vendored single-file libs, god-functions; near-zero on a
small-function corpus.

### 5. `repo_get` re-fetch awareness  *(6-part2·#4)*  — **net-new, low-hanging**
**Verified absent — and the helper is already written.** `repo_get` *records* each fetch
(`tools.py:235` `record_fetch`) but never *consults* the ledger; an `already_fetched(ref)` query
helper exists (`session.py:140-141`) with **no production caller** (only tests reference it).
Cheapest form: a "you fetched this at full earlier, unchanged" guard. Richer form (more work):
diff-only re-fetch since last view. Real token savings on long editing sessions; rides infra the
ledger already stores. Thematically adjacent to ROADMAP Step 7 (session resume / staleness).

---

## Near-free skill-prose tweaks (no build; ideally still A/B'd since they change behavior)

| Tweak | Why | Status today |
|---|---|---|
| Reconcile the scopes default literal to `["symbol"]` *(6·T1a)* | Two sources of truth disagree (`index_runner.py:255` = `["symbol","file","code"]` vs `config/settings.py:62` = `["symbol"]`). The bad literal is dead code via `Settings.load()`'s deep-merge, but **fires exactly on the path the reviewer scoped it to** — a `Settings(data=…)` built from a partial dict (embedding integrations / tests). The verdict is **valid and narrowly correct as scoped**: silent + precision-degrading where it does fire. One line. | latent foot-gun |
| Frame grep-vs-search as **interception** *(7·#1)* | The rule exists ("grep only for a *distinctive* token", SKILL.md:63-71) but is phrased as *capability* ("grep wins when…"). The reviewer's sharper framing: *"if you're about to grep for a **concept** rather than a **token**, stop and `repo_search`"* — intercepting a strong default. | substance shipped; phrasing tweak |
| Name **compaction** as a resume trigger *(7·#3)* | The mechanism is built and taught (`repo_session(resume)`), but the skill frames it as "continuing earlier work," not "after an auto-compaction you may have lost your retrieved evidence — consult the ledger." Post-compact, the agent won't know it had a prior session unless told. | one line in `session-ledger-protocol.md` |
| Nudge **conceptual** query formulation *(7·closing)* | The vector channel shines on "user lookup by identifier", but the agent feeds it `getUserById` by grep-reflex. No skill line says *"describe what the code does, not what it's named."* The low-confidence fan-out treats the *symptom*; an upfront nudge is cheaper. | genuine small gap |
| Add the "~3 searches → spawn a subagent" numeric trigger *(7·#5)* | `parallel-agent-protocol.md` gives a task-*size* table but not the crisp retrieval-depth trigger the reviewer wants. | substance shipped; trigger missing |

---

## Measure-first (instrument before writing code)

- **ast_block fire-rate *(6·T4)*.** The reviewer's *defect* claim is false (children already get
  distinct descriptors), but the *advice* — log what fraction of symbols exceed 60 lines and
  produce ≥2 blocks — still stands as a "does cAST earn its index cost?" check. Two log lines, no
  code change. (If it's under ~10%, the honest call is whether the layer pays for itself.)
- **Resident MCP-schema token count *(7·#2)*.** The tax is **real**: **16** registered tools (the
  doc says ~15 — one is the `repo_prompt_context` alias, `server.py:64`), several with multi-line
  docstrings (`repo_session` ~9 lines, `repo_brief`/`repo_get` ~7). Measure the actual resident
  cost before acting. **But the doc's relocation target is wrong:** it names `repo_impact`/
  `repo_edit_plan` as demotion candidates — those are the project's **measured grep-beating win**
  (impact-first is the whole token thesis); burying them behind a skill procedure is backwards.
  Legitimate moves: drop the alias, trim the verbose docstrings, and *consider* relocating a
  genuinely-rare tool like `repo_logic_map`. Any surface change rides the M3 gate (it changes what
  the agent reaches for) — and **nets against item 3's resident skeleton.**

---

## Deferred track (unchanged from the prior assessment)

- **affects produce→ingest *(6·T5)*.** Still the dormant, deliberately-gated layer
  (`ARCHITECTURE.md:73-79`; `ingest_affects` has no producer). The new "empty header" sub-claim is
  false (above), so even the small read-path guard the reviewer asked for is already in place.
  Sequence unchanged: **eval instrument → trust-labeled produce→ingest → framework extractors**,
  scoped to the repos actually targeted — never ahead of the gate.
- **Task-conditioned annotation *(6-part2·#6)*.** Accurate description (index-time summaries are
  task-agnostic — `hybrid_search.py:417,420`; `repo_context_pack` task-conditions *selection* but
  not *annotations*, and auto-selects no mode — `context_packer.py:34`, `:91`). But it's already
  half-owned by ROADMAP Step 6 (modes, mechanism shipped/unvalidated) and the card-as-reranker
  thesis. Net-new sliver = task-time *annotation* (why-this-card-matters), not re-ranking. Low
  priority, gate-bound.
- **Descriptor staleness per-card flag *(7·#7)*.** Real residual — no per-card "summary may be
  stale" marker in `repo_search` output — but mitigated more than the doc credits: reindex
  regenerates descriptor **and** embedding (`index_runner.py:316-319`), an `AutoReindexer`
  refreshes before read tools (`tools.py:143-153`), and the skill already warns code is ground
  truth. The net-new sliver: surface a stale marker on cards whose file changed post-index.

---

## What the docs got *right* that's worth banking (validation, not work)

- **The card model is already an LLM-rerank pass** (agent prunes on cheap cards before fetching).
  Correct, and it carries a real **tuning implication**: the recall set can be *looser/wider*
  because the agent filters it for nearly free — lean toward wider recall + cheap card-filter.
  Composes with ROADMAP Step 2 (auto-fan-out). Confirmed by the architecture.
- **The meta-point** — *feed me the edges of my information, not just information* — is the
  project's reason for existing. Items 2 and 3 above are the two places that principle is **not
  yet operationalized** ("what did my edit break" / "where am I"); 1·#1 (recall gap) and the
  ledger already cover the other two ("what's unverified" / "what I've seen") — modulo item 5.

---

## Bottom line / sequencing

1. **Today, no eval:** reconcile the scopes literal *(T1a)*; the four skill-prose tweaks. Land the
   two **measure-first** log lines (ast_block fire-rate; resident-schema token count) on the next
   index run — they convert items 1 and 7·#2 from arguments into decisions.
2. **Behind the gate, in value order:** module-body card *(1)* → post-edit breakage check *(2,
   with its recall-limit honesty)* → CLAUDE.md skeleton *(3, netted against 7·#2 and with
   staleness discipline)* → size hint + truncation notice *(4)* → re-fetch awareness *(5)*.
3. **Leave deferred:** affects produce→ingest; task-time annotation; the per-card stale flag —
   all gated, all owned by existing roadmap tracks.

> Improvements 6 & 7 are **excellent as validation of the trust-first direction and as a source of
> ~5 net-new items** — chiefly the post-edit breakage check, which is genuinely off the current
> roadmap and the highest-leverage write-side capability the project hasn't built. They are **not
> a plan to execute step-by-step**: six of their current-state premises are factually wrong, and
> their two budget-touching ideas (cut the tool tax / add a resident skeleton) **contradict each
> other** unless netted — the same "amplifies whatever is underneath" trap ROADMAP v2 exists to
> stop.
