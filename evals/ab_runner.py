"""A/B harness: vanilla Claude Code (Arm A) vs Claude Code + PandemoniumProtocol (Arm B).

For each seeded-bug task (evals/ab_tasks.py): copy the pristine repo, apply the bug, run an
agent twice (A then B, order shuffled per (task,repeat) to neutralise prompt-cache bias),
restore the tests from pristine (anti-cheat), grade by running the FULL suite (green == fixed
+ no regression), capture token usage/cost, diff the change, and have a blind Opus judge score
both fixes. Aggregates PAIRED per-task deltas (low-variance metrics like cost/tokens are the
headline; pass-rate at small N is reported, not led with).

Prereqs: D:\\_bench\\pristine = a clean working copy of the repo (no .venv/.git/.pandemonium/
.claude/.mcp.json), already verified green. `claude` on PATH. Run from the repo root.

Usage:
  python evals/ab_runner.py --validate              # derive+check each task's target tests (no agent)
  python evals/ab_runner.py --limit 1               # one full A/B (smoke the whole pipeline)
  python evals/ab_runner.py --repeats 2             # full standard sweep
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
BENCH = Path(os.environ.get("BENCH_ROOT", r"D:\_bench"))
PRISTINE = BENCH / "pristine"
RUNS = BENCH / "runs"
PY = SRC / ".venv" / "Scripts" / "python.exe"
PANDE = SRC / ".venv" / "Scripts" / "pandemonium.exe"
def _claude_exe() -> str:
    # Windows: `claude` on PATH is a .cmd/shell shim that CreateProcess can't launch from a
    # subprocess list. The real native exe lives under the npm package; use it directly.
    shim = shutil.which("claude")
    if shim:
        exe = (Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code"
               / "bin" / "claude.exe")
        if exe.exists():
            return str(exe)
    return "claude"


CLAUDE = _claude_exe()
AGENT_MODEL = os.environ.get("AB_AGENT_MODEL", "claude-sonnet-4-6")
JUDGE_MODEL = os.environ.get("AB_JUDGE_MODEL", "claude-opus-4-8")
MAX_TURNS = int(os.environ.get("AB_MAX_TURNS", "40"))

sys.path.insert(0, str(Path(__file__).parent))
from ab_tasks import ARMA_SYSTEM, TASKS  # noqa: E402

# Merge in externally-authored tasks (e.g. produced by the author-ab-tasks workflow) from
# evals/ab_tasks_extra.json, de-duped by id and by (module, find) so re-proposals don't
# double-count. Each entry: {"id","prompt","mutations":[[module,find,replace], ...]}.
_extra = Path(__file__).parent / "ab_tasks_extra.json"
if _extra.exists():
    _seen_ids = {t["id"] for t in TASKS}
    _seen_mut = {(m[0], m[1]) for t in TASKS for m in t["mutations"]}
    for _t in json.loads(_extra.read_text(encoding="utf-8")):
        _muts = [tuple(m) for m in _t["mutations"]]
        if _t["id"] in _seen_ids or any((m[0], m[1]) in _seen_mut for m in _muts):
            continue
        TASKS.append({"id": _t["id"], "prompt": _t["prompt"], "mutations": _muts})
        _seen_ids.add(_t["id"])
        _seen_mut.update((m[0], m[1]) for m in _muts)


def log(msg: str) -> None:
    print(f"[ab {datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def armb_system() -> str:
    """Arm B's system prompt = the real pandemonium skill body (frontmatter stripped)."""
    txt = (SRC / ".claude" / "skills" / "pandemonium" / "SKILL.md").read_text(encoding="utf-8")
    if txt.startswith("---"):
        txt = txt.split("---", 2)[-1]
    return ("PandemoniumProtocol's repo_* MCP tools are available. Follow this retrieval "
            "discipline:\n" + txt.strip())


def sh(cmd, cwd=None, env=None, timeout=None):
    return subprocess.run(cmd, cwd=cwd, env=env, timeout=timeout,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def copy_env(work: Path) -> dict:
    e = dict(os.environ)
    e["PYTHONPATH"] = str(work)
    e["HF_HUB_OFFLINE"] = "1"
    e["TRANSFORMERS_OFFLINE"] = "1"
    return e


def make_copy(work: Path) -> None:
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(PRISTINE, work,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pandemonium",
                                                  ".pytest_cache", "_ab_mcp.json"))


def apply_mutations(work: Path, mutations) -> None:
    for rel, find, repl in mutations:
        p = work / rel
        s = p.read_text(encoding="utf-8")
        if find not in s:
            raise RuntimeError(f"mutation target not found in {rel}: {find!r}")
        p.write_text(s.replace(find, repl, 1), encoding="utf-8")


def restore_tests(work: Path) -> None:
    """Anti-cheat: overwrite the copy's tests with the pristine versions before grading."""
    for sub in ("tests", "conftest.py"):
        src, dst = PRISTINE / sub, work / sub
        if not src.exists():
            continue
        if src.is_dir():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)


def run_suite(work: Path):
    """Run the full suite from cwd=copy. Returns (all_green, failed_nodeids, duration_s)."""
    t0 = time.time()
    r = sh([str(PY), "-m", "pytest", "-q", "--no-header", "--tb=no", "-rfE",
            "-p", "no:cacheprovider"], cwd=str(work), env=copy_env(work), timeout=1200)
    out = (r.stdout or "") + (r.stderr or "")
    failed = sorted(set(re.findall(r"^(?:FAILED|ERROR)\s+(\S+)", out, re.MULTILINE)))
    return r.returncode == 0, failed, round(time.time() - t0, 1)


def reindex(work: Path):
    # `index` takes the repo as a POSITIONAL arg (only `serve-mcp` uses --repo). A fresh
    # copy has no prior hashes, so incremental == full here.
    r = sh([str(PANDE), "index", str(work)], cwd=str(work),
           env=copy_env(work), timeout=900)
    return r.returncode == 0, (r.stdout or "")[-300:] + (r.stderr or "")[-300:]


def write_mcp(work: Path) -> Path:
    cfg = {"mcpServers": {"pandemonium": {"command": str(PANDE),
            "args": ["serve-mcp", "--repo", str(work)]}}}
    p = work / "_ab_mcp.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def run_agent(work: Path, prompt: str, arm: str):
    args = [CLAUDE, "-p", prompt, "--output-format", "json", "--model", AGENT_MODEL,
            "--dangerously-skip-permissions", "--max-turns", str(MAX_TURNS)]
    if arm == "B":
        args += ["--mcp-config", str(write_mcp(work)), "--strict-mcp-config",
                 "--append-system-prompt", armb_system()]
    else:
        args += ["--strict-mcp-config", "--append-system-prompt", ARMA_SYSTEM]
    t0 = time.time()
    r = sh(args, cwd=str(work), env=copy_env(work), timeout=1800)
    dur = round(time.time() - t0, 1)
    try:
        d = json.loads(r.stdout)
    except Exception:
        d = {"is_error": True, "result": "", "_stderr": (r.stderr or "")[-400:]}
    u = d.get("usage", {}) or {}
    return {
        "arm": arm, "wall_s": dur, "is_error": bool(d.get("is_error")),
        "num_turns": d.get("num_turns"), "cost_usd": d.get("total_cost_usd"),
        "in_tok": u.get("input_tokens", 0), "out_tok": u.get("output_tokens", 0),
        "cache_create": u.get("cache_creation_input_tokens", 0),
        "cache_read": u.get("cache_read_input_tokens", 0),
        "result_head": (d.get("result") or "")[:200].replace("\n", " "),
    }


def diff_of(work: Path, mutations=()) -> str:
    # Baseline = pristine WITH the seeded bug applied (what the agent actually started from),
    # so the diff shows the agent's FIX. Diffing against plain pristine yields ~nothing when a
    # correct fix reverts the bug back toward the original file (which broke the judge).
    mut_by_path: dict = {}
    for rel, find, repl in mutations:
        mut_by_path.setdefault(rel, []).append((find, repl))
    chunks = []
    for f in sorted(PRISTINE.rglob("*.py")):
        rel = f.relative_to(PRISTINE)
        if rel.parts and rel.parts[0] in ("tests", "evals"):
            continue
        relp = rel.as_posix()
        base = f.read_text(encoding="utf-8", errors="replace")
        for find, repl in mut_by_path.get(relp, []):
            base = base.replace(find, repl, 1)
        a = base.splitlines(keepends=True)
        wf = work / rel
        b = wf.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if wf.exists() else []
        d = list(difflib.unified_diff(a, b, relp, relp, n=2))
        if d:
            chunks.append("".join(d))
    return ("".join(chunks))[:4000] or "(no change vs the buggy baseline)"


def judge(task, diff_a, diff_b):
    order = [("A", diff_a), ("B", diff_b)]
    random.shuffle(order)
    prompt = (
        "You are grading two candidate fixes for the same coding task, blind. Score each on "
        "correctness and quality (1=wrong/harmful, 5=correct, minimal, idiomatic). You have "
        "EVERYTHING you need in the diffs below — do NOT use any tools, do not read files; "
        "judge only from what is shown.\n\n"
        f"TASK:\n{task['prompt']}\n\n=== SOLUTION 1 (diff) ===\n{order[0][1]}\n\n"
        f"=== SOLUTION 2 (diff) ===\n{order[1][1]}\n\n"
        'Reply with ONLY a JSON object (no prose): {"s1": <1-5>, "s2": <1-5>, '
        '"winner": "1"|"2"|"tie", "why": "<one sentence>"}'
    )
    # Forbid tools (so the judge can't peek at the real repo and can't burn turns tool-calling)
    # and run in a neutral cwd. A small max-turns buffer covers a stray tool attempt.
    r = sh([CLAUDE, "-p", prompt, "--output-format", "json", "--model", JUDGE_MODEL,
            "--strict-mcp-config", "--max-turns", "4", "--disallowedTools", "Bash", "Read",
            "Grep", "Glob", "Edit", "Write", "WebFetch", "WebSearch", "Task", "NotebookEdit",
            "TodoWrite"], cwd=str(BENCH), timeout=400)
    raw = ""
    try:
        raw = json.loads(r.stdout).get("result", "") or ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate prose around the JSON
        ans = json.loads(m.group(0))
        mp = {"1": order[0][0], "2": order[1][0]}
        sc = {order[0][0]: ans.get("s1"), order[1][0]: ans.get("s2")}
        win = mp.get(str(ans.get("winner")), ans.get("winner"))
        return {"score_A": sc.get("A"), "score_B": sc.get("B"), "winner": win,
                "why": ans.get("why", "")}
    except Exception as e:
        return {"error": f"{e!r}", "raw": (raw or r.stdout or r.stderr or "")[:300]}


def validate(tasks):
    log(f"VALIDATE {len(tasks)} task(s): deriving target tests (mutation must break >=1)")
    ok = []
    for t in tasks:
        work = RUNS / f"validate_{t['id']}"
        try:
            make_copy(work)
            apply_mutations(work, t["mutations"])
        except Exception as e:
            log(f"  {t['id']}: SETUP FAILED ({e}) -> MALFORMED")
            shutil.rmtree(work, ignore_errors=True)
            continue
        green, failed, dur = run_suite(work)
        status = "OK" if (not green and failed) else "MALFORMED (no tests broke)"
        log(f"  {t['id']}: broke {len(failed)} test(s) in {dur}s -> {status}")
        if failed:
            log(f"     targets: {', '.join(failed[:6])}{' …' if len(failed) > 6 else ''}")
            ok.append(t["id"])
        shutil.rmtree(work, ignore_errors=True)
    log(f"VALIDATE done: {len(ok)}/{len(tasks)} well-formed -> {ok}")
    return ok


def run_pair(task, rep):
    """One (task, repeat): run both arms (shuffled order), grade, judge. Returns rows."""
    arms = ["A", "B"]
    random.shuffle(arms)
    out = {}
    diffs = {}
    for arm in arms:
        work = RUNS / f"{task['id']}_r{rep}_{arm}"
        make_copy(work)
        apply_mutations(work, task["mutations"])
        idx_note = None
        if arm == "B":
            idx_ok, idx_note = reindex(work)
            log(f"    [{task['id']} r{rep} {arm}] reindex ok={idx_ok}")
        rec = run_agent(work, task["prompt"], arm)
        diffs[arm] = diff_of(work, task["mutations"])
        restore_tests(work)
        green, failed, gdur = run_suite(work)
        rec.update({"task": task["id"], "rep": rep, "suite_green": green,
                    "still_failing": failed[:8], "grade_s": gdur, "idx_note": idx_note,
                    "diff": diffs[arm][:2500]})
        out[arm] = rec
        log(f"    [{task['id']} r{rep} {arm}] green={green} cost=${rec['cost_usd']} "
            f"turns={rec['num_turns']} wall={rec['wall_s']}s")
        shutil.rmtree(work, ignore_errors=True)
    verdict = judge(task, diffs["A"], diffs["B"])
    out["A"]["judge"] = verdict
    out["B"]["judge"] = verdict
    return [out["A"], out["B"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--limit", type=int, default=len(TASKS))
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    random.seed(args.seed)
    RUNS.mkdir(parents=True, exist_ok=True)

    tasks = TASKS[: args.limit]
    if args.validate:
        validate(tasks)
        return

    good = set(validate(tasks))
    tasks = [t for t in tasks if t["id"] in good]
    results_path = BENCH / "results.jsonl"
    rows = []
    with results_path.open("w", encoding="utf-8") as fh:
        for t in tasks:
            for rep in range(args.repeats):
                log(f"== task {t['id']} repeat {rep} ==")
                for row in run_pair(t, rep):
                    rows.append(row)
                    fh.write(json.dumps(row) + "\n")
                    fh.flush()
    report(rows)


def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 4) if xs else None


def report(rows):
    A = [r for r in rows if r["arm"] == "A"]
    B = [r for r in rows if r["arm"] == "B"]
    by = {}
    for r in rows:
        by.setdefault((r["task"], r["rep"]), {})[r["arm"]] = r
    cost_delta, tok_delta = [], []
    a_wins = b_wins = ties = 0
    for (_, _), pair in by.items():
        if "A" in pair and "B" in pair:
            if pair["A"]["cost_usd"] and pair["B"]["cost_usd"]:
                cost_delta.append(pair["B"]["cost_usd"] - pair["A"]["cost_usd"])
            ta = (pair["A"]["in_tok"] + pair["A"]["out_tok"] + pair["A"]["cache_read"] + pair["A"]["cache_create"])
            tb = (pair["B"]["in_tok"] + pair["B"]["out_tok"] + pair["B"]["cache_read"] + pair["B"]["cache_create"])
            tok_delta.append(tb - ta)
            w = (pair["A"].get("judge") or {}).get("winner")
            a_wins += w == "A"; b_wins += w == "B"; ties += w == "tie"

    def passrate(rs):
        g = [r for r in rs if r["suite_green"]]
        return f"{len(g)}/{len(rs)}"

    lines = [
        "# Claude Code vs PandemoniumProtocol — A/B results", "",
        f"Agent model: {AGENT_MODEL} | Judge: {JUDGE_MODEL} | runs: {len(rows)}", "",
        "## Headline (paired, B = with protocol)", "",
        f"- Mean cost/task: A=${_mean([r['cost_usd'] for r in A])}  B=${_mean([r['cost_usd'] for r in B])}",
        f"- Mean cost delta (B-A), paired: ${_mean(cost_delta)}  (negative = protocol cheaper)",
        f"- Mean total-token delta (B-A), paired: {_mean(tok_delta)}",
        f"- Mean turns: A={_mean([r['num_turns'] for r in A])}  B={_mean([r['num_turns'] for r in B])}",
        "",
        "## Quality (noisy at this N — do not over-read)", "",
        f"- Test-suite green (fixed + no regression): A={passrate(A)}  B={passrate(B)}",
        f"- Blind judge wins: A={a_wins}  B={b_wins}  tie={ties}",
        "",
        "## Token breakdown (mean per run)", "",
        f"- A: in={_mean([r['in_tok'] for r in A])} out={_mean([r['out_tok'] for r in A])} "
        f"cache_create={_mean([r['cache_create'] for r in A])} cache_read={_mean([r['cache_read'] for r in A])}",
        f"- B: in={_mean([r['in_tok'] for r in B])} out={_mean([r['out_tok'] for r in B])} "
        f"cache_create={_mean([r['cache_create'] for r in B])} cache_read={_mean([r['cache_read'] for r in B])}",
        "  (Arm B carries a fixed cache_create tax from ~14 extra MCP tool schemas every run;",
        "   its benefit is variable — fewer whole-file reads. On a small repo the tax can exceed savings.)",
        "",
        "## Per task", "",
    ]
    for (task, rep), pair in sorted(by.items()):
        for arm in ("A", "B"):
            r = pair.get(arm)
            if r:
                j = r.get("judge") or {}
                lines.append(f"- {task} r{rep} {arm}: green={r['suite_green']} "
                             f"cost=${r['cost_usd']} turns={r['num_turns']} "
                             f"score={j.get('score_'+arm)} wall={r['wall_s']}s")
    (BENCH / "report.md").write_text("\n".join(str(x) for x in lines), encoding="utf-8")
    log(f"REPORT written -> {BENCH / 'report.md'}")
    print("\n".join(str(x) for x in lines))


if __name__ == "__main__":
    main()
