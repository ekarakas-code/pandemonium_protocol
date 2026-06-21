"""Walk the repository, prune ignored dirs, yield indexable file candidates."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, List, Optional

from pandemonium.indexer.ignore import IgnoreMatcher
from pandemonium.indexer.language_detector import detect
from pandemonium.util import rel_posix


@dataclass
class Candidate:
    abs_path: str
    rel_path: str  # repo-relative POSIX
    language: str
    size: int


def scan(repo_root: Any, matcher: IgnoreMatcher,
         max_file_bytes: int = 2_000_000,
         skipped_large: Optional[List[str]] = None) -> Iterator[Candidate]:
    """Yield indexable file candidates. Files over `max_file_bytes` are dropped — but no
    longer SILENTLY: their rel paths are appended to `skipped_large` (if provided) so the
    caller can surface them instead of an agent assuming a too-big file simply isn't there."""
    root = Path(repo_root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in place so we don't descend into them.
        dirnames[:] = [
            d for d in dirnames
            if not matcher.matches(rel_posix(Path(dirpath) / d, root))
        ]
        for fn in filenames:
            abs_p = Path(dirpath) / fn
            rel = rel_posix(abs_p, root)
            if matcher.matches(rel):
                continue
            language = detect(abs_p)
            if not language:
                continue
            try:
                size = abs_p.stat().st_size
            except OSError:
                continue
            if size > max_file_bytes:
                if skipped_large is not None:
                    skipped_large.append(rel)
                continue
            yield Candidate(str(abs_p), rel, language, size)
