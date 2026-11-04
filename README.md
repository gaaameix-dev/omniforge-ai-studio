# OmniForge AI Studio

[![ci](https://github.com/gaaameix-dev/omniforge-ai-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/gaaameix-dev/omniforge-ai-studio/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-328-brightgreen)](tests/)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A working multi-agent platform: one master orchestrator turns a plain-language goal
into a dependency-ordered plan, routes each task to a department agent with the
matching skill, runs tasks in parallel, reviews every output, reworks weak ones,
accepts subtasks delegated by agents, enforces a token budget, gates destructive
tools behind human approval, and keeps a durable, queryable record of everything.

Multi-user (accounts, roles, workspaces, API keys, password reset, MFA), an
encrypted secrets vault, checkpoints with crash recovery, verified backups,
scheduled and webhook-triggered workflows, signed plugin packages, multi-provider
model fallback, per-user cost accounting with quotas, a Python SDK, a live monitor,
an audit trail, English/Hindi UI and a sandboxed executor are built in.

It runs with **no API key** (deterministic planner + mock model) and goes live with
two environment variables. Nothing fakes success: unconfigured services report
`not_configured` and the dashboard says so.

```
goal ─► Master Orchestrator ─► plan (DAG) ─► route to agent ─► approval gate
            │                                      │
            │                                      ▼
            │                        execute ─► review ─► rework (bounded)
            │                                      │
            ▼                                      ▼
      dynamic scheduler  ◄── delegated subtasks  blackboard + artifacts
            │
            ▼
   SQLite (runs, tasks, events, tool calls) · BM25 knowledge · metrics · event stream
```

## Quickstart

```bash
git clone https://github.com/gaaameix-dev/omniforge-ai-studio.git
cd omniforge-ai-studio
pip install -e ".[dev]"
omniforge users --create you@example.com --password "a-good-password"   # optional
omniforge doctor              # environment + integrations + smoke run
omniforge eval                # 5 end-to-end eval cases
omniforge demo                # two goals, printed plan by plan
omniforge serve               # dashboard + API on http://127.0.0.1:8000
omniforge run "Build a 2D mobile puzzle game with 20 levels and a leaderboard"
pytest -q                     # 328 tests, ~2m40s
```

Live model (any OpenAI-compatible endpoint: OpenAI, Azure, Groq, Together,
OpenRouter, vLLM, Ollama at `http://localhost:11434/v1`, LM Studio):

```bash
cp .env.example .env          # OMNIFORGE_LLM_API_KEY=...  OMNIFORGE_MOCK_LLM=false
```

Multi-user mode (sessions, roles, workspaces, API keys, audit):

```bash
OMNIFORGE_AUTH_ENABLED=true OMNIFORGE_SECRET_KEY="a-long-random-string" omniforge serve
# then: curl -X POST localhost:8000/api/auth/register -d '{"email":...,"password":...}'
```

## What this actually is

- **Real orchestration.** Planning, validation, repair, routing, dependency waves,
  retries, review-and-rework, delegation, budgets, approvals and persistence are
  implemented and covered by tests.
- **Honest edges.** The 120 roles are prompt templates, not separately trained
  agents. In mock mode non-deterministic skills return clearly-labelled
  placeholders. GitHub writes, a live model, MCP servers and code execution each
  report `not_configured` until you supply credentials or flip the flag.
- **See the truth:** `omniforge doctor`, `omniforge integrations --verify`,
  `GET /api/integrations`, and `FEATURES.md` for the per-feature status table.

## Multi-user, roles and workspaces

With `OMNIFORGE_AUTH_ENABLED=true` the API requires a session token (`of_…`) or an
API key (`ofk_…`), and enforces a role ladder on every route:

| Role | Can |
|---|---|
| viewer | read runs, agents, skills, tools, knowledge |
| member | also create runs and projects |
| admin | also manage keys, secrets, workspaces, audit, control |
| owner | also delete the account / own the workspace |

First registration becomes owner. Workspaces hold members and projects; invitations
are by email. Accounts can be deleted (data deletion) and passwords changed with
proof of the current one. Everything privileged is written to the audit log.

Without auth enabled the process trusts the local operator, exactly as a single-user
tool should.

## Automation

| Piece | How it works |
|---|---|
| Schedules | interval or daily; `POST /api/schedules`, `POST /api/schedules/{id}/run` to fire now, `omniforge automation schedules` |
| Workflows | save any run's plan (`/api/workflows/from-run/{id}`), replay it with `{{param}}` substitution |
| Inbound webhooks | `POST /api/hooks/{token}/trigger` starts a workflow; the token is the credential |
| Outbound notifications | set `OMNIFORGE_WEBHOOK_URL`; every finished run posts a payload with retries and an audit row |
| Pause / stop | `/api/control/pause`, `/unpause`, `/stop`, `/clear` |
| Conditions | a task may carry `when: {param, equals/in/not_equals/exists}`; failing tasks are dropped and dependents re-linked |
| Load test | `omniforge loadtest --runs 20 --concurrency 4` reports p50/p95 and throughput |

## Operations: alerts, Postgres mirror, workspace

| Piece | How it works |
|---|---|
| Alert rules | metric + comparator + threshold + channel (email/webhook) + cooldown; evaluated when a run finishes. `omniforge alerts --create "spend,run_cost_usd,gt,1.0"` |
| PostgreSQL mirror | `omniforge db --sync postgresql://…` copies runs, tasks, events, documents, usage, audit and tool calls into PostgreSQL for SQL reporting; verified against PostgreSQL 16 |
| Workspace editor | dashboard Workspace view: upload, edit and save project files, then run Python in the sandbox |
| Workflow builder | dashboard Builder view: compose tasks, departments, dependencies and conditions, then save as a reusable workflow |

## Models, cost and quotas

`OMNIFORGE_LLM_PROVIDER` picks `openai`, `anthropic` or `gemini`; `OMNIFORGE_LLM_FALLBACK`
adds `provider:model` entries tried in order. A failing provider does not fail the
run - the gateway moves to the next one and records the failover.

Usage is priced per 1M tokens from a built-in table (override with
`OMNIFORGE_MODEL_PRICES`). Every run writes a usage row attributed to the user, and
`OMNIFORGE_USER_MONTHLY_TOKENS` enforces a monthly quota (HTTP 402 when exhausted).
`/api/billing/estimate` gives a pre-flight estimate, `/api/billing/usage` the real
numbers, and unknown models are reported as unpriced instead of guessed. Projects
can carry their own token quota, and `OMNIFORGE_ALERT_EMAIL` sends a mail when a run
fails.

## Plugins

A plugin is a signed, declarative package: skills and agent templates, no code.
Install, verify, enable, disable and remove from `/api/plugins`, the Automation
view, or `omniforge plugins`. Permissions are reviewed against an operator
allowlist before activation. See `docs/PLUGINS.md`.

## Python SDK

```python
from omniforge.client import OmniForgeClient

async with OmniForgeClient("http://localhost:8000", token="ofk_...") as client:
    run = await client.run("Build a mobile puzzle game")
    print(run["status"], run["summary"])
    await client.download_artifact(run["id"], "summary.md")
```

## Security model

| Concern | How it is handled |
|---|---|
| Prompt injection | every external payload is scanned, neutralised and wrapped in an `<<<UNTRUSTED_DATA>>>` block (`core/guard.py`); the agent is told it is data |
| Least privilege | each tool declares `permissions`; each role gets only what it needs (`github.write` only for DevOps/CI/Release roles, `code.exec` only for test roles) |
| Destructive actions | tools marked `destructive` need `allow_destructive` on the run; denials are logged |
| Secrets | read from the environment only; the API reports status, never values |
| API access | optional bearer token (`OMNIFORGE_API_KEY`) for `/api/*` and the WebSocket |
| Code execution | separate process, `-I -S`, empty env, temp cwd, CPU/memory/file rlimits, timeout, output cap — off by default, and documented as process isolation, **not** a container |
| Audit | every tool call is recorded (tool, agent, ok/failed, duration, args summary, error); privileged API actions are audited |
| Account recovery | single-use reset tokens by email, no account enumeration, sessions invalidated on reset |
| MFA | TOTP with drift tolerance and 8 single-use backup codes |
| Content policy | secret/PII detection with redaction, plus operator blocked terms, applied to goals and outputs |
| Network policy | optional host allowlist for `http_get` |
| Secrets vault | Fernet-encrypted values in SQLite; the API returns names only |
| Rate limits | token bucket per identity, 429 + `Retry-After` |
| Backups | SHA-256 manifest + SQLite integrity check; restore refuses to clobber |

## Dashboard

Mobile-first, zero build step, English/Hindi toggle, nine views:

**Chat** (goal input, task board, approvals, artifacts, live events) ·
**Projects** (create, filter runs) · **Departments** (10 cards) ·
**Agents** (search/filter 120 roles) · **Skills** (51, python vs llm) ·
**Tools & Integrations** (permissions, destructive flags, verify buttons) ·
**Monitor** (control, agents, timings, integration health) ·
**Security** (vault, API keys, audit log) ·
**Logs** (event stream + tool-call audit).

## CLI

| Command | What it does |
|---|---|
| `omniforge run "<goal>" [--json] [--auto-approve]` | plan and execute one goal |
| `omniforge serve [--host --port]` | API + dashboard |
| `omniforge eval [--case name] [--json]` | eval suite, non-zero exit on failure |
| `omniforge doctor` | environment, integrations and a smoke run |
| `omniforge integrations [--verify]` | integration status, optionally verified live |
| `omniforge tools` | every tool with its permissions |
| `omniforge replay <run_id>` | re-run a recorded goal |
| `omniforge history [--limit]` | recent runs from the store |
| `omniforge kb [query]` | knowledge base stats or search |
| `omniforge metrics` | storage counters as JSON |
| `omniforge users [--create EMAIL --password ... --role]` | list or create accounts |
| `omniforge backup [--verify ARCHIVE]` | create a verified backup / verify one |
| `omniforge restore --file ARCHIVE [--force]` | restore after verification |
| `omniforge automation workflows|schedules|hooks` | list automation objects |
| `omniforge usage [--pricing] [--all-time]` | token and cost usage |
| `omniforge plugins [--install|--enable|--disable|--remove]` | plugin packages |
| `omniforge vault [--rotate NEW_KEY]` | vault status or key rotation |
| `omniforge loadtest --runs N --concurrency C` | latency and throughput report |
| `omniforge db [--sync URL] [--verify URL]` | SQLite status, Postgres mirror, verification |
| `omniforge alerts [--create ...] [--delete ID]` | alert rules |
| `omniforge agents / skills / departments / info / demo` | inspect the framework |

## API

Auth off unless `OMNIFORGE_API_KEY` is set; then `/api/*` needs
`Authorization: Bearer <key>` (or `?token=`). `/`, `/metrics`, `/api/health` stay public.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/auth/register` · `/api/auth/login` · `/api/auth/logout` | accounts |
| GET/DELETE | `/api/auth/me` | profile · delete account (data deletion) |
| POST | `/api/auth/password` | change password |
| POST | `/api/auth/forgot` · `/api/auth/reset` | password reset by email |
| GET/POST | `/api/auth/mfa` · `/setup` · `/enable` · `/disable` · `/backup-codes` | TOTP |
| GET/POST | `/api/workspaces[/{id}][/members]` | workspaces and invitations |
| GET/POST/DELETE | `/api/keys[/{id}]` | API keys (shown once) |
| GET/POST/DELETE | `/api/secrets[/{name}]` | vault names · store · delete |
| GET | `/api/audit` · `/api/retention` | audit trail · retention policy |
| GET/POST | `/api/control[/pause|/unpause|/stop|/clear]` | pause, resume, emergency stop |
| GET | `/api/monitor` · `/api/models` | live state · provider status |
| GET/POST | `/api/workflows[/{id}][/run]` · `/api/workflows/from-run/{id}` | workflows |
| GET/POST | `/api/schedules[/{id}/run|enable|disable]` · `/api/schedules/tick` | schedules |
| GET/POST | `/api/hooks` · `POST /api/hooks/{token}/trigger` | webhooks |
| GET | `/api/notifications` · `POST /api/notifications/test` | delivery history |
| GET/POST | `/api/files` · `/api/projects/{id}/files` · `/api/files/{id}/download` | files |
| GET/POST | `/api/projects/{id}/export` · `/api/projects/import` | portable projects |
| GET | `/api/agents/performance` · `/api/memory` · `/api/provenance` | scoring, memory, provenance |
| GET | `/api/billing/pricing` · `/usage` · `/quota` · `POST /estimate` | cost |
| GET/POST/DELETE | `/api/plugins[/install][/{id}/enable|disable]` | plugins |
| GET/POST | `/api/moderation` · `/api/moderation/scan` | content policy |
| GET/POST | `/api/projects/{id}/members` · `/api/projects/{id}/quota` | project ACL and quota |
| GET | `/api/db` · POST `/api/db/sync` · GET `/api/db/verify` | backend status · mirror · verify |
| GET/POST/DELETE | `/api/alert-rules` · POST `/api/alert-rules/evaluate` | threshold alerts |
| GET/POST | `/api/sandbox` · `/api/sandbox/run` | sandbox status · run a snippet |
| PATCH | `/api/files/{id}` | editor save |
| GET/POST | `/api/plugins/registry` · `/api/plugins/registry/install` | registry index · install |
| GET | `/api/knowledge/search?mode=semantic` | hybrid retrieval (needs an embedding key) |
| GET/POST | `/api/runs/{id}/checkpoint` · `/resume` | checkpoint · crash recovery |
| GET | `/api/health` · `/api/framework` · `/api/stats` | liveness · catalogues+policy · counters |
| GET | `/api/metrics` · `/metrics` | metrics JSON · Prometheus text |
| GET | `/api/departments` · `/api/agents` · `/api/skills` · `/api/tools` | catalogues (tools include permissions) |
| GET | `/api/integrations` · POST `/api/integrations/{key}/verify` | status · live check |
| GET | `/api/tool-calls?run_id=` | audit trail |
| GET | `/api/knowledge/search?q=` | BM25 over past work |
| POST | `/api/projects` · GET `/api/projects` · `/api/projects/{id}` | projects |
| POST | `/api/runs` | `{goal, auto_approve?, project_id?, allow_destructive?}` → 201 |
| GET | `/api/runs?limit=&status=&project_id=` | recent runs |
| GET | `/api/runs/{id}?events=true` · `/events` | run record · event log |
| GET | `/api/runs/{id}/artifacts` · `/artifacts/{name}` | list · download |
| POST | `/api/runs/{id}/replay` · `/cancel` | replay · cancel |
| GET/POST | `/api/runs/{id}/approvals[/{task_id}]` | gates |
| WS | `/ws/runs/{id}` | history then live events |
| GET | `/` | dashboard |

```bash
curl -s localhost:8000/api/runs -H 'content-type: application/json' \
  -d '{"goal":"Ship an Android habit tracker","auto_approve":true}'
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OMNIFORGE_LLM_BASE_URL` / `_API_KEY` / `_MODEL` | OpenAI / empty / `gpt-4o-mini` | backend |
| `OMNIFORGE_MOCK_LLM` | `true` | deterministic backend, no key needed |
| `OMNIFORGE_MAX_PARALLEL` | `4` | concurrent agents |
| `OMNIFORGE_MAX_RETRIES` · `_RETRY_BASE_DELAY` | `2` · `0.4` | retry budget and backoff |
| `OMNIFORGE_MAX_TASKS` · `_RUN_TIMEOUT` · `_FAIL_FAST` | `40` · `0` · `true` | run limits |
| `OMNIFORGE_REVIEW_ENABLED` · `_REVIEW_MIN_CHARS` · `_REVIEW_WITH_LLM` · `_MAX_REWORK` | `true` · `40` · `false` · `1` | critic |
| `OMNIFORGE_REQUIRE_APPROVAL` | `false` | gate every task on a human |
| `OMNIFORGE_BUDGET_TOKENS` · `_PRICE_IN` · `_PRICE_OUT` | `0` · `0` · `0` | cost control |
| `GITHUB_TOKEN` · `OMNIFORGE_GITHUB_BASE_URL` | empty · api.github.com | GitHub reads/writes |
| `OMNIFORGE_ALLOW_CODE_EXEC` · `_SANDBOX_TIMEOUT` · `_SANDBOX_MEMORY_MB` | `false` · `5` · `256` | sandbox |
| `OMNIFORGE_DATA_DIR` | `./data` | SQLite DB + artifacts |
| `OMNIFORGE_AUTH_ENABLED` · `_SECRET_KEY` · `_SESSION_TTL_HOURS` | `false` · empty · `72` | multi-user · vault key · session life |
| `OMNIFORGE_MAIL_BACKEND` · `_SMTP_*` · `_PUBLIC_BASE_URL` | `console` · empty · localhost | reset mail |
| `OMNIFORGE_SCHEDULER_ENABLED` · `_TICK` · `_WEBHOOK_URL` | `true` · `5` · empty | automation |
| `OMNIFORGE_USER_MONTHLY_TOKENS` · `_MODEL_PRICES` | `0` · empty | quota · prices |
| `OMNIFORGE_PLUGINS_ENABLED` · `_PLUGIN_ALLOW_UNSIGNED` · `_PLUGIN_PERMISSIONS` | `true` · `true` · read-only set | plugins |
| `OMNIFORGE_MODERATION_ENABLED` · `_BLOCKED_TERMS` · `_HTTP_ALLOWLIST` | `true` · empty · empty | content and network policy |
| `OMNIFORGE_EMBEDDING_API_KEY` · `_EMBEDDING_MODEL` | empty · `text-embedding-3-small` | semantic search |
| `OMNIFORGE_ALERT_EMAIL` · `_ALERT_ON_FAILURE` | empty · `true` | failure alerts |
| `OMNIFORGE_DB_URL` | empty (SQLite) | database backend |
| `OMNIFORGE_PLUGIN_REQUIRE_SIGNATURE` | `false` | signed plugins only |
| `OMNIFORGE_MODERATION_LLM` | `false` | add a model-based content classifier |
| `ANTHROPIC_API_KEY` · `GEMINI_API_KEY` · `OMNIFORGE_LLM_PROVIDER` · `_LLM_FALLBACK` | empty · empty · `openai` · empty | providers |
| `OMNIFORGE_RATE_LIMIT_PER_MINUTE` · `_BURST` · `_ALLOW_ANONYMOUS` | `240` · `60` · `false` | rate limits · guest reads |
| `OMNIFORGE_API_KEY` · `_CORS_ORIGINS` | empty | single-key auth · CORS |

## How a run behaves

1. **Plan.** The model returns 3–7 tasks with dependencies; the plan is validated
   and repaired, falling back to the deterministic planner if still broken.
2. **Route.** Each task gets the best agent in its department (department match,
   skill coverage, primary skill, role-name overlap).
3. **Gate.** With approvals on, the task waits in `awaiting_approval`. Rejection
   skips it and everything downstream.
4. **Execute.** Ready tasks run in parallel; failures retry with backoff.
5. **Review.** The critic scores the output (empty, short, prompt echo, placeholder,
   missing required section, upstream copy, no structure). A failure returns the
   issues to the agent for a bounded rework.
6. **Delegate.** An agent may `ctx.spawn(...)` subtasks; the scheduler runs them as
   a new wave (depth ≤ 2, plan cap, dedupe).
7. **Merge.** Summary, artifacts, knowledge indexing, durable record, metrics.

Every step emits a structured event (`run.created`, `plan.created`, `task.started`,
`task.reviewed`, `task.retrying`, `approval.requested`, `tool.called`,
`task.finished`, `run.finished`, …), streamed over WebSocket and stored.

## Layout

```
omniforge/
  config.py              env-driven Settings
  core/  orchestrator · agent · registry · llm · review · retry · budget
         metrics · store · knowledge · memory · message_bus · tasks · events
         guard (injection defence) · sandbox · integrations · auth · vault
         ratelimit · backup · mailer · mfa · scheduler · notifier · billing
         plugins · plugin_registry · portable · uploads · moderation · gateway
         alerts · pgmirror · loadtest
  skills/ base · library (51) · handlers (9 deterministic)
  agents/ departments (10) · roles (120 + permissions)
  tools/  builtin · github · code · mcp (stdio + http)
  evals/  fixtures · runner
  client.py                async Python SDK
  api/    app · security · identity_routes · automation_routes
          content_routes · platform_routes
  cli.py
web/index.html           dashboard v3 (mobile-first)
tests/                   328 tests
docs/ARCHITECTURE.md     layers, invariants, state machine, extension points
docs/SECURITY.md         operator security checklist
docs/TROUBLESHOOTING.md  setup and failure playbook
docs/PLUGINS.md          plugin manifest, signing, lifecycle
FEATURES.md              implemented / tested / connected / pending
ROADMAP.md               item-by-item status for both production addenda
```

## Testing and evals

```bash
pytest -q          # 328 tests
omniforge eval     # 5 goal-level cases with explicit checks
ruff check .       # lint
```

Coverage includes plan validation and cycles, routing, retries, review rules and
rework, budget stops, delegation caps/depth/dedupe, BM25 ranking, persistence and
crash marking, restart durability, parallel runs, metrics, WebSocket, replay,
artifacts, projects, integrations, tool permissions and approval blocking, prompt
injection neutralisation, the code sandbox, GitHub request shapes, MCP stdio and
HTTP transports, the eval harness, identity/RBAC/sessions/API keys, the encrypted
vault, rate limiting, checkpoints and resume, pause/emergency-stop, idempotency,
priority scheduling, backup/verify/restore, password reset, MFA, mailer backends,
project ACLs and quotas, failure alerts, conditional workflows, the load harness,
embeddings and hybrid search, plugin install from URL, the Postgres mirror against a
real server, alert rules and cooldowns, the plugin registry index, the LLM classifier,
the workspace editor and the workflow builder,
the scheduler and webhook triggers, workflow replay and parameter substitution,
notifier retries, uploads, portable export/import, agent scoring, memory, provenance,
content policy, provider adapters and gateway failover, billing and quotas, plugin
install/signature/permissions, and the SDK end to end.

## Deploy

```bash
docker compose up --build     # http://localhost:8000, data in a named volume
```

Non-root image, healthcheck, no dashboard build step. CI lints and tests on Python
3.10–3.12 and runs the eval suite.

## Limits worth knowing

- Single process: the bus and scheduler are in-process; no distributed workers or
  external queue.
- A restart marks in-flight runs `failed` ("interrupted by restart"); resume them
  from their checkpoint with `POST /api/runs/{id}/resume`.
- Multi-user mode covers accounts, roles, workspaces, audit, reset and MFA; per-project
  ACLs and per-project quotas are not built yet (see `ROADMAP.md`).
- The scheduler runs inside the API process. For more than one API replica, run the
  scheduler on a single replica or disable it elsewhere (`OMNIFORGE_SCHEDULER_ENABLED`).
- Plugins are local packages; there is no remote marketplace.
- Approval gates block indefinitely by design; cancel the run to release them.
- The critic is heuristic by default; `OMNIFORGE_REVIEW_WITH_LLM=true` adds a real
  critique at the cost of a second model call per task.
- BM25 retrieval is lexical: good for names and domain nouns, weak for paraphrase.
- The sandbox is process isolation with rlimits, not a security boundary. Do not
  expose `code_exec` to untrusted users without a real sandbox (container, gVisor).
- Physical Android/iOS testing was not performed; the layout is mobile-first CSS,
  verified structurally, not on a device.
