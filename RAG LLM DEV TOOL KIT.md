
[[PART 2]]
# ProjectRAG

**ProjectRAG** is a local-first codebase intelligence and retrieval system designed to make software projects searchable, understandable, and token-efficient for LLM coding agents such as Claude Code, Cursor, Aider, OpenHands, or custom agents.

The goal is simple:

> Parse a software project once, convert it into structured searchable knowledge, and retrieve only the most relevant code/documentation context when an LLM agent needs it.

ProjectRAG helps reduce token usage, avoid repeated manual file exploration, and improve the accuracy of coding agents by combining local embeddings, keyword search, symbol lookup, and structured metadata.

---

## 1. Problem Statement

Modern LLM coding agents often spend too many tokens exploring repositories.

A typical agent workflow looks like this:

```text
User asks a coding question
→ Agent lists folders
→ Agent opens many files
→ Agent runs grep/search repeatedly
→ Agent reads large files
→ Agent spends thousands of tokens
→ Agent finally finds the relevant function or class
```

This is inefficient for large projects.

ProjectRAG changes the workflow:

```text
User asks a coding question
→ Agent queries ProjectRAG
→ ProjectRAG retrieves exact files, functions, symbols, tests, and docs
→ Agent receives compact context
→ Agent writes better code using fewer tokens
```

---

## 2. Project Goals

ProjectRAG aims to provide:

- Local-first repository indexing
    
- No Docker requirement
    
- Local embedding model support
    
- Low CPU usage
    
- Code-aware chunking
    
- Vector search
    
- Keyword search
    
- Exact symbol search
    
- Metadata-based retrieval
    
- Token-budgeted context packs
    
- Claude Code integration through MCP
    
- Privacy-friendly operation for company/internal repositories
    

---

## 3. Target Users

ProjectRAG is intended for:

- Developers using Claude Code or other coding agents
    
- Teams working with large repositories
    
- Companies that cannot send code to external services
    
- Developers who want local RAG over codebases
    
- Engineering teams with internal coding standards and documentation
    
- Teams working with mixed code, configuration, and process documents
    

---

## 4. Main Use Cases

### 4.1 Code Search

Example:

```bash
projectrag search "where is vendor email sent after purchase order approval?"
```

Expected output:

```text
1. src/Workflow/PurchaseOrderEmailService.cs
2. src/Sap/SapOutputService.cs
3. src/Workflow/PurchaseOrderWorkflow.cs
4. tests/PurchaseOrderEmailTests.cs
```

---

### 4.2 Symbol Lookup

Example:

```bash
projectrag symbol SendPurchaseOrderEmail
```

Expected output:

```text
Symbol: SendPurchaseOrderEmail
File: src/Workflow/PurchaseOrderEmailService.cs
Lines: 42-118
Type: Method
Class: PurchaseOrderEmailService

Related:
- Called by: PurchaseOrderWorkflow.OnApproved
- Calls: VendorRepository.GetPrimaryEmail
- Tests: PurchaseOrderEmailTests.cs
```

---

### 4.3 Context Pack Generation

Example:

```bash
projectrag context "fix missing vendor email after SAP PO approval" --tokens 4000
```

Expected output:

```text
Context Pack:
- Most relevant files
- Important methods/classes
- Line ranges
- Summaries
- Related tests
- Risk notes
- Suggested inspection order
```

---

### 4.4 Claude Code Integration

Claude Code can query ProjectRAG through an MCP server.

Example Claude Code request:

```text
Use ProjectRAG to find the most relevant context for fixing the PO vendor email issue.
```

ProjectRAG returns compact, structured context instead of forcing Claude to explore the entire repository manually.

---

## 5. Recommended MVP Stack

The first version should be simple, local, and easy to install.

```text
Language: Python
Vector Database: LanceDB
Metadata Database: SQLite
Keyword Search: SQLite FTS5
Code Parser: Tree-sitter
Embedding Model: BAAI/bge-small-en-v1.5
CLI Framework: Typer
API Framework: FastAPI
MCP Integration: Python MCP SDK
```

---

## 6. Why This Stack?

### 6.1 LanceDB

LanceDB is used as the local vector database.

It is suitable because:

- It runs locally
    
- It does not require Docker
    
- It works as an embedded database
    
- It supports metadata filtering
    
- It is easy to use from Python
    
- It is good enough for local RAG use cases
    

---

### 6.2 SQLite

SQLite is used for structured metadata.

It stores:

- Repositories
    
- Files
    
- Symbols
    
- Chunks
    
- Relationships
    
- File hashes
    
- Indexing history
    

SQLite is also used for keyword search through FTS5.

---

### 6.3 Tree-sitter

Tree-sitter is used to parse code structurally.

Instead of splitting files every fixed number of tokens, ProjectRAG should chunk code by meaningful units:

- Class
    
- Method
    
- Function
    
- Interface
    
- Enum
    
- Route handler
    
- Configuration block
    

This is important because code should not be chunked randomly.

---

### 6.4 Local Embedding Model

The recommended first embedding model is:

```text
BAAI/bge-small-en-v1.5
```

Reasons:

- Low CPU cost
    
- Small model size
    
- Fast local inference
    
- Good retrieval quality for MVP
    
- Easy to use with sentence-transformers
    

Later alternatives:

```text
nomic-ai/nomic-embed-text-v1.5
BAAI/bge-base-en-v1.5
BAAI/bge-m3
jinaai/jina-embeddings-v2-base-code
```

---

## 7. High-Level Architecture

```text
                 ┌─────────────────────────┐
                 │     User / LLM Agent     │
                 │ Claude Code / Aider /    │
                 │ Cursor / OpenHands       │
                 └────────────┬────────────┘
                              │
                              ▼
                 ┌─────────────────────────┐
                 │      ProjectRAG API      │
                 │ CLI / REST / MCP Server  │
                 └────────────┬────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│ Vector Search   │ │ Keyword Search  │ │ Symbol Search   │
│ LanceDB         │ │ SQLite FTS5     │ │ SQLite Metadata │
└─────────────────┘ └─────────────────┘ └─────────────────┘
          └───────────────────┬───────────────────┘
                              ▼
                 ┌─────────────────────────┐
                 │   Hybrid Result Merger   │
                 └────────────┬────────────┘
                              ▼
                 ┌─────────────────────────┐
                 │ Token-Budgeted Context  │
                 │ Pack Generator          │
                 └─────────────────────────┘
```

---

## 8. Repository Indexing Flow

```text
1. Scan repository files
2. Ignore unnecessary folders and sensitive files
3. Detect file language
4. Parse code using Tree-sitter
5. Extract symbols and chunks
6. Store metadata in SQLite
7. Generate embeddings locally
8. Store vectors in LanceDB
9. Add keyword index using SQLite FTS5
10. Save file hashes for incremental indexing
```

---

## 9. Default Ignored Files

ProjectRAG should include a `.projectragignore` file.

Example:

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

## 10. Folder Structure

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
      hybrid_search.py
      context_packer.py

    graph/
      relationships.py
      impact_analysis.py

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

  docs/
    ARCHITECTURE.md
    INDEXING.md
    RETRIEVAL.md
    MCP_INTEGRATION.md
    SECURITY.md
```

---

## 11. Database Design

### 11.1 SQLite Tables

#### repositories

```sql
CREATE TABLE repositories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

#### files

```sql
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    path TEXT NOT NULL,
    language TEXT,
    content_hash TEXT NOT NULL,
    size_bytes INTEGER,
    last_indexed_at TEXT NOT NULL,
    FOREIGN KEY(repo_id) REFERENCES repositories(id)
);
```

#### symbols

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
    FOREIGN KEY(repo_id) REFERENCES repositories(id),
    FOREIGN KEY(file_id) REFERENCES files(id)
);
```

#### chunks

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

#### relationships

```sql
CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    source_symbol_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    target_symbol_id TEXT,
    target_name TEXT,
    confidence REAL DEFAULT 1.0,
    FOREIGN KEY(repo_id) REFERENCES repositories(id)
);
```

#### chunks_fts

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id,
    content,
    summary,
    path,
    symbol_name
);
```

---

## 12. LanceDB Schema

The LanceDB table should store vectorized chunks.

Table name:

```text
code_chunks
```

Recommended fields:

```text
id
repo_id
file_id
symbol_id
path
language
chunk_type
symbol_name
start_line
end_line
text
summary
vector
```

Example row:

```json
{
  "id": "chunk_001",
  "repo_id": "repo_purchase_workflow",
  "file_id": "file_001",
  "symbol_id": "symbol_001",
  "path": "src/Workflow/PurchaseOrderEmailService.cs",
  "language": "csharp",
  "chunk_type": "method",
  "symbol_name": "SendPurchaseOrderEmail",
  "start_line": 42,
  "end_line": 118,
  "text": "public async Task SendPurchaseOrderEmail(...) { ... }",
  "summary": "Sends purchase order email to vendor with SAP PDF output.",
  "vector": [0.012, -0.022, 0.034]
}
```

---

## 13. Retrieval Strategy

ProjectRAG should use hybrid retrieval.

Do not use vector search alone.

Recommended retrieval flow:

```text
1. Exact symbol search
2. Keyword search using SQLite FTS5
3. Semantic vector search using LanceDB
4. Merge results
5. Remove duplicates
6. Rerank if needed
7. Build context pack within token budget
```

---

## 14. Hybrid Search Scoring

Each result can receive a combined score:

```text
final_score =
    symbol_score * 0.40 +
    keyword_score * 0.30 +
    vector_score * 0.30
```

Example:

```text
Exact symbol match: high priority
Keyword match: medium/high priority
Semantic match: medium priority
```

This is important because code search often depends on exact names.

Examples:

```text
FormData
Server
Log
SendMail
config.xml
SAPOutput
VendorEmail
PurchaseOrderEmailService
```

---

## 15. Context Pack Output

The context pack is the main output for LLM agents.

Example command:

```bash
projectrag context "fix missing vendor email after PO approval" --tokens 4000
```

Example output:

```md
# Context Pack

## Task

Fix missing vendor email after PO approval.

## Most Relevant Files

### 1. src/Workflow/PurchaseOrderEmailService.cs

Reason: Main service responsible for sending vendor PO emails.

Relevant symbols:

- SendPurchaseOrderEmail, lines 42-118
- BuildEmailBody, lines 120-188
- AttachPurchaseOrderPdf, lines 190-230

Summary:

This file handles vendor email notification after purchase order approval. It fetches the vendor email, builds a bilingual email body, attaches the SAP-generated PDF, and sends the message.

---

### 2. src/Sap/SapOutputService.cs

Reason: Generates the purchase order PDF before the email is sent.

Relevant symbols:

- GeneratePurchaseOrderPdf, lines 88-134

Summary:

This file calls SAP output generation logic and returns the PDF bytes used by the email service.

---

### 3. tests/PurchaseOrderEmailTests.cs

Reason: Contains tests for vendor email behavior.

Relevant tests:

- SendsEmailWhenVendorHasEmail
- LogsWarningWhenVendorEmailMissing

---

## Suggested Inspection Order

1. PurchaseOrderEmailService.cs
2. SapOutputService.cs
3. PurchaseOrderEmailTests.cs

## Risks

- Vendor email selection may be shared with invoice email logic.
- SAP output failure may prevent email generation.
- Missing vendor email should be logged without exposing sensitive data.
```

---

## 16. CLI Commands

### Initialize ProjectRAG

```bash
projectrag init
```

Creates local configuration and data folders.

---

### Index Repository

```bash
projectrag index C:\path\to\repo
```

Or:

```bash
projectrag index /path/to/repo
```

---

### Search Repository

```bash
projectrag search "where is purchase order email sent?"
```

---

### Lookup Symbol

```bash
projectrag symbol SendPurchaseOrderEmail
```

---

### Generate Context Pack

```bash
projectrag context "fix missing vendor email after SAP PO approval" --tokens 4000
```

---

### Start API Server

```bash
projectrag serve-api
```

---

### Start MCP Server

```bash
projectrag serve-mcp
```

---

## 17. API Design

### 17.1 Search Endpoint

```http
POST /search
```

Request:

```json
{
  "repo_id": "repo_purchase_workflow",
  "query": "where is vendor email sent?",
  "top_k": 10,
  "mode": "hybrid"
}
```

Response:

```json
{
  "results": [
    {
      "path": "src/Workflow/PurchaseOrderEmailService.cs",
      "symbol": "SendPurchaseOrderEmail",
      "start_line": 42,
      "end_line": 118,
      "score": 0.92,
      "reason": "Main method responsible for vendor email sending."
    }
  ]
}
```

---

### 17.2 Symbol Endpoint

```http
GET /symbol/{symbol_name}
```

Example:

```http
GET /symbol/SendPurchaseOrderEmail
```

---

### 17.3 Context Pack Endpoint

```http
POST /context-pack
```

Request:

```json
{
  "repo_id": "repo_purchase_workflow",
  "task": "fix missing vendor email after SAP PO approval",
  "token_budget": 4000,
  "include_tests": true,
  "include_relationships": true
}
```

---

## 18. MCP Tools

ProjectRAG should expose the following MCP tools.

### repo_search

Searches the indexed repository.

```text
repo_search(query, top_k)
```

---

### repo_symbol

Finds a symbol by name.

```text
repo_symbol(symbol_name)
```

---

### repo_context_pack

Creates a token-budgeted context pack.

```text
repo_context_pack(task, token_budget)
```

---

### repo_find_tests

Finds tests related to a symbol, file, or task.

```text
repo_find_tests(target)
```

---

### repo_impact

Finds likely affected files for a change request.

```text
repo_impact(change_description)
```

---

### repo_reindex

Reindexes changed files.

```text
repo_reindex()
```

---

## 19. Local Embedding Configuration

Default model:

```text
BAAI/bge-small-en-v1.5
```

Recommended settings:

```text
Normalize embeddings: true
Batch size: 16 or 32
Chunk size: 256-512 tokens
Vector dimension: 384
Device: CPU
```

Example configuration:

```yaml
embedding:
  provider: sentence-transformers
  model: BAAI/bge-small-en-v1.5
  device: cpu
  normalize: true
  batch_size: 32
```

---

## 20. Project Configuration

Example `projectrag.yaml`:

```yaml
project:
  name: purchase-workflow
  root: C:\Projects\purchase-workflow

storage:
  sqlite_path: .projectrag/projectrag.db
  lancedb_path: .projectrag/lancedb

embedding:
  provider: sentence-transformers
  model: BAAI/bge-small-en-v1.5
  device: cpu
  normalize: true
  batch_size: 32

indexing:
  chunk_min_lines: 5
  chunk_max_tokens: 512
  include_tests: true
  incremental: true

retrieval:
  mode: hybrid
  vector_top_k: 20
  keyword_top_k: 20
  symbol_top_k: 10
  final_top_k: 10

context_pack:
  default_token_budget: 4000
  include_summaries: true
  include_line_ranges: true
  include_tests: true
  include_risks: true
```

---

## 21. Security Requirements

ProjectRAG should be safe for internal repositories.

### 21.1 Local First

All indexing and retrieval should run locally by default.

No source code should be sent to external APIs unless explicitly configured.

---

### 21.2 Secret Protection

ProjectRAG should not index:

- `.env` files
    
- private keys
    
- certificates
    
- credentials
    
- secrets folders
    
- production configuration files
    

---

### 21.3 Read-Only Default

MCP tools should be read-only by default.

ProjectRAG should not modify source code.

Its role is to retrieve and package context.

---

### 21.4 Audit Logging

ProjectRAG should log:

- Repository indexed
    
- Files indexed
    
- Queries executed
    
- MCP tool calls
    
- Context packs generated
    

---

## 22. Incremental Indexing

ProjectRAG should avoid reindexing unchanged files.

For each file, store:

```text
file path
content hash
last indexed timestamp
language
size
```

When indexing again:

```text
If file hash unchanged:
    skip file

If file hash changed:
    delete old chunks
    parse again
    embed new chunks
    update metadata
```

---

## 23. Supported File Types

Initial support:

```text
.cs
.js
.ts
.tsx
.jsx
.py
.sql
.xml
.json
.yaml
.yml
.md
txt
```

Priority for the first version:

```text
C#
JavaScript
TypeScript
Markdown
XML
JSON
SQL
```

---

## 24. Quality Metrics

ProjectRAG should measure whether it actually improves coding-agent performance.

Recommended metrics:

```text
retrieval_precision_at_5
retrieval_precision_at_10
time_to_relevant_file
tokens_saved_estimate
number_of_files_agent_needed_to_open
number_of_search_calls_reduced
context_pack_user_rating
```

---

## 25. Example Token Savings

Without ProjectRAG:

```text
Agent opens 12 files
Agent reads 8,000-20,000 tokens
Agent spends time finding the right method
```

With ProjectRAG:

```text
Agent receives 3 files
Agent reads 2,000-4,000 tokens
Agent starts implementation faster
```

---

## 26. MVP Roadmap

### Phase 1 — Local Indexer

Deliverables:

```text
projectrag init
projectrag index
SQLite metadata store
LanceDB vector store
Local embeddings
Basic file scanning
```

---

### Phase 2 — Code-Aware Chunking

Deliverables:

```text
Tree-sitter parsing
Function/class/method chunks
Line number metadata
Language detection
Incremental indexing
```

---

### Phase 3 — Hybrid Retrieval

Deliverables:

```text
Vector search
SQLite FTS5 keyword search
Exact symbol search
Result merging
Duplicate removal
```

---

### Phase 4 — Context Pack Generator

Deliverables:

```text
projectrag context
Token-budgeted output
Relevant files
Relevant symbols
Summaries
Tests
Risk notes
```

---

### Phase 5 — MCP Server

Deliverables:

```text
projectrag serve-mcp
repo_search tool
repo_symbol tool
repo_context_pack tool
repo_find_tests tool
```

---

### Phase 6 — API Server

Deliverables:

```text
FastAPI server
/search endpoint
/symbol endpoint
/context-pack endpoint
```

---

### Phase 7 — Optional Web UI

Deliverables:

```text
Repository search screen
Symbol browser
Context pack viewer
Index status dashboard
```

---

## 27. Installation

### Windows

```bash
python -m venv .venv
.venv\Scripts\activate
pip install lancedb sentence-transformers tree-sitter tree-sitter-language-pack fastapi uvicorn typer rich
```

### Linux/macOS

```bash
python -m venv .venv
source .venv/bin/activate
pip install lancedb sentence-transformers tree-sitter tree-sitter-language-pack fastapi uvicorn typer rich
```

---

## 28. Example Python Embedding Code

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-small-en-v1.5")

texts = [
    "public async Task SendPurchaseOrderEmail(...) { ... }",
    "Generates SAP purchase order PDF output."
]

vectors = model.encode(
    texts,
    normalize_embeddings=True,
    batch_size=32
)

print(vectors.shape)
```

---

## 29. Example LanceDB Usage

```python
import lancedb
import pandas as pd
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-small-en-v1.5")

docs = [
    {
        "id": "chunk_001",
        "path": "src/Workflow/PurchaseOrderEmailService.cs",
        "symbol": "SendPurchaseOrderEmail",
        "text": "public async Task SendPurchaseOrderEmail(...) { ... }"
    }
]

vectors = model.encode(
    [doc["text"] for doc in docs],
    normalize_embeddings=True
).tolist()

rows = []

for doc, vector in zip(docs, vectors):
    rows.append({
        "id": doc["id"],
        "path": doc["path"],
        "symbol": doc["symbol"],
        "text": doc["text"],
        "vector": vector
    })

db = lancedb.connect(".projectrag/lancedb")
table = db.create_table("code_chunks", pd.DataFrame(rows), mode="overwrite")

query = "where is purchase order email sent?"
query_vector = model.encode(query, normalize_embeddings=True).tolist()

results = table.search(query_vector).limit(5).to_pandas()

print(results[["path", "symbol", "text"]])
```

---

## 30. Future Enhancements

Possible future improvements:

```text
Graph-based code relationship search
Call graph extraction
Test coverage mapping
Git history indexing
Pull request summary indexing
Local reranker model
ONNX quantized embedding model
VS Code extension
Web UI
Multi-repository search
Team-shared local index
Permission-aware retrieval
```

---

## 31. Final Product Definition

ProjectRAG is:

> A local-first repository intelligence layer that parses code structurally, stores local embeddings and metadata, performs hybrid retrieval, and generates compact context packs for LLM coding agents.

The core architecture is:

```text
Tree-sitter
+ Local embedding model
+ LanceDB
+ SQLite
+ SQLite FTS5
+ Hybrid retrieval
+ Context pack generator
+ MCP server
```

The most important principle:

> Do not make the LLM search the entire project. Make the project searchable before the LLM arrives.