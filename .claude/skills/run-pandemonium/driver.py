#!/usr/bin/env python
"""Driver / smoke harness for the PandemoniumProtocol protocol — "start using it".

Drives the protocol end-to-end the way an agent actually uses it: builds an index for a
repo, then exercises the read surface (search / get / symbol / graph / impact / context)
via the real `pandemonium` CLI, and boots the MCP stdio server with the official MCP
client and calls a tool. Prints PASS/FAIL per step; exit code is non-zero on any failure.

Usage (from the PandemoniumProtocol repo root, with its venv active or on PATH):
    python .claude/skills/run-pandemonium/driver.py            # self-contained C++ sample
    python .claude/skills/run-pandemonium/driver.py --repo D:/path/to/your/repo
    python .claude/skills/run-pandemonium/driver.py --no-mcp   # skip the MCP boot (faster)
    python .claude/skills/run-pandemonium/driver.py --keep     # keep the sample/index dir

The first `index` loads the embedding model (~130 MB; downloaded once, then cached), so a
cold run takes ~30-60 s. Everything after that is fast.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SAMPLE_FILES = {
    "src/physics.h": (
        "#pragma once\n"
        "namespace rts { namespace sim {\n"
        "/// Integrates one simulation step from a body's velocity (header constexpr).\n"
        "constexpr float computeStepFromVelocity(float v) { return v * 0.016f; }\n"
        "}}\n"
    ),
    "src/world.cpp": (
        '#include "physics.h"\n'
        "namespace rts { namespace sim {\n"
        "class World { public:\n"
        "  // A nested-namespace qualified call — the universal C++ idiom.\n"
        "  void tick(float vel) { float s = rts::sim::computeStepFromVelocity(vel); }\n"
        "};\n"
        "}}\n"
    ),
}
SAMPLE_REF = "src/physics.h::rts.sim.computeStepFromVelocity"

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
_results: list[tuple[str, bool, str]] = []


def find_pandemonium() -> str:
    """The `pandemonium` console script — prefer the venv next to this interpreter."""
    bindir = Path(sys.executable).parent
    for name in ("pandemonium.exe", "pandemonium"):
        cand = bindir / name
        if cand.exists():
            return str(cand)
    found = shutil.which("pandemonium")
    if found:
        return found
    sys.exit(f"{RED}pandemonium console script not found.{RESET} "
             "Install it: `pip install -e .` into this Python's environment.")


def step(name: str, cmd: list[str], expect: str | None = None) -> str:
    """Run a CLI command; PASS iff exit 0 and (expect is None or expect in output)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    ok = proc.returncode == 0 and (expect is None or expect in out)
    _results.append((name, ok, "" if ok else out[-600:]))
    mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {name}")
    return out


def make_sample(root: Path) -> None:
    for rel, body in SAMPLE_FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def mcp_smoke(pandemonium: str, repo: str) -> None:
    """Boot the real stdio MCP server and call a tool through the official client."""
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run() -> tuple[int, str]:
        params = StdioServerParameters(command=pandemonium,
                                       args=["serve-mcp", "--repo", repo])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                res = await session.call_tool("repo_get", {"ref": SAMPLE_REF,
                                                            "view": "signature"})
                txt = res.content[0].text if res.content else ""
                return len(tools), txt

    try:
        ntools, txt = asyncio.run(asyncio.wait_for(run(), timeout=150))
        ok = ntools > 0 and "computeStepFromVelocity" in txt
        _results.append(("mcp: boot + repo_get(view=signature)", ok, "" if ok else txt[:400]))
        print(f"  [{GREEN + 'PASS' + RESET if ok else RED + 'FAIL' + RESET}] "
              f"mcp: boot ({ntools} tools) + repo_get(view=signature)")
    except Exception as exc:  # noqa: BLE001 - smoke surfaces any boot failure as FAIL
        _results.append(("mcp: boot", False, repr(exc)))
        print(f"  [{RED}FAIL{RESET}] mcp: boot -> {exc!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Drive the PandemoniumProtocol protocol end-to-end.")
    ap.add_argument("--repo", help="Drive an existing repo instead of the built-in C++ sample.")
    ap.add_argument("--no-mcp", action="store_true", help="Skip the MCP stdio boot check.")
    ap.add_argument("--keep", action="store_true", help="Keep the generated sample dir.")
    args = ap.parse_args()

    pandemonium = find_pandemonium()
    print(f"{DIM}pandemonium: {pandemonium}{RESET}")

    tmp = None
    if args.repo:
        repo = str(Path(args.repo).resolve())
        ref, ident = None, None  # unknown for an arbitrary repo -> skip ref-specific steps
    else:
        tmp = Path(tempfile.mkdtemp(prefix="pande-demo-"))
        repo = str(tmp)
        make_sample(tmp)
        ref, ident = SAMPLE_REF, "computeStepFromVelocity"
        print(f"{DIM}sample repo: {repo}{RESET}")

    print("\n== build the index ==")
    step("init", [pandemonium, "init", repo])
    step("index --full (loads embedding model on first run)",
         [pandemonium, "index", repo, "--full"], expect="Indexed")

    print("\n== drive the read surface (CLI) ==")
    if ident:
        step("search <identifier> (exact short-circuit)",
             [pandemonium, "search", ident, "--repo", repo], expect="score=1.0")
        step("symbol <name>", [pandemonium, "symbol", ident, "--repo", repo], expect=ident)
        step("get --view signature",
             [pandemonium, "get", ref, "--view", "signature", "--repo", repo],
             expect="view=signature")
        step("impact (callers + prod/test split)",
             [pandemonium, "impact", ref, "--repo", repo], expect="callers")
        step("graph (callees/callers/inherits)",
             [pandemonium, "graph", ref, "--repo", repo], expect="Graph")
    step("context (token-budgeted pack)",
         [pandemonium, "context", "simulation step from velocity", "--repo", repo],
         expect="Context Pack")
    step("changed (staleness dry-run)", [pandemonium, "changed", "--repo", repo])
    step("map (repo orientation)", [pandemonium, "map", "--repo", repo])

    if not args.no_mcp:
        print("\n== boot + drive the MCP server (stdio) ==")
        if ref:
            mcp_smoke(pandemonium, repo)
        else:
            print(f"  {DIM}(skipped: --repo given, no known ref to fetch){RESET}")

    if tmp and not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    elif tmp:
        print(f"\n{DIM}kept sample dir: {tmp}{RESET}")

    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print(f"\n{'='*48}\n{passed}/{total} steps passed")
    for name, ok, detail in _results:
        if not ok:
            print(f"{RED}FAILED{RESET} {name}\n{DIM}{detail}{RESET}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
