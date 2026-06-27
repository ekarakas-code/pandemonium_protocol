"""Impact-dimension A/B: vanilla Claude Code (Arm A, grep/read) vs + PandemoniumProtocol (Arm B,
repo_impact/repo_graph), measured on CALLER ENUMERATION over a real external repo.

Why this exists. `qa_ab_runner.py` measures code LOCATION (find one file+symbol). On a verbose-C#
repo the symbol name IS a near-restatement of the question, so grep wins and — because the agent
finishes in <13s, before the per-run serve-mcp cold-start finishes warming — Arm B usually never
calls an MCP tool ([NO-MCP]). That harness tests the dimension the protocol was NEVER expected to
win (ROADMAP guardrail: "search+get lost to grep on token count; the compact win came from impact").

This harness tests the dimension it WAS built for: "name every method that DIRECTLY calls X." The
protocol answers with one `repo_impact` call returning resolved callers (enclosing symbols, with a
confidence floor); vanilla must grep the name, open each hit, map it to its enclosing method, and
filter false positives (comments, string literals, same-name methods, the def + interface decl).
Enumerating all callers also takes long enough that the ~13s warm-up finishes mid-run, so Arm B
actually engages MCP — removing the cold-start artifact that muted qa_ab_runner.

Grading is OBJECTIVE: precision / recall / F1 / exact-set over a caller SET derived INDEPENDENTLY by
grep (anti-circular; each call site mapped to its enclosing method, def + interface decl excluded).
Both arms are READ-ONLY (Edit/Write/Bash/Task/... disallowed), so Arm A can't shell out to the
`pandemonium` CLI and the live repo can't be mutated.

Usage:
  QA_IMPACT_TASKS=evals/qa_impact_tasks_igya.json QA_OUT=C:/tmp/_impact \\
    .venv/Scripts/python.exe evals/qa_impact_ab_runner.py --limit 1 --repeats 1   # smoke
  ... evals/qa_impact_ab_runner.py                                                 # full sweep
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path

# Reuse the stateless plumbing from the location harness (claude exe + pandemonium exe resolution,
# offline env, JSONL stream parse, MCP-call counting, warm calibration, MCP-config writer, leaf
# normaliser, mean). Importing it runs its module body (TASKS default, ARMB_SYSTEM from SKILL.md) —
# harmless: QA_TASKS stays unset here, and we override base.TASKS/base.OUT before any call uses them.
import qa_ab_runner as base
from qa_ab_runner import (  # noqa: E402  (after sys.path is implicitly the evals/ dir)
    copy_env, _load_stream, _count_mcp_calls, sh, _leaf, _mean,
    CLAUDE, MAX_TURNS, TIMEOUT, AGENT_MODEL, DISALLOWED, ARMB_SYSTEM,
)

OUT = Path(os.environ.get("QA_OUT", r"D:\_impact"))
base.OUT = OUT  # so base.write_mcp() / base.calibrate_warm() use our bench dir

# Impact gold: [{id, repo, target, desc, gold_callers:[leaf names]}]. Derived by independent grep.
_TASKS_FILE = os.environ.get("QA_IMPACT_TASKS")
TASKS = json.loads(Path(_TASKS_FILE).read_text(encoding="utf-8")) if _TASKS_FILE else []

QUESTION = (
    "In the repository in your working directory, find EVERY function/method that DIRECTLY calls the "
    "method named `{target}` ({desc}).\n\n"
    "Rules: count only DIRECT call sites (not indirect/transitive callers); do NOT include the "
    "definition of `{target}` itself, nor any interface/abstract DECLARATION of it. For each direct "
    "call site, name its ENCLOSING method (the function the call sits inside).\n\n"
    "Investigate efficiently, then end your reply with EXACTLY one line — the enclosing method names "
    "of all direct callers, comma-separated, and NOTHING after it:\n"
    "CALLERS: name1, name2, ...\n"
    "(If there are no callers, write `CALLERS: none`.)"
)

ARMA_SYSTEM = (
    "You are a senior engineer answering a change-impact question about the repository in your "
    "working directory: who calls a given method. Investigate efficiently with the file tools "
    "available (Read, Grep, Glob), enumerate every direct call site, and map each to its enclosing "
    "method. Be exhaustive but precise — do not invent callers you have not seen.")

# Arm B prompt. In THIS environment a spawned `claude -p` is given the pandemonium MCP tools
# DEFERRED behind ToolSearch (the init tool list shows 0 mcp__ tools + ToolSearch present), so an
# agent left to its own devices never discovers them and just greps — measured as [NO-MCP] (probe
# 2026-06-27). To measure the protocol's efficacy WHEN USED (the user-chosen question), force the
# discovery+use path: load the repo_* tools via ToolSearch, then make repo_impact the primary tool.
# The ToolSearch round-trip is a real cost of using the protocol in this env and is reported as such.
IMPACT_ARMB_SYSTEM = (
    "TOOL ACCESS — READ FIRST: The PandemoniumProtocol repo_* tools (repo_impact, repo_graph, "
    "repo_search, repo_get) are NOT in your default tool list; they are available via ToolSearch. "
    "Your FIRST action MUST be to call ToolSearch (query e.g. "
    "\"select:repo_impact,repo_graph,repo_search,repo_get\", or keyword \"repo impact callers graph\") "
    "to load them. For THIS caller-enumeration task you MUST use repo_impact as your PRIMARY method: "
    "call repo_impact on the target symbol to get its resolved direct callers, instead of grepping "
    "the codebase by hand. Use Read/Grep only to confirm or disambiguate the tool's output, never as "
    "your first-line search.\n\n" + ARMB_SYSTEM)


def log(msg: str) -> None:
    print(f"[imp {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_callers(text: str):
    """Parse the forced `CALLERS: a, b, c` line into a set of leaf-normalised names. None if no
    well-formed line (format violation -> scored as a miss, like qa_ab_runner). `none` -> empty set."""
    m = re.search(r"CALLERS:\s*(.+?)\s*$", text or "", re.IGNORECASE | re.MULTILINE)
    if m is None:
        return None
    raw = m.group(1).strip()
    if raw.lower() in ("none", "n/a", "(none)", "-"):
        return set()
    parts = [p.strip().strip("`\"'") for p in re.split(r"[,;]", raw)]
    return {_leaf(p) for p in parts if p and p.lower() not in ("none", "n/a")}


def score(task, text: str):
    gold = {_leaf(g) for g in task["gold_callers"]}
    ans = parse_callers(text)
    if ans is None:
        return {"parsed": False, "ans_n": 0, "gold_n": len(gold), "tp": 0,
                "precision": 0.0, "recall": 0.0, "f1": 0.0, "exact": False,
                "ans_callers": None, "missing": sorted(gold), "extra": []}
    tp = len(ans & gold)
    precision = tp / len(ans) if ans else (1.0 if not gold else 0.0)
    recall = tp / len(gold) if gold else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"parsed": True, "ans_n": len(ans), "gold_n": len(gold), "tp": tp,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "exact": ans == gold, "ans_callers": sorted(ans),
            "missing": sorted(gold - ans), "extra": sorted(ans - gold)}


def _err_rec(task, arm, rep, wall, warm, reason):
    return {"task": task["id"], "repo": Path(task["repo"]).name, "arm": arm, "rep": rep,
            "wall_s": wall, "warm_s": warm, "wall_adj_s": round(max(wall - warm, 0.1), 1),
            "is_error": True, "error_reason": reason, "num_turns": None, "cost_usd": None,
            "in_tok": 0, "out_tok": 0, "cache_create": 0, "cache_read": 0, "tot_tok": 0,
            "mcp_calls": 0, "parsed": False, "ans_n": 0, "gold_n": len(task["gold_callers"]),
            "tp": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "exact": False,
            "ans_callers": None, "missing": [], "extra": [], "result": "", "stderr_tail": ""}


MCP_CONFIGS = {}


def run_agent(task, arm, rep):
    repo = task["repo"]
    prompt = QUESTION.format(target=task["target"], desc=task["desc"])
    # REALISTIC-UPLIFT framing (user-chosen 2026-06-27): both arms keep Read/Grep/Glob, so Arm B =
    # the vanilla toolset PLUS repo_impact. The question is "does ADDING the protocol help an agent
    # that still has grep as a fallback?" — not "is repo_impact better than grep in isolation" (the
    # tool-ceiling framing, which would strip Grep/Glob from Arm B). Neither arm may shell out: Bash
    # is in DISALLOWED, and on Windows the agent also has a PowerShell tool (Select-String == grep)
    # that would let it side-step BOTH the protocol and the counted Grep tool (probe 2026-06-27:
    # mcp=4 BUT PowerShell=5) — so PowerShell is disallowed for both, routing search through the
    # counted Grep tool / the protocol. Whether Arm B actually fires repo_impact when grep is ALSO
    # available is the empirical question the one-shot probe settles: a soft prompt alone did NOT
    # fire it (Sonnet just greps), so IMPACT_ARMB_SYSTEM hardens the directive (ToolSearch-first).
    disallowed = list(DISALLOWED) + ["PowerShell"]
    args = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--model", AGENT_MODEL, "--dangerously-skip-permissions",
            "--max-turns", str(MAX_TURNS), "--disallowedTools", *disallowed]
    if arm == "B":
        args += ["--mcp-config", MCP_CONFIGS[repo], "--strict-mcp-config",
                 "--append-system-prompt", IMPACT_ARMB_SYSTEM]
    else:
        args += ["--strict-mcp-config", "--append-system-prompt", ARMA_SYSTEM]
    warm = base.WARM_S if arm == "B" else 0.0
    t0 = time.time()
    err = None
    try:
        r = sh(args, cwd=repo, env=copy_env(repo), timeout=TIMEOUT)
    except Exception as e:
        err = "timeout" if e.__class__.__name__ == "TimeoutExpired" else "spawn"
        if err == "spawn":
            log(f"   arm {arm} spawn failed: {e!r}")
    wall = round(time.time() - t0, 1)
    if err is not None:
        return _err_rec(task, arm, rep, wall, warm, err)
    events = _load_stream(r.stdout)
    d = next((ev for ev in reversed(events)
              if isinstance(ev, dict) and ev.get("type") == "result"), None)
    if d is None:
        rec = _err_rec(task, arm, rep, wall, warm, "bad-json")
        rec["stderr_tail"] = (r.stderr or "")[-300:]
        return rec
    mcp_calls = 0 if arm == "A" else _count_mcp_calls(events)
    u = d.get("usage", {}) or {}
    text = d.get("result") or ""
    sc = score(task, text)
    in_tok, out_tok = u.get("input_tokens", 0), u.get("output_tokens", 0)
    cc, cr = u.get("cache_creation_input_tokens", 0), u.get("cache_read_input_tokens", 0)
    return {"task": task["id"], "repo": Path(repo).name, "arm": arm, "rep": rep,
            "wall_s": wall, "warm_s": warm, "wall_adj_s": round(max(wall - warm, 0.1), 1),
            "is_error": bool(d.get("is_error")),
            "error_reason": ("result-error" if d.get("is_error") else None),
            "num_turns": d.get("num_turns"), "cost_usd": d.get("total_cost_usd"),
            "in_tok": in_tok, "out_tok": out_tok, "cache_create": cc, "cache_read": cr,
            "tot_tok": in_tok + out_tok + cc + cr, "mcp_calls": mcp_calls, **sc,
            "result": (text or "")[:1600].replace("\n", " "), "stderr_tail": (r.stderr or "")[-300:]}


def _rate(rs, key):
    return f"{sum(1 for r in rs if r.get(key))}/{len(rs)}" if rs else "0/0"


def report(rows, path):
    L = {"A": "vanilla (grep)", "B": "protocol (impact)"}
    fmt = lambda v: "—" if v is None else str(v)
    lines = ["# Impact-dimension A/B — caller enumeration (vanilla grep vs + PandemoniumProtocol)", "",
             f"Agent: {AGENT_MODEL} | runs: {len(rows)} | grading: precision/recall/F1/exact over a "
             f"grep-derived caller SET (anti-circular)", "",
             f"Arm B `wall_adj_s` subtracts the one-off serve-mcp warm-up (~{base.WARM_S}s).", ""]
    nomcp = [r for r in rows if r["arm"] == "B" and not r["is_error"] and r.get("mcp_calls", 0) == 0]
    if nomcp:
        lines += [f"!! {len(nomcp)} Arm-B run(s) made 0 MCP calls — protocol never fired ([NO-MCP]).", ""]

    def block(title, rs):
        out = [f"## {title}", "",
               f"  {'arm':20s}{'prec':>7}{'recall':>8}{'F1':>7}{'exact':>7}"
               f"{'cost$':>9}{'tot_tok':>10}{'turns':>7}{'mcp':>5}{'wall_adj':>9}"]
        errA = sum(1 for r in rs if r["arm"] == "A" and r["is_error"])
        errB = sum(1 for r in rs if r["arm"] == "B" and r["is_error"])
        for a in ("A", "B"):
            good = [r for r in rs if r["arm"] == a and not r["is_error"]]
            if not [r for r in rs if r["arm"] == a]:
                continue
            out.append(
                f"  {L[a]:20s}{fmt(_mean([r['precision'] for r in good])):>7}"
                f"{fmt(_mean([r['recall'] for r in good])):>8}"
                f"{fmt(_mean([r['f1'] for r in good])):>7}{_rate(good, 'exact'):>7}"
                f"{fmt(_mean([r['cost_usd'] for r in good])):>9}"
                f"{fmt(_mean([r['tot_tok'] for r in good])):>10}"
                f"{fmt(_mean([r['num_turns'] for r in good])):>7}"
                f"{fmt(_mean([r['mcp_calls'] for r in good])):>5}"
                f"{fmt(_mean([r['wall_adj_s'] for r in good])):>9}")
        out.append(f"  errors: A={errA} B={errB}")
        byt = {}
        for r in rs:
            if not r["is_error"]:
                byt.setdefault((r["task"], r["rep"]), {})[r["arm"]] = r
        f1d, rcd, cd, td = [], [], [], []
        for p in byt.values():
            if "A" in p and "B" in p and p["A"]["cost_usd"] is not None and p["B"]["cost_usd"] is not None:
                f1d.append(p["B"]["f1"] - p["A"]["f1"]); rcd.append(p["B"]["recall"] - p["A"]["recall"])
                cd.append(p["B"]["cost_usd"] - p["A"]["cost_usd"]); td.append(p["B"]["tot_tok"] - p["A"]["tot_tok"])
        out += ["", f"  paired delta (protocol - vanilla): F1={fmt(_mean(f1d))}  recall={fmt(_mean(rcd))}  "
                f"cost=${fmt(_mean(cd))}  tok={fmt(_mean(td))}", ""]
        return out

    lines += block("Overall", rows)
    lines += ["## Per run", ""]
    for r in sorted(rows, key=lambda x: (x["task"], x["rep"], x["arm"])):
        tag = (" [NO-MCP]" if r["arm"] == "B" and not r["is_error"] and r.get("mcp_calls", 0) == 0 else "")
        tag += f" [ERR:{r.get('error_reason')}]" if r["is_error"] else ""
        lines.append(
            f"- {r['task']} r{r['rep']} {L[r['arm']]}: P={r['precision']} R={r['recall']} F1={r['f1']} "
            f"exact={r['exact']} cost=${fmt(r['cost_usd'])} tok={r['tot_tok']} turns={fmt(r['num_turns'])} "
            f"mcp={r['mcp_calls']} -> got={fmt(r['ans_callers'])} missing={r['missing']} extra={r['extra']}{tag}")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    log(f"REPORT -> {path}")
    print("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=len(TASKS))
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    if not TASKS:
        raise SystemExit("no tasks: set QA_IMPACT_TASKS=<gold.json>")
    random.seed(args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    global MCP_CONFIGS
    MCP_CONFIGS = {t["repo"]: base.write_mcp(t["repo"]) for t in TASKS}
    base.TASKS = TASKS  # so base.calibrate_warm() warms TASKS[0].repo (our repo, not the D:\ defaults)
    if base.WARM_S <= 0:
        base.WARM_S = base.calibrate_warm()
    tasks = TASKS[:args.limit]
    rows = []
    results_path = OUT / "qa_impact_results.jsonl"
    results_path.write_text("", encoding="utf-8")
    for rep in range(args.repeats):
        for t in tasks:
            arms = ["A", "B"]
            random.shuffle(arms)  # neutralise prompt-cache order bias per (task, rep)
            for arm in arms:
                log(f"== {t['id']} rep{rep} arm {arm} (target={t['target']}) ==")
                rec = run_agent(t, arm, rep)
                rows.append(rec)
                with results_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
                sc = (f"P={rec['precision']} R={rec['recall']} F1={rec['f1']} exact={rec['exact']}"
                      if not rec["is_error"] else f"ERR:{rec['error_reason']}")
                log(f"   arm {arm}: {sc} cost=${rec['cost_usd']} turns={rec['num_turns']} "
                    f"mcp={rec['mcp_calls']} wall={rec['wall_s']}s")
    report(rows, OUT / "qa_impact_report.md")


if __name__ == "__main__":
    main()
