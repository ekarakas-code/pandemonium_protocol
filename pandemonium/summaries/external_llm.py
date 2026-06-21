"""Opt-in external-LLM summarizer (off by default).

Only reachable when `summaries.provider == external_llm` and `summaries.enabled` is
true, with the `[llm]` extra installed (`pip install pandemonium[llm]`). It subclasses
the heuristic summarizer so symbol/chunk summaries stay local & cheap; only the
file-level summary is sent to the API. **Every external call is audit-logged** — this
is the one path that sends code off-machine.
"""

from __future__ import annotations

from typing import List, Optional

from pandemonium.summaries.summarizer import HeuristicSummarizer


class ExternalLLMSummarizer(HeuristicSummarizer):
    def __init__(self, model: str = "claude-haiku-4-5", max_tokens: int = 256, audit=None):
        self.model = model
        self.max_tokens = max_tokens
        self.audit = audit

    @classmethod
    def from_settings(cls, settings, audit=None) -> "ExternalLLMSummarizer":
        ext = settings.section("summaries").get("external", {})
        return cls(model=ext.get("model", "claude-haiku-4-5"),
                   max_tokens=int(ext.get("max_tokens", 256)), audit=audit)

    def _complete(self, prompt: str) -> str:
        import anthropic  # only imported when actually enabled

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            getattr(b, "text", "") for b in msg.content
            if getattr(b, "type", None) == "text"
        ).strip()

    def summarize_file(self, path: str, language: str, text: str, symbols: List) -> str:
        prompt = (
            f"Summarize this {language} source file in 1-2 sentences for a code-search "
            f"index. Be concrete about its responsibility. Path: {path}\n\n{text[:6000]}"
        )
        try:
            if self.audit:
                self.audit.log("summary_external_call", target="file", path=path,
                               model=self.model)
            out = self._complete(prompt)
            return out or super().summarize_file(path, language, text, symbols)
        except Exception:
            return super().summarize_file(path, language, text, symbols)
