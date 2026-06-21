# Reference rules

A **ref** is a durable handle to an exact code location. Three forms:

```
path                          -> the whole file
path::Qualified.Name          -> a symbol (preferred — survives edits)
path:start-end                -> a line span (last-resort fallback)
```

## Why symbol refs, not line numbers

`path:start-end` goes stale the instant lines shift. `path::Qualified.Name` does not —
`repo_get` **re-parses the current file and re-finds the symbol by name**, so the ref a
search gave you yesterday still resolves after the file was edited. Always prefer the
`::Qualified.Name` form when quoting a ref back.

- Qualified names include the enclosing scope: `Retriever.search`,
  `app.Calculator.add` (namespace.class.method), etc.
- Works across languages (Python, C++, C#, JavaScript, TypeScript).

## Fetching

`repo_get(ref, expand="exact"|"neighbors"|"parent"|"file")` — start `exact`, widen only
as needed (see `retrieval-protocol.md`).

## Staleness — `repo_changed`

The index is a snapshot. If a file changed after the last index, its symbols may be out
of date.

- After edits, or before relying on something you fetched earlier, call
  `repo_changed("<ref> <ref> ...")` (empty = check all indexed files).
- If a ref is reported **stale / changed**, run `repo_reindex_changed()` and re-fetch
  before trusting it.
- `repo_get` itself re-resolves a symbol ref live, so the *code* it returns is current
  even if the index is slightly behind — but the index's summaries/ranking aren't, so
  reindex when in doubt.
