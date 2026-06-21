"""Heuristic test discovery (MVP, no call graph).

Find files that (a) match test-naming conventions and (b) reference the target via the
keyword index. Good enough to point an agent at the right tests before it edits code.
"""

from __future__ import annotations

import re
from typing import List

_TEST_TOKENS = {"test", "tests", "spec"}
# Split a filename into word tokens on non-alphanumerics AND camelCase boundaries, so
# `FooTests`/`foo_test`/`foo.spec.js` yield a standalone `test`/`tests`/`spec` token while
# `contest`/`latest`/`fastest` stay a single token (the old substring `"test" in base`
# misclassified those as tests).
_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")


def _name_tokens(basename: str) -> set:
    tokens = set()
    for part in re.split(r"[^A-Za-z0-9]+", basename):
        for tok in _TOKEN_RE.findall(part):
            tokens.add(tok.lower())
    return tokens


def is_test_path(path: str) -> bool:
    low = path.lower()
    if low.startswith(("test/", "tests/")) or "/tests/" in low or "/test/" in low:
        return True
    base = path.rsplit("/", 1)[-1]
    if base.lower().startswith("conftest"):  # pytest support file
        return True
    return bool(_name_tokens(base) & _TEST_TOKENS)


def find_tests(sqlite, repo_id: str, target: str, limit: int = 10) -> List[str]:
    rows = sqlite.fts.search(target, limit=40)
    meta = sqlite.get_chunks([cid for cid, _ in rows])
    seen: List[str] = []
    for cid, _ in rows:
        row = meta.get(cid)
        if row is None:
            continue
        path = row["path"]
        if is_test_path(path) and path not in seen:
            seen.append(path)
        if len(seen) >= limit:
            break
    return seen
