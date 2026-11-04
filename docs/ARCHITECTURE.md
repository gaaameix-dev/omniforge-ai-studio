# Architecture

## Layers

| Layer | Module | Responsibility |
|---|---|---|
| Control | `core/orchestrator.py` | plan, route, gate, schedule, review, delegate, merge |
| Execution | `core/agent.py` | bind a role to LLM + skills + tools and run one task |
| Capability | `skills/`, `agents/`, `tools/` | what can be done, and by whom |
| State | `core/store.py`, `core/memory.py` | durable record and shared working memory |
| Policy | `core/review.py`, `core/retry.py`, `core/budget.py` | what counts as acceptable, how often to retry, what it may cost |
| Observability | `core/message_bus.py`, `core/metrics.py` | events and counters |
| Trust | `core/guard.py`, `tools/base.py`, `api/security.py` | injection defence, permissions, approvals, auth |
| Identity | `core/auth.py`, `api/identity_routes.py` | accounts, sessions, RBAC, workspaces, audit |
| Custody | `core/vault.py`, `core/backup.py` | encrypted secrets, verified backups |
| External | `core/integrations.py`, `tools/github.py`, `tools/mcp.py` | verified service connections |
| Surface | `api/`, `cli.py`, `web/` | HTTP, terminal, dashboard |

Dependencies point downward only: the orchestrator imports agents, not the reverse.
A skill never imports the orchestrator; it receives a `SkillContext`.

## Run state machine

```
planning ──► running ──► done
   │           │  ▲
   │           │  └── awaiting_approval (per task gate)
   │           ├──► failed        (no task completed / budget / timeout)
   │           └──► cancelled     (human)
   └──────────► failed            (planner raised)
```

Task states: `pending → running → done | failed | skipped`, plus
`awaiting_approval` while a gate is open. `skipped` means "will never run":
its dependency failed, a human rejected it, or the run stopped.

## Invariants

1. A task never runs before every task in `depends_on` is `done`.
2. `failed` is terminal for a task; nothing downstream of it runs.
3. A plan's task count never exceeds `max_tasks_per_run`, including delegated tasks.
4. Delegation depth (parent chain) never exceeds 2.
5. Every state transition is persisted before the next one starts.
6. The orchestrator is the only writer of plan structure while a run is live.
7. Events are append-only and ordered per run.
8. A budget breach stops new tasks; in-flight tasks finish their current call.

## Scheduling

```
loop:
  pending = tasks in PENDING
  ready   = pending whose deps are all DONE
  if pending and not ready  -> blocked: mark skipped, stop
  if budget.exceeded        -> stop
  if deadline passed        -> stop
  run ready in parallel (semaphore = max_parallel)
  if any failed and fail_fast -> stop
```

Because the loop recomputes `ready` each pass, a task appended by delegation is
picked up automatically once its parent completes — no special case.

## Review and rework

`Reviewer.review()` returns `{score, passed, issues, placeholder}`. Heuristics:
empty, shorter than `min_chars`, prompt echo, placeholder marker, missing
`required_any` section, verbatim copy of upstream context, no structure. A failing
review raises `ReviewRejected`, which the retry loop treats like any other retryable
error: the issues become `feedback`, the agent is called again, up to `max_rework`.
`OMNIFORGE_REVIEW_WITH_LLM=true` averages a model critique into the score.

## Persistence

SQLite, WAL, one connection guarded by a lock:

- `runs` — goal, status, strategy, tokens, cost, summary, meta
- `tasks` — one row per task, with deps as JSON, review score, parent
- `events` — append-only log of every bus event
- `documents` — knowledge base corpus (run summary + each task result)

`save_run` is an upsert of the run plus a replace of its task rows, so a partial
write can never leave a run with stale tasks. On startup, runs left in
`planning/running/awaiting_approval` are marked failed with "interrupted by restart".

## Trust boundaries

```
user goal ──────────────► trusted instruction (wrapped as data in prompts)
tool output / web page ─► UNTRUSTED: scanned, neutralised, wrapped in a data block
upstream agent output ──► semi-trusted: passed through the same wrapper on reuse
credentials ────────────► environment only; never serialised into a prompt or API
```

1. A tool cannot run without the capability the calling role holds.
2. A `destructive` tool cannot run unless the run carries `allow_destructive`.
3. Every attempt - allowed or denied - is written to `tool_calls`.
4. Integration status is derived from a live check, never from configuration alone.

## Identity and authorisation

```
request ─► resolve_identity (session of_… | api key ofk_… | legacy key | anonymous)
        ─► auth enabled?  no  ─► single-key check (if configured) ─► operator
                          yes ─► 401 if anonymous (unless read-only guest)
                              ─► action_for(method, path) ─► allows(role, action) ─► 403
        ─► rate limit bucket (user | kind | ip) ─► handler
```

Roles are a ladder, not a matrix, so a new route only needs one decision: which rung
it requires (`read`, `run`, `manage`, `own`). Anything privileged is written to
`audit` before the response is returned.

## Extension points

| Want | Do |
|---|---|
| New skill | register a `Skill` (handler for deterministic, template for LLM) |
| New role | register an `AgentTemplate` with a skill list |
| New tool | register a `Tool` with an async `run` |
| New MCP server | `MCPClient([...])` or `MCPHttpClient(url)` + `register_mcp_tools` |
| Different retrieval | subclass/replace `KnowledgeBase.search` |
| Different critic | replace `orchestrator.reviewer` |
| Different planner | pass any `BaseLLM` whose `plan()` returns `{tasks: [...]}` |
