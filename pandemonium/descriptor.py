"""Descriptor builder — the text we actually embed (Phase 2).

We embed a clean, labeled natural-language descriptor (summary + signature +
qualified name + tags/identifiers) rather than raw source. The raw code is stored
separately and fetched on demand via repo_get. This is the central bet: the vector
channel answers "which unit is conceptually relevant?"; exact matching is handled by
the symbol/keyword channels; code delivery is handled by repo_get.

Raw-code and descriptor+excerpt remain bake-off variants for Phase 5.
"""

from __future__ import annotations

from typing import Optional

_TAG_LABELS = [
    ("Responsibilities", "responsibilities"),
    ("Depends on", "depends_on"),
    ("Domain", "domain"),
    ("Search terms", "search_terms"),
    ("Side effects", "side_effects"),
    ("Entrypoints", "entrypoints"),
]


def build_descriptor(path: str, scope: Optional[str], language: Optional[str],
                     qualified_name: Optional[str], signature: Optional[str],
                     summary: Optional[str], tags: Optional[dict]) -> str:
    lines = [f"Language: {language or ''}",
             f"Scope: {scope or ''}",
             f"Path: {path}"]
    if qualified_name:
        lines.append(f"Qualified name: {qualified_name}")
    if signature:
        lines.append(f"Signature: {signature}")
    if summary:
        lines.append(f"Summary: {summary}")
    tags = tags or {}
    for label, key in _TAG_LABELS:
        vals = tags.get(key) or []
        if vals:
            lines.append(f"{label}: {', '.join(vals)}")
    return "\n".join(lines)
