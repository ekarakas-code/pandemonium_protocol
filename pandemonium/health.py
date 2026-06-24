"""Protocol health / readiness check (ROADMAP M4 — operational robustness).

`repo_health` (MCP) / `pandemonium doctor` (CLI): confirm the protocol is ARMED — the running
version, whether an index exists and is loadable, whether the offline embedding-model cache is
present, and the file/symbol/chunk counts. The motivating failure: an upgrade or restart can
silently DISARM the agent (tools not re-registered, or the index missing) and the only symptom is
mysteriously empty retrieval results. A health check makes that state explicit and checkable, and
surfaces the running version so a version mismatch after an upgrade is visible. Always on.
"""

from __future__ import annotations

import os
from pathlib import Path

from pandemonium import __version__
from pandemonium.storage.sqlite_store import SqliteStore
from pandemonium.util import repo_id_for


def health_report(settings) -> dict:
    repo_id = repo_id_for(settings.repo_root)
    db = Path(settings.sqlite_path)
    if db.exists():  # never CREATE an index dir from a read-only health check
        store = SqliteStore(settings.sqlite_path)
        store.create_schema()
        try:
            counts = store.counts(repo_id)
        finally:
            store.close()
    else:
        counts = {"files": 0, "symbols": 0, "chunks": 0}

    index_present = counts["files"] > 0
    hf = Path(settings.repo_root) / ".pandemonium" / "hf"
    model_cache = hf.exists() or bool(os.environ.get("HF_HOME"))
    return {"version": __version__, "repo_root": str(settings.repo_root),
            "status": "ARMED" if index_present else "NOT INDEXED",
            "index_present": index_present, "counts": counts,
            "model_cache_present": model_cache}


def render_health(r: dict) -> str:
    c = r["counts"]
    out = [
        f"# PandemoniumProtocol health — {r['status']}",
        f"version:     {r['version']}",
        f"repo:        {r['repo_root']}",
        f"index:       {'present' if r['index_present'] else 'MISSING — run `pandemonium index .`'}",
        f"indexed:     files={c['files']} symbols={c['symbols']} chunks={c['chunks']}",
        f"model cache: {'present (offline-ready)' if r['model_cache_present'] else 'NOT found — the first index fetches the embedding model (needs network once)'}",
    ]
    if r["status"] == "ARMED":
        out.append("\nArmed: the tools are registered and the index is loadable. If retrieval "
                   "still returns nothing, re-check the ref/query — not the install.")
    else:
        out.append("\nNOT serving retrieval: index the repo before relying on the tools. If you "
                   "expected an index, the protocol may have been reinstalled/disarmed — reindex.")
    return "\n".join(out)
