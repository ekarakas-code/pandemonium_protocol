# ProjectRAG Design Document v2

  

**Document purpose:** This second design file consolidates the expanded ProjectRAG concept: an LLM-facing, local-first repository intelligence and context-routing layer. It captures the workflow, architecture, tracking model, retrievable trees, notes system, MCP/CLI/API surface, storage model, and implementation roadmap.

  

**Core idea:** ProjectRAG should prevent LLM coding agents from wasting tokens by manually browsing repositories. Instead, an LLM connects to ProjectRAG through a prompt/tool contract and receives the correct files, folders, symbols, code chunks, notes, tests, and architecture context for the current task.

  

---

  

## 1. Updated Product Definition

  

ProjectRAG is a local-first repository intelligence system for LLM coding agents. It builds and maintains a searchable, structured, version-aware knowledge layer over a software project, including:

  

- physical file and folder tree

- semantic folder/context tree

- notes tree

- code architecture summary

- file summaries

- folder summaries

- symbols and code chunks

- dependencies and relationships

- tests and test relationships

- coding rules and project conventions

- change history

- git-like file tracking

- task-specific context packs

  

The purpose is not only to retrieve code. The purpose is to prepare the LLM to behave like a developer who already understands the project.

  

---

  

## 2. Target Workflow

  

### 2.1 Standard LLM Workflow

  

```text

1. LLM enters the software project.

2. The project prompt instructs the LLM to use ProjectRAG first.

3. The LLM connects to ProjectRAG through MCP, CLI, or REST API.

4. The LLM requests context based on the task.

5. ProjectRAG returns the correct files, code pieces, symbols, tests, notes, and architecture context.

6. The LLM performs the coding task using the returned context.

7. After source changes, ProjectRAG detects changed files and reindexes/re-embeds only affected content.

```

  

### 2.2 What ProjectRAG Prevents

  

Without ProjectRAG, LLMs often:

  

```text

list folders

open many files

grep repeatedly

read large files

guess which files matter

spend thousands of tokens before finding the right code

```

  

With ProjectRAG, the LLM asks:

  

```text

Where is this logic?

Which files are relevant?

Which symbols are related?

What notes exist?

What changed recently?

Which tests are affected?

What architecture context matters?

```

  

---

  

## 3. Main Design Principles

  

1. **Local first:** source code, embeddings, metadata, notes, and context packs are stored locally by default.

2. **LLM-facing by design:** ProjectRAG is designed to serve coding agents, not only human search.

3. **Token efficiency:** return only task-relevant context within a defined token budget.

4. **Code-aware indexing:** parse code by meaningful units such as classes, methods, functions, routes, and configuration blocks.

5. **Hybrid retrieval:** combine symbol search, keyword search, vector search, metadata filtering, and notes retrieval.

6. **Version-aware context:** detect modified, deleted, renamed, and added files; re-embed only changed pieces.

7. **Retrievable trees:** files, folders, and notes must all be accessible as structured trees.

8. **Notes as first-class knowledge:** notes can be attached to projects, folders, files, symbols, code chunks, tasks, changes, and architecture areas.

9. **Read-only default:** ProjectRAG retrieves, explains, and packages context. It does not modify source code unless explicitly designed to do so in later phases.

10. **Privacy by default:** secrets and sensitive files are ignored unless explicitly configured.

  

---

  

## 4. Core Architecture

  

```text

Prompt-Led LLM Agent

        |

        v

ProjectRAG MCP / CLI / REST API

        |

        +--> Project Map

        +--> File Tree

        +--> Context Tree

        +--> Notes Tree

        +--> Architecture Summary

        +--> Symbol Index

        +--> File and Folder Summaries

        +--> Dependency Graph

        +--> Git / Snapshot Tracker

        +--> Hybrid Retrieval Engine

        +--> Context Pack Generator

        +--> Change Impact Analyzer

        |

        v

Local Storage

        |

        +--> SQLite metadata database

        +--> SQLite FTS5 keyword index

        +--> LanceDB vector database

        +--> Local snapshot database

        +--> Optional Git integration

```

  

---

  

## 5. Project Map Layer

  

ProjectRAG should generate a project-level map that gives the LLM a fast understanding of the repository.

  

### 5.1 Project Map Contents

  

```text

Project name

Repository root

Main technology stack

Application type

Entry points

Folder tree

Important folders

Important configuration files

Build files

Runtime files

Database files

Test folders

Documentation folders

Generated/ignored folders

Suggested first reading order

```

  

### 5.2 Example CLI

  

```bash

projectrag map

```

  

### 5.3 Example MCP Tool

  

```text

repo_map()

```

  

---

  

## 6. Architecture Summary Layer

  

The architecture summary is a natural-language explanation of how the project works.

  

### 6.1 Architecture Summary Contents

  

```text

System overview

Main modules

Module responsibilities

Data flow

External integrations

Internal dependencies

Key abstractions

Configuration flow

Database usage

Logging pattern

Error handling pattern

Authentication/authorization pattern

Test strategy

Known architecture risks

```

  

### 6.2 Example CLI

  

```bash

projectrag architecture

```

  

### 6.3 Example MCP Tool

  

```text

repo_architecture()

```

  

---

  

## 7. Three Retrievable Trees

  

ProjectRAG must expose three different tree views.

  

### 7.1 File Tree

  

The file tree is the physical repository structure.

  

```text

src/

  Workflow/

    PurchaseOrderWorkflow.cs

    PurchaseOrderEmailService.cs

  Sap/

    SapOutputService.cs

    VendorRepository.cs

  Email/

    EmailService.cs

tests/

  PurchaseOrderEmailTests.cs

```

  

CLI:

  

```bash

projectrag tree files --depth 3

```

  

MCP:

  

```text

repo_file_tree(depth=3)

```

  

### 7.2 Context Tree

  

The context tree explains what folders mean.

  

```text

src/

  Purpose: Main application source code

  

src/Workflow/

  Purpose: Business workflow orchestration

  Key files:

    - PurchaseOrderWorkflow.cs

    - PurchaseOrderEmailService.cs

  

src/Sap/

  Purpose: SAP integration and output generation

  Key files:

    - SapOutputService.cs

    - VendorRepository.cs

```

  

CLI:

  

```bash

projectrag tree context --depth 3

```

  

MCP:

  

```text

repo_context_tree(depth=3)

```

  

### 7.3 Notes Tree

  

The notes tree is the project memory layer.

  

```text

notes/

  architecture/

    - Overall workflow architecture

    - SAP integration assumptions

  

  folders/

    src/Sap/

      - SAP PDF generation affects email flow

      - Vendor data comes from SAP master records

  

  files/

    src/Sap/SapOutputService.cs

      - Do not bypass SAP output validation

  

  symbols/

    SendPurchaseOrderEmail

      - Must log missing vendor email

      - Should not expose sensitive data in logs

  

  tasks/

    fix-missing-vendor-email/

      - Investigated files

      - Current hypothesis

      - Related tests

```

  

CLI:

  

```bash

projectrag tree notes

```

  

MCP:

  

```text

repo_notes_tree(scope="all")

```

  

---

  

## 8. Notes as First-Class Objects

  

Notes should be indexed, searchable, filterable, linked, and retrievable.

  

### 8.1 Why Notes Matter

  

LLMs often need non-obvious knowledge that is not directly visible in code:

  

```text

This file handles legacy SAP behavior.

Do not change this method without checking config.xml.

This function is used by reflection.

Vendor email logic depends on SAP master data.

This folder contains generated code; avoid manual edits.

This test is weak and should not be treated as full validation.

```

  

### 8.2 Note Types

  

```text

project_note

folder_note

file_note

symbol_note

chunk_note

architecture_note

business_rule_note

risk_note

todo_note

llm_instruction_note

change_note

debug_note

task_note

```

  

### 8.3 Note Targets

  

A note can target:

  

```text

project

folder

file

symbol

chunk

task

change

architecture area

external integration

configuration file

test file

```

  

### 8.4 Example CLI

  

```bash

projectrag note add --file src/Sap/SapOutputService.cs \

  "This service generates SAP purchase order PDF output. If it fails, vendor email sending also fails."

```

  

```bash

projectrag notes --file src/Sap/SapOutputService.cs

```

  

### 8.5 Example MCP Tools

  

```text

repo_notes(target="src/Sap/SapOutputService.cs")

repo_add_note(target_type="file", target_path="src/Sap/SapOutputService.cs", content="...")

repo_update_note(note_id="note_001", content="...")

```

  

---

  

## 9. Git-Like Tracking and Incremental Re-Embedding

  

ProjectRAG must know when source files change and refresh only the affected indexed data.

  

### 9.1 Tracking Modes

  

ProjectRAG should support two tracking modes.

  

#### Mode A - Existing Git Repository

  

If a project already has `.git`, ProjectRAG can use:

  

```bash

git status

git diff

git log

git ls-files

```

  

This allows ProjectRAG to detect:

  

```text

modified files

added files

deleted files

renamed files

changed symbols

changed line ranges

recent commits

branch-specific changes

```

  

#### Mode B - ProjectRAG Snapshot Tracking

  

If no Git repository exists, ProjectRAG maintains its own local snapshots using file hashes.

  

For each file, ProjectRAG stores:

  

```text

path

content hash

size

modified time

last indexed time

snapshot id

language

```

  

### 9.2 Incremental Reindex Flow

  

```text

1. Detect changed files.

2. Detect deleted files.

3. Detect renamed files if possible.

4. For unchanged files, skip parsing and embedding.

5. For changed files, delete old chunks, symbols, vectors, and file summaries.

6. Re-parse changed files.

7. Recreate symbols and chunks.

8. Recompute embeddings only for changed chunks.

9. Update keyword index.

10. Update metadata and relationship graph.

11. Update affected folder summaries and notes links if required.

12. Save a new index snapshot.

```

  

### 9.3 Example CLI

  

```bash

projectrag changed

projectrag reindex-changed

projectrag sync

```

  

### 9.4 Example MCP Tools

  

```text

repo_changed_files()

repo_change_summary()

repo_reindex_changed()

```

  

---

  

## 10. Change Intelligence

  

ProjectRAG should not only say which files changed. It should summarize what changed and what may be affected.

  

### 10.1 Change Summary Output

  

```md

# Change Summary

  

## Modified Files

- src/Workflow/PurchaseOrderEmailService.cs

- src/Sap/SapOutputService.cs

  

## Changed Symbols

- SendPurchaseOrderEmail

- GeneratePurchaseOrderPdf

  

## Potentially Affected Areas

- Vendor email resolution

- SAP PDF generation

- Purchase order approval workflow

  

## Recommended Tests

- PurchaseOrderEmailTests.cs

- SapOutputServiceTests.cs

```

  

### 10.2 Impact Analysis Questions

  

ProjectRAG should answer:

  

```text

What changed since the last index?

Which symbols changed?

Which tests are related?

Which files depend on this file?

Which notes mention this area?

Should embeddings be refreshed?

Which context packs may now be stale?

```

  

---

  

## 11. Hybrid Retrieval Strategy

  

ProjectRAG should not depend only on vector search.

  

### 11.1 Retrieval Flow

  

```text

1. Exact symbol search

2. Keyword search using SQLite FTS5

3. Semantic vector search using LanceDB

4. Metadata filtering

5. Notes retrieval

6. Relationship graph expansion

7. Test relationship lookup

8. Result merging

9. Duplicate removal

10. Reranking

11. Token-budgeted context pack generation

```

  

### 11.2 Suggested Scoring

  

```text

final_score =

    symbol_score * 0.30 +

    keyword_score * 0.20 +

    vector_score * 0.20 +

    note_score * 0.15 +

    relationship_score * 0.15

```

  

For code projects, exact symbols and paths should often outrank semantic similarity.

  

---

  

## 12. Context Packs

  

The context pack is the primary output for LLM agents.

  

### 12.1 Context Pack Contents

  

```text

Task

Project location

Relevant file tree

Relevant context tree

Relevant notes tree excerpts

Architecture summary

Relevant files

Relevant code chunks

Relevant symbols

Business rules

Coding conventions

Recent change notes

Related tests

Risk warnings

Suggested inspection order

Suggested implementation path

Token budget summary

```

  

### 12.2 Example CLI

  

```bash

projectrag context "fix missing vendor email after SAP PO approval" --tokens 4000

```

  

### 12.3 Example MCP Tool

  

```text

repo_prompt_context(task="fix missing vendor email after SAP PO approval", token_budget=4000)

```

  

### 12.4 Example Output Shape

  

```md

# LLM Prompt Context

  

## Task

Fix missing vendor email after SAP PO approval.

  

## Project Area

Workflow / SAP integration / Email sending

  

## Relevant File Tree

...

  

## Relevant Files

1. src/Workflow/PurchaseOrderEmailService.cs

2. src/Sap/SapOutputService.cs

3. tests/PurchaseOrderEmailTests.cs

  

## Relevant Symbols

- SendPurchaseOrderEmail

- GeneratePurchaseOrderPdf

- VendorRepository.GetPrimaryEmail

  

## Relevant Notes

- Vendor email depends on SAP master data.

- Missing email should be logged without exposing sensitive data.

  

## Related Tests

- SendsEmailWhenVendorHasEmail

- LogsWarningWhenVendorEmailMissing

  

## Risks

- Vendor email selection may be shared with invoice email logic.

- SAP output failure may prevent email generation.

  

## Suggested Implementation Path

1. Inspect vendor email retrieval.

2. Inspect SAP PDF generation dependency.

3. Update email service behavior.

4. Add or update tests.

```

  

---

  

## 13. LLM Agent Contract

  

The prompt that introduces ProjectRAG to an LLM should define a strict usage order.

  

### 13.1 Recommended Prompt Contract

  

```text

You are working inside a software project that has ProjectRAG available.

Before editing code, always call ProjectRAG.

Do not blindly list folders or open random files.

Use ProjectRAG to retrieve only the relevant project context.

  

Recommended order:

1. repo_map

2. repo_file_tree or repo_context_tree

3. repo_search

4. repo_symbol

5. repo_notes

6. repo_prompt_context

7. repo_impact

8. repo_find_tests

  

Before modifying production code, request related tests.

After changes, call repo_changed_files and repo_reindex_changed.

Prefer exact symbol results over vector-only results.

If ProjectRAG context is insufficient, explain what is missing before exploring manually.

```

  

---

  

## 14. MCP Tool Surface

  

### 14.1 Essential MCP Tools

  

```text

repo_map

repo_architecture

repo_file_tree

repo_context_tree

repo_notes_tree

repo_search

repo_symbol

repo_file_summary

repo_folder_summary

repo_notes

repo_add_note

repo_update_note

repo_context_pack

repo_prompt_context

repo_find_tests

repo_impact

repo_changed_files

repo_change_summary

repo_reindex_changed

repo_dependencies

repo_coding_rules

```

  

### 14.2 Most Important Tool

  

```text

repo_prompt_context(task, token_budget)

```

  

This tool should be optimized for LLM coding agents. It should return the complete context needed to start solving the task without manual repository exploration.

  

---

  

## 15. CLI Surface

  

Recommended CLI commands:

  

```bash

projectrag init

projectrag index <path>

projectrag sync

projectrag changed

projectrag reindex-changed

projectrag map

projectrag architecture

projectrag tree files --depth 3

projectrag tree context --depth 3

projectrag tree notes

projectrag search "query"

projectrag symbol SymbolName

projectrag notes --file path/to/file.cs

projectrag note add --file path/to/file.cs "note text"

projectrag context "task" --tokens 4000

projectrag impact "change description"

projectrag tests "target symbol or file"

projectrag serve-api

projectrag serve-mcp

```

  

---

  

## 16. Storage Model

  

### 16.1 repositories

  

```sql

CREATE TABLE repositories (

    id TEXT PRIMARY KEY,

    name TEXT NOT NULL,

    root_path TEXT NOT NULL,

    tracking_mode TEXT NOT NULL,

    created_at TEXT NOT NULL,

    updated_at TEXT NOT NULL

);

```

  

### 16.2 files

  

```sql

CREATE TABLE files (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    path TEXT NOT NULL,

    language TEXT,

    content_hash TEXT NOT NULL,

    size_bytes INTEGER,

    last_indexed_at TEXT NOT NULL,

    summary TEXT,

    importance INTEGER DEFAULT 0,

    FOREIGN KEY(repo_id) REFERENCES repositories(id)

);

```

  

### 16.3 tree_nodes

  

```sql

CREATE TABLE tree_nodes (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    path TEXT NOT NULL,

    parent_path TEXT,

    node_type TEXT NOT NULL,

    name TEXT NOT NULL,

    language TEXT,

    summary TEXT,

    importance INTEGER DEFAULT 0,

    file_count INTEGER DEFAULT 0,

    symbol_count INTEGER DEFAULT 0,

    content_hash TEXT,

    last_indexed_at TEXT

);

```

  

### 16.4 symbols

  

```sql

CREATE TABLE symbols (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    file_id TEXT NOT NULL,

    symbol_type TEXT NOT NULL,

    name TEXT NOT NULL,

    qualified_name TEXT,

    signature TEXT,

    start_line INTEGER,

    end_line INTEGER,

    summary TEXT,

    content_hash TEXT,

    FOREIGN KEY(repo_id) REFERENCES repositories(id),

    FOREIGN KEY(file_id) REFERENCES files(id)

);

```

  

### 16.5 chunks

  

```sql

CREATE TABLE chunks (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    file_id TEXT NOT NULL,

    symbol_id TEXT,

    chunk_type TEXT NOT NULL,

    language TEXT,

    path TEXT NOT NULL,

    start_line INTEGER,

    end_line INTEGER,

    content TEXT NOT NULL,

    summary TEXT,

    content_hash TEXT NOT NULL,

    FOREIGN KEY(repo_id) REFERENCES repositories(id),

    FOREIGN KEY(file_id) REFERENCES files(id),

    FOREIGN KEY(symbol_id) REFERENCES symbols(id)

);

```

  

### 16.6 notes

  

```sql

CREATE TABLE notes (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    target_type TEXT NOT NULL,

    target_id TEXT,

    target_path TEXT,

    note_type TEXT NOT NULL,

    title TEXT,

    content TEXT NOT NULL,

    created_by TEXT,

    created_at TEXT NOT NULL,

    updated_at TEXT NOT NULL,

    importance INTEGER DEFAULT 0,

    tags TEXT,

    content_hash TEXT

);

```

  

### 16.7 note_links

  

```sql

CREATE TABLE note_links (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    note_id TEXT NOT NULL,

    linked_target_type TEXT NOT NULL,

    linked_target_id TEXT,

    linked_target_path TEXT,

    relationship_type TEXT

);

```

  

### 16.8 relationships

  

```sql

CREATE TABLE relationships (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    source_type TEXT NOT NULL,

    source_id TEXT NOT NULL,

    relationship_type TEXT NOT NULL,

    target_type TEXT,

    target_id TEXT,

    target_name TEXT,

    confidence REAL DEFAULT 1.0,

    FOREIGN KEY(repo_id) REFERENCES repositories(id)

);

```

  

### 16.9 snapshots

  

```sql

CREATE TABLE index_snapshots (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    created_at TEXT NOT NULL,

    trigger_type TEXT,

    changed_files_count INTEGER,

    deleted_files_count INTEGER,

    summary TEXT

);

```

  

### 16.10 file_snapshots

  

```sql

CREATE TABLE file_snapshots (

    id TEXT PRIMARY KEY,

    repo_id TEXT NOT NULL,

    snapshot_id TEXT NOT NULL,

    path TEXT NOT NULL,

    content_hash TEXT NOT NULL,

    size_bytes INTEGER,

    modified_time TEXT,

    indexed_at TEXT

);

```

  

---

  

## 17. Default Ignored Files

  

ProjectRAG should include `.projectragignore`.

  

```gitignore

.git/

node_modules/

bin/

obj/

dist/

build/

target/

.vs/

.idea/

.vscode/

__pycache__/

.env

.env.*

*.pem

*.key

*.pfx

*.cer

*.zip

*.rar

*.7z

secrets/

credentials/

```

  

---

  

## 18. Recommended MVP Technology Stack

  

```text

Language: Python

Vector Database: LanceDB

Metadata Database: SQLite

Keyword Search: SQLite FTS5

Parser: Tree-sitter

Embedding Model: BAAI/bge-small-en-v1.5

CLI Framework: Typer

API Framework: FastAPI

MCP Integration: Python MCP SDK

```

  

---

  

## 19. Suggested Folder Structure

  

```text

projectrag/

  pyproject.toml

  README.md

  .projectragignore

  

  projectrag/

    __init__.py

  

    cli/

      main.py

  

    indexer/

      scanner.py

      language_detector.py

      tree_sitter_parser.py

      chunker.py

      hasher.py

      index_runner.py

      tracker.py

      git_tracker.py

      snapshot_tracker.py

  

    summaries/

      project_map.py

      architecture_summary.py

      file_summary.py

      folder_summary.py

  

    embeddings/

      local_embedder.py

  

    storage/

      sqlite_store.py

      lancedb_store.py

      fts_store.py

  

    retrieval/

      vector_search.py

      keyword_search.py

      symbol_search.py

      notes_search.py

      hybrid_search.py

      context_packer.py

  

    graph/

      relationships.py

      dependency_graph.py

      impact_analysis.py

  

    notes/

      notes_store.py

      notes_tree.py

      notes_api.py

  

    tracking/

      changed_files.py

      change_summary.py

      reindex_changed.py

  

    server/

      api.py

  

    mcp/

      mcp_server.py

      tools.py

  

    config/

      settings.py

  

  tests/

    test_indexing.py

    test_search.py

    test_context_pack.py

    test_notes.py

    test_tracking.py

  

  docs/

    ARCHITECTURE.md

    INDEXING.md

    RETRIEVAL.md

    NOTES.md

    TRACKING.md

    MCP_INTEGRATION.md

    SECURITY.md

```

  

---

  

## 20. MVP Roadmap

  

### Phase 1 - Repository Scanner and Tracker

  

```text

projectrag init

projectrag index

file hashing

git detection

snapshot creation

changed-file detection

.projectragignore support

```

  

### Phase 2 - Code Tree and Metadata

  

```text

file tree

context tree

language detection

file metadata

folder summaries

file summaries

important file detection

```

  

### Phase 3 - Code-Aware Parsing

  

```text

Tree-sitter parsing

classes

methods

functions

interfaces

line ranges

imports

basic relationships

```

  

### Phase 4 - Notes System

  

```text

add notes

update notes

retrieve notes

notes tree

notes linked to files/folders/symbols/tasks

notes included in context packs

```

  

### Phase 5 - Hybrid Retrieval

  

```text

SQLite FTS5 keyword search

LanceDB vector search

exact symbol search

notes search

relationship expansion

ranking and deduplication

```

  

### Phase 6 - Context Packs for LLMs

  

```text

task-specific context

architecture summary

relevant file tree

relevant notes tree

relevant files

symbols

tests

risks

implementation hints

token budget

```

  

### Phase 7 - Incremental Reindexing

  

```text

detect modified files

detect deleted files

detect renamed files

remove stale chunks

re-embed changed chunks

update affected summaries

update dependency graph

save new snapshot

```

  

### Phase 8 - MCP Agent Integration

  

```text

repo_map

repo_file_tree

repo_context_tree

repo_notes_tree

repo_search

repo_symbol

repo_prompt_context

repo_impact

repo_find_tests

repo_reindex_changed

```

  

### Phase 9 - API Server and Optional UI

  

```text

FastAPI server

/search endpoint

/symbol endpoint

/context-pack endpoint

/notes endpoint

/tree endpoint

/change-summary endpoint

optional web UI

```

  

---

  

## 21. Security Requirements

  

### 21.1 Local First

  

No source code, notes, embeddings, or metadata should leave the local machine unless explicitly configured.

  

### 21.2 Secret Protection

  

ProjectRAG should avoid indexing:

  

```text

.env files

private keys

certificates

credentials

secrets folders

production connection strings

binary archives

```

  

### 21.3 Read-Only Default

  

MCP tools should be read-only by default, except explicit note tools and reindexing tools.

  

### 21.4 Audit Logging

  

ProjectRAG should log:

  

```text

repositories indexed

files indexed

files skipped

queries executed

MCP tool calls

notes added or updated

context packs generated

reindex operations

changed files detected

```

  

---

  

## 22. Final Design Statement

  

ProjectRAG should become the LLM's structured project memory.

  

It should let an LLM enter a project and immediately retrieve:

  

```text

where it is

how the project is organized

which architecture area matters

which folders/files are relevant

which symbols and code chunks matter

which notes explain hidden knowledge

which tests are related

what changed recently

which risks should be considered

what context can be ignored

```

  

The central principle is:

  

> Do not make the LLM search the entire project. Make the project searchable, understandable, and version-aware before the LLM arrives.