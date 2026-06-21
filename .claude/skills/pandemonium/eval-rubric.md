# Eval rubric — did retrieval help?

The goal is **fewer tokens AND fewer reasoning steps** — enough code to be correct,
not so much you lose focus. After a task, self-check:

- Did you find the right code **without reading whole files**?
- **Fetches-to-resolution:** how many `repo_get` calls before you reached the
  source-of-truth symbol? Fewer is better. (Many fetches → your `repo_search` queries
  weren't specific enough.)
- Did you **avoid re-searching / re-fetching** (used the ledger)?
- Did you **verify impact** (callers / tests / config / docs) before declaring done?
- Did `repo_changed` catch any stale fetches before you trusted them?

Red flags that the loop wasn't used well:
- You read several whole files, or fetched >5 refs, for a focused task.
- You pasted a whole `repo_search` result into your reasoning instead of picking refs.
- Two sub-agents inspected the same area.
- You edited before identifying the owning symbol + its tests.
