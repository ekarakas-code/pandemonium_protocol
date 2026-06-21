"""Token-budgeted context pack assembler — the primary output for LLM agents.

Runs hybrid retrieval on the task, groups hits by file, and greedily fills a markdown
pack within the token budget: cheap structural sections (task, project area, file
list, inspection order, related tests, budget summary) are always included; expensive
per-file code excerpts fill the remaining budget in score order.
"""

from __future__ import annotations

from typing import List, Optional

from pandemonium.models import SearchResult
from pandemonium.retrieval.hybrid_search import Retriever
from pandemonium.retrieval.tests_finder import find_tests
from pandemonium.tokens.counter import TokenCounter


class ContextPacker:
    def __init__(self, settings, retriever: Optional[Retriever] = None,
                 counter: Optional[TokenCounter] = None):
        self.settings = settings
        self.retriever = retriever or Retriever(settings)
        tokenizer = settings.section("context_pack").get("tokenizer", "cl100k_base")
        self.counter = counter or TokenCounter(tokenizer)

    def build(self, task: str, token_budget: Optional[int] = None,
              mode: Optional[str] = None) -> str:
        cp = self.settings.section("context_pack")
        budget = int(token_budget or cp.get("default_token_budget", 4000))
        max_chunk_chars = int(cp.get("max_chunk_chars", 1600))
        reserve = 220  # leave room for the trailing cheap sections

        results = self.retriever.search(task, mode=mode)  # Step 6: mode re-ranks only
        order: List[str] = []
        by_path: dict[str, List[SearchResult]] = {}
        for r in results:
            by_path.setdefault(r.path, []).append(r)
            if r.path not in order:
                order.append(r.path)

        out: List[str] = ["# Context Pack", "", f"## Task\n{task}"]

        areas: List[str] = []
        for path in order:
            top = path.split("/")[0] if "/" in path else "(root)"
            if top not in areas:
                areas.append(top)
        if areas:
            out.append("## Project Area\n" + ", ".join(areas[:6]))

        if order:
            listing = ["## Relevant Files"]
            for i, path in enumerate(order, 1):
                listing.append(f"{i}. `{path}` — {by_path[path][0].reason or 'relevant'}")
            out.append("\n".join(listing))
        else:
            out.append("_No indexed context matched this task. Try `pandemonium index .` "
                       "first, or rephrase the task._")

        # Per-file detail, greedily within budget.
        if order:
            out.append("## File Details")
            truncated = False
            for path in order:
                block = self._file_block(path, by_path[path], max_chunk_chars)
                projected = self.counter.count("\n\n".join(out + [block]))
                if projected > budget - reserve and len(out) > 4:
                    truncated = True
                    break
                out.append(block)
            if truncated:
                out.append(f"_(detail truncated to fit the {budget}-token budget; "
                           f"{len(order)} files matched)_")

        out.append("## Suggested Inspection Order\n"
                   + "\n".join(f"{i}. `{p}`" for i, p in enumerate(order[:6], 1)))

        tests = find_tests(self.retriever.sqlite, self.retriever.repo_id, task)
        if tests:
            out.append("## Related Tests\n" + "\n".join(f"- `{t}`" for t in tests[:8]))

        out.append("## Risks\n"
                   "- Verify with the related tests above before relying on retrieved context.\n"
                   "- Retrieval is heuristic; confirm exact behavior in the cited line ranges.")

        body = "\n\n".join(out)
        used = self.counter.count(body)
        return body + f"\n\n## Token Budget\nused ~{used} / {budget} tokens"

    def _file_block(self, path: str, hits: List[SearchResult], max_chunk_chars: int) -> str:
        ranked = sorted(hits, key=lambda r: r.score, reverse=True)
        top = ranked[0]
        lines = [f"### `{path}`", f"Reason: {top.reason or 'relevant to the task'}"]

        frow = self.retriever.sqlite.get_file(self.retriever.repo_id, path)
        if frow is not None and frow["summary"]:
            lines.append(f"Summary: {frow['summary']}")

        symbols = [r for r in ranked if r.symbol_name]
        if symbols:
            sym = ", ".join(
                f"`{r.symbol_name}` (L{r.start_line}-{r.end_line})" for r in symbols[:6])
            lines.append(f"Symbols: {sym}")

        excerpt = (top.content or "")[:max_chunk_chars]
        lang = top.language or ""
        lines.append(f"```{lang}\n{excerpt}\n```")
        return "\n".join(lines)

    def close(self) -> None:
        self.retriever.close()


def build_context_pack(settings, task: str, token_budget: Optional[int] = None,
                       mode: Optional[str] = None) -> str:
    packer = ContextPacker(settings)
    try:
        return packer.build(task, token_budget=token_budget, mode=mode)
    finally:
        packer.close()
