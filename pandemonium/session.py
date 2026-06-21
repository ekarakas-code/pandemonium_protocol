"""Session ledger — what the agent has already discovered this session (Phase 7).

Persisted per session under `.pandemonium/sessions/<id>.json`. The Skill consults it
before searching broadly and avoids re-fetching a ref unless its file changed, so long
sessions stop rediscovering the same code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Optional

from pandemonium.util import now_iso

LEDGER_FIELDS = [
    "searched_queries", "returned_refs", "fetched_refs", "edited_files",
    "confirmed_facts", "open_questions", "agent_findings", "invalidated_assumptions",
    # Graph-aware memory: confirmed_edges/stale_refs are auto-recorded by the graph +
    # fetch tools; rejected_edges is agent-asserted only (never auto-filled).
    "confirmed_edges", "rejected_edges", "stale_refs",
]

# Fields that assert CODE STATE — a resume re-validates these against the current code
# (Step 7). open_questions / agent_findings are commentary and aren't re-validated. A
# ruled-out edge (rejected_edges) un-rules-out if the code changes, so it's included.
FACT_FIELDS = ("confirmed_facts", "invalidated_assumptions", "rejected_edges")


def _safe_id(session_id: str) -> str:
    return "".join(c for c in session_id if c.isalnum() or c in "-_") or "default"


def ledger_path(settings, session_id: str) -> Path:
    return settings.data_dir / "sessions" / f"{_safe_id(session_id)}.json"


def _coerce(entry) -> dict:
    """A ledger entry as {text, ref, hash, at}. Plain strings (legacy / unanchored notes)
    lift to text-only; anchored facts are already dicts. One coercion point so render/resume
    never sprinkle isinstance checks."""
    if isinstance(entry, dict):
        ref = entry.get("ref")
        return {"text": entry.get("text", ""),
                "ref": ref if isinstance(ref, str) else None,  # guard a corrupted ledger
                "hash": entry.get("hash"), "at": entry.get("at")}
    return {"text": str(entry), "ref": None, "hash": None, "at": None}


def _fmt_entry(entry) -> str:
    c = _coerce(entry)
    return c["text"] + (f"  [{c['ref']}]" if c["ref"] else "")


class SessionLedger:
    def __init__(self, path: Any, session_id: str):
        self.path = Path(path)
        self.session_id = session_id
        self.data: dict = {"session_id": session_id, "created_at": now_iso(),
                           **{f: [] for f in LEDGER_FIELDS}}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass

    @classmethod
    def open(cls, settings, session_id: str) -> "SessionLedger":
        return cls(ledger_path(settings, session_id), session_id)

    def _add(self, field: str, value: str) -> None:
        lst = self.data.setdefault(field, [])
        if value and value not in lst:
            lst.append(value)
            self.save()

    def _extend(self, field: str, values: Iterable[str]) -> None:
        """Add many values, saving once (graph tools record many edges per call)."""
        lst = self.data.setdefault(field, [])
        changed = False
        for v in values:
            if v and v not in lst:
                lst.append(v)
                changed = True
        if changed:
            self.save()

    def record_query(self, query: str) -> None:
        self._add("searched_queries", query)

    def record_returned_refs(self, refs: Iterable[str]) -> None:
        for r in refs:
            self._add("returned_refs", r)

    def record_fetch(self, ref: str) -> None:
        self._add("fetched_refs", ref)

    def record_edge(self, edge: str) -> None:
        """A confidently-resolved relationship, e.g. "A.m -> B.n" (A.m calls B.n)."""
        self._add("confirmed_edges", edge)

    def record_edges(self, edges: Iterable[str]) -> None:
        self._extend("confirmed_edges", edges)

    def record_stale(self, ref: str) -> None:
        self._add("stale_refs", ref)

    def record_stale_refs(self, refs: Iterable[str]) -> None:
        self._extend("stale_refs", refs)

    # `edited_files` is populated by the agent via add()/repo_session note — the MCP
    # server is read-only, so there is no in-process edit event to auto-hook.
    def add(self, field: str, value: str) -> None:
        if field in LEDGER_FIELDS:
            self._add(field, value)

    def record_fact(self, field: str, text: str, ref: Optional[str] = None,
                    file_hash: Optional[str] = None, at: Optional[str] = None) -> None:
        """Record a fact, optionally ANCHORED to a ref + the ref's file hash at record time,
        so a resume can tell whether the CODE the fact rests on changed since (Step 7). An
        unanchored fact is stored as a plain string (backward-compatible). Dedup by
        (text, ref)."""
        if field not in LEDGER_FIELDS or not text:
            return
        lst = self.data.setdefault(field, [])
        for e in lst:
            c = _coerce(e)
            if c["text"] == text and c["ref"] == ref:
                return
        lst.append({"text": text, "ref": ref, "hash": file_hash, "at": at or now_iso()}
                   if ref else text)
        self.save()

    def entries(self, field: str) -> List[dict]:
        return [_coerce(e) for e in (self.data.get(field) or [])]

    def already_searched(self, query: str) -> bool:
        return query in self.data.get("searched_queries", [])

    def already_fetched(self, ref: str) -> bool:
        return ref in self.data.get("fetched_refs", [])

    def render(self) -> str:
        lines = [f"# Session ledger: {self.session_id}"]
        labels = {
            "searched_queries": "Already searched",
            "fetched_refs": "Already fetched",
            "returned_refs": "Refs surfaced",
            "edited_files": "Edited files",
            "confirmed_facts": "Confirmed facts",
            "open_questions": "Open questions",
            "agent_findings": "Agent findings",
            "invalidated_assumptions": "Invalidated assumptions",
            "confirmed_edges": "Confirmed edges (graph-resolved)",
            "rejected_edges": "Rejected edges (ruled out)",
            "stale_refs": "Known-stale refs",
        }
        for field, label in labels.items():
            vals = self.data.get(field) or []
            if vals:
                lines.append(f"\n## {label} ({len(vals)})")
                lines += [f"- {_fmt_entry(v)}" for v in vals[:50]]
        return "\n".join(lines) if len(lines) > 1 else "# Session ledger: (empty)"

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1, default=str),
                                 encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Session resume (Step 7) — RENDER-ONLY, with airtight staleness.
#
# The honest reading of "verified-now vs believed-then": resume NEVER re-checks a fact's
# content, only whether the CODE it was anchored to has drifted. So EVERY resumed fact is
# believed-then — an unchanged anchor is necessary-but-not-sufficient for the fact to still
# be true (it could have been wrong when recorded). Hence there is no "verified"/"current"
# state; the strongest positive is "anchor unchanged since recorded — NOT re-verified". The
# reliable signal is the negative: a changed/missing anchor means "re-verify". Resume must
# also never copy facts forward into the new ledger (that would re-present believed-then as
# freshly confirmed — laundering the staleness this whole step exists to surface).
# ---------------------------------------------------------------------------
# Tag vocabulary — deliberately no "verified"/"current" positive claim (see above).
_RESUME_TAG = {
    "current": "· as recorded (anchor unchanged — NOT re-verified):",
    "changed": "⚠ STALE — the code this rested on changed since; re-verify:",
    "missing": "⚠ STALE — the anchored file is gone; re-verify:",
    "unanchored": "? unverifiable (no recorded baseline to re-check):",
}


def ledger_files(settings) -> List[Path]:
    d = settings.data_dir / "sessions"
    return sorted(d.glob("*.json")) if d.exists() else []


def latest_prior_ledger(settings, exclude_id: str):
    """The most-recently-modified ledger that ISN'T the current session — what `resume`
    reads. Returns (SessionLedger | None, mtime | None)."""
    cur = _safe_id(exclude_id)
    best: Optional[Path] = None
    best_m = -1.0
    for p in ledger_files(settings):
        if p.stem == cur:
            continue
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > best_m:
            best_m, best = m, p
    if best is None:
        return None, None
    return SessionLedger(best, best.stem), best_m


def _revalidate(settings, entry: dict) -> str:
    """-> 'current' | 'changed' | 'missing' | 'unanchored'. Compares the ref's file hash NOW
    to the hash stored when the fact was recorded — i.e. did the CODE the fact rests on
    change since? File-level ON PURPOSE: it over-flags (any change in the file flags the
    fact) but only in the SAFE direction — never optimize to symbol-span granularity, which
    would UNDER-flag. Reuses the indexer's own hasher so a no-op reindex shows no drift."""
    from pandemonium.indexer.hasher import read_file
    from pandemonium import refs as refs_mod

    ref = entry.get("ref")
    if not ref or entry.get("hash") is None:
        return "unanchored"
    path = refs_mod.parse_ref(ref)[0]
    read = read_file(settings.repo_root / path)
    if read is None:
        return "missing"
    return "current" if read[2] == entry["hash"] else "changed"


def _touched_stale(settings, ledger: "SessionLedger") -> List[str]:
    """Paths the session touched (fetched/returned refs, edited files, anchored fact refs)
    that are now out of sync with the index."""
    from pandemonium import refs as refs_mod
    from pandemonium import service

    d = ledger.data
    paths = set()
    for r in (d.get("fetched_refs") or []) + (d.get("returned_refs") or []):
        paths.add(refs_mod.parse_ref(r)[0])
    for f in (d.get("edited_files") or []):
        paths.add(f)
    for field in FACT_FIELDS:
        for e in ledger.entries(field):
            if e["ref"]:
                paths.add(refs_mod.parse_ref(e["ref"])[0])
    touched = sorted(p for p in paths if p)
    if not touched:
        # The session touched nothing anchorable. service.staleness([]) means "scan the
        # WHOLE repo" (repo_changed('') semantics) — passing [] here would fabricate
        # untouched files as "touched, reindex them". A resume must never invent provenance.
        return []
    rows = service.staleness(settings, touched)
    return [r["path"] for r in rows if r["stale"]]


def _resume_next(ledger: "SessionLedger", stale: List[str]) -> str:
    d = ledger.data
    bits = []
    if stale:
        bits.append(f"reindex / re-fetch {len(stale)} changed file(s) before trusting prior "
                    "refs (repo_reindex_changed)")
    oq = d.get("open_questions") or []
    if oq:
        bits.append(f"resolve open question: {_fmt_entry(oq[0])}")
    if not bits:
        bits.append("re-confirm the facts above (they are believed-then), then continue")
    return "\n".join(f"- {b}" for b in bits)


def render_resume(settings, ledger: "SessionLedger", last_active: Optional[str] = None) -> str:
    out = [f"# Resume — prior session `{ledger.session_id}`"]
    if last_active:
        out.append(f"(last active {last_active})")
    out.append("\n_Every fact below is **believed-then**: resume re-checks only the code each "
               "was anchored to, never the claim itself. An unchanged anchor is NOT "
               "re-verification — treat all of these as leads to re-confirm._")
    d = ledger.data
    q = d.get("searched_queries") or []
    if q:
        out.append("\n## Last task(s)\n" + "\n".join(f"- {x}" for x in q[-5:]))
    fetched = d.get("fetched_refs") or []
    if fetched:
        out.append(f"\n## Symbols / refs inspected ({len(fetched)})")
        out += [f"- {x}" for x in fetched[:20]]
    for field, label in (("confirmed_facts", "Confirmed facts"),
                         ("invalidated_assumptions", "Invalidated assumptions"),
                         ("rejected_edges", "Rejected edges")):
        ents = ledger.entries(field)
        if not ents:
            continue
        out.append(f"\n## {label} (anchors re-checked — not the claims)")
        for e in ents:
            out.append(f"- {_RESUME_TAG[_revalidate(settings, e)]} {e['text']}"
                       + (f"  [{e['ref']}]" if e["ref"] else ""))
    oq = d.get("open_questions") or []
    if oq:
        out.append("\n## Open questions\n" + "\n".join(f"- {_fmt_entry(x)}" for x in oq))
    stale = _touched_stale(settings, ledger)
    if stale:
        out.append(f"\n## Touched files now out of sync with the index ({len(stale)})")
        out += [f"- {p}" for p in stale[:15]]
    out.append("\n## Recommended next\n" + _resume_next(ledger, stale))
    return "\n".join(out)
