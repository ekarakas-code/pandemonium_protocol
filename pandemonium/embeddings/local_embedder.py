"""Local embeddings via sentence-transformers (bge-small, CPU).

The model is loaded lazily on first use so that commands which don't embed
(`init`, pure metadata lookups, unit tests) never pay the import/load cost.
"""

from __future__ import annotations

import threading
from typing import List, Optional, Sequence


class LocalEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", device: str = "cpu",
                 normalize: bool = True, batch_size: int = 32, query_prefix: str = "",
                 trust_remote_code: bool = False):
        self.model_name = model_name
        self.device = device
        self.normalize = normalize
        self.batch_size = batch_size
        self.query_prefix = query_prefix or ""
        self.trust_remote_code = trust_remote_code
        self._model = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings) -> "LocalEmbedder":
        e = settings.section("embedding")
        return cls(
            model_name=e.get("model", "BAAI/bge-small-en-v1.5"),
            device=e.get("device", "cpu"),
            normalize=e.get("normalize", True),
            batch_size=e.get("batch_size", 32),
            query_prefix=e.get("query_prefix", ""),
            trust_remote_code=e.get("trust_remote_code", False),
        )

    def _load(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    from pandemonium.logging.trace import trace
                    trace(f"loading embedding model {self.model_name} (device={self.device})")
                    # Local-first: use the already-cached snapshot WITHOUT contacting the
                    # Hub. By default sentence-transformers/huggingface_hub revalidate the
                    # cached revision over the network (an ETag round-trip) on every cold
                    # load — needless for an offline tool, and a hang risk if the network
                    # stalls. Fall back to a networked fetch ONLY when the model isn't
                    # cached yet (first-ever setup).
                    try:
                        self._model = SentenceTransformer(
                            self.model_name, device=self.device,
                            trust_remote_code=self.trust_remote_code,
                            local_files_only=True)
                    except Exception as e:
                        trace(f"model {self.model_name} not in local cache ({e!r}); "
                              "fetching from the Hub once")
                        self._model = SentenceTransformer(
                            self.model_name, device=self.device,
                            trust_remote_code=self.trust_remote_code)
                    trace(f"embedding model {self.model_name} ready")
        return self._model

    @property
    def dim(self) -> int:
        # sentence-transformers renamed get_sentence_embedding_dimension ->
        # get_embedding_dimension; prefer the new name (silences the FutureWarning) and
        # fall back to the old one for pinned older versions, then a safe default.
        model = self._load()
        for attr in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
            fn = getattr(model, attr, None)
            if fn is not None:
                try:
                    return int(fn())
                except Exception:
                    break
        return 384

    def embed_documents(self, texts: Sequence[str]) -> List[List[float]]:
        texts = list(texts)
        if not texts:
            return []
        model = self._load()
        vecs = model.encode(
            texts,
            normalize_embeddings=self.normalize,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vecs]

    def embed_query(self, text: str) -> List[float]:
        model = self._load()
        vec = model.encode(
            [self.query_prefix + (text or "")],
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
        )[0]
        return list(map(float, vec))
