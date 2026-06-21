"""Token counting for budgeting context packs.

tiktoken (`cl100k_base` by default) as a proxy, with a documented chars/4 fallback if
tiktoken is unavailable or its BPE file can't be fetched offline. Set
`TIKTOKEN_CACHE_DIR` before first use to pre-seed/cache for air-gapped runs.
"""

from __future__ import annotations


class TokenCounter:
    def __init__(self, encoding: str = "cl100k_base"):
        self.encoding_name = encoding
        self._enc = None  # None = unloaded, False = unavailable

    def _encoder(self):
        if self._enc is None:
            try:
                import tiktoken
                self._enc = tiktoken.get_encoding(self.encoding_name)
            except Exception:
                self._enc = False
        return self._enc or None

    def count(self, text: str) -> int:
        if not text:
            return 0
        enc = self._encoder()
        if enc is None:
            return max(1, len(text) // 4)
        try:
            return len(enc.encode(text))
        except Exception:
            return max(1, len(text) // 4)
