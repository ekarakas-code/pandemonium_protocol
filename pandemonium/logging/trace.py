"""Lightweight stderr tracing for the long-running MCP server.

MCP speaks JSON-RPC over STDOUT, so any diagnostic line MUST go to STDERR — a stray
write to stdout corrupts the protocol stream. Claude Code captures each MCP server's
stderr into its own logs (…/mcp-logs-pandemonium/*.jsonl), so these lines are how you
follow a live server: startup, embedding warm-up, and per-tool START / OK / FAILED with
elapsed time. Best-effort: tracing must never break the operation it records.
"""

from __future__ import annotations

import sys

from pandemonium.util import now_iso


def trace(msg: str) -> None:
    try:
        sys.stderr.write(f"[pandemonium {now_iso()}] {msg}\n")
        sys.stderr.flush()
    except Exception:
        # Diagnostics must never take down the server.
        pass
