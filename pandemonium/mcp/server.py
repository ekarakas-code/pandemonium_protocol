"""FastMCP stdio server exposing PandemoniumProtocol's repo_* tools."""

from __future__ import annotations


def build_server(settings, embedder=None):
    """Construct the FastMCP server and register tools (no transport started).
    Split out from `serve` so the tool surface can be verified without blocking.
    `embedder` is an optionally pre-warmed LocalEmbedder reused by the ToolContext (see
    `serve` — it is loaded single-threaded before the event loop to dodge a native-import
    deadlock)."""
    from mcp.server.fastmcp import FastMCP

    from pandemonium.mcp.tools import ToolContext

    ctx = ToolContext(settings, embedder=embedder)
    mcp = FastMCP("pandemonium")

    @mcp.tool()
    def repo_map(mode: str = "default") -> str:
        """Project map. mode='default' is a quick orientation (stack, folders, entry
        points, important files). Focused modes answer one question without reading code:
        'architecture' (areas × language), 'entrypoints' (CLI/route/handler symbols),
        'domains' (heuristic domain groups), 'tests' (test files), 'changed' (files that
        drifted from the index)."""
        return ctx.repo_map(mode=mode)

    @mcp.tool()
    def repo_search(query: str, top_k: int = 10, mode: str = "") -> str:
        """Hybrid search (symbol + keyword + vector) over the indexed repository.
        Returns ranked files/symbols with line ranges. Prefer exact symbol results.
        Optional mode re-ranks (weights only): 'impact' (favour the exact symbol you'll
        edit), 'discovery' (favour semantic recall for vague intent), 'bugfix' (favour
        literal keyword/error tokens). EXPERIMENTAL presets — only 'discovery' has
        (single-type) evidence; 'impact'/'bugfix' are unvalidated. Omit for the tuned default."""
        return ctx.repo_search(query, top_k=top_k, mode=mode)

    @mcp.tool()
    def repo_symbol(symbol_name: str) -> str:
        """Resolve a symbol by name to its file, line range, signature, and summary."""
        return ctx.repo_symbol(symbol_name)

    @mcp.tool()
    def repo_get(ref: str, expand: str = "exact", view: str = "full") -> str:
        """Fetch exact code for a stable ref from repo_search (e.g. `path::Qualified.Name`).
        Edit-stable: symbol refs are re-found by name in the live file. expand widens =
        exact | neighbors | file | parent (default exact; widen only when needed). A partial
        ast_block child auto-upgrades to its complete parent symbol — pass expand="block" to
        get just the raw block instead (flagged not-safe-to-reason-from-alone). view narrows
        to save tokens = full | signature | head:N | lines:a-b — use 'signature' when you only
        need a symbol's shape, not its body."""
        return ctx.repo_get(ref, expand=expand, view=view)

    @mcp.tool()
    def repo_context_pack(task: str, token_budget: int = 4000, mode: str = "") -> str:
        """Primary agent tool: a token-budgeted context pack for a coding task —
        relevant files, symbols, line ranges, summaries, tests, and inspection order.
        Optional mode re-ranks (weights only): 'impact' / 'discovery' / 'bugfix' —
        EXPERIMENTAL presets (only 'discovery' has single-type evidence; 'impact'/'bugfix'
        unvalidated); omit for the tuned default."""
        return ctx.repo_context_pack(task, token_budget=token_budget, mode=mode)

    @mcp.tool()
    def repo_prompt_context(task: str, token_budget: int = 4000, mode: str = "") -> str:
        """Alias of repo_context_pack."""
        return ctx.repo_context_pack(task, token_budget=token_budget, mode=mode)

    @mcp.tool()
    def repo_find_tests(target: str) -> str:
        """Find tests related to a symbol, file, or task."""
        return ctx.repo_find_tests(target)

    @mcp.tool()
    def repo_reindex_changed() -> str:
        """Incrementally reindex changed files (the only write tool)."""
        return ctx.repo_reindex_changed()

    @mcp.tool()
    def repo_graph(ref: str, evidence: bool = False) -> str:
        """Related code for a ref: who it calls (callees), who calls it (callers),
        imports, inheritance, members, and tests. Start from a ref you got from
        repo_search / repo_symbol — use it to pull *related* code without reading files.
        Each edge shows ~confidence; pass evidence=true for the resolution evidence behind
        each (receiver `this`→class, qualified scope, name-collision) so you can tell a
        verified caller from a possible one. Unverified edges carry a one-shot grep to
        confirm them."""
        return ctx.repo_graph(ref, evidence=evidence)

    @mcp.tool()
    def repo_impact(ref: str) -> str:
        """What may break if `ref` changes: directly + transitively affected callers,
        the files they live in, and tests to run. Conservative (only confidently-resolved
        callers) — use before editing a widely-used symbol."""
        return ctx.repo_impact(ref)

    @mcp.tool()
    def repo_edit_plan(ref: str) -> str:
        """Before editing a symbol, get a ranked change plan: the primary target, direct
        callers to keep compatible, tests to update, dependencies to read, coupling
        hypotheses to verify, risks, and a suggested fetch order. Composes repo_impact +
        repo_graph + repo_find_tests — use it instead of editing blind."""
        return ctx.repo_edit_plan(ref)

    @mcp.tool()
    def repo_logic_map(topic: str) -> str:
        """Conceptual flow for a topic/concept: the relevant symbols, the domains and
        files they live in, and how they call each other. Use to understand how a
        feature/concept works across the codebase before diving in."""
        return ctx.repo_logic_map(topic)

    @mcp.tool()
    def repo_brief(task: str) -> str:
        """Pre-flight brief for a task — the one-call way to START. Returns, HARD-SEPARATED:
        a HEURISTIC block (task interpretation, ranked likely targets, likely call flow —
        GUESSES from search) and a VERIFIED block (impact, confident callers, related
        tests, staleness, risks — graph facts about the one anchored target). Carries an
        anchor-confidence tier; at LOW confidence it WITHHOLDS the verified block and tells
        you how to disambiguate, because a confident-wrong brief is worse than none. Use it
        before editing when you only have the intent; it composes repo_search + repo_edit_plan."""
        return ctx.repo_brief(task)

    @mcp.tool()
    def repo_session(action: str = "get", field: str = "", value: str = "",
                     ref: str = "") -> str:
        """Session memory ledger. action='get' returns what this session already
        searched / fetched / found — including graph edges auto-recorded by
        repo_graph/repo_impact (confirmed_edges) and stale refs (stale_refs). action='note'
        with field + value records a fact (field: confirmed_facts | open_questions |
        agent_findings | invalidated_assumptions | edited_files | rejected_edges); pass
        ref='path::Symbol' to ANCHOR a fact to the code it rests on, so a later resume can
        flag it when that code changes. action='resume' renders the most recent PRIOR session
        — last task, refs inspected, confirmed facts RE-VALIDATED against the current code
        (anchor-unchanged vs STALE), touched files now out of sync, and a recommended next.
        Resume facts are 'believed-then', NOT re-verified — leads to re-confirm, not truth.
        Check the ledger before searching broadly; don't re-fetch a ref already in it."""
        return ctx.repo_session(action, field=field, value=value, ref=ref)

    @mcp.tool()
    def repo_changed(refs: str = "") -> str:
        """Staleness check: are the files behind these refs (space/comma-separated; empty
        = all indexed files) changed since indexing? A fetched symbol may be out of date
        if its file changed after the last index."""
        return ctx.repo_changed(refs)

    return mcp


def serve(settings) -> None:
    import time

    from pandemonium.embeddings.local_embedder import LocalEmbedder
    from pandemonium.logging.trace import trace

    trace(f"MCP server starting (repo={settings.repo_root})")
    # Warm the embedding stack NOW — single-threaded, before the asyncio event loop and
    # LanceDB's background event-loop thread exist. The first lazy `import sentence_transformers`
    # pulls a native chain (sklearn -> scipy.special .pyd) that deadlocks on the Windows
    # loader lock when it races those background threads — it froze repo_brief/repo_search
    # indefinitely (incident 2026-06-20). Doing it here also makes the first embedding call
    # instant. The warmed embedder is handed to the server so it is reused (no second load).
    embedder = LocalEmbedder.from_settings(settings)
    t0 = time.perf_counter()
    try:
        trace("warming embedding model (single-threaded, pre-serve)…")
        embedder._load()
        trace(f"embedding model ready in {time.perf_counter() - t0:.1f}s")
    except Exception as e:  # never block startup on warm-up; tools fall back to lazy load
        trace(f"embedding warm-up failed ({e!r}); continuing — will load lazily")
    trace("serving (stdio)")
    build_server(settings, embedder=embedder).run(transport="stdio")
