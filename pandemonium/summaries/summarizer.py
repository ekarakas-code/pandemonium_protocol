"""Summarizer interface + heuristic (default) implementation + provider selection.

`HeuristicSummarizer` is fully local and deterministic: it extracts signatures, the
first docstring/leading comment, and the symbol list. The opt-in external-LLM
provider lives in `external_llm.py` and is only selected when
`summaries.provider == external_llm` AND `summaries.enabled` (and the `[llm]` extra
is installed).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

_DOCSTRING_RE = re.compile(r'("""|\'\'\')(.*?)(\1)', re.DOTALL)
_COMMENT_PREFIXES = ("#", "//", "/*", "*", "<!--", "--")
# Line-comment markers for the leading-comment extractor. `#` is deliberately EXCLUDED:
# for the parseable non-Python languages (C++/C#/JS/TS) `#` is a preprocessor directive
# (`#include`/`#define`), not a doc comment — and Python keeps its docstring path.
_LEADING_LINE_PREFIXES = ("///", "//")


def first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    m = re.search(r"\.\s", text)
    end = m.start() + 1 if m else len(text)
    return text[:min(end, limit)].strip()


def extract_docstring(content: str) -> str:
    m = _DOCSTRING_RE.search(content or "")
    return first_sentence(m.group(2).strip()) if m else ""


def _clean_comment_line(line: str) -> str:
    """Strip line/block comment scaffolding from one line -> its prose."""
    s = line.strip()
    for pre in ("/**", "///", "//", "/*"):  # longest-first so /// isn't eaten by //
        if s.startswith(pre):
            s = s[len(pre):].strip()
            break
    if s.endswith("*/"):
        s = s[:-2].strip()
    if s.startswith("*"):       # JSDoc/Doxygen continuation bullet " * text"
        s = s[1:].strip()
    return s.strip()


def extract_leading_comment(preceding: List[str]) -> str:
    """First sentence of the doc comment in the lines immediately ABOVE a symbol — the
    Doxygen/JSDoc/`//` convention for C++/C#/JS/TS, where the doc lives above the symbol
    (not in the body like a Python docstring). Returns '' when the symbol isn't preceded
    by a comment block. `preceding` is the handful of source lines just above the symbol."""
    if not preceding:
        return ""
    i = len(preceding) - 1
    while i >= 0 and not preceding[i].strip():  # skip blank lines between comment & symbol
        i -= 1
    if i < 0:
        return ""
    last = preceding[i].strip()
    block: List[str] = []
    if last.endswith("*/") or last.startswith("/*"):  # block comment /* ... */ or /** */
        while i >= 0:
            block.append(preceding[i])
            if "/*" in preceding[i]:
                break
            i -= 1
        block.reverse()
    elif last.startswith(_LEADING_LINE_PREFIXES):       # a run of //  /// lines
        while i >= 0 and preceding[i].strip().startswith(_LEADING_LINE_PREFIXES):
            block.append(preceding[i])
            i -= 1
        block.reverse()
    else:
        return ""
    text = " ".join(p for p in (_clean_comment_line(b) for b in block) if p)
    return first_sentence(text)


class Summarizer:
    """Interface. All methods return a short one-line summary string."""

    def summarize_symbol(self, symbol, content: str, language: Optional[str] = None,
                         preceding: Optional[List[str]] = None) -> str:  # pragma: no cover
        raise NotImplementedError

    def summarize_file(self, path: str, language: str, text: str, symbols: List) -> str:
        raise NotImplementedError

    def summarize_chunk(self, content: str, language: str) -> str:
        raise NotImplementedError


class HeuristicSummarizer(Summarizer):
    def summarize_symbol(self, symbol, content: str, language: Optional[str] = None,
                         preceding: Optional[List[str]] = None) -> str:
        sig = (symbol.signature or symbol.name).strip()
        doc = extract_docstring(content)  # Python triple-quote docstring (in-body)
        # Non-Python languages put the doc ABOVE the symbol (Doxygen/JSDoc/`//`). Without
        # this, every C++/C#/JS/TS summary collapses to the bare signature, leaving the
        # embedded descriptor near-meaningless — the vector channel's "central bet" unfunded.
        if not doc and language and language != "python":
            doc = extract_leading_comment(preceding or [])
        return f"{sig} — {doc}" if doc else sig

    def summarize_file(self, path: str, language: str, text: str, symbols: List) -> str:
        top = [s for s in symbols if "." not in (s.qualified_name or s.name)]
        names = [s.name for s in top][:12]
        doc = ""
        if language == "python":
            doc = extract_docstring(text[:2000])
        elif language in ("markdown", "text"):
            for line in text.splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped:
                    doc = first_sentence(stripped)
                    break
        parts = []
        if doc:
            parts.append(doc)
        if names:
            parts.append("Defines: " + ", ".join(names))
        return " | ".join(parts) if parts else Path(path).name

    def summarize_chunk(self, content: str, language: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(_COMMENT_PREFIXES):
                return first_sentence(stripped, 120)
        for line in content.splitlines():
            if line.strip():
                return first_sentence(line.strip(), 120)
        return ""


def get_summarizer(settings, audit=None) -> Summarizer:
    s = settings.section("summaries")
    if s.get("provider") == "external_llm" and s.get("enabled"):
        if getattr(settings, "offline", True):
            # Offline kill-switch: this provider sends code to the Anthropic API.
            from pandemonium.logging.trace import trace
            trace("offline=true: external_llm summaries disabled, using heuristic "
                  "(set offline: false in pandemonium.yaml to allow API calls)")
            return HeuristicSummarizer()
        try:
            from pandemonium.summaries.external_llm import ExternalLLMSummarizer
            return ExternalLLMSummarizer.from_settings(settings, audit=audit)
        except Exception:
            # Missing [llm] extra / API key -> stay local.
            return HeuristicSummarizer()
    return HeuristicSummarizer()
