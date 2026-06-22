# PandemoniumProtocol (ProjectRAG)

Local-first codebase intelligence for LLM coding agents. Parse a repository once
into structured, searchable knowledge — symbols, code-aware chunks, a keyword
index, local vector embeddings, and metadata — then hand an agent a compact,
**token-budgeted context pack** instead of making it grep and read whole files.

> Don't make the LLM search the entire project. Make the project searchable,
> understandable, and version-aware before the LLM arrives.

This is the **MVP vertical slice**: `init → index → hybrid search → context pack →
CLI + MCP`, Python-first, fully local. (Notes, retrievable trees, git tracking,
change-intelligence, and the REST API are later phases — see
`docs/ARCHITECTURE.md`.)

## Install (Windows / Python 3.12)

```powershell
python -m venv .venv
.venv\Scripts\activate

# 1) CPU-only torch FIRST (keeps the install ~300 MB instead of ~2 GB)
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 2) the package (pulls lancedb, sentence-transformers, tree-sitter, mcp, ...)
pip install -e .

# optional: opt-in external-LLM summaries (sends code off-machine when enabled)
# pip install -e ".[llm]"
```

Linux/macOS is identical except `source .venv/bin/activate` and the default torch
wheel is already CPU on most platforms.

### Offline / air-gapped first run

The embedding model (`BAAI/bge-small-en-v1.5`, ~130 MB) and tiktoken's BPE file
download **once** on first use, then run fully offline. To control where they
cache (and to force offline after the first run):

```powershell
$env:HF_HOME = "$PWD\.pandemonium\hf"        # model cache inside the project
$env:TIKTOKEN_CACHE_DIR = "$PWD\.pandemonium\tiktoken"
# after the first successful run:
$env:HF_HUB_OFFLINE = "1"
```

## Quickstart

```powershell
pandemonium init .                         # create .pandemonium/ + pandemonium.yaml
pandemonium index .                         # scan, parse, chunk, embed (incremental)
pandemonium search "where is vendor email sent?"
pandemonium symbol SendPurchaseOrderEmail
pandemonium context "fix missing vendor email after PO approval" --tokens 4000
pandemonium serve-mcp                       # stdio MCP server for Claude Code / agents
```

## Use from Claude Code (MCP)

Add to `.mcp.json` (project root):

```json
{
  "mcpServers": {
    "pandemonium": {
      "command": "pandemonium",
      "args": ["serve-mcp", "--repo", "."]
    }
  }
}
```

> **The `command` must resolve in the environment Claude Code launches.** `pandemonium`
> works only if the package is on PATH (install it, or activate your venv first). For
> local dev against this repo's venv, use the full path instead —
> `.venv\Scripts\pandemonium.exe` (Windows) or `.venv/bin/pandemonium` (Unix).

Tools exposed (15): `repo_map` (`--mode` = default | architecture | entrypoints |
domains | tests | changed), `repo_search` (returns **cards** — refs + summaries + tags,
no code), `repo_symbol`, `repo_get` (fetch exact code by ref — `expand` =
exact|neighbors|file|parent, `view` = full|signature|head:N|lines:a-b), **`repo_brief`**
(pre-flight brief — verified graph-facts vs heuristic guesses, hard-separated),
**`repo_graph`** (callers/callees/imports/inheritance/tests), **`repo_impact`** (what a
change affects), **`repo_edit_plan`** (ranked "how to change this"), **`repo_logic_map`**
(conceptual flow for a topic), `repo_context_pack` (`repo_prompt_context`),
`repo_find_tests`, `repo_session` (session ledger), `repo_changed` (staleness),
`repo_reindex_changed`. All read-only except
reindex; every call is audit-logged. Static call/import/inherit graph edges are extracted
for **Python, C++, C#, Dart, and JS/TS** (resolution is language-scoped). **HTML and CSS**
are parsed into symbol anchors (element `id`s, CSS rule selectors) but carry no edges;
other languages still index symbols/text and surface an explicit "edges not extracted"
notice.

## Use as a Claude **skill**

`.claude/skills/pandemonium/` is a thin operating discipline that teaches an agent to
use the tools well: **search cards → fetch exact code by ref → use the session ledger →
coordinate parallel agents → verify impact** — finding code *and related code* without
reading whole files. Copy that folder into any project's `.claude/skills/` (alongside
the `.mcp.json` above) to enable it. `SKILL.md` is the entry point; the
`*-protocol.md` files load on demand.

## How it works

```
scan (.pandemoniumignore + secret filters)
  -> language detect + content hash (incremental skip)
  -> tree-sitter parse  -> symbols (class/function/method, line ranges)
  -> code-aware chunks  -> heuristic summaries
  -> local embeddings   -> LanceDB (vectors) + SQLite (metadata) + FTS5 (keyword)

query
  -> symbol + keyword + vector search
  -> normalize per channel, weighted merge, dedup
  -> token-budgeted context pack (markdown)
```

## Privacy / local-first

Nothing leaves the machine by default. Secrets (`.env`, keys, certs,
`secrets/`, `credentials/`), binaries, and the `.pandemonium/` store itself are
never indexed. The external-LLM summarizer is the only path that can send code
out — it requires both the `[llm]` extra and `summaries.enabled: true`, and every
such call is written to the audit log.
