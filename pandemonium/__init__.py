"""PandemoniumProtocol (ProjectRAG).

Local-first codebase intelligence: parse a repo once into structured, searchable
knowledge (symbols, code-aware chunks, keyword index, vectors, metadata) and return
compact, token-budgeted context packs to LLM coding agents.

MVP vertical slice: init -> index -> hybrid search -> context pack -> CLI + MCP.
"""

import os as _os

# Local-first / offline by default. This tool indexes and searches entirely on-machine,
# so pin huggingface_hub/transformers to offline BEFORE they are imported anywhere: a cold
# embedding-model load then never makes a network round-trip (the default revision/ETag
# re-check) and can never hang on a stalled connection. `setdefault` leaves an escape
# hatch — export HF_HUB_OFFLINE=0 for a one-time model download on a fresh machine.
# The `offline` config flag (config/settings.py) governs the opt-in external-LLM providers.
_os.environ.setdefault("HF_HUB_OFFLINE", "1")
_os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

__version__ = "0.1.0"
