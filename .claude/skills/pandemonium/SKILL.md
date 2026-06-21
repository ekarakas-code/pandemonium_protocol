---
name: pandemonium
description: >-
  Retrieval discipline for codebases that have PandemoniumProtocol available (an MCP
  server exposing repo_search / repo_get / repo_session / repo_changed / repo_map /
  repo_context_pack / repo_find_tests). Use this whenever you need to understand,
  navigate, implement in, fix, refactor, review, or extend a codebase WITHOUT reading
  whole files — to find code and related code cheaply. Triggers: "implement this
  feature", "fix this bug", "find where X happens", "what does this affect", "refactor
  X", "understand this codebase", "update all the related places", "use parallel agents
  to inspect this". Do NOT use for a trivial single-file edit you can already see.
---

# PandemoniumProtocol — retrieval discipline

PandemoniumProtocol is **not a search box — it's an attention-management system.**
Search returns cheap, tagged **cards** (a `ref` + summary + tags + line range, *no
code*). You read the cards, decide what matters, and fetch exact code **only for what
you chose**, by `ref`. This keeps context small and focused.

> Core rule: **search cards → choose → `repo_get` exact code.** Never dump raw search
> results or read whole files by default. Summaries guide retrieval; `repo_get`
> confirms reality (the code is the source of truth).

## Decision rule — which tool, when

Pick the tool by **what you already know**, not by habit. This ordering is measured, not
aesthetic: on an impact task, `repo_impact` cost ~184 tokens at quality 95 vs grep+read's
~528 tokens at quality 75 — *and* it surfaced a guarding test that calls the caller, which
grep can't see. The graph (impact/edit_plan), not retrieval, is where the token win lives.

| You know… | Use | Not |
|---|---|---|
| **A symbol you're about to edit** (signature/behavior change) | `repo_impact` / `repo_edit_plan` **first** | editing blind, then search+get for callers |
| **An exact symbol name** | `repo_symbol` | a fuzzy `repo_search` |
| **Only the intent** ("where do we X?") | `repo_search` (cards) | grep guesses |
| **About to *edit* but only have the intent** | `repo_brief` (likely targets + verified impact in one call) | guessing the target, then editing blind |
| **Nothing yet — just *orienting/reading*** | `repo_context_pack` (or skim `repo_map`) | reading files top-down |
| **A chosen ref, and you need the code** | `repo_get` (after narrowing) | fetching every card |

Two encoded lessons:

- **Impact-first before any non-local edit.** Don't reach for `repo_search` to find
  callers — `repo_impact(ref)` / `repo_edit_plan(ref)` give the blast radius, tests, and a
  fetch order in one call. `search`+`get` *lost* to grep on token count; impact *won*.
- **Cards and signatures before bodies.** Read the card (ref + summary + tags), then fetch
  `view="signature"` to confirm shape, and only pull the full body for the symbol you'll
  actually edit. Whole bodies you only need to *confirm* are wasted tokens.

- **`repo_brief` is the one-call pre-flight when you have the intent but not the target.**
  It hard-separates a **HEURISTIC** block (task interpretation + ranked likely targets +
  likely call flow — *guesses* from search) from a **VERIFIED** block (impact, confident
  callers, related tests, staleness, risks — *graph facts* about the one anchored target),
  and stamps an anchor-confidence tier. The verified facts are always *about the anchor
  symbol*, **not proof it's the right target** — read the tier: **HIGH** = the anchor is as
  certain as the tool gets (edit-ready); **MEDIUM** = the verified facts still show, but
  confirm the target (`repo_get`/`repo_symbol`) before relying on them; **LOW** = the
  verified block is **withheld** on purpose and you're pointed at disambiguation. Honor that
  refusal rather than forcing an edit; a confident-wrong brief is worse than no brief. Treat
  the HEURISTIC block as leads to confirm with `repo_get`; once the target is known, drop to
  `repo_edit_plan(ref)` for the focused plan.

### grep is still the baseline — but only for a *distinctive* token

grep wins when you know the **exact, distinctive** string — a unique error message, a rare
constant, a one-of-a-kind symbol name. The rule is *distinctive*, not merely *exact*: grep
floods on a known-but-overloaded token (`size`, `get`), a short token, or a substring
(`bleed` inside `disbleed`, `pulse` inside `impulse`). When the token is common, short, or
a substring — or you're mapping a blast radius rather than locating one line — use the
graph/cards instead. Reach for grep to **confirm** a specific unverified caller, not to
discover them.

## The loop

1. **Classify the task.** Trivial single-file edit you can already see → just do it,
   skip this skill. Otherwise continue.
2. **Resume / check the session ledger first.** Continuing earlier work? `repo_session`
   (action="resume") renders the prior session with its confirmed facts re-validated against
   the *current* code (anchor-unchanged vs ⚠ STALE) — believed-then, render-only. Within a
   session use action="get". Don't re-search or re-fetch something already there. When you
   `note` a confirmed fact, **anchor it with `ref=`** so a later resume can flag it if that
   code changes (unanchored facts are forever "unverifiable").
3. **Search for cards** — `repo_search("<intent>")`. Read the refs + summaries + tags.
   Do NOT fetch code yet.
4. **Fetch exact code for the few refs that matter** — `repo_get(ref)`, default
   `expand="exact"`; widen to `neighbors` / `parent` / `file` only when the symbol
   alone is insufficient. Budget: 1–3 fetches to start, ≤5 before you edit.
5. **Build a compact working context** — the task, the relevant refs (with why each
   matters), the fetched code, the likely edit targets, open questions. This is far
   smaller than the files.
6. **Edit**, then **verify impact** — re-search for callers / tests / config / docs,
   and `repo_changed` any refs you fetched earlier to make sure they're still current.

## Hard rules (do / don't)

- **Do** lead with `repo_search`; **don't** read whole files as the default move.
- **Do** fetch exact code by `ref`; **don't** trust a summary as truth — confirm in the
  code via `repo_get`.
- **Do** prefer exact symbol matches over fuzzy semantic ones.
- **Don't** re-run a search or re-fetch a ref already in the ledger (`repo_session`).
- **Don't** use `path:line-range` as a durable identity — use `path::Qualified.Name`
  refs (they survive edits; `repo_get` re-resolves them live).
- **Don't** assume a symbol is current if its file changed after indexing — check
  `repo_changed`. If stale, `repo_reindex_changed` then re-fetch.
- **Don't** edit before you've identified the source-of-truth symbol + its dependencies
  + its tests.

## Retrieval budget

| Stage | Budget |
|---|---|
| initial cards reviewed | 10–20 |
| initial fetches | 1–3 |
| fetched refs before editing | ≤5 (more only with reason) |
| whole-file fetch | only when justified |
| per sub-agent | ≤3–5 fetches |

The goal is **enough code to be correct, not so much that you lose focus.**

## Going deeper

- Tool-by-tool rules and budgets → `retrieval-protocol.md`
- Before changing a central symbol (impact, callers, tests, confidence tiers) → `impact-protocol.md`
- Delegating to parallel agents (task capsules + roles) → `parallel-agent-protocol.md`
- Preserving what you found across a long session → `session-ledger-protocol.md`
- Refs, `repo_get` expand modes, staleness → `ref-rules.md`
- Did retrieval actually help? → `eval-rubric.md`
