"""Heuristic project map (repo_map / `pandemonium map`).

Cheap derivation from the indexed `files`/`chunks` tables. The default mode is a quick
orientation (stack, folders, entry points, important files). Focused modes answer a
specific question without re-reading code:

    architecture | entrypoints | domains | tests | changed

All modes run on already-indexed data — no new indexing. (Richer architecture summaries
remain a later phase.)
"""

from __future__ import annotations

import json
from collections import Counter

from pandemonium.retrieval.tests_finder import is_test_path
from pandemonium.util import repo_id_for

MODES = ("default", "architecture", "entrypoints", "domains", "tests", "changed")

_IMPORTANT = ("readme", "pyproject.toml", "setup.py", "setup.cfg", "package.json",
              "requirements.txt", "dockerfile", "makefile", "pandemonium.yaml",
              "cargo.toml", "go.mod", ".csproj", ".sln")
_ENTRY_HINTS = ("main.py", "__main__.py", "app.py", "/cli/", "/server/", "manage.py",
                "index.js", "index.ts", "program.cs")


def _is_important(path: str) -> bool:
    base = path.lower().rsplit("/", 1)[-1]
    return any(k in base for k in _IMPORTANT)


def _looks_entry(path: str) -> bool:
    low = path.lower()
    return any(h in low for h in _ENTRY_HINTS)


def _parse_tags(raw):
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _top_folder(path: str) -> str:
    return path.split("/")[0] if "/" in path else "(root)"


# --- mode builders -----------------------------------------------------------
def _build_default(settings, sqlite, repo_id) -> dict:
    files = sqlite.files(repo_id)
    languages = Counter(f["language"] for f in files if f["language"])
    folders: Counter = Counter()
    for f in files:
        folders[_top_folder(f["path"])] += 1
    return {
        "mode": "default",
        "name": settings.project_name,
        "root": str(settings.repo_root),
        "languages": languages.most_common(),
        "counts": sqlite.counts(repo_id),
        "folders": folders.most_common(20),
        "important_files": [f["path"] for f in files if _is_important(f["path"])][:15],
        "entry_points": [f["path"] for f in files if _looks_entry(f["path"])][:10],
    }


def _build_architecture(settings, sqlite, repo_id) -> dict:
    """Top-level areas (folders) × language mix — the shape of the codebase."""
    tree: dict = {}
    for f in sqlite.files(repo_id):
        tree.setdefault(_top_folder(f["path"]), Counter())[f["language"] or "?"] += 1
    areas = sorted(
        ({"area": folder, "files": sum(c.values()), "languages": c.most_common()}
         for folder, c in tree.items()),
        key=lambda a: -a["files"])
    return {"mode": "architecture", "name": settings.project_name,
            "root": str(settings.repo_root), "areas": areas,
            "counts": sqlite.counts(repo_id)}


def _build_entrypoints(settings, sqlite, repo_id) -> dict:
    """Symbols tagged as entry points (CLI command, API route, handler, main, ...),
    grouped by entry-point kind."""
    groups: dict = {}
    for row in sqlite.chunk_tags(repo_id):
        anchor = row["ref"] or row["path"]
        for label in _parse_tags(row["tags"]).get("entrypoints", []):
            bucket = groups.setdefault(label, [])
            if anchor not in bucket:
                bucket.append(anchor)
    out = sorted(({"kind": k, "refs": sorted(v)} for k, v in groups.items()),
                 key=lambda g: -len(g["refs"]))
    return {"mode": "entrypoints", "name": settings.project_name,
            "root": str(settings.repo_root), "groups": out}


def _build_domains(settings, sqlite, repo_id) -> dict:
    """Heuristic domains (from the `domain` tag — path-derived), by symbol count."""
    counter: Counter = Counter()
    for row in sqlite.chunk_tags(repo_id):
        for d in _parse_tags(row["tags"]).get("domain", []):
            counter[d] += 1
    return {"mode": "domains", "name": settings.project_name,
            "root": str(settings.repo_root), "domains": counter.most_common(25)}


def _build_tests(settings, sqlite, repo_id) -> dict:
    """Test files (by path convention) and their language mix."""
    files = sqlite.files(repo_id)
    test_files = sorted(f["path"] for f in files if is_test_path(f["path"]))
    by_lang = Counter(f["language"] for f in files
                      if is_test_path(f["path"]) and f["language"])
    return {"mode": "tests", "name": settings.project_name,
            "root": str(settings.repo_root), "test_files": test_files,
            "by_language": by_lang.most_common(), "total_files": len(files)}


def _build_changed(settings, sqlite, repo_id) -> dict:
    """Files that drifted from the index (changed / missing since last index)."""
    from pandemonium import service  # local import: service imports this module
    rows = service.staleness(settings, None)
    changed = [{"path": r["path"], "state": r["state"]}
               for r in rows if r["state"] != "current"]
    return {"mode": "changed", "name": settings.project_name,
            "root": str(settings.repo_root), "changed": changed, "checked": len(rows)}


_BUILDERS = {
    "default": _build_default,
    "architecture": _build_architecture,
    "entrypoints": _build_entrypoints,
    "domains": _build_domains,
    "tests": _build_tests,
    "changed": _build_changed,
}


def build_repo_map(settings, sqlite, mode: str = "default") -> dict:
    repo_id = repo_id_for(settings.repo_root)
    builder = _BUILDERS.get(mode, _build_default)
    return builder(settings, sqlite, repo_id)


# --- renderers ---------------------------------------------------------------
def _render_default(m: dict) -> str:
    lines = [f"# Repo Map: {m['name']}", f"Root: {m['root']}", "", "## Stack"]
    if m["languages"]:
        lines += [f"- {lang}: {n} files" for lang, n in m["languages"][:8]]
    else:
        lines.append("- (nothing indexed yet — run `pandemonium index .`)")
    c = m["counts"]
    lines.append(f"\nFiles: {c['files']} | Symbols: {c['symbols']} | Chunks: {c['chunks']}")
    lines.append("\n## Top Folders")
    lines += [f"- {folder}/ ({n})" for folder, n in m["folders"][:12]]
    if m["entry_points"]:
        lines.append("\n## Likely Entry Points")
        lines += [f"- {p}" for p in m["entry_points"]]
    if m["important_files"]:
        lines.append("\n## Important Files")
        lines += [f"- {p}" for p in m["important_files"]]
    return "\n".join(lines)


def _render_architecture(m: dict) -> str:
    c = m["counts"]
    out = [f"# Architecture: {m['name']}", f"Root: {m['root']}",
           f"\nFiles: {c['files']} | Symbols: {c['symbols']} | Chunks: {c['chunks']}",
           "\n## Areas (top-level folders)"]
    if not m["areas"]:
        out.append("- (nothing indexed yet — run `pandemonium index .`)")
    for a in m["areas"]:
        langs = ", ".join(f"{lang} {n}" for lang, n in a["languages"])
        out.append(f"- {a['area']}/ — {a['files']} files ({langs})")
    return "\n".join(out)


def _render_entrypoints(m: dict) -> str:
    out = [f"# Entry points: {m['name']}", f"Root: {m['root']}"]
    if not m["groups"]:
        out.append("\n- (none detected — entry-point tags are heuristic)")
    for g in m["groups"]:
        out.append(f"\n## {g['kind']} ({len(g['refs'])})")
        out += [f"- {r}" for r in g["refs"][:30]]
    return "\n".join(out)


def _render_domains(m: dict) -> str:
    out = [f"# Domains: {m['name']}", f"Root: {m['root']}", "",
           "## Domains (heuristic, by symbol count)"]
    if not m["domains"]:
        out.append("- (none — index first)")
    out += [f"- {d} ({n})" for d, n in m["domains"]]
    return "\n".join(out)


def _render_tests(m: dict) -> str:
    out = [f"# Tests: {m['name']}", f"Root: {m['root']}",
           f"\n{len(m['test_files'])} test file(s) of {m['total_files']} indexed"]
    if m["by_language"]:
        out.append("\n## By language")
        out += [f"- {lang}: {n}" for lang, n in m["by_language"]]
    out.append("\n## Test files")
    out += [f"- {p}" for p in m["test_files"][:50]] or ["- (none found)"]
    return "\n".join(out)


def _render_changed(m: dict) -> str:
    out = [f"# Changed since index: {m['name']}", f"Root: {m['root']}",
           f"\n{len(m['changed'])} of {m['checked']} indexed file(s) drifted"]
    if not m["changed"]:
        out.append("\nAll indexed files are current.")
    else:
        out.append("")
        out += [f"- {c['path']}  [{c['state']}]" for c in m["changed"][:50]]
        out.append("\n_Reindex with `pandemonium index .` (or repo_reindex_changed)._")
    return "\n".join(out)


_RENDERERS = {
    "default": _render_default,
    "architecture": _render_architecture,
    "entrypoints": _render_entrypoints,
    "domains": _render_domains,
    "tests": _render_tests,
    "changed": _render_changed,
}


def render_repo_map(m: dict) -> str:
    return _RENDERERS.get(m.get("mode", "default"), _render_default)(m)
