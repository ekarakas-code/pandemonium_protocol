"""Q&A A/B harness: vanilla Claude Code (Arm A) vs Claude Code + PandemoniumProtocol (Arm B),
measured across THREE real repos of increasing size (Python / C# / C++).

Unlike `ab_runner.py` (seeded bugs graded by a pytest suite — hardwired to THIS repo), this
harness asks repo-agnostic **code-location questions** whose correct answer (file + symbol) was
derived INDEPENDENTLY by grep (anti-circular; see the TASKS gold below). For each question it runs
an agent twice — Arm A (file tools only) and Arm B (+ pandemonium MCP + the real skill prompt),
order shuffled per question to neutralise prompt-cache bias — and captures cost / tokens / turns /
wall time from `claude -p --output-format json`. Quality is the OBJECTIVE gold-hit: did the arm
name the right file (primary) and symbol (secondary), parsed from a forced final `ANSWER:` line.

Both arms are READ-ONLY: Edit/Write/NotebookEdit/Bash/WebFetch/WebSearch/Task are disallowed, so
(1) the live repos can't be mutated by a --dangerously-skip-permissions agent and (2) Arm A can't
invoke the `pandemonium` CLI (Bash) — Arm A is protocol-free by construction, Arm B reaches the
protocol only via MCP tools. Both keep Read/Grep/Glob, so neither is crippled for code search.

Arm B speed note: every `claude -p` Arm-B run spawns a FRESH serve-mcp that warms the embedding
model (~7s) before answering — a harness artifact absent in real (warm) usage. We parse that
warm-up from stderr and report Arm B wall both raw and warm-adjusted.

Usage:
  .venv/Scripts/python.exe evals/qa_ab_runner.py --limit 1 --repeats 1   # smoke (1 task, A+B)
  .venv/Scripts/python.exe evals/qa_ab_runner.py                         # full sweep (9 tasks)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
PANDE = SRC / ".venv" / "Scripts" / "pandemonium.exe"
OUT = Path(os.environ.get("QA_OUT", r"D:\_bench"))


def _claude_exe() -> str:
    shim = shutil.which("claude")
    if shim:
        exe = (Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code"
               / "bin" / "claude.exe")
        if exe.exists():
            return str(exe)
    return "claude"


CLAUDE = _claude_exe()
AGENT_MODEL = os.environ.get("QA_AGENT_MODEL", "claude-sonnet-4-6")
MAX_TURNS = int(os.environ.get("QA_MAX_TURNS", "30") or "30")
TIMEOUT = int(os.environ.get("QA_TIMEOUT", "900") or "900")
# One-off serve-mcp embedding warm-up (s), subtracted from Arm B wall. Calibrated at startup;
# this is a per-run harness artifact (the server is persistent/warm in real usage).
WARM_S = float(os.environ.get("QA_WARM_S", "0") or 0)
# Warm-up calibration bounds. Only an explicit 'ready in <n>s' server log sets the warm-up value;
# otherwise we use WARM_S_DEFAULT (never the measured boot wall, which over-subtracts). Every value
# is clamped to WARM_S_MAX so a bad/garbage match can't drive Arm B wall_adj_s to its 0.1 floor.
# WARM_BUDGET_S also bounds how long calibration may block waiting on the server's stderr.
WARM_S_DEFAULT = 7.0
WARM_S_MAX = 20.0
WARM_BUDGET_S = 90.0

# Repos under test (absolute). Each was `index --full`-ed on 2026-06-23 against current protocol.
PP = r"D:\PandemoniumProtocol"
RV = r"D:\ERHAN_RANDEVU\randevum2"
SG = r"D:\SomeStrategyGame"

# id, repo, q, gold_files (repo-relative; scored by basename OR path), gold_symbols (leaf-matched).
# Gold derived by independent grep, NOT via the protocol (anti-circular).
TASKS = [
    {"id": "pp_fingerprint", "repo": PP,
     "q": ("In this repository a code symbol can be re-found by a structural fingerprint of its "
           "body even after the symbol has been renamed. Which function computes that body "
           "fingerprint?"),
     "gold_files": ["pandemonium/util.py"], "gold_symbols": ["fingerprint_for"]},
    {"id": "pp_embed_config", "repo": PP,
     "q": ("Where is the embedding model's name (the BAAI/bge model) and its vector dimension "
           "configured in this repository?"),
     "gold_files": ["pandemonium/config/settings.py"], "gold_symbols": ["DEFAULTS", "embedding"]},
    {"id": "pp_prod_test_split", "repo": PP,
     "q": ("Which function takes a symbol's callers/references and splits them into production "
           "versus test buckets?"),
     "gold_files": ["pandemonium/graph.py"], "gold_symbols": ["_split_prod_test"]},

    {"id": "rv_reschedule", "repo": RV,
     "q": ("When a customer moves an existing appointment to a different time slot, which method "
           "decides whether the new slot is available and cleanly handles a conflict by rolling "
           "back the move?"),
     "gold_files": ["backend/src/Randevum.Infrastructure/Booking/AppointmentRescheduleStore.cs"],
     "gold_symbols": ["ApplyAsync"]},
    {"id": "rv_slot_predicate", "repo": RV,
     "q": ("Which pure, deterministic method computes whether a single proposed appointment time "
           "slot fits inside a working-hours window and does not overlap any busy interval?"),
     "gold_files": ["backend/src/Randevum.Application/Availability/SlotMath.cs"],
     "gold_symbols": ["SlotIsOpen"]},
    {"id": "rv_waitlist_job", "repo": RV,
     "q": ("When the waitlist matcher finds that a slot has become available for a waiting "
           "customer, which method builds the notification job record that then gets enqueued?"),
     "gold_files": ["backend/src/Randevum.Infrastructure/Booking/WaitlistMatcher.cs"],
     "gold_symbols": ["BuildJob"]},

    {"id": "sg_astar", "repo": SG,
     "q": ("Where is the A* graph-search that finds the shortest walkable path between two grid "
           "cells implemented?"),
     "gold_files": ["src/spatial/AStar.cpp"], "gold_symbols": ["AStar::findPath", "findPath"]},
    {"id": "sg_world_tick", "repo": SG,
     "q": ("Which function is the per-tick orchestrator that runs all the simulation systems in "
           "order each frame (movement, combat, damage application, death, etc.)?"),
     "gold_files": ["src/ecs/World.cpp"], "gold_symbols": ["World::tick", "tick"]},
    {"id": "sg_movement", "repo": SG,
     "q": ("Where does the simulation integrate soldier velocities into new positions each tick — "
           "checking terrain passability and detecting arrival at the move target? Name the "
           "scalar source-of-truth function."),
     "gold_files": ["src/sim/systems/MovementSystem.cpp", "src/sim/systems/MovementSystem.hpp"],
     "gold_symbols": ["runMovementScalar", "runMovement"]},
]

QUESTION_SUFFIX = (
    "\n\nInvestigate the repository in your working directory, then answer concisely (2-4 "
    "sentences) naming the SINGLE file and the function/method/symbol that is the answer. End "
    "your reply with EXACTLY one line in this format, and nothing after it:\n"
    "ANSWER: file=<repo-relative path> symbol=<name>")

ARMA_SYSTEM = (
    "You are a senior engineer answering a code-location question about the repository in your "
    "working directory. Investigate efficiently using the file tools available to you (Read, "
    "Grep, Glob), locate the exact site, and be precise about the file and symbol.")

DISALLOWED = ["Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch", "Task"]


def log(msg: str) -> None:
    print(f"[qa {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


# Arm B's system prompt = the real pandemonium skill body (frontmatter stripped), computed ONCE
# at import (hoisted from the old armb_system() helper). The child reads MCP_CONFIGS[repo] (a
# per-repo file on disk, written once in main()) at launch. FIX #10.
def _armb_system():
    txt = (SRC / ".claude" / "skills" / "pandemonium" / "SKILL.md").read_text(encoding="utf-8")
    if txt.startswith("---"):
        txt = txt.split("---", 2)[-1]
    return ("PandemoniumProtocol's repo_* MCP tools are available. Follow this retrieval "
            "discipline:\n" + txt.strip())


ARMB_SYSTEM = _armb_system()
# Filled in by main() once OUT exists: {repo_abspath: mcp_config_file_path}. FIX #10.
MCP_CONFIGS = {}


def sh(cmd, cwd=None, env=None, timeout=None):
    return subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def copy_env(repo):
    # Ported from ab_runner.py: force the embedding stack OFFLINE and point HF/tiktoken at THIS
    # repo's (.pandemonium) cache so Arm B's serve-mcp can load the bge model + BPE without a
    # network call. Without this Arm B silently de-protocols offline (reindex/embed fails). The
    # caches live in SRC/.pandemonium (the protocol repo), NOT in the target `repo`. FIX #9.
    e = dict(os.environ)
    e["HF_HUB_OFFLINE"] = "1"
    e["TRANSFORMERS_OFFLINE"] = "1"
    hf = SRC / ".pandemonium" / "hf"
    tk = SRC / ".pandemonium" / "tiktoken"
    if "HF_HOME" not in e and hf.exists():
        e["HF_HOME"] = str(hf)
    if "TIKTOKEN_CACHE_DIR" not in e and tk.exists():
        e["TIKTOKEN_CACHE_DIR"] = str(tk)
    return e


def _load_stream(stdout):
    # --output-format stream-json emits one JSON object per line (JSONL), not a single object or
    # array. Parse line-by-line, skipping blanks and any unparseable line. Returns the list of
    # parsed event dicts (possibly empty).
    events = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def _count_mcp_calls(events):
    # Count assistant tool_use blocks whose tool name is in the MCP namespace ("mcp__*", e.g.
    # mcp__pandemonium__repo_search) across all stream events. Recursive walk so we don't depend
    # on the exact nesting (events -> message -> content[] -> {type:tool_use,name}); tool_result
    # blocks are a different type, so there's no double-count. FIX #8.
    n = 0

    def walk(obj):
        nonlocal n
        if isinstance(obj, dict):
            if obj.get("type") == "tool_use" and str(obj.get("name", "")).startswith("mcp__"):
                n += 1
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(events)
    return n


def _err_rec(task, arm, wall, warm, reason):
    # Well-formed CONTRACT error rec (FIX #3): excluded from every mean/ratio/delta by region A.
    # is_error True, numerics zeroed (cost/turns None), no hits, error_reason set. Same key set as
    # a normal rec so region A can render it uniformly.
    return {
        "task": task["id"], "repo": Path(task["repo"]).name, "arm": arm,
        "wall_s": wall, "warm_s": warm, "wall_adj_s": round(max(wall - warm, 0.1), 1),
        "is_error": True, "error_reason": reason, "num_turns": None, "cost_usd": None,
        "in_tok": 0, "out_tok": 0, "cache_create": 0, "cache_read": 0,
        "tot_tok": 0, "mcp_calls": 0,
        "ans_file": None, "ans_symbol": None, "parsed": False,
        "file_hit": False, "symbol_hit": False, "hit": False,
        "result": "", "stderr_tail": "",
    }


def calibrate_warm() -> float:
    """Boot serve-mcp once and read the embedding warm-up time it logs to its own stderr, so Arm B
    wall can be reported warm-adjusted. (Inside a `claude -p` run that stderr is swallowed by the
    MCP client, so it can't be parsed per-run — measure it once here instead.)

    Only an EXPLICIT 'embedding model ready in <n>s' match (see mcp/server.py) sets the value; we
    NEVER fall back to the measured boot wall (it captures full process startup, which then floors
    Arm B wall_adj_s to ~0.1). On no match / clean exit / spawn failure we use WARM_S_DEFAULT, and
    every value is clamped to WARM_S_MAX so a garbage match can't poison the adjustment either.

    Hang-safety: warm-up logs BEFORE the server's blocking stdio run (server.py), so the process
    never EOFs on its own. A blocking p.stderr.readline() on a silent child (the 2026-06-20 scipy
    loader-lock deadlock server.py warms to avoid) would hang forever — a loop/time check above the
    readline() can't interrupt it. So a threading.Timer terminates the child at the deadline, which
    closes its stderr write handle and makes readline() return EOF, unblocking the loop. (Timer is
    used instead of signal.alarm, which is POSIX-only and unavailable on Windows.)"""
    import threading
    try:
        p = subprocess.Popen([str(PANDE), "serve-mcp", "--repo", PP],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except Exception as e:
        val = min(WARM_S_DEFAULT, WARM_S_MAX)
        log(f"warm calibrate: spawn failed ({e}); defaulting {val}s")
        return val
    val = None
    wd = threading.Timer(WARM_BUDGET_S, p.terminate)  # enforced deadline (interrupts blocking read)
    wd.daemon = True
    wd.start()
    try:
        while True:
            line = p.stderr.readline()
            if not line:  # EOF: clean exit OR the watchdog terminate() closed the pipe
                break
            m = re.search(r"ready in ([\d.]+)s", line)
            if m:
                val = float(m.group(1))
                break
            if "serving (stdio)" in line:  # warm-up done but un-timed (e.g. warm-up exception)
                break
    finally:
        try:
            wd.cancel()
        except Exception:
            pass
        try:
            p.terminate()
        except Exception:
            pass
        try:
            p.wait(timeout=5)  # reap the child so it isn't left as a zombie
        except Exception:
            pass
        for pipe in (p.stderr, p.stdin):  # close FDs we opened so they aren't leaked
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass
    if val is None:
        val = WARM_S_DEFAULT  # no explicit match — DON'T capture boot wall; use documented default
    val = min(val, WARM_S_MAX)
    log(f"warm calibrate: embedding warm-up = {val}s (subtracted from Arm B wall)")
    return val


def write_mcp(repo: str) -> str:
    cfg = {"mcpServers": {"pandemonium": {"command": str(PANDE),
            "args": ["serve-mcp", "--repo", repo]}}}
    # One file per distinct repo (content differs per repo, so a single shared path can't be
    # reused across the three repos). Name it by the repo's basename. FIX #10.
    p = OUT / f"_qa_mcp_{Path(repo).name}.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


def parse_answer(text: str):
    m = re.search(r"ANSWER:\s*file\s*=\s*(.*?)[\s,]+symbol\s*=\s*(.+)$",
                  text or "", re.IGNORECASE | re.MULTILINE)
    if m:
        return m.group(1).strip().strip("`\"'"), m.group(2).strip().strip("`\"'")
    return None, None


def _leaf(sym: str) -> str:
    return re.split(r"[:.]", sym.strip())[-1].lower()


def _word_in(needle, hay):
    """Word-boundary / exact match of `needle` inside lowercased `hay` (no bare substring), so
    short golds (tick, embedding, util.py) don't over-match getTicker / embeddings / myutil.py.
    `needle` is lowercased + regex-escaped, so leaves/full-forms with `::`, `.`, `/` are literal."""
    needle = (needle or "").strip().lower()
    if not needle:
        return False
    return re.search(r"\b" + re.escape(needle) + r"\b", hay) is not None


def score(task, text: str):
    ans_file, ans_sym = parse_answer(text)
    # FIX #5: no well-formed ANSWER line parsed => format violation => miss (no prose fallback).
    if ans_file is None and ans_sym is None:
        return {"ans_file": None, "ans_symbol": None, "parsed": False,
                "file_hit": False, "symbol_hit": False, "hit": False}
    # FIX #6c: slash-normalize the parsed file before matching (so backslash answers reach the
    # full-path branch, whose golds are forward-slash).
    file_hay = (ans_file or "").lower().replace("\\", "/")
    sym_hay = (ans_sym or "").lower()
    # FIX #6a: word-boundary / exact-leaf match (no bare substring) so short golds (tick, embedding)
    # don't over-match getTicker / ticket / embeddings / prose echoes.
    file_hit = any(_word_in(Path(gf).name, file_hay)
                   or _word_in(gf, file_hay)
                   for gf in task["gold_files"])
    sym_hit = any(_word_in(_leaf(gs), sym_hay) or _word_in(gs, sym_hay)
                  for gs in task["gold_symbols"])
    return {"ans_file": ans_file, "ans_symbol": ans_sym, "parsed": True,
            "file_hit": bool(file_hit), "symbol_hit": bool(sym_hit),
            "hit": bool(file_hit and sym_hit)}


def run_agent(task, arm):
    repo = task["repo"]
    prompt = task["q"] + QUESTION_SUFFIX
    # stream-json (+ --verbose, which --print requires) emits one JSON event per line: a final
    # type=="result" event byte-identical to plain --output-format json, PLUS the assistant/user
    # message events whose content[] carries the tool_use blocks we need for mcp_calls (FIX #8).
    args = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--model", AGENT_MODEL, "--dangerously-skip-permissions",
            "--max-turns", str(MAX_TURNS), "--disallowedTools", *DISALLOWED]
    if arm == "B":
        args += ["--mcp-config", MCP_CONFIGS[repo], "--strict-mcp-config",
                 "--append-system-prompt", ARMB_SYSTEM]
    else:
        args += ["--strict-mcp-config", "--append-system-prompt", ARMA_SYSTEM]
    # Arm B spawns a fresh serve-mcp that warms the embedding model (~7s) before it can answer —
    # a per-run harness artifact, absent in real warm usage. Subtract the calibrated constant.
    warm = WARM_S if arm == "B" else 0.0
    t0 = time.time()
    err_reason = None
    try:
        r = sh(args, cwd=repo, env=copy_env(repo), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        err_reason = "timeout"
    except (OSError, ValueError) as e:
        err_reason = "spawn"
        log(f"   arm {arm} spawn failed: {e!r}")
    except Exception as e:
        err_reason = "spawn"
        log(f"   arm {arm} unexpected failure: {e!r}")
    wall = round(time.time() - t0, 1)
    if err_reason is not None:
        return _err_rec(task, arm, wall, warm, err_reason)
    # Parse the JSONL stream: keep every event for mcp counting, and pick the result event
    # (the last type=="result") as the canonical summary object.
    events = _load_stream(r.stdout)
    d = None
    for ev in events:
        if isinstance(ev, dict) and ev.get("type") == "result":
            d = ev
    if d is None:
        # No well-formed result event -> treat as bad JSON, but stash a stderr tail for triage.
        rec = _err_rec(task, arm, wall, warm, "bad-json")
        rec["stderr_tail"] = (r.stderr or "")[-300:]
        return rec
    mcp_calls = 0 if arm == "A" else _count_mcp_calls(events)
    u = d.get("usage", {}) or {}
    text = d.get("result") or ""
    sc = score(task, text)
    in_tok, out_tok = u.get("input_tokens", 0), u.get("output_tokens", 0)
    cc, cr = u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0)
    rec = {
        "task": task["id"], "repo": Path(repo).name, "arm": arm,
        "wall_s": wall, "warm_s": warm, "wall_adj_s": round(max(wall - warm, 0.1), 1),
        "is_error": bool(d.get("is_error")),
        "error_reason": ("result-error" if d.get("is_error") else None),
        "num_turns": d.get("num_turns"), "cost_usd": d.get("total_cost_usd"),
        "in_tok": in_tok, "out_tok": out_tok, "cache_create": cc, "cache_read": cr,
        "tot_tok": in_tok + out_tok + cc + cr, "mcp_calls": mcp_calls,
        **sc, "result": (text or "")[:1600].replace("\n", " "),
        "stderr_tail": (r.stderr or "")[-300:],
    }
    return rec


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 4) if xs else None


def _rate(rs, key):
    return f"{sum(1 for r in rs if r.get(key))}/{len(rs)}" if rs else "0/0"


def report(rows, path):
    repos = []
    for r in rows:
        if r["repo"] not in repos:
            repos.append(r["repo"])
    L = {"A": "vanilla (no protocol)", "B": "protocol"}
    fmt = lambda v: "—" if v is None else str(v)
    lines = ["# Q&A A/B — vanilla Claude Code vs + PandemoniumProtocol", "",
             f"Agent: {AGENT_MODEL} | runs: {len(rows)} | "
             f"objective gold-hit grading (file + symbol)", "",
             f"Arm B `wall_adj_s` subtracts the one-off serve-mcp embedding warm-up "
             f"(~{WARM_S}s, calibrated; absent in real warm usage); `cache_create` is the "
             f"MCP-schema tax.", ""]

    # Arm B runs where the protocol never fired (0 MCP calls), errors excluded (an errored
    # rec also carries mcp_calls==0 by default and is already counted in the errors line).
    nomcp = [r for r in rows if r["arm"] == "B" and not r["is_error"]
             and r.get("mcp_calls", 0) == 0]
    if nomcp:
        lines += [f"!! WARNING: {len(nomcp)} Arm-B run(s) made 0 PandemoniumProtocol MCP calls "
                  f"— the protocol never fired (flagged [NO-MCP] below). NOT silently dropped.",
                  ""]

    def block(title, rs):
        out = [f"## {title}", "",
               f"  {'arm':24s}{'file_hit':>9}{'sym_hit':>8}{'cost$':>9}"
               f"{'tot_tok':>10}{'cache_cr':>10}{'turns':>7}{'wall':>7}{'wall_adj':>9}"]
        # Error counts are computed independently of the display loop so the line always
        # renders both arms, even when an arm has zero rows or all of them errored.
        errA = sum(1 for r in rs if r["arm"] == "A" and r["is_error"])
        errB = sum(1 for r in rs if r["arm"] == "B" and r["is_error"])
        for a in ("A", "B"):
            ar = [r for r in rs if r["arm"] == a]
            if not ar:
                continue
            # Drop errored rows from every mean and from the _rate ratios (num AND denom),
            # so means/rates/deltas all describe one population. Arms whose rows ALL errored
            # still print (means —, rate 0/0); the errN count lives on the errors line.
            good = [r for r in ar if not r["is_error"]]
            out.append(
                f"  {L[a]:24s}{_rate(good, 'file_hit'):>9}{_rate(good, 'symbol_hit'):>8}"
                f"{fmt(_mean([r['cost_usd'] for r in good])):>9}"
                f"{fmt(_mean([r['tot_tok'] for r in good])):>10}"
                f"{fmt(_mean([r['cache_create'] for r in good])):>10}"
                f"{fmt(_mean([r['num_turns'] for r in good])):>7}"
                f"{fmt(_mean([r['wall_s'] for r in good])):>7}"
                f"{fmt(_mean([r['wall_adj_s'] for r in good])):>9}")
        out.append(f"  errors: A={errA} B={errB}")
        # Paired deltas B-A, keyed by (task, rep) so --repeats>1 doesn't collapse to the last
        # rep. Errored rows are excluded here, so cost and tok deltas share ONE population:
        # a pair contributes to BOTH iff both arms are present (and neither errored).
        byt = {}
        for r in rs:
            if not r["is_error"]:
                byt.setdefault((r["task"], r["rep"]), {})[r["arm"]] = r
        cds, tds = [], []
        for p in byt.values():
            # Both deltas share ONE population (a pair contributes to cost AND tok or to neither),
            # and we guard cost_usd against None so a non-errored row that somehow lacks a cost
            # (missing total_cost_usd in the result event) can't raise TypeError mid-report.
            if ("A" in p and "B" in p
                    and p["A"]["cost_usd"] is not None and p["B"]["cost_usd"] is not None):
                cds.append(p["B"]["cost_usd"] - p["A"]["cost_usd"])
                tds.append(p["B"]["tot_tok"] - p["A"]["tot_tok"])
        out += ["", f"  paired delta (protocol - vanilla): cost=${fmt(_mean(cds))}  "
                f"tok={fmt(_mean(tds))}", ""]
        return out

    lines += block("Overall (all repos)", rows)
    for rp in repos:
        lines += block(rp, [r for r in rows if r["repo"] == rp])

    lines += ["## Per run", ""]
    for r in sorted(rows, key=lambda x: (x["repo"], x["task"], x["rep"], x["arm"])):
        # Flag Arm-B runs where the protocol never fired (errors excluded — see above).
        nomcp_tag = (" [NO-MCP]" if r["arm"] == "B" and not r["is_error"]
                     and r.get("mcp_calls", 0) == 0 else "")
        err_tag = f" [ERR:{r.get('error_reason')}]" if r["is_error"] else ""
        lines.append(
            f"- {r['repo']}/{r['task']} r{r['rep']} {L[r['arm']]}: file_hit={r['file_hit']} "
            f"sym_hit={r['symbol_hit']} cost=${fmt(r['cost_usd'])} tot_tok={r['tot_tok']} "
            f"turns={fmt(r['num_turns'])} wall={r['wall_s']}s adj={r['wall_adj_s']}s "
            f"-> {fmt(r['ans_file'])} :: {fmt(r['ans_symbol'])}{nomcp_tag}{err_tag}")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    log(f"REPORT -> {path}")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=len(TASKS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    # Precompute the per-repo MCP config files ONCE (one file per distinct repo; the child reads
    # the file at launch, so it must exist on disk before the first Arm-B run). FIX #10.
    global MCP_CONFIGS
    MCP_CONFIGS = {t["repo"]: write_mcp(t["repo"]) for t in TASKS}
    global WARM_S
    if WARM_S <= 0:
        WARM_S = calibrate_warm()
    tasks = TASKS[: args.limit]
    results_path = OUT / "qa_results.jsonl"
    rows = []
    with results_path.open("w", encoding="utf-8") as fh:
        for t in tasks:
            for rep in range(args.repeats):
                arms = ["A", "B"]
                random.shuffle(arms)
                for arm in arms:
                    log(f"== {t['id']} ({Path(t['repo']).name}) rep{rep} arm {arm} ==")
                    # run_agent already returns a well-formed error rec on any failure; this guard
                    # is belt-and-suspenders so a single bad run can NEVER abort the sweep before
                    # report() (FIX #3).
                    try:
                        rec = run_agent(t, arm)
                    except Exception as e:
                        log(f"   arm {arm} run_agent raised (caught): {e!r}")
                        rec = _err_rec(t, arm, 0.0, WARM_S if arm == "B" else 0.0, "spawn")
                    rec["rep"] = rep
                    rows.append(rec)
                    fh.write(json.dumps(rec) + "\n")
                    fh.flush()
                    log(f"   arm {arm}: file_hit={rec['file_hit']} sym_hit={rec['symbol_hit']} "
                        f"cost=${rec['cost_usd']} turns={rec['num_turns']} mcp={rec['mcp_calls']} "
                        f"wall={rec['wall_s']}s adj={rec['wall_adj_s']}s err={rec['is_error']}")
    report(rows, OUT / "qa_report.md")


if __name__ == "__main__":
    main()
