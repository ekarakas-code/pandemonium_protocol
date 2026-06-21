# Parallel-agent protocol

For broad tasks, delegate to sub-agents — but the failure mode is **agents
rediscovering the same code and wasting tokens.** Prevent it with strict **task
capsules**: each agent starts from known refs and returns compact evidence, not prose.

## When to use parallel agents

| Task size | Agents |
|---|---|
| small one-file fix | none |
| medium feature / bug | 2–3 |
| large refactor | 4–6 |
| unknown legacy system | architecture + implementation + tests first |

Use parallel agents only when the task has enough breadth to justify them. A vague
"go inspect the repo" is wrong — assign each agent a **distinct purpose**.

## Task capsule (give each sub-agent exactly this)

```
Objective: <one concrete thing — e.g. "find every caller of AuthService.validateToken">
Start from: <known refs, if any>
Retrieval rules:
  - repo_session(get) first; don't redo what's already there.
  - repo_search for cards; fetch ≤3 exact refs unless more is justified.
  - Do NOT read whole files unless necessary.
Return ONLY:
  - relevant refs (path::Qualified.Name)
  - why each matters (one line)
  - any exact code you fetched that's load-bearing
  - confidence + recommended edit targets + risks
```

## Suggested roles (don't always launch all)

| Agent | Finds |
|---|---|
| Architecture | core design + ownership boundaries |
| Implementation | concrete edit targets |
| Dependency | callers / callees / side effects |
| Test | relevant tests + coverage gaps |
| Risk | breaking changes, hidden assumptions, security |
| Docs/API | public interfaces, README/config if relevant |

## Merge

Collect the capsules, dedup refs, and write **one** compact working context: the edit
targets, the evidence behind each, and the open risks. Record durable conclusions to
the ledger with `repo_session(action="note", field="agent_findings", value=...)` so the
main thread (and future agents) don't redo the work.
