"""Embedding-model bake-off (Phase 5).

Index the SAME corpus into an ISOLATED dir per model, with the SAME enriched
descriptors (only the embedding model varies), then eval each. Fixed-corpus A/B —
the lesson from Phase 4: never compare across edits.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from run_eval import _config_metrics  # noqa: E402

from pandemonium.config import Settings  # noqa: E402
from pandemonium.indexer.index_runner import run_index  # noqa: E402
from pandemonium.retrieval.hybrid_search import Retriever  # noqa: E402

BGE_PREFIX = "Represent this sentence for searching relevant passages: "
MODELS = [
    ("BAAI/bge-small-en-v1.5", False, BGE_PREFIX),       # current baseline, 384-d
    ("BAAI/bge-base-en-v1.5", False, BGE_PREFIX),        # bigger general text, 768-d
    # code-specific, standard distilroberta arch (loads on transformers 5.x), 768-d
    ("flax-sentence-embeddings/st-codesearch-distilroberta-base", False, ""),
]


def _variant_settings(model: str, trust: bool, prefix: str):
    s = Settings.load(".")
    slug = model.split("/")[-1].replace(".", "_")
    s.data["embedding"]["model"] = model
    s.data["embedding"]["trust_remote_code"] = trust
    s.data["embedding"]["query_prefix"] = prefix
    s.data["enrichment"]["provider"] = "cache"      # same descriptors for all
    s.data["indexing"]["scopes"] = ["symbol"]        # Phase-4 winner
    s.data["storage"]["sqlite_path"] = f".pandemonium/bake_{slug}/db.sqlite"
    s.data["storage"]["lancedb_path"] = f".pandemonium/bake_{slug}/lancedb"
    return s


def main() -> None:
    print(f"{'model':38s} {'dim':>4} {'P@1':>5} {'P@3':>5} {'P@5':>5} "
          f"{'symP5':>6} {'MRR':>5} {'miss':>4} {'idx_s':>6}")
    for model, trust, prefix in MODELS:
        try:
            settings = _variant_settings(model, trust, prefix)
            t0 = time.time()
            run_index(settings, incremental=False)
            idx_s = time.time() - t0
            retriever = Retriever(settings)
            m = _config_metrics(retriever, None)
            dim = retriever.embedder.dim
            retriever.close()
            print(f"{model:38s} {dim:4d} {m['p1']:5.3f} {m['p3']:5.3f} {m['p5']:5.3f} "
                  f"{m['symP5']:6.3f} {m['mrr']:5.3f} {m['misses']:4d} {idx_s:6.1f}")
        except Exception as e:
            print(f"{model:38s}  FAILED: {type(e).__name__}: {str(e)[:90]}")


if __name__ == "__main__":
    main()
