"""Offline determinism check for the qa_ab_runner fixes (no claude calls).
Exercises the pure functions + report aggregation against synthetic rows."""
import sys, json, re, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import qa_ab_runner as q

fails = []
def ck(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)

T = {t["id"]: t for t in q.TASKS}

# ---- parse_answer ----
f, s = q.parse_answer("blah\nANSWER: file=pandemonium/util.py symbol=fingerprint_for")
ck("parse ok", f == "pandemonium/util.py" and s == "fingerprint_for")
ck("parse trailing prose", q.parse_answer("ANSWER: file=x symbol=foo (the method)")[1] == "foo (the method)")
ck("parse none", q.parse_answer("no answer line here") == (None, None))

# ---- _word_in (FIX #6a) ----
ck("word boundary blocks substring", q._word_in("tick", "getticker") is False)
ck("word boundary matches scoped", q._word_in("tick", "world::tick") is True)
ck("word embedding blocks plural", q._word_in("embedding", "embeddings model") is False)

# ---- score ----
# FIX #5: no ANSWER line => miss even if prose names the gold
sc = q.score(T["pp_fingerprint"], "the answer is fingerprint_for in pandemonium/util.py")
ck("FIX#5 no-answer => not parsed", sc["parsed"] is False and sc["hit"] is False)
# correct answer
sc = q.score(T["pp_fingerprint"], "ANSWER: file=pandemonium/util.py symbol=fingerprint_for")
ck("correct => hit", sc["hit"] is True)
# FIX #6a: getTicker must NOT satisfy 'tick'
sc = q.score(T["sg_world_tick"], "ANSWER: file=src/ecs/World.cpp symbol=getTicker")
ck("FIX#6a getTicker not sym_hit", sc["symbol_hit"] is False)
sc = q.score(T["sg_world_tick"], "ANSWER: file=src/ecs/World.cpp symbol=World::tick")
ck("World::tick sym_hit", sc["symbol_hit"] is True)
# FIX #6b: class name (== file stem) no longer a valid symbol for C#
sc = q.score(T["rv_reschedule"], "ANSWER: file=.../AppointmentRescheduleStore.cs symbol=AppointmentRescheduleStore")
ck("FIX#6b class-name not sym_hit", sc["symbol_hit"] is False)
sc = q.score(T["rv_reschedule"], "ANSWER: file=.../AppointmentRescheduleStore.cs symbol=ApplyAsync")
ck("FIX#6b method IS sym_hit", sc["symbol_hit"] is True)
# FIX #6c: backslash answer reaches the full-path/basename branch
sc = q.score(T["sg_astar"], r"ANSWER: file=src\spatial\AStar.cpp symbol=findPath")
ck("FIX#6c backslash file_hit", sc["file_hit"] is True)

# ---- _count_mcp_calls (FIX #8) ----
ev = [
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__pandemonium__repo_search"},
        {"type": "text", "text": "..."}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Grep"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__pandemonium__repo_get"}]}},
    {"type": "result", "result": "done", "usage": {}},
]
ck("FIX#8 counts only mcp__ tool_use", q._count_mcp_calls(ev) == 2)
ck("FIX#8 no mcp => 0", q._count_mcp_calls([{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}]) == 0)

# ---- report aggregation (FIX #1, #2, #4) ----
def srow(task, repo, arm, rep, cost, tok, mcp=0, num=3):
    return {"task": task, "repo": repo, "arm": arm, "rep": rep, "wall_s": 5.0, "warm_s": 0.0,
            "wall_adj_s": 5.0, "is_error": False, "error_reason": None, "num_turns": num,
            "cost_usd": cost, "in_tok": tok, "out_tok": 0, "cache_create": 0, "cache_read": 0,
            "tot_tok": tok, "mcp_calls": mcp, "ans_file": "f", "ans_symbol": "s", "parsed": True,
            "file_hit": True, "symbol_hit": True, "hit": True, "result": "", "stderr_tail": ""}

rows = [
    # task T1, 2 reps: per-rep B-A cost deltas are +0.5 and +1.0 -> mean 0.75 (proves BOTH reps used, FIX#1)
    srow("t1", "PP", "A", 0, 1.0, 100, mcp=0), srow("t1", "PP", "B", 0, 1.5, 150, mcp=3),
    srow("t1", "PP", "A", 1, 2.0, 200, mcp=0), srow("t1", "PP", "B", 1, 3.0, 250, mcp=2),
    # task T2: B errored -> excluded from means/rates/deltas; counted in errors line (FIX#4).
    # Pass repo="PP" so _err_rec's basename matches the other rows (single repo => 2 blocks).
    srow("t2", "PP", "A", 0, 5.0, 500, mcp=0),
    q._err_rec({"id": "t2", "repo": "PP"}, "B", 9.0, 0.0, "timeout") | {"rep": 0},
    # task T3: B non-error but 0 mcp -> [NO-MCP] flag (FIX#8)
    srow("t3", "PP", "A", 0, 4.0, 400, mcp=0), srow("t3", "PP", "B", 0, 4.2, 420, mcp=0),
]
tmp = pathlib.Path(tempfile.gettempdir()) / "qa_report_test.md"
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    q.report(rows, tmp)
out = tmp.read_text(encoding="utf-8")

# Overall delta uses t1(2 reps: +0.5,+1.0) + t3(+0.2) => mean 0.5667. The collapse-to-last-rep
# bug would instead give (1.0+0.2)/2 = 0.6, so 0.5667 (and NOT 0.6) proves both reps are used.
ck("FIX#1 paired cost delta uses BOTH reps (0.5667, not 0.6)", "cost=$0.5667" in out and "cost=$0.6 " not in out)
ck("FIX#2 paired tok delta uses BOTH reps (40.0)", "tok=40.0" in out)
ck("FIX#4 errors line present", re.search(r"errors: A=0 B=1", out) is not None)
ck("FIX#4 no literal 'None' in table", "None" not in out)
ck("FIX#8 NO-MCP warning present", "NO-MCP" in out and "never fired" in out)
ck("FIX#10 per-run shows reps r0 and r1", "/t1 r0 " in out and "/t1 r1 " in out)
ck("FIX#3 errored run tagged [ERR:timeout]", "[ERR:timeout]" in out)

print("\n=== qa_report_test.md (excerpt) ===")
print("\n".join(l for l in out.splitlines() if l.strip())[:1])  # keep output small
for l in out.splitlines():
    if "paired delta" in l or "errors:" in l or "NO-MCP" in l:
        print(l)

print("\nRESULT:", "ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}")
sys.exit(1 if fails else 0)
