# Session-ledger protocol

The ledger is the session's memory of what's already been discovered, so long sessions
stop re-reading the same code. It fills itself: `repo_search` records the query + the
refs it surfaced, `repo_get` records each fetch, **`repo_graph`/`repo_impact` record the
confident edges they resolve (`confirmed_edges`), and `repo_get`/`repo_changed` record
anything found stale (`stale_refs`)** — so you don't re-run graph discovery you already did.

## Use it

- **Resuming earlier work? Start with `repo_session(action="resume")`.** It renders the most
  recent *prior* session — last task, refs inspected, confirmed facts, open questions — with
  each anchored fact **re-validated against the current code**: `· as recorded (anchor
  unchanged — NOT re-verified)` vs `⚠ STALE — the code this rested on changed; re-verify`. It
  is RENDER-ONLY and every fact is **believed-then**: an unchanged anchor is *not*
  re-verification — treat them as leads to re-confirm, not as established truth. Use it instead
  of rediscovering what the last session already mapped.
- **Within a session**, call `repo_session(action="get")` before searching broadly. If the
  answer is already there (a ref you fetched, a fact you confirmed), use it — don't re-search.
- **Don't re-fetch** a ref that's already in `fetched_refs` — unless `repo_changed`
  says its file moved.
- **Record durable conclusions as you go — and ANCHOR them with `ref=`** so a later resume can
  flag them if that code changes. An unanchored fact is forever "unverifiable":
  - `repo_session(action="note", field="confirmed_facts", value="token expiry is handled here, not the middleware", ref="auth/service.py::AuthService.validateToken")`
  - `field="open_questions"` — things still unknown
  - `field="agent_findings"` — what a sub-agent established
  - `field="edited_files"` — what you changed
  - `field="invalidated_assumptions"` — a belief the code disproved
  - `field="rejected_edges"` — a relationship you investigated and ruled out (e.g.
    "LoginController does NOT call validateToken directly"). The only edge field you fill
    by hand; `confirmed_edges` fills itself.

## Ledger fields

```
searched_queries        returned_refs        fetched_refs
edited_files            confirmed_facts      open_questions
agent_findings          invalidated_assumptions
confirmed_edges (auto)  stale_refs (auto)    rejected_edges (manual)
```

> **Scope:** the ledger is per-process (one file per MCP session). `action="resume"` reads
> the most recent *prior* session across restarts (it never writes to or merges into the
> current one — render-only). A parallel sub-agent's findings still land in *its* ledger, not
> yours — to merge, have it report back and `note` the result into `agent_findings` /
> `confirmed_edges`. Cross-agent auto-merge isn't built yet.

## Rhythm

1. Continuing prior work in a new session? `repo_session(resume)` first; otherwise
   `repo_session(get)` at the start of the task.
2. Work the card→fetch loop; the ledger auto-tracks searches and fetches.
3. `note` the facts/questions/findings worth keeping — **anchor each with `ref=`**.
4. After edits, `note` edited_files and re-check `repo_changed` on anything you fetched.
