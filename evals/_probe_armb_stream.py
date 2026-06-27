"""One-shot diagnostic: run a single Arm-B (protocol) invocation on the HARDEST impact task
with full stream-json captured, to settle whether mcp=0 is genuine non-use or a harness artifact.
Reuses the exact qa_ab_runner plumbing (CLAUDE exe, ARMB_SYSTEM, MCP config, offline env)."""
import json, sys
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
import qa_ab_runner as base
from qa_ab_runner import (CLAUDE, AGENT_MODEL, MAX_TURNS, DISALLOWED, ARMB_SYSTEM,
                          copy_env, sh, write_mcp)
import qa_impact_ab_runner as imp

OUT = Path(r"D:/_impact_probe"); OUT.mkdir(parents=True, exist_ok=True)
base.OUT = OUT
TARGET = sys.argv[1] if len(sys.argv) > 1 else "drawFilledRectsBatched"
tasks = json.loads(Path(r"D:/PandemoniumProtocol/evals/qa_impact_tasks_sg.json").read_text("utf-8"))
t = next(x for x in tasks if x["target"] == TARGET)
repo = t["repo"]
mcp = write_mcp(repo)
prompt = imp.QUESTION.format(target=t["target"], desc=t["desc"])
args = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
        "--model", AGENT_MODEL, "--dangerously-skip-permissions",
        "--max-turns", str(MAX_TURNS), "--disallowedTools", *(DISALLOWED + ["PowerShell"]),
        "--mcp-config", mcp, "--strict-mcp-config",
        "--append-system-prompt", imp.IMPACT_ARMB_SYSTEM]
print(f"[probe] task={TARGET} repo={repo}\n[probe] running Arm B ...", flush=True)
r = sh(args, cwd=repo, env=copy_env(repo), timeout=900)
(OUT / "armb_stream.jsonl").write_text(r.stdout or "", encoding="utf-8")
(OUT / "armb_stderr.txt").write_text(r.stderr or "", encoding="utf-8")

events = base._load_stream(r.stdout)
names = []
def walk(o):
    if isinstance(o, dict):
        if o.get("type") == "tool_use":
            names.append(o.get("name"))
        for v in o.values():
            walk(v)
    elif isinstance(o, list):
        for v in o:
            walk(v)
walk(events)

print("\n===== PROBE SUMMARY =====")
# 1) init/system event tool list
for ev in events:
    if isinstance(ev, dict) and ev.get("type") == "system":
        tl = ev.get("tools")
        if tl is not None:
            mcpt = [x for x in tl if str(x).startswith("mcp__")]
            print(f"INIT tools: {len(tl)} total; mcp__ tools present: {len(mcpt)}")
            print(f"  mcp__ tool names: {mcpt}")
            print(f"  has ToolSearch: {'ToolSearch' in tl}")
        break
# 2) every tool_use name
from collections import Counter
print("ALL tool_use names (count):", dict(Counter(names)))
# 3) client-side mcp counter + result
print("mcp__ count (_count_mcp_calls):", base._count_mcp_calls(events))
d = next((e for e in reversed(events) if isinstance(e, dict) and e.get("type") == "result"), None)
if d:
    print("num_turns:", d.get("num_turns"), "cost:", d.get("total_cost_usd"))
    print("RESULT tail:", (d.get("result") or "")[-500:])
print("stderr tail:", (r.stderr or "")[-300:])
