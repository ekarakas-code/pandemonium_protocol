# Impact protocol — before you change a symbol

Editing a symbol that other code depends on is where agents do the most damage. This is
the discipline for changing *anything that isn't purely local*. It builds on the main
loop in `SKILL.md` (search cards → fetch exact → edit → verify).

> Core rule: **never change a public/central symbol until you've seen its callers and its
> tests.** `repo_impact` tells you the blast radius for ~one tool call instead of grepping.

## When this applies

- You're changing a function/method's **signature, return shape, raised errors, or
  side effects** (not just its internal body).
- The symbol is **called from more than one place**, is an entry point, or you don't
  yet know who depends on it.
- You're renaming/moving/deleting a symbol.

Skip it only for a genuinely local change (a private helper with one caller you can already
see).

## The steps

1. **Resolve the target.** Get a durable `ref` (`path::Qualified.Name`) from
   `repo_search` / `repo_symbol`. Don't run impact on a guess.
2. **Run `repo_impact(ref)`.** Read three things: *directly affected callers*, *affected
   files*, *tests to run*. This is conservative by design — it under-claims rather than
   misleads.
3. **Fetch the direct callers** with `repo_get` (start `exact`, widen to `neighbors` if a
   call site's surrounding context matters). You're checking: does my change break how
   each caller uses this symbol?
4. **Fetch the tests** (`repo_find_tests(target)` or the tests from impact). Read them
   *before* editing — they encode the contract you must preserve. The test section is
   **name-matched**, so confirm each one actually exercises the symbol.
5. **Edit.**
6. **Verify.** Re-run the tests. Then `repo_changed` on every ref you fetched (your edit
   may have moved their lines); if the index is stale, `repo_reindex_changed` and
   re-fetch before trusting any further graph result.
7. **Record.** Note `edited_files` and any `invalidated_assumptions` to the ledger
   (`repo_session(action="note", ...)`) so parallel/later agents don't redo the analysis.

## Read the confidence tiers — not every edge is equal

`repo_graph` / `repo_impact` output is tiered. Trust accordingly:

| Tier | What it is | How to treat it |
|---|---|---|
| **Certain** | direct AST `calls` / `imports` / `inherits` (high resolution `~`) | dependable; these callers really exist |
| **Likely** | by-name / ambiguous-receiver resolutions, **name-matched tests** | confirm with `repo_get` before relying on it |
| **Hypothesis** | `affects` (LLM-inferred), `similar` (vector) | **not facts.** Leads to investigate, never ground truth — confirm in code |

Specifically: treat the **Affects (LLM-inferred hypotheses)** section as "things worth
checking," and confirm each by fetching the code. They are regenerable guesses, and they
are **not** revalidated when the underlying code changes — an `affects` edge can be stale.

**Evidence on request, confirmation built in.** Every edge shows `~confidence`; pass
`evidence=true` to `repo_graph` to see *why* each resolved (`this`→class, qualified scope,
unique name, or name-collision) so you can tell a verified caller from a guess without
trusting the number. And you don't have to remember to grep: every **unverified** edge
(possible caller / ambiguous callee) prints its own one-shot `confirm: grep …` command —
run it as-is to promote a "possible" caller to a confirmed one before you rely on it.

## Two traps

- **Empty impact on an unsupported language is meaningless.** Graph edges are extracted
  for Python, C++, C#, and JS/TS. For any other language the tool prints a notice saying
  so — an empty result there means "edges never extracted," not "nothing depends on it."
  Fall back to `repo_search` for callers and read manually.
- **Impact under-claims on purpose.** Ambiguous callers are dropped to avoid compounding
  error over hops. So treat the caller list as a *floor*, not a complete set — for a
  high-stakes change, also `repo_search` the symbol name to catch dynamic/ambiguous uses.
