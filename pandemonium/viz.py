"""Graph visualization — export the relationship graph to a self-contained HTML page.

Rendered with **cytoscape.js compound nodes**: directory -> file -> symbol nesting shows
folder/file structure structurally (the `parent` pointer expresses containment, so no
explicit `contains` edges are needed). Call/inherit edges cross between symbol nodes.

Edge correctness reuses the project's own resolver in :mod:`pandemonium.graph` (the
`relationships` table stores edges UNRESOLVED — `target_name`, not `target_id`):

* **calls**    — `_callees_of(store, idx, sym)` per symbol -> caller->callee, once. Name
                 collisions come back separately and are rendered dashed/faded.
* **inherits** — `out_edges(sym, "inherits")` yields a base *name*; resolved to a class
                 symbol via `idx.by_name` (language-scoped).

Intra-repo by design: calls to stdlib/third-party and external base classes don't resolve
-> no edge (expected, not a gap). Read-only; never run during indexing.

Layout: **fcose** for the initial tidy paint + a "Tidy" button; **cola** (`infinite:true`)
for the live pull/push force sliders (continuous physics, compound-aware). Folders are
collapsible via cytoscape-expand-collapse.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Optional

_ASSET_DIR = Path(__file__).parent / "_assets"

# Loaded in dependency order; a shim bridges webcola's `cola` global -> `webcola` (which
# cytoscape-cola's UMD looks for). Each is inlined so the output works fully offline.
_LIB_ORDER = [
    "cytoscape.min.js",
    "layout-base.js",
    "cose-base.js",
    "cytoscape-fcose.js",
    "cola.min.js",
    "cytoscape-cola.js",
    "cytoscape-expand-collapse.js",
]
_CDN = {
    "cytoscape.min.js": "https://unpkg.com/cytoscape@3.30.3/dist/cytoscape.min.js",
    "layout-base.js": "https://unpkg.com/layout-base@2.0.1/layout-base.js",
    "cose-base.js": "https://unpkg.com/cose-base@2.2.0/cose-base.js",
    "cytoscape-fcose.js": "https://unpkg.com/cytoscape-fcose@2.2.0/cytoscape-fcose.js",
    "cola.min.js": "https://unpkg.com/webcola@3.4.0/WebCola/cola.min.js",
    "cytoscape-cola.js": "https://unpkg.com/cytoscape-cola@2.5.1/cytoscape-cola.js",
    "cytoscape-expand-collapse.js":
        "https://unpkg.com/cytoscape-expand-collapse@4.1.1/cytoscape-expand-collapse.js",
}

# Colors for folder tinting, by top-level area (directory). Extended cyclically.
_PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948",
    "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d37295",
]

# Confidence-gradient cut for the call-edge tiers — a viz-presentation choice, intentionally
# stricter than graph.py's CALLER_MIN_CONFIDENCE=0.6 binary. `resolve_call` emits a discrete
# gradient: 0.9 (this/self) · 0.8 (qualified/bare/module unique) · 0.6 (unique name, NO
# receiver) · 0.35-0.4 (collision = ambiguous). >= this = "confident" (resolved with
# receiver/scope context); below (the 0.6 band) = "possible" (a name-only match, grep to
# confirm). Tiering on the ambiguous flag alone left `possible` provably empty (verified on
# both a Python and a C++ repo — every non-ambiguous resolve is >= 0.6).
_STRONG_CONF = 0.8


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _top_area(path: str) -> str:
    p = _norm(path)
    return p.split("/", 1)[0] if "/" in p else "(root)"


def build_graph_data(settings, *, min_confidence: float = 0.0,
                     include_empty_files: bool = True,
                     min_degree: int = 0) -> dict[str, Any]:
    """Return cytoscape elements (`{nodes, edges, areas, stats}`) for the repo graph.

    Folders and files become compound (container) nodes; symbols nest inside their file.
    ``include_empty_files`` adds file/folder boxes for indexed files that carry no symbols
    (configs, docs) so the whole tree is shown, not only code-bearing files.
    ``min_degree`` drops symbol nodes with fewer than that many edges (and edges touching
    them) — the big lever for shrinking a large graph: ``1`` removes the isolated-symbol
    noise, higher values keep only well-connected hubs.
    """
    from pandemonium.graph import GraphIndex, _callees_of, _sym_ref
    from pandemonium.retrieval.tests_finder import is_test_path
    from pandemonium.storage.sqlite_store import SqliteStore
    from pandemonium.util import repo_id_for

    store = SqliteStore(settings.sqlite_path)
    store.create_schema()
    repo_id = repo_id_for(settings.repo_root)
    try:
        idx = GraphIndex(store, repo_id)
        syms = idx.by_id  # id -> symbol dict (path, language, symbol_type, ...)
        files = list(store.files(repo_id))

        areas = sorted({_top_area(s["path"]) for s in syms.values()}
                       | {_top_area(f["path"]) for f in files})
        area_color = {a: _PALETTE[i % len(_PALETTE)] for i, a in enumerate(areas)}

        nodes: dict[str, dict] = {}   # id -> cytoscape `data` dict
        edges: list[dict] = []
        degree: dict[str, int] = {}

        def ensure_dir_chain(path: str) -> Optional[str]:
            """Create folder nodes for every directory ancestor of ``path``; return the
            immediate parent dir id (or None for a repo-root file)."""
            p = _norm(path)
            if "/" not in p:
                return None
            parent: Optional[str] = None
            acc = ""
            for seg in p.split("/")[:-1]:
                acc = f"{acc}/{seg}" if acc else seg
                did = "dir::" + acc
                if did not in nodes:
                    area = acc.split("/", 1)[0]
                    d = {"id": did, "label": seg, "kind": "dir", "ntype": "dir",
                         "area": area, "color": area_color.get(area, "#888"), "path": acc,
                         "test": is_test_path(acc)}
                    if parent:
                        d["parent"] = parent
                    nodes[did] = d
                parent = did
            return parent

        def ensure_file(path: str, language: str = "") -> str:
            p = _norm(path)
            fid = "file::" + p
            if fid not in nodes:
                parent = ensure_dir_chain(p)
                area = _top_area(p)
                d = {"id": fid, "label": p.rsplit("/", 1)[-1], "kind": "file",
                     "ntype": "file", "area": area, "color": area_color.get(area, "#888"),
                     "lang": language or "", "path": p, "test": is_test_path(p)}
                if parent:
                    d["parent"] = parent
                nodes[fid] = d
            return fid

        if include_empty_files:
            for f in files:
                ensure_file(f["path"], f["language"] or "")

        def ensure_symbol(sid: str) -> None:
            if sid in nodes or sid not in syms:
                return
            s = syms[sid]
            fid = ensure_file(s["path"], s.get("language") or "")
            stype = s.get("symbol_type") or "symbol"
            lines = (f":{s['start_line']}-{s.get('end_line', s['start_line'])}"
                     if s.get("start_line") else "")
            nodes[sid] = {
                "id": sid, "label": s["name"], "parent": fid, "kind": "symbol",
                "ntype": stype, "area": _top_area(s["path"]), "ref": _sym_ref(s),
                "lang": s.get("language") or "", "path": s["path"], "lines": lines,
                "qname": s.get("qualified_name") or s["name"],
                "summary": (s.get("summary") or "")[:240], "deg": 0,
                "test": is_test_path(s["path"]),
            }
            degree.setdefault(sid, 0)

        for sid in syms:
            ensure_symbol(sid)

        def add_edge(src: str, tgt: str, etype: str, conf: float, via: str,
                     ambiguous: bool, evidence: str = "") -> None:
            if src not in nodes or tgt not in nodes or src == tgt:
                return
            # Tier the call edges so the page can separate verified graph-facts from
            # heuristic guesses (ROADMAP Step 3 / #9): confident (>= _STRONG_CONF — resolved
            # with receiver/scope context) vs possible (a 0.6 name-only match, no receiver) vs
            # ambiguous (name collision). See _STRONG_CONF for why the cut is 0.8, not 0.6.
            if etype == "inherits":
                cls = "inherits"
            elif ambiguous:
                cls = "ambiguous"
            elif conf >= _STRONG_CONF:
                cls = "calls confident"
            else:
                cls = "calls possible"
            edges.append({"data": {"id": f"e{len(edges)}", "source": src, "target": tgt,
                                   "etype": etype, "conf": round(conf, 2), "via": via,
                                   "ev": (evidence or "")[:200]},
                          "classes": cls})
            degree[src] = degree.get(src, 0) + 1
            degree[tgt] = degree.get(tgt, 0) + 1

        n_confident = n_possible = n_ambig = n_inherits = 0
        for sid, sym in syms.items():
            callees, ambiguous = _callees_of(store, idx, sym)
            for rec in callees:
                if rec["confidence"] >= min_confidence and rec["id"] in nodes:
                    add_edge(sid, rec["id"], "calls", rec["confidence"],
                             rec.get("via", ""), False, rec.get("evidence", ""))
                    if rec["confidence"] >= _STRONG_CONF:
                        n_confident += 1
                    else:
                        n_possible += 1
            for rec in ambiguous:
                if rec["confidence"] >= min_confidence and rec["id"] in nodes:
                    add_edge(sid, rec["id"], "calls", rec["confidence"],
                             rec.get("via", ""), True, rec.get("evidence", ""))
                    n_ambig += 1
            lang = sym.get("language") or ""
            for e in store.out_edges(sid, "inherits"):
                base = e["target_name"]
                hits = idx.by_name.get((lang, base)) or []
                cands = [h for h in hits if h.get("symbol_type") in
                         ("class", "struct", "interface")] or hits
                if len(cands) == 1 and cands[0]["id"] != sid:
                    add_edge(sid, cands[0]["id"], "inherits", 1.0, base, False,
                             f"base class: {base}")
                    n_inherits += 1

        for sid in syms:
            if sid in nodes:
                nodes[sid]["deg"] = degree.get(sid, 0)

        # Prune low-degree symbol nodes (and their incident edges) — the main lever for a
        # large graph. Containers (dir/file) are never pruned; an emptied file box just
        # collapses to nothing. Single pass: degrees are the pre-prune connectivity.
        if min_degree > 0:
            drop = {sid for sid in syms
                    if sid in nodes and degree.get(sid, 0) < min_degree}
            if drop:
                nodes = {nid: d for nid, d in nodes.items() if nid not in drop}
                edges = [e for e in edges if e["data"]["source"] not in drop
                         and e["data"]["target"] not in drop]

        cy_nodes = [{"data": d} for d in nodes.values()]
        n_dirs = sum(1 for d in nodes.values() if d["kind"] == "dir")
        n_files = sum(1 for d in nodes.values() if d["kind"] == "file")
        sym_nodes = [d for d in nodes.values() if d["kind"] == "symbol"]
        n_syms_shown = len(sym_nodes)
        n_syms_test = sum(1 for d in sym_nodes if d.get("test"))
        stats = {
            "symbols": len(syms), "symbols_shown": n_syms_shown,
            "symbols_test": n_syms_test, "symbols_prod": n_syms_shown - n_syms_test,
            "nodes": len(nodes), "folders": n_dirs, "files": n_files,
            # `calls` (= confident + possible) and `calls_ambiguous` are kept stable for the
            # CLI echo; the per-tier counts are additive.
            "calls": n_confident + n_possible,
            "calls_confident": n_confident, "calls_possible": n_possible,
            "calls_ambiguous": n_ambig, "inherits": n_inherits,
            "edges": len(edges), "areas": len(areas),
            "repo": settings.project_name, "repo_root": str(settings.repo_root),
        }
        return {
            "nodes": cy_nodes, "edges": edges,
            "areas": [{"name": a, "color": area_color[a]} for a in areas],
            "stats": stats,
        }
    finally:
        store.close()


def _inline_libs() -> str:
    parts: list[str] = []
    for name in _LIB_ORDER:
        p = _ASSET_DIR / name
        if p.exists():
            parts.append(f"<script>/* {name} */\n{p.read_text(encoding='utf-8')}\n</script>")
        else:  # graceful fallback if a vendored asset is missing
            parts.append(f'<script src="{_CDN[name]}"></script>')
        if name == "cola.min.js":
            # cytoscape-cola's UMD reads global `webcola`; webcola exposes `cola`.
            parts.append("<script>window.webcola=window.webcola||window.cola;</script>")
    return "\n".join(parts)


def render_html(data: dict, *, focus_ref: Optional[str] = None,
                collapsed: bool = False, layout: str = "fcose") -> str:
    """Render the graph data to a single self-contained HTML page."""
    # `<` is escaped so a stray `<`/`</script>` inside a summary or evidence string (now
    # populated from real C/Doxygen comments) can't break out of the inlined <script> block.
    payload = json.dumps({
        "nodes": data["nodes"], "edges": data["edges"],
        "areas": data["areas"], "stats": data["stats"],
        "focus": focus_ref, "collapsed": bool(collapsed),
        "layout": layout if layout in ("fcose", "cola") else "fcose",
    }, ensure_ascii=False).replace("<", "\\u003c")
    s = data["stats"]
    conf_n = s.get("calls_confident", s["calls"])
    poss_n = s.get("calls_possible", 0)
    subtitle = html.escape(
        f"{s['repo']} — {s['folders']} folders · {s['files']} files · {s['symbols']} symbols "
        f"({s.get('symbols_test', 0)} test) · {s['calls']} calls "
        f"({conf_n} confident / {poss_n} possible / {s['calls_ambiguous']} ambiguous) "
        f"· {s['inherits']} inherits")
    return (_TEMPLATE
            .replace("__LIB__", _inline_libs())
            .replace("__PAYLOAD__", payload)
            .replace("__SUBTITLE__", subtitle))


def export(settings, out_path: Any, *, min_confidence: float = 0.0,
           focus_ref: Optional[str] = None, collapsed: bool = False,
           layout: str = "fcose", min_degree: int = 0) -> dict:
    """Build + write the HTML. Returns the stats dict."""
    data = build_graph_data(settings, min_confidence=min_confidence, min_degree=min_degree)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data, focus_ref=focus_ref, collapsed=collapsed,
                               layout=layout), encoding="utf-8")
    return data["stats"]


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Pandemonium — relationship graph</title>
__LIB__
<style>
  :root { --bg:#171722; --panel:#222438; --fg:#e7e9f3; --muted:#9aa0b8; --accent:#76b7b2; }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--fg);
              font-family:'Segoe UI',system-ui,sans-serif; overflow:hidden; }
  #cy { position:absolute; inset:0; }
  #bar { position:absolute; top:0; left:0; right:0; padding:9px 16px; z-index:5;
         pointer-events:none; background:linear-gradient(#171722ee,#17172200); }
  #bar h1 { margin:0; font-size:15px; font-weight:600; }
  #bar .sub { font-size:12px; color:var(--muted); }
  .panel { position:absolute; background:var(--panel); border-radius:11px; padding:12px;
           box-shadow:0 8px 30px #0008; z-index:6; font-size:13px; }
  #panel { top:56px; left:12px; width:266px; max-height:calc(100% - 74px); overflow:auto; }
  h2 { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--muted);
       margin:15px 0 7px; } h2:first-child { margin-top:0; }
  #search { width:100%; padding:7px 9px; border-radius:7px; border:1px solid #3a3d57;
            background:#15151f; color:var(--fg); font-size:13px; }
  .row { display:flex; align-items:center; gap:7px; margin:5px 0; cursor:pointer; }
  .row input[type=checkbox]{ accent-color:var(--accent); }
  .swatch { width:11px; height:11px; border-radius:3px; flex:0 0 auto; }
  .legend { max-height:150px; overflow:auto; }
  .legend .row { font-size:12px; color:#cdd2e6; }
  button { background:#34374f; color:var(--fg); border:0; border-radius:7px; padding:6px 10px;
           font-size:12px; cursor:pointer; margin:3px 4px 0 0; } button:hover{ background:#444863; }
  .slider { width:100%; accent-color:var(--accent); margin:2px 0 0; }
  .sliderlabel { display:flex; justify-content:space-between; font-size:12px; color:#cdd2e6; }
  .count { color:var(--muted); font-size:11px; }
  #detail { bottom:12px; left:12px; width:312px; display:none; }
  #detail .ref { color:var(--accent); word-break:break-all; font-family:Consolas,monospace;
                 font-size:12px; }
  #detail .meta { color:var(--muted); margin:4px 0; } #detail .sum { margin-top:6px; line-height:1.4; }
  #hint { position:absolute; bottom:10px; right:14px; font-size:11px; color:var(--muted); z-index:6; }
  .leg2 { display:flex; flex-wrap:wrap; gap:8px 12px; font-size:11px; color:#cdd2e6; }
  .leg2 span { display:inline-flex; align-items:center; gap:5px; }
  .dotc { width:10px; height:10px; border-radius:50%; } .boxc{ width:11px;height:9px;border-radius:2px; }
  .edgc { width:15px; height:3px; border-radius:2px; flex:0 0 auto; }
</style>
</head>
<body>
<div id="cy"></div>
<div id="bar"><h1>Pandemonium — relationship graph</h1><div class="sub">__SUBTITLE__</div></div>

<div id="panel" class="panel">
  <h2>Search</h2>
  <input id="search" placeholder="symbol name… (Enter to focus)" autocomplete="off">
  <div id="searchcount" class="count"></div>

  <h2>Forces (live)</h2>
  <div class="sliderlabel"><span>Pull / attraction</span><span id="pullv">50</span></div>
  <input class="slider" type="range" id="pull" min="0" max="100" value="50">
  <div class="sliderlabel"><span>Push / repulsion</span><span id="pushv">40</span></div>
  <input class="slider" type="range" id="push" min="0" max="100" value="40">
  <div class="sliderlabel"><span>Gravity (Tidy)</span><span id="gravv">30</span></div>
  <input class="slider" type="range" id="grav" min="0" max="100" value="30">
  <label class="row"><input type="checkbox" id="physics"> Physics running (live forces)</label>
  <div><button id="tidy">Tidy</button><button id="fit">Fit</button><button id="reset">Reset</button></div>

  <h2>Folders</h2>
  <div><button id="collapseAll">Collapse all</button><button id="expandAll">Expand all</button></div>

  <h2>Edges</h2>
  <label class="row"><input type="checkbox" id="e-confident" checked> Confident calls <span class="count" id="c-confident"></span></label>
  <div class="count" style="margin:5px 0 1px">Unverified — grep to confirm:</div>
  <label class="row"><input type="checkbox" id="e-possible" checked> &nbsp;Possible <span class="count">(low conf)</span> <span class="count" id="c-possible"></span></label>
  <label class="row"><input type="checkbox" id="e-ambiguous"> &nbsp;Ambiguous <span class="count">(name collision)</span> <span class="count" id="c-ambiguous"></span></label>
  <label class="row"><input type="checkbox" id="e-inherits" checked> Inherits <span class="count" id="c-inherits"></span></label>
  <div class="sliderlabel"><span>Min call confidence</span><span id="confv">0.00</span></div>
  <input class="slider" type="range" id="conf" min="0" max="1" step="0.05" value="0">

  <h2>Code</h2>
  <label class="row"><input type="checkbox" id="hide-tests"> Hide test code</label>

  <h2>Areas <span id="areatoggle" class="count" style="cursor:pointer">(toggle all)</span></h2>
  <div id="legend" class="legend"></div>

  <h2>Legend</h2>
  <div class="leg2">
    <span><i class="dotc" style="background:#4e79a7"></i>function</span>
    <span><i class="dotc" style="background:#59a14f"></i>method</span>
    <span><i class="boxc" style="background:#b07aa1"></i>class</span>
    <span><i class="boxc" style="background:#2a2d42;border:1px solid #555"></i>file</span>
    <span><i class="dotc" style="background:#2a2d42;border:1px dashed #b9bed0"></i>test</span>
  </div>
  <div class="leg2" style="margin-top:6px">
    <span><i class="edgc" style="background:#8fd19e"></i>confident <span class="count">(verified)</span></span>
    <span><i class="edgc" style="background:#e0b25a"></i>possible</span>
    <span><i class="edgc" style="background:#7a7f95"></i>ambiguous</span>
    <span><i class="edgc" style="background:#f28e2b"></i>inherits</span>
  </div>
  <div class="count" style="margin-top:3px">possible + ambiguous = unverified (grep to confirm)</div>
</div>

<div id="detail" class="panel"></div>
<div id="hint">scroll=zoom · drag=pan · click node/edge=details · dbl-click folder=collapse · cue ⊕/⊖ on boxes</div>

<script>
const DATA = __PAYLOAD__;

// HTML-escape any data string before it goes into innerHTML — summaries/evidence/refs can
// hold `<`, `>`, `&` (C++ templates `<T>`, `operator<`, Doxygen markup).
function esc(s){ return (s==null?'':String(s)).replace(/[&<>"]/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

const cy = cytoscape({
  container: document.getElementById('cy'),
  elements: { nodes: DATA.nodes, edges: DATA.edges },
  wheelSensitivity: 0.2,
  // Large-graph render perf: hide edges + drop to a texture during pan/zoom (the bezier/
  // straight redraw of thousands of edges is what stutters), no motion blur, 1x pixels.
  hideEdgesOnViewport: true, textureOnViewport: true, motionBlur: false, pixelRatio: 1,
  style: [
    { selector:'node[kind="dir"]', style:{
        'shape':'round-rectangle','background-color':'data(color)','background-opacity':0.07,
        'border-color':'data(color)','border-width':1,'border-opacity':0.55,
        'label':'data(label)','color':'data(color)','font-size':'11px','font-weight':'bold',
        'text-valign':'top','text-halign':'left','text-margin-x':6,'text-margin-y':2,
        'padding':'16px','min-zoomed-font-size':7 } },
    { selector:'node[kind="file"]', style:{
        'shape':'round-rectangle','background-color':'#2a2d42','background-opacity':0.5,
        'border-color':'#3a3d57','border-width':1,'label':'data(label)','color':'#9aa0b8',
        'font-size':'8px','text-valign':'top','text-halign':'center','text-margin-y':-1,
        'padding':'9px','min-zoomed-font-size':6 } },
    { selector:'node[kind="symbol"]', style:{
        'background-color':'#6c8ebf','shape':'ellipse',
        'width':'mapData(deg,0,18,13,44)','height':'mapData(deg,0,18,13,44)',
        'label':'data(label)','font-size':'7px','color':'#dfe3f0','text-valign':'center',
        'text-halign':'center','min-zoomed-font-size':6,'text-max-width':'70px' } },
    { selector:'node[ntype="function"]', style:{ 'background-color':'#4e79a7' } },
    { selector:'node[ntype="method"]',   style:{ 'background-color':'#59a14f' } },
    { selector:'node[ntype="class"]',     style:{ 'background-color':'#b07aa1','shape':'round-rectangle' } },
    { selector:'node[ntype="struct"]',    style:{ 'background-color':'#b07aa1','shape':'round-rectangle' } },
    { selector:'node[ntype="interface"]', style:{ 'background-color':'#edc948','shape':'round-rectangle' } },
    // Test-path nodes (#6 prod/test split): dashed muted outline so test code reads as
    // distinct from production at a glance. `[?test]` matches the truthy flag only.
    { selector:'node[?test][kind="symbol"]', style:{ 'border-width':2,'border-style':'dashed',
        'border-color':'#b9bed0','border-opacity':0.9,'background-opacity':0.5 } },
    { selector:'node[?test][kind="file"]', style:{ 'border-style':'dashed','border-color':'#6f6a8a' } },
    { selector:'node[?test][kind="dir"]',  style:{ 'border-style':'dashed' } },
    { selector:'edge', style:{ 'curve-style':'straight','width':1,'line-color':'#5a6a90',
        'target-arrow-color':'#5a6a90','target-arrow-shape':'triangle','arrow-scale':0.7,'opacity':0.65 } },
    { selector:'edge.calls', style:{ 'line-color':'#7f9bd0','target-arrow-color':'#7f9bd0',
        'width':'mapData(conf,0,1,0.5,3.5)','opacity':0.7 } },
    // Tier overrides MUST follow `.calls` (cytoscape is last-wins): confident = solid green,
    // possible = dashed amber (unverified), ambiguous = dotted grey (name collision).
    { selector:'edge.confident', style:{ 'line-color':'#8fd19e','target-arrow-color':'#8fd19e',
        'opacity':0.85 } },
    { selector:'edge.possible', style:{ 'line-style':'dashed','line-color':'#e0b25a',
        'target-arrow-color':'#e0b25a','opacity':0.6 } },
    { selector:'edge.ambiguous', style:{ 'line-style':'dotted','line-color':'#7a7f95',
        'target-arrow-color':'#7a7f95','opacity':0.4,'width':0.6 } },
    { selector:'edge.inherits', style:{ 'line-color':'#f28e2b','target-arrow-color':'#f28e2b',
        'width':2,'opacity':0.9 } },
    { selector:'edge:selected', style:{ 'line-color':'#ffffff','target-arrow-color':'#ffffff',
        'line-style':'solid','opacity':1,'width':3,'z-index':99 } },
    { selector:'.faded', style:{ 'opacity':0.07,'text-opacity':0 } },
    { selector:'node:selected', style:{ 'border-width':3,'border-color':'#ffffff',
        'border-style':'solid','border-opacity':1 } },
    { selector:'.cy-expand-collapse-collapsed-node', style:{ 'background-opacity':0.85,
        'shape':'round-rectangle' } },
  ],
});

// ---- layout engines --------------------------------------------------------
function fcoseOpts(){ const g = +document.getElementById('grav').value;
  return { name:'fcose', quality:'default', animate:true, animationDuration:600, fit:true,
           padding:34, nodeSeparation:80, idealEdgeLength:60, nodeRepulsion:6500,
           gravity:0.05 + g*0.012, gravityRange:3.8, nestingFactor:0.1, packComponents:true,
           randomize:true, nodeDimensionsIncludeLabels:true }; }
function colaOpts(){ const pull=+document.getElementById('pull').value,
                           push=+document.getElementById('push').value;
  return { name:'cola', infinite:true, fit:false, animate:true, randomize:false,
           edgeLength: 235 - pull*1.9, nodeSpacing: 4 + push*0.9, avoidOverlap:true,
           handleDisconnected:true, convergenceThreshold:0.001, nodeDimensionsIncludeLabels:true }; }
let cola=null;
function startCola(){ stopCola(); cola=cy.layout(colaOpts()); cola.run();
  document.getElementById('physics').checked=true; }
function stopCola(){ if(cola){ cola.stop(); cola=null; } }
function tidy(){ stopCola(); document.getElementById('physics').checked=false;
  cy.layout(fcoseOpts()).run(); }

// ---- expand / collapse -----------------------------------------------------
const ec = cy.expandCollapse({
  layoutBy:{ name:'fcose', animate:true, randomize:false, fit:false, padding:24 },
  fisheye:false, animate:true, undoable:false, cueEnabled:true,
  expandCollapseCuePosition:'top-left', expandCollapseCueSize:11 });
document.getElementById('collapseAll').onclick=()=>ec.collapseAll();
document.getElementById('expandAll').onclick=()=>ec.expandAll();
cy.on('dbltap','node[kind="dir"],node[kind="file"]', e=>{ const n=e.target;
  if(ec.isCollapsible(n)) ec.collapse(n); else if(ec.isExpandable(n)) ec.expand(n); });

// ---- area legend + filtering ----------------------------------------------
const hiddenAreas=new Set();
const legend=document.getElementById('legend');
DATA.areas.forEach(a=>{ const r=document.createElement('label'); r.className='row';
  // esc() the area name (a top-level dir name — attacker-controllable when graphing an
  // untrusted repo). Entities in the data-area attr are decoded back by the parser, so
  // dataset.area still equals the raw n.data('area') and the toggle round-trip is preserved.
  r.innerHTML=`<input type=checkbox checked data-area="${esc(a.name)}">`+
    `<span class=swatch style="background:${esc(a.color)}"></span><span>${esc(a.name)||'(root)'}</span>`;
  legend.appendChild(r); });

// Edge-tier counts in the toggle labels — make the verified/unverified split transparent:
// on a Python repo `possible` is often 0 while `ambiguous` (default-off) holds the unverified
// bulk; `_callers_of` treats both as "possible callers (unverified)", so the legend groups them.
(function(){ const s=DATA.stats||{};
  const set=(id,n)=>{ const el=document.getElementById(id); if(el) el.textContent=(n==null?'':n); };
  set('c-confident', s.calls_confident); set('c-possible', s.calls_possible);
  set('c-ambiguous', s.calls_ambiguous); set('c-inherits', s.inherits); })();
legend.addEventListener('change', e=>{ const a=e.target.dataset.area; if(a===undefined) return;
  e.target.checked?hiddenAreas.delete(a):hiddenAreas.add(a); applyFilters(); });
document.getElementById('areatoggle').onclick=()=>{ const bs=legend.querySelectorAll('input');
  const anyOn=[...bs].some(b=>b.checked);
  bs.forEach(b=>{ b.checked=!anyOn; b.checked?hiddenAreas.delete(b.dataset.area)
                                            :hiddenAreas.add(b.dataset.area); }); applyFilters(); };

function edgeTypeOn(ed){ if(ed.hasClass('inherits')) return document.getElementById('e-inherits').checked;
  if(ed.hasClass('ambiguous')) return document.getElementById('e-ambiguous').checked;
  if(ed.hasClass('possible')) return document.getElementById('e-possible').checked;
  return document.getElementById('e-confident').checked; }
function applyFilters(){
  const conf=parseFloat(document.getElementById('conf').value);
  const hideTests=document.getElementById('hide-tests').checked;
  cy.batch(()=>{
    cy.nodes().forEach(n=>{ const hidden = hiddenAreas.has(n.data('area')) || (hideTests && n.data('test'));
      n.style('display', hidden?'none':'element'); });
    cy.edges().forEach(ed=>{ const ok = edgeTypeOn(ed) && (ed.data('conf')>=conf || ed.hasClass('inherits'));
      ed.style('display', ok?'element':'none'); });
  });
}
['e-confident','e-possible','e-ambiguous','e-inherits','hide-tests'].forEach(id=>
  document.getElementById(id).addEventListener('change',applyFilters));
const confEl=document.getElementById('conf');
confEl.addEventListener('input',()=>{ document.getElementById('confv').textContent=
  parseFloat(confEl.value).toFixed(2); applyFilters(); });

// ---- force sliders (live via cola) ----------------------------------------
let t=null;
function liveForces(){ clearTimeout(t); t=setTimeout(()=>startCola(),120); }
const pullEl=document.getElementById('pull'), pushEl=document.getElementById('push'),
      gravEl=document.getElementById('grav');
pullEl.addEventListener('input',()=>{ document.getElementById('pullv').textContent=pullEl.value; liveForces(); });
pushEl.addEventListener('input',()=>{ document.getElementById('pushv').textContent=pushEl.value; liveForces(); });
gravEl.addEventListener('input',()=>{ document.getElementById('gravv').textContent=gravEl.value; });
document.getElementById('physics').addEventListener('change', e=> e.target.checked?startCola():stopCola());
document.getElementById('tidy').onclick=tidy;
document.getElementById('fit').onclick=()=>cy.fit(cy.elements(':visible'),40);

// ---- hover highlight -------------------------------------------------------
cy.on('mouseover','node[kind="symbol"]', e=>{ const n=e.target;
  cy.elements().addClass('faded');
  n.closedNeighborhood().removeClass('faded'); n.ancestors().removeClass('faded'); });
cy.on('mouseout','node', ()=> cy.elements().removeClass('faded'));

// ---- details panel ---------------------------------------------------------
const detail=document.getElementById('detail');
function showDetail(n){
  if(!n || n.data('kind')!=='symbol'){ detail.style.display='none'; return; }
  const id=n.id(); let cin=0,cout=0;
  cy.edges().forEach(ed=>{ if(ed.data('target')===id)cin++; if(ed.data('source')===id)cout++; });
  const testBadge = n.data('test') ? ' · <span style="color:#e0b25a">test</span>' : '';
  detail.innerHTML=`<div class=ref>${esc(n.data('ref')||n.data('label'))}</div>`+
    `<div class=meta>${esc(n.data('ntype'))} · ${esc(n.data('lang')||'')} · ${esc(n.data('area')||'')}${testBadge}</div>`+
    `<div class=meta>${esc(n.data('path')||'')}${esc(n.data('lines')||'')}</div>`+
    `<div class=meta>callers in: ${cin} · calls out: ${cout}</div>`+
    `<div class=sum>${esc(n.data('summary')||'')}</div>`;
  detail.style.display='block';
}
// Edge click -> show the resolution evidence (#9): WHY this edge resolved at its confidence.
function showEdgeDetail(ed){
  const s=cy.getElementById(ed.data('source')), t=cy.getElementById(ed.data('target'));
  const tier = ed.hasClass('inherits')?'inherits':ed.hasClass('ambiguous')?'ambiguous'
             :ed.hasClass('possible')?'possible':'confident';
  detail.innerHTML=`<div class=ref>${esc(s.data('label'))} &rarr; ${esc(t.data('label'))}</div>`+
    `<div class=meta>${esc(ed.data('etype'))} · ${tier} · conf ${ed.data('conf')}</div>`+
    (ed.data('via')?`<div class=meta>via: ${esc(ed.data('via'))}</div>`:'')+
    `<div class=sum>${ed.data('ev')?esc(ed.data('ev')):'<i>no evidence recorded</i>'}</div>`;
  detail.style.display='block';
}
cy.on('tap', e=>{ if(e.target===cy){ detail.style.display='none';
  cy.elements().removeClass('faded'); cy.elements().unselect(); }});
cy.on('tap','node[kind="symbol"]', e=>showDetail(e.target));
cy.on('tap','edge', e=>{ cy.elements().unselect(); e.target.select(); showEdgeDetail(e.target); });

// ---- search ----------------------------------------------------------------
const search=document.getElementById('search');
function doSearch(go){ const q=search.value.trim().toLowerCase();
  const sc=document.getElementById('searchcount');
  if(!q){ sc.textContent=''; return; }
  const hits=cy.nodes('[kind="symbol"]').filter(n=>(n.data('label')||'').toLowerCase().includes(q)
    || (n.data('ref')||'').toLowerCase().includes(q));
  sc.textContent=hits.length+' match'+(hits.length===1?'':'es');
  if(go && hits.length){ cy.elements().unselect(); hits.select();
    cy.animate({ fit:{ eles:hits, padding:60 }, duration:400 }); showDetail(hits[0]); }
}
search.addEventListener('input',()=>doSearch(false));
search.addEventListener('keydown',e=>{ if(e.key==='Enter') doSearch(true); });

// ---- reset -----------------------------------------------------------------
document.getElementById('reset').onclick=()=>{ stopCola();
  hiddenAreas.clear(); legend.querySelectorAll('input').forEach(b=>b.checked=true);
  document.getElementById('e-confident').checked=true; document.getElementById('e-possible').checked=true;
  document.getElementById('e-inherits').checked=true; document.getElementById('e-ambiguous').checked=false;
  document.getElementById('hide-tests').checked=false;
  confEl.value=0; document.getElementById('confv').textContent='0.00';
  pullEl.value=50; pushEl.value=40; gravEl.value=30;
  document.getElementById('pullv').textContent='50'; document.getElementById('pushv').textContent='40';
  document.getElementById('gravv').textContent='30';
  search.value=''; doSearch(false); applyFilters(); ec.expandAll(); cy.layout(fcoseOpts()).run(); };

// ---- initial layout + focus ------------------------------------------------
function focusRef(){ if(!DATA.focus) return;
  const n=cy.nodes('[kind="symbol"]').filter(x=>x.data('ref')===DATA.focus);
  if(n.length){ const hood=n.closedNeighborhood();
    cy.elements().addClass('faded'); hood.removeClass('faded'); n.ancestors().removeClass('faded');
    n.select(); cy.animate({ fit:{ eles:hood, padding:80 }, duration:500 }); showDetail(n[0]); } }
// Set filter display states first; then for a collapsed start, fold the hierarchy BEFORE the
// main layout so fcose arranges only the top-level folder boxes (fast on a big repo) instead of
// every symbol — expanding a folder then lays out just its children on demand. Without this,
// --collapsed still paid the full-graph layout up front (the big-graph perf complaint).
applyFilters();
if(DATA.collapsed) ec.collapseAll();
const initial = cy.layout(fcoseOpts());
initial.one('layoutstop', ()=>{ focusRef(); });
initial.run();
</script>
</body>
</html>
"""
