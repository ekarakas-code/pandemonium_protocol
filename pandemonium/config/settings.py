"""Settings: defaults deep-merged with an optional ``pandemonium.yaml``.

All on-disk paths are resolved relative to the repository root unless absolute.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Optional

import yaml

# ---------------------------------------------------------------------------
# Default configuration (mirrors docs Part 1 §20, adapted to PandemoniumProtocol)
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, Any] = {
    # Local-first kill-switch. True (default) = the project makes NO network calls: the
    # embedding model loads from the local cache only (reinforced by HF_HUB_OFFLINE, set in
    # pandemonium/__init__.py) and the opt-in external-LLM providers below are refused. Set
    # to false ONLY to use summaries.provider=external_llm or enrichment.provider=claude_cli
    # — those send code off-machine.
    "offline": True,
    "project": {
        "name": "pandemonium-protocol",
    },
    "storage": {
        "sqlite_path": ".pandemonium/pandemonium.db",
        "lancedb_path": ".pandemonium/lancedb",
        "audit_log": ".pandemonium/audit.log",
    },
    "embedding": {
        # provider: NOT read (descriptive only) — the loader is sentence-transformers
        # regardless; only model/device/normalize/batch_size/dim/query_prefix are consumed.
        "provider": "sentence-transformers",
        "model": "BAAI/bge-small-en-v1.5",
        "device": "cpu",
        "normalize": True,
        "batch_size": 32,
        "dim": 384,
        # bge models want this instruction prepended to *queries* (not documents).
        "query_prefix": "Represent this sentence for searching relevant passages: ",
    },
    "indexing": {
        # chunk_min_lines / chunk_max_tokens: NOT read yet — RESERVED for the token-aware
        # chunking bet (IMPROVEMENTS.md "Bigger bets").
        "chunk_min_lines": 5,
        "chunk_max_tokens": 512,
        # cAST subchunking (Improvements4 #3): a parsed symbol longer than this many lines is
        # ALSO split into block-complete `ast_block` children (the full symbol is always kept
        # as one complete card). Below the threshold a symbol stays a single chunk.
        "subchunk_min_lines": 60,
        # include_tests: NOT read (vestigial) — test files are always scanned + indexed.
        "include_tests": True,
        # incremental: NOT read — the incremental/full choice is the `--full` CLI flag
        # (service.index(incremental=not full)) / the function arg, not this key.
        "incremental": True,
        "max_file_bytes": 2_000_000,  # skip very large files (reported, not silent)
        # Emitted scopes. Phase-4 bake-off verdict: symbol-primary wins — file-scope
        # cards hurt ranking and code-windows-for-parsed add pure cost (2x overlap, 3x
        # vectors, zero gain). Non-parsed files always get 'block' coverage regardless.
        "scopes": ["symbol"],
        # languages: NOT read (vestigial + misleading) — which languages get structurally
        # parsed is decided by language_detector.PARSEABLE (cpp/c_sharp/js/ts already on) +
        # extension detection, NOT this key. Left for now; honor or delete in a config sweep.
        "languages": ["python"],
        # Step 8: merge a C++ header declaration's doc comment (+ decl-site ref) onto the
        # matching out-of-line `.cpp` definition. Off-switch for the per-file sibling-header
        # probe; a no-op for non-C++ and for translation units with no sibling header.
        "cpp_header_merge": True,
    },
    "retrieval": {
        # mode: NOT read — the per-call task mode is a parameter (CLI --mode / MCP arg);
        # "hybrid" here describes the strategy, it is not a consumed default.
        "mode": "hybrid",
        "vector_top_k": 20,
        "keyword_top_k": 20,
        "symbol_top_k": 10,
        "final_top_k": 10,
        # MVP weights = Part 1 (note/relationship channels are empty in the MVP).
        "weights": {"symbol": 0.40, "keyword": 0.30, "vector": 0.30},
        # A bare-identifier query that names an EXACT symbol returns the symbol card
        # directly, skipping the keyword/vector channels (and the embedding-model load).
        "exact_short_circuit": True,
        # Patch 4/5 structural reranker (retrieval.rerank_signals): on a CODE-intent query,
        # demote PROSE cards below code (Patch 4) and demote bulk-data / generated constant
        # cards (Patch 5), using FREE signals (language / chunk_type). DEFAULT OFF — a ranking
        # change must be proven on EXTERNAL repos (run_eval --tasks crossover), never tuned to
        # the 15-query dogfood gold. Bypasses the exact-symbol short-circuit + the channel
        # baselines (those must stay byte-identical). Sub-flags ablate each signal.
        "rerank": False,
        "rerank_prose": True,
        "rerank_density": True,
        # Auto-indexer (server self-heal): read tools incrementally reindex files changed
        # since the last check before serving, so mid-session edits are reflected without a
        # manual repo_reindex_changed. Debounced by auto_reindex_min_interval seconds (a
        # cheap mtime scan is skipped within the window). Set auto_reindex=false to disable.
        "auto_reindex": True,
        "auto_reindex_min_interval": 2.0,
        # Step 2 trust primitive: when a result set is LOW confidence (top hits cluster on
        # one symbol family while a query domain term is uncovered — the `.size()` failure),
        # fan out per-term sub-queries and re-rank by domain coverage. Never fires on the
        # exact-symbol fast path or scope-filtered search.
        "auto_fanout": True,
        "fanout_max_subqueries": 4,
        # Step 6 context modes — RANKING-WEIGHT presets only (Phase 4 already settled
        # scope=symbol-only, so modes never touch scope/chunk-type). Selected per call via
        # mode=; absent/unknown -> the default weights above. The presets are PRINCIPLED but
        # NOT yet validated: a mode is only proven by *differential* (crossover) performance
        # across query types. The #11 multi-type/multi-repo matrix that enables that is now
        # built (evals/run_eval.py --matrix over evals/fixtures/matrix/), but the modes-crossover
        # run THROUGH it is still pending; until then they ship as labelled hypotheses.
        #   impact   — editing a known-ish symbol: favour exact symbol matches.
        #   discovery— vague "where do we X": favour semantic (vector) recall.
        #   bugfix   — trace a literal token (error/log): favour keyword/bm25. (Premise is
        #              THIN on this repo — few distinctive error literals — so it's unexercised
        #              here; validate on a repo that has them.)
        "modes": {
            "impact": {"weights": {"symbol": 0.55, "keyword": 0.25, "vector": 0.20}},
            "discovery": {"weights": {"symbol": 0.25, "keyword": 0.25, "vector": 0.50}},
            "bugfix": {"weights": {"symbol": 0.25, "keyword": 0.55, "vector": 0.20}},
        },
    },
    "context_pack": {
        "default_token_budget": 4000,
        "tokenizer": "cl100k_base",
        # include_summaries / include_line_ranges / include_tests / include_risks: NOT read
        # (vestigial) — the context pack always emits summaries, line ranges, related tests,
        # and risks; these are not honored toggles.
        "include_summaries": True,
        "include_line_ranges": True,
        "include_tests": True,
        "include_risks": True,
        "max_chunk_chars": 1600,  # per-file code excerpt cap before budgeting
    },
    "summaries": {
        # heuristic (default, fully local) | external_llm (opt-in, off by default)
        "provider": "heuristic",
        "enabled": False,
        "external": {
            "model": "claude-haiku-4-5",
            "max_tokens": 256,
        },
    },
    "enrichment": {
        # heuristic (default) | cache (precomputed ref->{summary,tags}) | claude_cli
        "provider": "heuristic",
        "cache_path": ".pandemonium/enrichment.json",
        "model": "claude-haiku-4-5",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class Settings:
    DATA_DIR_NAME = ".pandemonium"
    CONFIG_FILENAME = "pandemonium.yaml"

    def __init__(self, data: dict, repo_root: Path, config_path: Optional[Path] = None):
        self.data = data
        self.repo_root = repo_root
        self.config_path = config_path

    @classmethod
    def load(cls, repo_root: Any, config_path: Any = None) -> "Settings":
        repo_root = Path(repo_root).resolve()
        cfg = Path(config_path) if config_path else (repo_root / cls.CONFIG_FILENAME)
        data = copy.deepcopy(DEFAULTS)
        if cfg.exists():
            user = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            if isinstance(user, dict):
                _deep_merge(data, user)
        return cls(data, repo_root, cfg)

    # -- resolved paths -----------------------------------------------------
    def _resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else (self.repo_root / path)

    @property
    def data_dir(self) -> Path:
        return self.repo_root / self.DATA_DIR_NAME

    @property
    def sqlite_path(self) -> Path:
        return self._resolve(self.data["storage"]["sqlite_path"])

    @property
    def lancedb_path(self) -> Path:
        return self._resolve(self.data["storage"]["lancedb_path"])

    @property
    def audit_log_path(self) -> Path:
        return self._resolve(self.data["storage"]["audit_log"])

    # -- convenience accessors ---------------------------------------------
    @property
    def project_name(self) -> str:
        return self.data["project"]["name"]

    @property
    def offline(self) -> bool:
        """Local-first kill-switch (default True). When True the project makes no network
        calls — the opt-in external-LLM providers are refused (see get_summarizer /
        load_enricher) and the embedding model loads from the local cache only."""
        return bool(self.data.get("offline", True))

    def section(self, name: str) -> dict:
        return self.data.get(name, {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)
