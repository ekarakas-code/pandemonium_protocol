"""Self-healing incremental reindex for the long-lived MCP server (auto-indexer, Option A).

The MCP server is reactive (stdio — it can't watch the filesystem and push), so the index
drifts the moment an agent edits a file. Rather than rely on the agent remembering
``repo_reindex_changed``, the read tools call :meth:`AutoReindexer.maybe_refresh` first: a
cheap, debounced ``mtime`` scan detects changed / added / removed files and, only then,
triggers an incremental reindex on the **already-warm** embedder (no cold model load).

Deliberately synchronous + single-threaded: the reindex runs on the calling (event-loop)
thread, NOT a background watcher thread. A background thread doing native embedding / import
work is exactly the Windows loader-lock class that froze the server before — so we don't go
there. The cost model instead is "pay a tiny ``stat`` sweep per read (debounced), and an
incremental reindex only when something actually changed."

Limitation: detection is by ``mtime`` (+ path set). An editor always bumps ``mtime`` on
write, so real edits are caught; a content change that somehow preserves ``mtime`` is not.
The incremental reindex is itself content-hash gated, so a spurious ``mtime`` bump with
identical content costs only a scan, not a re-embed.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Dict, Optional

from pandemonium.indexer.ignore import IgnoreMatcher
from pandemonium.indexer.scanner import scan
from pandemonium.models import IndexStats


class AutoReindexer:
    def __init__(self, settings, embedder=None, min_interval: float = 2.0,
                 clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.embedder = embedder            # warm embedder, reused so reindex pays no cold load
        self.min_interval = float(min_interval)
        self._clock = clock
        self._last_check = float("-inf")
        self._snapshot: Optional[Dict[str, float]] = None  # rel_path -> mtime; None = unprimed

    def _scan_mtimes(self) -> Dict[str, float]:
        root = Path(self.settings.repo_root)
        matcher = IgnoreMatcher.load(root)
        max_bytes = self.settings.section("indexing").get("max_file_bytes", 2_000_000)
        out: Dict[str, float] = {}
        for cand in scan(root, matcher, max_file_bytes=max_bytes, skipped_large=[]):
            try:
                out[cand.rel_path] = Path(cand.abs_path).stat().st_mtime
            except OSError:
                pass
        return out

    def prime(self) -> None:
        """Record current file mtimes as the baseline. Call once when the index is known
        fresh (e.g. at server start, right after the warm-up index)."""
        self._snapshot = self._scan_mtimes()
        self._last_check = self._clock()

    def maybe_refresh(self, force: bool = False) -> Optional[IndexStats]:
        """If files changed since the last baseline (debounced by ``min_interval``), run an
        incremental reindex on the warm embedder and return its stats; else return None.
        The caller should reset its retriever/packer when stats.indexed/deleted > 0 so the
        next query reads fresh data."""
        now = self._clock()
        if not force and (now - self._last_check) < self.min_interval:
            return None
        self._last_check = now
        current = self._scan_mtimes()
        if self._snapshot is None:           # first call primes the baseline, no reindex
            self._snapshot = current
            return None
        if current == self._snapshot:
            return None
        from pandemonium.indexer.index_runner import Indexer
        indexer = Indexer(self.settings, embedder=self.embedder)
        try:
            stats = indexer.run(incremental=True)
        finally:
            indexer.close()
        self._snapshot = current
        return stats
