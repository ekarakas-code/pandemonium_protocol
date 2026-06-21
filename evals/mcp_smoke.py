"""Real MCP boot check: spawn `serve-mcp` as a stdio server and drive it with the
official MCP client (initialize -> tools/list -> call a tool). Hard timeout so it
can't hang. Usage: python evals/mcp_smoke.py [command]   (default: the venv exe)."""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CMD = sys.argv[1] if len(sys.argv) > 1 else ".venv/Scripts/pandemonium.exe"


async def run() -> None:
    params = StdioServerParameters(command=CMD, args=["serve-mcp", "--repo", "."])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"BOOTED via '{CMD}': {len(tools)} tools")
            print("  " + ", ".join(sorted(t.name for t in tools)))
            res = await session.call_tool("repo_symbol", {"symbol_name": "hybrid_search"})
            txt = res.content[0].text if res.content else ""
            print("  repo_symbol(hybrid_search) -> " + (txt.splitlines() or ["(empty)"])[0])


asyncio.run(asyncio.wait_for(run(), timeout=120))
print("MCP_SMOKE_OK")
