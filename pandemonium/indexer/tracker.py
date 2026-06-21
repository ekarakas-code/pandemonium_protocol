"""Change tracking.

MVP uses hash-based snapshot tracking (no git required): a file is unchanged iff its
content hash matches the stored `files.content_hash`. `GitTracker` is a placeholder
that records the `git` tracking mode; real `git diff` integration is a later phase and
currently behaves identically to the snapshot tracker.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


class SnapshotTracker:
    mode = "snapshot"

    @staticmethod
    def is_unchanged(stored_row: Optional[Any], content_hash: str) -> bool:
        return stored_row is not None and stored_row["content_hash"] == content_hash


class GitTracker(SnapshotTracker):
    mode = "git"


def select_tracker(repo_root: Any) -> SnapshotTracker:
    return GitTracker() if (Path(repo_root) / ".git").exists() else SnapshotTracker()
