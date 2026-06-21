"""Test support: a deterministic offline embedder + store helpers.

`FakeEmbedder` is a bag-of-words hashing embedder so the suite never downloads the
130 MB model and stays fully offline/deterministic. Dim 64 keeps it fast; the LanceDB
table dim is taken from settings, so we override `embedding.dim` to match.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import List

from pandemonium.config import Settings

DIM = 64
_TOK = re.compile(r"[A-Za-z0-9_]+")


class FakeEmbedder:
    def __init__(self, dim: int = DIM):
        self.dim = dim

    def _vec(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _TOK.findall((text or "").lower()):
            idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_documents(self, texts) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vec(text)


def make_settings(root) -> Settings:
    settings = Settings.load(root)
    settings.data["embedding"]["dim"] = DIM
    return settings


def reindex(settings, incremental: bool = True):
    from pandemonium.indexer.index_runner import Indexer
    indexer = Indexer(settings, embedder=FakeEmbedder())
    try:
        return indexer.run(incremental=incremental)
    finally:
        indexer.close()


def make_retriever(settings):
    from pandemonium.retrieval.hybrid_search import Retriever
    return Retriever(settings, embedder=FakeEmbedder())


def make_packer(settings):
    from pandemonium.retrieval.context_packer import ContextPacker
    return ContextPacker(settings, retriever=make_retriever(settings))
