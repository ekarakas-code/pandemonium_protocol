---
name: run-pandemonium
description: Start using the PandemoniumProtocol protocol. Use when asked to set up / run / start pandemonium on a repo, index a codebase for it, boot its MCP server, verify the protocol works, or drive repo_search / repo_get / repo_graph / repo_impact / repo_context_pack.
---

PandemoniumProtocol is a **local-first codebase-intelligence tool**: it indexes a repo
(symbols + a call/import/inherit graph + vector embeddings) and serves it two ways — the
`pandemonium` **CLI** and an **MCP server** exposing `repo_*` tools for an LLM agent. To
"start using it" you (1) build an index for a repo, then (2) drive it via the CLI or wire
the MCP server into your agent.

**Verify everything works first** with the driver — it builds an index and exercises the
full CLI + MCP surface end-to-end:

```bash
.venv/Scripts/python.exe .claude/skills/run-pandemonium/driver.py
```

All paths below are relative to the PandemoniumProtocol repo root. Verified on **Windows 11,
Python 3.12.6** (the venv lives at `.venv/Scripts/`; on Linux/macOS it's `.venv/bin/`).

## Prerequisites

- **Python ≥ 3.10** (`.venv/Scripts/python.exe --version` → `Python 3.12.6` here).
- No OS packages beyond Python. The dependency closure is heavy (`sentence-transformers`
  pulls `torch`, plus `lancedb`/`pyarrow`/`tree-sitter-*`), and the **first index downloads
  the ~130 MB `bge-small` embedding model** (then cached under the HF cache).

## Setup (one-time, from a clean clone)

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e .
```

The console script `pandemonium` is installed into `.venv/Scripts/pandemonium.exe` (this is
the binary the MCP config and the driver call). `pip install -e .[llm]` additionally enables
the opt-in external-LLM summarizer; the default install pulls zero cloud SDKs.

## Run (agent path) — the driver

The driver is the harness. It generates a tiny C++ sample repo, indexes it, drives every
read command, and boots the MCP stdio server with the official MCP client:

```bash
.venv/Scripts/python.exe .claude/skills/run-pandemonium/driver.py            # self-contained sample
.venv/Scripts/python.exe .claude/skills/run-pandemonium/driver.py --repo D:/path/to/your/repo
.venv/Scripts/python.exe .claude/skills/run-pandemonium/driver.py --no-mcp   # skip MCP boot (faster)
```

Expected tail (a cold first run takes ~30–60 s for the model load, then it's fast):

```
== boot + drive the MCP server (stdio) ==
  [PASS] mcp: boot (15 tools) + repo_get(view=signature)

================================================
11/11 steps passed
```

Confirm the MCP server boots on its own with the official client:

```bash
.venv/Scripts/python.exe evals/mcp_smoke.py .venv/Scripts/pandemonium.exe
```

→ prints `BOOTED via ...: 15 tools` and `MCP_SMOKE_OK`.

## Start using it on YOUR repo

**1. Index the target repo** (positional path; loads the model on first run):

```bash
.venv/Scripts/pandemonium.exe init D:/path/to/your/repo
.venv/Scripts/pandemonium.exe index D:/path/to/your/repo --full
```

`--full` forces re-processing of every file. Use it on the first index **and after any
protocol upgrade** — a plain `index` is incremental and **skips files whose content hash is
unchanged**, so it would keep stale edges/summaries (see Gotchas). For day-to-day edits,
plain `index` (incremental) is correct.

**2. Drive it from the CLI** (read commands take `--repo PATH`; `init`/`index` take the path
positionally). Every one of these is verified:

```bash
P=.venv/Scripts/pandemonium.exe; R=D:/path/to/your/repo
"$P" search computeStepFromVelocity --repo "$R"                 # hybrid search -> ranked cards
"$P" symbol computeStepFromVelocity --repo "$R"                 # name -> file:lines + signature
"$P" get "src/physics.h::rts.sim.computeStepFromVelocity" --view signature --repo "$R"
"$P" impact "src/physics.h::rts.sim.computeStepFromVelocity" --repo "$R"   # callers + prod/test split
"$P" graph  "src/physics.h::rts.sim.computeStepFromVelocity" --repo "$R"   # callees/callers/inherits
"$P" context "how is the simulation step computed" --repo "$R" # token-budgeted context pack
"$P" changed --repo "$R"                                        # pre-index dry-run: new/changed/deleted files
"$P" map --repo "$R"                                            # repo orientation
```

`get --view` narrows output to save tokens: `signature | head:N | lines:a-b | full`.

**3. Wire the MCP server into your agent** so an LLM gets the `repo_*` tools. Add this to
the target project's MCP config (e.g. `.mcp.json`); `--repo .` resolves to the server's
working directory (the target repo):

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

> **Resolve `command` to YOUR install.** `"pandemonium"` works only if it's on the PATH that
> your agent launches with (global install, or the venv is active). Otherwise use the FULL path
> to the executable — find it with `(Get-Command pandemonium).Source` (PowerShell) or
> `which pandemonium` (POSIX); e.g. `C:/path/to/PandemoniumProtocol/.venv/Scripts/pandemonium.exe`
> (forward slashes, or escaped `\\` in JSON). Do **not** copy a path from another machine.

The agent then has 15 tools — `repo_context_pack` (start a task), `repo_brief` (pre-flight
verified-vs-guess brief), `repo_search` → `repo_get`, `repo_graph` / `repo_impact` /
`repo_edit_plan`, `repo_symbol`, `repo_find_tests`, `repo_changed`, `repo_session`,
`repo_logic_map`, `repo_prompt_context`, `repo_map`, `repo_reindex_changed`. For the retrieval
discipline (use these instead of reading whole files), see the colocated `pandemonium` skill at
`.claude/skills/pandemonium/`.

## Gotchas

- **First index is slow and chatty.** It loads/downloads the embedding model; the
  `Warning: You are sending unauthenticated requests to the HF Hub` and `Loading weights`
  lines are **harmless**. Cached afterward.
- **Incremental-skip trap.** Indexing keys on file **content hash**. After you change the
  *protocol code* (not the repo's files), a normal `index` / `reindex-changed` / the
  `repo_reindex_changed` MCP tool will **skip every unchanged file** and keep the old
  edges/summaries. Run `index --full` once to pick up new extraction logic.
- **Windows file lock on reinstall.** `pip install -e .` fails with
  `OSError: [WinError 32] ... pandemonium.exe ... used by another process` if a `pandemonium`
  / `serve-mcp` process is still running. Stop it first:
  `Get-Process pandemonium | Stop-Process -Force`.
- **Language coverage.** Symbols + graph edges are extracted only for Python, C++, C#, and
  JS/TS; other files are still searchable as text but carry no graph. An empty
  graph/impact for those means "edges never extracted," not "nothing depends on it."
- **`init` indexes its own `pandemonium.yaml`.** Don't be surprised by a `scanned` count one
  higher than your source files.

## Troubleshooting

- **`index` reports `0 symbols`** → the language isn't parseable, or the tree-sitter grammar
  for it is missing. Confirm the files are Python/C++/C#/JS/TS; reinstall deps if needed.
- **Agent has no `repo_*` tools** → the MCP server isn't registered (check the target
  project's `.mcp.json`) or the absolute path to `pandemonium.exe` is wrong.
- **`pip install -e .` → WinError 32** → a pandemonium process holds the exe; stop it (above)
  and retry.
- **Driver step FAILS** → it prints the failing step name + the last ~600 chars of output;
  re-run that single CLI command directly to see the full error.
