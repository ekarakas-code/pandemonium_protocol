"""Append-only JSONL audit log.

Records repositories/files indexed & skipped, queries, MCP tool calls, context
packs, and reindex operations (docs §21.4). Auditing must never break the
operation it is recording, so all writes are best-effort.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pandemonium.util import now_iso


class AuditLog:
    def __init__(self, path: Any):
        self.path = Path(path)

    def log(self, event: str, **fields: Any) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            record = {"ts": now_iso(), "event": event}
            record.update(fields)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Never let auditing failures propagate.
            pass
