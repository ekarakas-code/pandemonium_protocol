"""Per-call usage logging + stats for Pandemonium tools.

Records ONE row per tool call (MCP server and CLI) into a ``tool_calls`` table in the
index DB (``storage.sqlite_path``): timestamp, surface, session, repo, tool, the question
(primary input), an answer preview, request/response token spend (tiktoken — the same
``cl100k_base`` counter used for context-pack budgeting), latency, and ok/error.

The table is owned here (created lazily via ``CREATE TABLE IF NOT EXISTS``), independent of
the index schema, so it works on a repo indexed before this feature without a reindex. It
shares the index DB *file* but writes via its own short-lived connection per insert, which
sidesteps thread-affinity / connection-reset / reindex-write-lock concerns — the robust
choice for infrequent, agent/human-paced calls.

Best-effort throughout — like :class:`AuditLog`, a logging failure must NEVER break the tool
call it records. Inspect with ``pandemonium stats`` / ``pandemonium logs``.
"""

from __future__ import annotations

import inspect
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pandemonium.tokens.counter import TokenCounter
from pandemonium.util import now_iso, repo_id_for

# Priority order for picking the "question" (primary input) from a tool's bound args.
_QUESTION_PARAMS = ("query", "task", "ref", "target", "topic",
                    "symbol_name", "refs", "action", "mode")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    surface      TEXT    NOT NULL,
    session_id   TEXT    NOT NULL,
    repo_id      TEXT    NOT NULL,
    repo         TEXT,
    tool         TEXT    NOT NULL,
    question     TEXT,
    inputs_json  TEXT,
    req_tokens   INTEGER,
    resp_tokens  INTEGER,
    resp_chars   INTEGER,
    resp_preview TEXT,
    ms           REAL,
    ok           INTEGER,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_repo    ON tool_calls(repo_id, ts);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool    ON tool_calls(tool);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
"""

_INSERT = (
    "INSERT INTO tool_calls "
    "(ts,surface,session_id,repo_id,repo,tool,question,inputs_json,"
    " req_tokens,resp_tokens,resp_chars,resp_preview,ms,ok,error) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _connect(path: Any) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def _extract_inputs(raw: Callable, args: tuple, kwargs: dict):
    """Recover named args from a wrapped (bound) tool method and pick the question.

    Returns ``(question, inputs)`` where ``question`` is the primary input string (first
    present/non-empty of :data:`_QUESTION_PARAMS`) and ``inputs`` is every other bound arg.
    """
    try:
        bound = inspect.signature(raw).bind(*args, **kwargs)
        bound.apply_defaults()
        named = dict(bound.arguments)
    except Exception:
        named = dict(kwargs)
    named.pop("self", None)
    question, qkey = "", None
    for key in _QUESTION_PARAMS:
        val = named.get(key)
        if isinstance(val, str) and val.strip():
            question, qkey = val, key
            break
    inputs = {k: v for k, v in named.items() if k != qkey}
    return question, inputs


class UsageLogger:
    """Best-effort per-call usage writer. One instance per surface/session."""

    def __init__(self, settings, surface: str, session_id: str,
                 repo_id: Optional[str] = None):
        self.settings = settings
        self.surface = surface
        self.session_id = session_id
        self.repo_id = repo_id or repo_id_for(settings.repo_root)
        self.repo = str(settings.repo_root)
        sec = settings.section("usage_logging") or {}
        self.enabled = bool(sec.get("enabled", True))
        self.capture = str(sec.get("capture_response", "preview"))
        self.preview_chars = int(sec.get("preview_chars", 200))
        self._counter: Optional[TokenCounter] = None

    # -- token counting (reuses the context-pack tokenizer) -----------------
    def _count(self, text: str) -> int:
        if not text:
            return 0
        if self._counter is None:
            tok = self.settings.section("context_pack").get("tokenizer", "cl100k_base")
            self._counter = TokenCounter(tok)
        try:
            return self._counter.count(text)
        except Exception:
            return 0

    def _preview(self, response: str) -> Optional[str]:
        if response is None or self.capture == "none":
            return None
        if self.capture == "full":
            return response
        return response[: self.preview_chars]

    # -- MCP path: binds the wrapped method's signature to recover inputs ----
    def record_call(self, tool: str, raw: Callable, args: tuple, kwargs: dict,
                    result: Any, ms: float, ok: bool = True, error: str = "") -> None:
        if not self.enabled:
            return
        try:
            question, inputs = _extract_inputs(raw, args, kwargs)
            response = result if isinstance(result, str) else (
                "" if result is None else str(result))
            self.record(tool, question, inputs, response, ms, ok=ok, error=error)
        except Exception:
            pass  # never break the tool call

    # -- low-level path: used directly by the CLI ---------------------------
    def record(self, tool: str, question: str, inputs: dict, response: str,
               ms: float, ok: bool = True, error: str = "") -> None:
        if not self.enabled:
            return
        try:
            response = response or ""
            row = (
                now_iso(), self.surface, self.session_id, self.repo_id, self.repo, tool,
                question or "",
                json.dumps(inputs or {}, ensure_ascii=False, default=str),
                self._count(question or ""), self._count(response), len(response),
                self._preview(response), round(float(ms), 2), 1 if ok else 0, error or "",
            )
            conn = _connect(self.settings.sqlite_path)
            try:
                conn.executescript(_SCHEMA)  # idempotent; works on a pre-existing DB
                conn.execute(_INSERT, row)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # best-effort: a logging failure must never surface


def cli_logger(settings) -> UsageLogger:
    """A CLI-surface logger keyed to this process (session = ``cli-<pid>``)."""
    return UsageLogger(settings, "cli", f"cli-{os.getpid()}")


def run(settings, tool: str, question: str, inputs: dict, call: Callable,
        to_text: Optional[Callable[[Any], str]] = None) -> Any:
    """Time ``call()`` (the CLI's service op), log a usage row, and return its result.

    ``to_text(result) -> str`` derives the answer text for token counting / preview; if
    omitted, a ``str`` result is used as-is and anything else logs an empty answer. Logging
    happens in ``finally`` so failures and empty results are still recorded.
    """
    log = cli_logger(settings)
    t0 = time.perf_counter()
    ok, err, result = True, "", None
    try:
        result = call()
        return result
    except Exception as e:
        ok, err = False, repr(e)
        raise
    finally:
        ms = (time.perf_counter() - t0) * 1000.0
        try:
            if not ok or result is None:
                resp = ""
            elif to_text is not None:
                resp = to_text(result)
            elif isinstance(result, str):
                resp = result
            else:
                resp = ""
        except Exception:
            resp = ""
        log.record(tool, question, inputs, resp, ms, ok=ok, error=err)


# ---------------------------------------------------------------------------
# Reading / aggregation (powers `pandemonium stats` and `pandemonium logs`)
# ---------------------------------------------------------------------------
def read_calls(settings, *, repo_id: Optional[str] = None, tool: Optional[str] = None,
               session: Optional[str] = None, surface: Optional[str] = None,
               since: Optional[str] = None, limit: Optional[int] = None) -> list:
    """Return matching ``tool_calls`` rows (most recent first) as dicts. Best-effort: an
    unreadable/absent DB yields an empty list rather than raising."""
    try:
        conn = _connect(settings.sqlite_path)
    except Exception:
        return []
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)  # tolerate a fresh DB with no table yet
        where, params = [], []
        for col, val in (("repo_id", repo_id), ("tool", tool), ("session_id", session),
                         ("surface", surface)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        if since:
            where.append("ts >= ?")
            params.append(since)
        sql = "SELECT * FROM tool_calls"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in conn.execute(sql, params)]
    except Exception:
        return []
    finally:
        conn.close()


def _percentile(sorted_vals: list, p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def aggregate(rows: list) -> dict:
    """Roll rows up per tool (count, errors, latency avg/p50/p95, token totals) plus an
    overall summary (total calls, total token spend, sessions/repos, time window)."""
    by_tool: dict = {}
    for r in rows:
        t = r.get("tool") or "?"
        b = by_tool.setdefault(t, {"tool": t, "calls": 0, "errors": 0,
                                   "_ms": [], "req_tokens": 0, "resp_tokens": 0})
        b["calls"] += 1
        if not r.get("ok"):
            b["errors"] += 1
        if r.get("ms") is not None:
            b["_ms"].append(float(r["ms"]))
        b["req_tokens"] += int(r.get("req_tokens") or 0)
        b["resp_tokens"] += int(r.get("resp_tokens") or 0)
    tools = []
    for b in by_tool.values():
        ms = sorted(b.pop("_ms"))
        b["ms_avg"] = round(sum(ms) / len(ms), 1) if ms else 0.0
        b["ms_p50"] = round(_percentile(ms, 0.50), 1)
        b["ms_p95"] = round(_percentile(ms, 0.95), 1)
        b["resp_tokens_avg"] = round(b["resp_tokens"] / b["calls"]) if b["calls"] else 0
        tools.append(b)
    tools.sort(key=lambda x: x["calls"], reverse=True)
    summary = {
        "total_calls": sum(t["calls"] for t in tools),
        "total_errors": sum(t["errors"] for t in tools),
        "total_req_tokens": sum(t["req_tokens"] for t in tools),
        "total_resp_tokens": sum(t["resp_tokens"] for t in tools),
        "sessions": sorted({r.get("session_id") for r in rows if r.get("session_id")}),
        "repos": sorted({r.get("repo") for r in rows if r.get("repo")}),
        "first_ts": min((r.get("ts") for r in rows if r.get("ts")), default=""),
        "last_ts": max((r.get("ts") for r in rows if r.get("ts")), default=""),
    }
    return {"summary": summary, "tools": tools}


def render_stats(agg: dict) -> str:
    s = agg["summary"]
    tools = agg["tools"]
    if not s["total_calls"]:
        return "No tool calls logged yet. Run some CLI commands or use the MCP server first."
    lines = [
        f"Usage stats — {s['total_calls']} calls, {s['total_errors']} error(s), "
        f"{len(s['sessions'])} session(s), {len(s['repos'])} repo(s)",
        f"Window: {s['first_ts']} … {s['last_ts']}",
        f"Token spend (protocol context cost): req={s['total_req_tokens']:,}  "
        f"resp={s['total_resp_tokens']:,}",
        "",
    ]
    hdr = (f"{'tool':<22}{'calls':>6}{'err':>5}{'ms avg':>9}{'ms p50':>9}"
           f"{'ms p95':>9}{'resp tok':>10}{'tok/call':>9}")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for t in tools:
        lines.append(
            f"{t['tool']:<22}{t['calls']:>6}{t['errors']:>5}{t['ms_avg']:>9.1f}"
            f"{t['ms_p50']:>9.1f}{t['ms_p95']:>9.1f}{t['resp_tokens']:>10,}"
            f"{t['resp_tokens_avg']:>9,}")
    return "\n".join(lines)


def render_logs(rows: list) -> str:
    if not rows:
        return "No tool calls logged yet."
    out = []
    for r in rows:
        status = "ok" if r.get("ok") else "ERR"
        out.append(
            f"{r.get('ts','')}  [{r.get('surface','')}] {r.get('session_id','')}  "
            f"{r.get('tool','')}  req={r.get('req_tokens',0)} resp={r.get('resp_tokens',0)} "
            f"{(r.get('ms') or 0):.0f}ms {status}")
        q = (r.get("question") or "").replace("\n", " ").strip()
        if q:
            out.append(f"    Q: {q[:117] + '…' if len(q) > 118 else q}")
        prev = (r.get("resp_preview") or "").replace("\n", " ").strip()
        if prev:
            out.append(f"    A: {prev[:117] + '…' if len(prev) > 118 else prev}")
        if not r.get("ok") and r.get("error"):
            out.append(f"    ! {r['error']}")
    return "\n".join(out)
