"""PandemoniumProtocol CLI (Typer)."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
import yaml
from rich.console import Console

from pandemonium import mapping, service, usage
from pandemonium.config import DEFAULTS, Settings
from pandemonium.indexer.ignore import DEFAULT_IGNORE
from pandemonium.util import repo_id_for

# Windows consoles default to cp1252; emit UTF-8 so code excerpts and typography
# (em dashes, arrows, box-drawing) in `context`/`map`/`search` output render correctly.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

app = typer.Typer(add_completion=False, help="Local-first codebase intelligence for LLM agents.")
console = Console()

_REPO_OPT = typer.Option(".", "--repo", "-r", help="Repository root.")


@app.command()
def init(path: str = typer.Argument(".", help="Repository root to initialize.")) -> None:
    """Create the .pandemonium/ data dir, a sample config, and an ignore file."""
    root = Path(path).resolve()
    (root / ".pandemonium").mkdir(parents=True, exist_ok=True)

    cfg = root / "pandemonium.yaml"
    if not cfg.exists():
        cfg.write_text("# PandemoniumProtocol configuration\n"
                       + yaml.safe_dump(DEFAULTS, sort_keys=False), encoding="utf-8")
        console.print(f"[green]created[/] {cfg.name}")
    ignore = root / ".pandemoniumignore"
    if not ignore.exists():
        ignore.write_text(DEFAULT_IGNORE, encoding="utf-8")
        console.print(f"[green]created[/] {ignore.name}")

    # Keep the local index (machine-specific, rebuilt by the tool) out of version control.
    gitignore = root / ".gitignore"
    entry = ".pandemonium/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if entry not in existing.splitlines():
        with gitignore.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("# PandemoniumProtocol local index (machine-specific, rebuilt by the tool)\n"
                     f"{entry}\n")
        console.print(f"[green]updated[/] .gitignore (+{entry})")

    console.print(f"[bold green]Initialized[/] PandemoniumProtocol at {root}")
    console.print("Next: [bold]pandemonium index .[/]")


@app.command()
def index(path: str = typer.Argument(".", help="Repository root to index."),
          full: bool = typer.Option(False, "--full",
                                    help="Re-embed all files, ignoring unchanged hashes.")) -> None:
    """Scan, parse, chunk, embed, and store the repository (incremental by default)."""
    settings = Settings.load(path)
    with console.status("Indexing (first run downloads the embedding model)..."):
        stats = service.index(settings, incremental=not full)
    console.print(
        f"[green]Indexed[/] scanned={stats.scanned} indexed={stats.indexed} "
        f"skipped={stats.skipped} deleted={stats.deleted} "
        f"symbols={stats.symbols} chunks={stats.chunks}")
    if stats.skipped_too_large:
        mb = settings.section("indexing").get("max_file_bytes", 2_000_000) / 1_000_000
        console.print(f"[yellow]warning:[/] {stats.skipped_too_large} file(s) skipped for "
                      f"exceeding max_file_bytes (~{mb:.1f} MB) — raise indexing.max_file_bytes "
                      f"to include them.")
    if stats.indexed and not stats.symbols:
        console.print("[yellow]warning:[/] 0 symbols extracted — ensure the tree-sitter "
                      "grammar for this repo's language is installed.")


@app.command("reindex-changed")
def reindex_changed(path: str = typer.Argument(".")) -> None:
    """Incrementally reindex changed files."""
    index(path=path, full=False)


@app.command()
def sync(path: str = typer.Argument(".")) -> None:
    """Alias for reindex-changed."""
    index(path=path, full=False)


@app.command()
def changed(path: str = _REPO_OPT) -> None:
    """List files that would be (re)indexed, without writing anything."""
    settings = Settings.load(path)
    ch = usage.run(settings, "changed", "", {},
                   lambda: service.detect_changes(settings),
                   lambda c: f"new={len(c['new'])} changed={len(c['changed'])} "
                             f"deleted={len(c['deleted'])} unchanged={len(c['unchanged'])}")
    for label in ("new", "changed", "deleted"):
        items = ch[label]
        console.print(f"[bold]{label}[/] ({len(items)})")
        for p in items:
            console.print(f"  {p}")
    console.print(f"[dim]unchanged: {len(ch['unchanged'])}[/]")
    too_large = ch.get("skipped_too_large") or []
    if too_large:
        console.print(f"[yellow]skipped (too large) ({len(too_large)})[/]")
        for p in too_large:
            console.print(f"  {p}")


def _tagline(tags) -> str:
    if not tags:
        return ""
    bits = []
    for key in ("side_effects", "entrypoints", "domain"):
        vals = tags.get(key) or []
        if vals:
            bits.append(f"{key}={','.join(vals)}")
    return " | ".join(bits)


@app.command()
def search(query: str, repo: str = _REPO_OPT,
           top_k: int = typer.Option(10, "--top-k", "-k"),
           mode: str = typer.Option("", "--mode",
                                    help="re-rank preset: impact | discovery | bugfix "
                                         "(weights only; EXPERIMENTAL — only discovery has "
                                         "evidence; omit for default)")) -> None:
    """Hybrid search the indexed repository — returns cards (refs + summaries, no code)."""
    settings = Settings.load(repo)
    results = usage.run(
        settings, "search", query, {"top_k": top_k, "mode": mode},
        lambda: service.search(settings, query, top_k=top_k, mode=mode or None),
        lambda rs: "\n".join(f"{r.ref or r.path} {r.summary or ''}" for r in rs))
    if not results:
        console.print("[yellow]No results. Have you run `pandemonium index .`?[/]")
        raise typer.Exit()
    for i, r in enumerate(results, 1):
        ref = r.ref or f"{r.path}:{r.start_line}-{r.end_line}"
        console.print(f"[bold]{i}.[/] [green]{ref}[/] "
                      f"[dim]{r.scope or r.chunk_type}[/] score={r.score}")
        if r.summary:
            console.print(f"    {r.summary}")
        tagline = _tagline(r.tags)
        if tagline:
            console.print(f"    [dim]{tagline}[/]")
        console.print(f"    [dim]{r.reason} · fetch: pandemonium get \"{ref}\"[/]")


@app.command()
def get(ref: str,
        expand: str = typer.Option("exact", "--expand", "-e",
                                   help="exact | neighbors | file | parent"),
        view: str = typer.Option("full", "--view", "-v",
                                  help="full | signature | head:N | lines:a-b (narrow to save tokens)"),
        repo: str = _REPO_OPT) -> None:
    """Fetch exact code for a stable ref (e.g. `path::Qualified.Name`)."""
    settings = Settings.load(repo)
    resolved = usage.run(
        settings, "get", ref, {"expand": expand, "view": view},
        lambda: service.get(settings, ref, expand=expand, view=view),
        lambda r: r.code if r else "")
    if resolved is None:
        console.print(f"[yellow]Could not resolve ref:[/] {ref}")
        raise typer.Exit(1)
    flag = " [red](stale: re-find by name failed)[/]" if resolved.stale else ""
    view_note = "" if resolved.view in ("full", None) else f" view={resolved.view}"
    console.print(f"[green]{resolved.path}:{resolved.start_line}-{resolved.end_line}[/] "
                  f"[dim]{resolved.scope} expand={resolved.expand}{view_note}[/]{flag}")
    if resolved.decl_ref:  # C++ out-of-line def: also declared in a sibling header
        console.print(f"[dim]declared in {resolved.decl_ref}[/]")
    typer.echo(resolved.code)


@app.command()
def symbol(name: str, repo: str = _REPO_OPT) -> None:
    """Resolve a symbol by name to its file and line range."""
    settings = Settings.load(repo)
    matches = usage.run(
        settings, "symbol", name, {},
        lambda: service.symbol(settings, name),
        lambda ms: "\n".join(f"{m['name']} {m['path']}:{m['start_line']}" for m in ms))
    if not matches:
        console.print(f"[yellow]No symbol matching '{name}'.[/]")
        raise typer.Exit()
    for m in matches:
        console.print(f"[bold]{m['name']}[/] [dim]{m['type']}[/] "
                      f"{m['path']}:{m['start_line']}-{m['end_line']}")
        if m["signature"]:
            console.print(f"    {m['signature']}")
        if m["summary"]:
            console.print(f"    {m['summary']}")


@app.command()
def context(task: str,
            tokens: int = typer.Option(4000, "--tokens", "-t", help="Token budget."),
            mode: str = typer.Option("", "--mode",
                                     help="re-rank preset: impact | discovery | bugfix "
                                          "(weights only; omit for default)"),
            repo: str = _REPO_OPT) -> None:
    """Build a token-budgeted context pack for a task (markdown to stdout)."""
    settings = Settings.load(repo)
    typer.echo(usage.run(
        settings, "context", task, {"tokens": tokens, "mode": mode},
        lambda: service.context_pack(settings, task, token_budget=tokens, mode=mode or None)))


@app.command()
def tests(target: str, repo: str = _REPO_OPT) -> None:
    """Find tests related to a symbol, file, or task."""
    settings = Settings.load(repo)
    found = usage.run(settings, "tests", target, {},
                      lambda: service.tests(settings, target),
                      lambda fs: "\n".join(fs))
    if not found:
        console.print("[yellow]No related tests found.[/]")
        raise typer.Exit()
    for p in found:
        console.print(f"- {p}")


@app.command()
def graph(ref: str, repo: str = _REPO_OPT,
          evidence: bool = typer.Option(
              False, "--evidence", help="show the resolution evidence behind each edge")
          ) -> None:
    """Related code for a ref: callers, callees, imports, inheritance, members, tests."""
    settings = Settings.load(repo)
    from pandemonium.graph import render_graph
    g = usage.run(settings, "graph", ref, {"evidence": evidence},
                  lambda: service.graph_for(settings, ref),
                  lambda gg: render_graph(gg, show_evidence=evidence) if gg else "")
    if g is None:
        console.print(f"[yellow]Ref not found in the graph:[/] {ref}")
        raise typer.Exit(1)
    typer.echo(render_graph(g, show_evidence=evidence))


@app.command()
def impact(ref: str, repo: str = _REPO_OPT) -> None:
    """What may break if a ref changes: callers, transitive impact, files, tests."""
    settings = Settings.load(repo)
    from pandemonium.graph import render_impact
    g = usage.run(settings, "impact", ref, {},
                  lambda: service.impact_for(settings, ref),
                  lambda gg: render_impact(gg) if gg else "")
    if g is None:
        console.print(f"[yellow]Ref not found in the graph:[/] {ref}")
        raise typer.Exit(1)
    typer.echo(render_impact(g))


@app.command()
def doctor(repo: str = _REPO_OPT) -> None:
    """Health check: version, index presence, model cache, counts — is the protocol armed?
    Run this when retrieval comes back empty to tell 'not indexed / disarmed' from a bad query."""
    settings = Settings.load(repo)
    from pandemonium.health import render_health
    typer.echo(render_health(service.health(settings)))


@app.command()
def check(target: str = typer.Argument(""), repo: str = _REPO_OPT) -> None:
    """Post-edit breakage FLOOR: call-sites whose callee was removed/renamed, changed
    signatures, dangling imports — for edits not yet indexed. Gated: retrieval.breakage_check."""
    settings = Settings.load(repo)
    from pandemonium.breakage import render_breakage
    result = usage.run(settings, "check", target, {},
                       lambda: service.breakage(settings, target or None),
                       lambda res: render_breakage(res))
    typer.echo(render_breakage(result))


@app.command()
def plan(ref: str, repo: str = _REPO_OPT) -> None:
    """Edit plan for a ref: target, callers, tests, deps, risks, and fetch order."""
    settings = Settings.load(repo)
    from pandemonium.graph import render_edit_plan
    p = usage.run(settings, "plan", ref, {},
                  lambda: service.edit_plan(settings, ref),
                  lambda pp: render_edit_plan(pp) if pp else "")
    if p is None:
        console.print(f"[yellow]Ref not found in the graph:[/] {ref}")
        raise typer.Exit(1)
    typer.echo(render_edit_plan(p))


@app.command("logic-map")
def logic_map(topic: str, repo: str = _REPO_OPT) -> None:
    """Conceptual flow for a topic: relevant symbols, domains, files, and call flow."""
    settings = Settings.load(repo)
    from pandemonium.graph import render_logic_map
    g = usage.run(settings, "logic_map", topic, {},
                  lambda: service.logic_map(settings, topic),
                  lambda gg: render_logic_map(gg) if gg else "")
    if g is None:
        console.print("[yellow]No matches for that topic.[/]")
        raise typer.Exit()
    typer.echo(render_logic_map(g))


@app.command()
def brief(task: str, repo: str = _REPO_OPT) -> None:
    """Pre-flight brief for a task: likely targets (guesses) + verified impact/tests/risks
    (graph facts), hard-separated, with an anchor-confidence tier and a next action."""
    settings = Settings.load(repo)
    from pandemonium.brief import render_brief
    b = usage.run(settings, "brief", task, {},
                  lambda: service.brief(settings, task),
                  lambda bb: render_brief(bb))
    typer.echo(render_brief(b))


@app.command()
def session(action: str = typer.Argument("resume", help="resume | get"),
            repo: str = _REPO_OPT) -> None:
    """Inspect the session ledger. 'resume' renders the most recent prior session with its
    confirmed facts RE-VALIDATED against the current code (believed-then, not re-verified);
    'get' dumps the ledger raw."""
    settings = Settings.load(repo)
    from pandemonium.session import latest_prior_ledger, render_resume
    led, mtime = latest_prior_ledger(settings, "")  # _safe_id("")=="default"; no mcp-* ledger uses that
    if led is None:
        console.print("[yellow]No session ledger found.[/]")
        raise typer.Exit()
    if action == "get":
        typer.echo(led.render())
        return
    last = None
    if mtime:
        import datetime
        last = datetime.datetime.fromtimestamp(mtime).isoformat(timespec="minutes")
    typer.echo(render_resume(settings, led, last_active=last))


@app.command()
def map(
    repo: str = _REPO_OPT,
    mode: str = typer.Option(
        "default", "--mode", "-m",
        help="default | architecture | entrypoints | domains | tests | changed"),
) -> None:
    """Print the project map. --mode focuses it: architecture (areas × language),
    entrypoints, domains, tests, or changed (files that drifted from the index)."""
    settings = Settings.load(repo)
    if mode not in mapping.MODES:
        raise typer.BadParameter(f"mode must be one of {', '.join(mapping.MODES)}")
    typer.echo(mapping.render_repo_map(
        usage.run(settings, "map", mode, {},
                  lambda: service.repo_map(settings, mode=mode),
                  lambda mm: mapping.render_repo_map(mm))))


@app.command()
def viz(
    repo: str = _REPO_OPT,
    out: str = typer.Option("pandemonium-graph.html", "--out", "-o",
                            help="Output HTML file."),
    ref: str = typer.Option(None, "--ref",
                            help="Center the view on a ref's ego network (e.g. path::Name)."),
    collapsed: bool = typer.Option(False, "--collapsed",
                                   help="Start with folders collapsed (high-level map)."),
    layout: str = typer.Option("fcose", "--layout",
                               help="Initial layout engine: fcose | cola."),
    min_confidence: float = typer.Option(0.0, "--min-confidence",
                                         help="Drop call edges below this confidence."),
    min_degree: int = typer.Option(0, "--min-degree",
                                   help="Drop symbol nodes with fewer than N edges "
                                        "(1 = hide isolated symbols; shrinks big graphs)."),
    open_browser: bool = typer.Option(True, "--open/--no-open",
                                      help="Open the result in the default browser."),
) -> None:
    """Export the relationship graph to a self-contained interactive HTML page.

    Folders and files are shown as nested boxes (cytoscape compound nodes) containing
    their symbols; call/inherit edges cross between them. Pull/push forces are adjustable
    live in the page.
    """
    settings = Settings.load(repo)
    from pandemonium import viz as viz_mod
    out_path = Path(out).resolve()
    stats = viz_mod.export(settings, out_path, min_confidence=min_confidence,
                           focus_ref=ref, collapsed=collapsed, layout=layout,
                           min_degree=min_degree)
    shown = stats.get("symbols_shown", stats["symbols"])
    sym_txt = (f"{shown}/{stats['symbols']} symbols shown"
               if shown != stats["symbols"] else f"{stats['symbols']} symbols")
    console.print(
        f"[green]Wrote[/] {out_path}  "
        f"[dim]({stats['folders']} folders · {stats['files']} files · {sym_txt} "
        f"· {stats['calls']} calls ({stats['calls_ambiguous']} ambiguous) "
        f"· {stats['inherits']} inherits · {stats['nodes']} nodes)[/]")
    if not stats["nodes"]:
        console.print("[yellow]Graph is empty — has the repo been indexed?[/]")
    if open_browser:
        import webbrowser
        webbrowser.open(out_path.as_uri())


@app.command()
def stats(repo: str = _REPO_OPT,
          since: str = typer.Option("", "--since",
                                    help="Only calls on/after this ISO ts (e.g. 2026-06-01)."),
          tool: str = typer.Option("", "--tool", help="Filter to one tool (e.g. repo_search)."),
          session: str = typer.Option("", "--session", help="Filter to one session id."),
          surface: str = typer.Option("", "--surface", help="mcp | cli."),
          as_json: bool = typer.Option(False, "--json", help="Emit the aggregate as JSON.")
          ) -> None:
    """Aggregated usage stats from the tool_calls log: per-tool call counts, latency
    (avg/p50/p95) and request/response token spend. Scope with --since/--tool/--surface."""
    settings = Settings.load(repo)
    rows = usage.read_calls(settings, repo_id=repo_id_for(settings.repo_root),
                            tool=tool or None, session=session or None,
                            surface=surface or None, since=since or None)
    agg = usage.aggregate(rows)
    if as_json:
        import json as _json
        typer.echo(_json.dumps(agg, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(usage.render_stats(agg))


@app.command()
def logs(repo: str = _REPO_OPT,
         limit: int = typer.Option(20, "--limit", "-n", help="How many recent calls to show."),
         tool: str = typer.Option("", "--tool", help="Filter to one tool."),
         session: str = typer.Option("", "--session", help="Filter to one session id."),
         surface: str = typer.Option("", "--surface", help="mcp | cli."),
         as_json: bool = typer.Option(False, "--json", help="Emit raw rows as JSON.")) -> None:
    """Recent raw tool calls from the usage log — question, answer preview, token spend,
    latency, ok/error — most recent first."""
    settings = Settings.load(repo)
    rows = usage.read_calls(settings, repo_id=repo_id_for(settings.repo_root),
                            tool=tool or None, session=session or None,
                            surface=surface or None, limit=limit)
    if as_json:
        import json as _json
        typer.echo(_json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(usage.render_logs(rows))


@app.command("serve-mcp")
def serve_mcp(repo: str = _REPO_OPT) -> None:
    """Start the MCP server (stdio) exposing repo_* tools to coding agents."""
    settings = Settings.load(repo)
    from pandemonium.mcp.server import serve
    serve(settings)


if __name__ == "__main__":
    app()
