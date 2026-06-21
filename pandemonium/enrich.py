"""Enrichment provider — overrides a chunk's heuristic summary + tags with a
higher-quality source. Misses always fall back to the heuristic automatically.

Providers (`enrichment.provider` in config):
  heuristic   default — no override; the indexer's heuristic summary + tags stand.
  cache       read precomputed enrichments (ref -> {summary, tags}) from a JSON file
              (e.g. produced by the `enrich-symbols` subagent workflow or a batch run).
  claude_cli  shell out to the `claude` CLI per symbol — uses existing Claude Code
              auth (no API key). Fine for standalone/small runs; for bulk indexing
              pre-populate a cache instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from pandemonium.tags import TAG_FIELDS


class Enrichment:
    __slots__ = ("summary", "tags")

    def __init__(self, summary: Optional[str], tags: dict):
        self.summary = summary
        self.tags = tags


def _coerce_tags(d: dict) -> dict:
    return {k: list(d.get(k) or []) for k in TAG_FIELDS}


class Enricher:
    """Base = heuristic: no override."""

    def get(self, ref: Optional[str], **ctx) -> Optional[Enrichment]:
        return None


class CacheEnricher(Enricher):
    def __init__(self, cache: dict):
        self.cache = cache

    def get(self, ref, **ctx):
        entry = self.cache.get(ref) if ref else None
        if not entry:
            return None
        return Enrichment(entry.get("summary") or None, _coerce_tags(entry))


class ClaudeCliEnricher(Enricher):
    """Per-symbol enrichment via `claude -p` (no API key). Experimental/slow for bulk."""

    def __init__(self, model: Optional[str] = None, timeout: int = 60):
        self.model = model
        self.timeout = timeout

    def get(self, ref, *, code: str = "", language: str = "", **ctx):
        if not ref or not code.strip():
            return None
        instruction = (
            "Return ONLY a JSON object (no prose) describing this code symbol, grounded "
            "strictly in the code:\n"
            '{"summary": "<=160 chars", "responsibilities": [], "depends_on": [], '
            '"domain": [], "search_terms": [], "side_effects": [], "entrypoints": []}\n\n'
            f"Symbol: {ref}\nLanguage: {language}\n\n{code[:4000]}"
        )
        try:
            cmd = ["claude", "-p", instruction]
            if self.model:
                cmd += ["--model", self.model]
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=self.timeout)
            data = _extract_json(out.stdout)
            if not data:
                return None
            return Enrichment(data.get("summary") or None, _coerce_tags(data))
        except Exception:
            return None


def _extract_json(text: str) -> Optional[dict]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None


def load_enricher(settings) -> Enricher:
    sec = settings.section("enrichment")
    provider = sec.get("provider", "heuristic")
    if provider == "cache":
        path = Path(sec.get("cache_path", ".pandemonium/enrichment.json"))
        if not path.is_absolute():
            path = settings.repo_root / path
        if path.exists():
            try:
                return CacheEnricher(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                return Enricher()
        return Enricher()
    if provider == "claude_cli":
        if getattr(settings, "offline", True):
            # Offline kill-switch: claude_cli shells out to the `claude` CLI (which calls
            # the network). Stay heuristic unless offline is explicitly disabled.
            from pandemonium.logging.trace import trace
            trace("offline=true: claude_cli enrichment disabled, using heuristic "
                  "(set offline: false in pandemonium.yaml to allow the claude CLI)")
            return Enricher()
        return ClaudeCliEnricher(model=sec.get("model"))
    return Enricher()
