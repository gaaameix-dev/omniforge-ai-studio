# Feature status

Legend
- **Implemented** — code exists and runs.
- **Tested** — covered by the automated suite (`pytest -q`, 328 tests).
- **Connected** — wired to a real external service *in this checkout*. "Needs key"
  means the code path is real but there is no credential here, so it reports
  `not_configured` instead of pretending to work.

Nothing in this table is aspirational. `omniforge doctor`, `omniforge integrations`
and `GET /api/integrations` report the same truth at runtime.

## Core orchestration

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Master Orchestrator (plan → route → gate → execute → merge) | Yes | Yes | Yes | `core/orchestrator.py` |
| LLM planning with JSON schema + validation | Yes | Yes | Needs key | mock mode uses the deterministic planner |
| Deterministic fallback planner | Yes | Yes | Yes | keyword routing, always available |
| Plan repair (unknown dept/skill, cycles) | Yes | Yes | Yes | invalid plans fall back, never crash |
| Dependency waves / dynamic scheduler | Yes | Yes | Yes | tolerates plan growth from delegation |
| Intelligent routing (dept → skill → agent) | Yes | Yes | Yes | scored, 120 roles |
| Parallel execution with concurrency cap | Yes | Yes | Yes | `OMNIFORGE_MAX_PARALLEL` |
| Retry with exponential backoff + jitter | Yes | Yes | Yes | per task |
| Review loop + bounded rework | Yes | Yes | Yes | heuristic critic; LLM critique opt-in |
| Agent-to-agent delegation | Yes | Yes | Yes | depth ≤ 2, task cap, dedupe |
| Human approval gates | Yes | Yes | Yes | approve / reject / resume |
| Final synthesis / handoff summary | Yes | Yes | Yes | written to artifacts |
| Wall-clock run timeout | Yes | Yes | Yes | `OMNIFORGE_RUN_TIMEOUT` |
| Fail-fast on permanent task failure | Yes | Yes | Yes | dependents marked `skipped` |

## Agents, skills, departments

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| 120 agent role templates (12 × 10 departments) | Yes | Yes | Yes | each has skills, tools, permissions, system prompt |
| 51 skills (9 deterministic handlers) | Yes | Yes | Yes | deterministic ones need no model |
| Modular skill registration | Yes | Yes | Yes | `skills.register(Skill(...))` |
| Role-level permission grants | Yes | Yes | Yes | least privilege, per role name |
| Custom roles from bundled skills | Yes | Yes | Yes | tested with a "VR Game Engineer" |

## Communication and state

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Shared message bus (pub/sub, history, listeners) | Yes | Yes | Yes | in-process |
| Live event stream over WebSocket | Yes | Yes | Yes | history then stream |
| Blackboard (run-scoped shared memory) | Yes | Yes | Yes | upstream results visible to later tasks |
| Durable SQLite store (runs, tasks, events, tool calls, documents, projects) | Yes | Yes | Yes | WAL, migrations, crash marking |
| Run persistence across restart | Yes | Yes | Yes | old runs served from the store |
| Cross-run knowledge base (BM25) | Yes | Yes | Yes | indexed after every run |
| Projects (group runs under one project) | Yes | Yes | Yes | `/api/projects` |

## Tools and integrations

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Tool registry with permissions, I/O schema, availability | Yes | Yes | Yes | `GET /api/tools` |
| Tool-call logging (ok/failed, duration, args summary) | Yes | Yes | Yes | stored + `GET /api/tool-calls` |
| Destructive tools require approval | Yes | Yes | Yes | run flag `allow_destructive` |
| Integration status with live verification | Yes | Yes | Yes | `omniforge integrations --verify` |
| HTTP fetch tool (sanitised, untrusted block) | Yes | Yes | Yes (public) | network depends on your runtime |
| GitHub reads (repo, file, issues, user) | Yes | Yes | Needs token for higher limits | public reads work unauthenticated |
| GitHub writes (commit, branch, PR, issue, workflow dispatch) | Yes | Yes | Needs key | `GITHUB_TOKEN` with repo scope |
| MCP over stdio | Yes | Yes | Yes (local servers) | tested against a fake server |
| MCP over streamable HTTP | Yes | Yes | Needs a server URL | tested with an injected transport |
| Sandboxed code execution | Yes | Yes | Off by default | `OMNIFORGE_ALLOW_CODE_EXEC=true` |

## Security

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Prompt-injection scanning + neutralisation + untrusted wrapping | Yes | Yes | Yes | `core/guard.py` |
| Tool permission enforcement | Yes | Yes | Yes | denies and logs the attempt |
| Approval before destructive actions | Yes | Yes | Yes | per run, explicit flag |
| Secrets only from environment, never returned by the API | Yes | Yes | Yes | API reports status, never values |
| Bearer-token auth for API + WebSocket | Yes | Yes | Yes | `OMNIFORGE_API_KEY` |
| Sandboxed execution (process + rlimits + timeout) | Yes | Yes | Off by default | not a container; see limits |
| Tool call audit trail | Yes | Yes | Yes | SQLite `tool_calls` |

## Identity, security and operations (v0.4)

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Accounts, sessions, logout | Yes | Yes | Yes | PBKDF2, hashed tokens, expiry |
| RBAC (viewer/member/admin/owner) | Yes | Yes | Yes | enforced in middleware |
| Workspaces and invitations | Yes | Yes | Yes | membership table |
| API keys (shown once, revocable) | Yes | Yes | Yes | hashed at rest |
| Encrypted secrets vault | Yes | Yes | Yes | Fernet; values never returned |
| Audit log | Yes | Yes | Yes | privileged actions + auth events |
| Rate limiting | Yes | Yes | Yes | token bucket, 429 + Retry-After |
| Account deletion / retention | Yes | Yes | Yes | self-service |
| Global pause / emergency stop | Yes | Yes | Yes | `/api/control/*` |
| Checkpoints and crash resume | Yes | Yes | Yes | per-wave checkpoints |
| Verified backups and restore | Yes | Yes | Yes | manifest + integrity check |
| Priority scheduling / idempotency | Yes | Yes | Yes | per-task priority, run keys |
| Monitoring endpoint + view | Yes | Yes | Yes | `/api/monitor` |
| Hindi/English UI | Yes | Yes (keys) | Yes | 73 keys, toggle in header |
| Password reset by email | No | No | No | needs a mailer |
| MFA | No | No | No | pending |
| Per-project ACLs, quotas | No | No | No | see ROADMAP |

## Automation, platform and cost (v0.5)

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Password reset by email | Yes | Yes | Console backend; SMTP ready | single-use tokens |
| TOTP MFA + backup codes | Yes | Yes | Yes | login gate + `X-MFA-Required` |
| Vault key rotation | Yes | Yes | Yes | `omniforge vault --rotate` |
| HTTP host allowlist | Yes | Yes | Yes | `OMNIFORGE_HTTP_ALLOWLIST` |
| Schedules (interval/daily) | Yes | Yes | Yes | manual fire + tick endpoint |
| Workflow templates + replay | Yes | Yes | Yes | `{{param}}` substitution |
| Inbound webhooks | Yes | Yes | Yes | token is the credential |
| Outbound notifications | Yes | Yes | Needs URL | retries + audit rows |
| Project file uploads/downloads | Yes | Yes | Yes | size/type limits, SHA-256 |
| Portable export / import | Yes | Yes | Yes | zip format |
| Agent performance scoring | Yes | Yes | Yes | `/api/agents/performance` |
| Per-agent memory + API | Yes | Yes | Yes | search, delete |
| Provenance + licences | Yes | Yes | Yes | checksums per artifact |
| Content policy (secrets, PII, terms) | Yes | Yes | Yes | redaction + blocking |
| Anthropic / Gemini adapters | Yes | Yes | Needs keys | real request shapes |
| Provider fallback gateway | Yes | Yes | Yes | failover + latency |
| Price table + usage + quota | Yes | Yes | Yes | 402 on quota |
| Cost estimate | Yes | Yes | Yes | labelled as an estimate |
| Plugin packages + signing | Yes | Yes | Yes | verified/unsigned trust |
| Python SDK | Yes | Yes | Yes | async client |
| Accessibility pass | Yes | Partly | Yes | skip link, focus, reduced motion |
| Lite mode | Yes | Yes | Yes | `?lite=1` |
| Remote plugin marketplace | No | No | No | local packages only |
| Load-test harness | No | No | No | needs a target environment |
| Vector embeddings (hybrid BM25 + cosine) | Yes | Yes | Needs key | `core/embeddings.py` |
| Project ACLs and per-project quota | Yes | Yes | Yes | `api/access.py` |
| Conditional workflow tasks | Yes | Yes | Yes | declarative `when` |
| Load-test harness | Yes | Yes | Yes | `omniforge loadtest` |
| Failure alerts by email | Yes | Yes | Console backend | `OMNIFORGE_ALERT_EMAIL` |
| Plugin install from URL | Yes | Yes | Needs a URL | signature-only mode available |
| PostgreSQL mirror | Yes | Yes | Yes | verified against PostgreSQL 16 |
| Native PostgreSQL store | No | No | No | mirror only; dialect shim still open |
| Alert rules with cooldown | Yes | Yes | Yes | email + webhook |
| Plugin registry index | Yes | Yes | Needs a URL | checksum verified |
| LLM content classifier | Yes | Yes | Needs key | off by default |
| Workspace editor + sandbox run | Yes | Yes | Yes | dashboard view |
| Workflow builder (form) | Yes | Yes | Yes | dashboard view |

## Verification

| Check | Result |
|---|---|
| Tests | 328 |
| Line coverage | 83% |
| Endpoints documented vs implemented | 62 / 62 |
| i18n keys (en/hi) | 126 / 126 |
| Eval suite | 5/5 |

## Surface

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| FastAPI service (runs, approvals, replay, artifacts, metrics, knowledge, projects, integrations) | Yes | Yes | Yes | |
| Prometheus metrics | Yes | Yes | Yes | `/metrics` |
| Dashboard: chat, projects, departments, agents, skills, tools/integrations, monitor, security, logs | Yes | Yes (served + structure) | Yes | zero build step, EN/HI |
| Mobile-first responsive layout | Yes | Partly | Yes | CSS verified; not tested on a physical Android device |
| CLI: run, serve, eval, doctor, replay, history, kb, metrics, integrations, tools, users, backup, restore | Yes | Yes | Yes | |
| Dockerfile + compose + CI + Makefile | Yes | Lint/CI config only | Not built here | no Docker daemon in this environment |

## Pending / not built

| Item | Status | Why |
|---|---|---|
| Multi-agent debate or voting between candidate outputs | Pending | single-pass review today |
| Vector embeddings (semantic retrieval) | Pending | BM25 only; swap `KnowledgeBase.search` |
| Distributed workers / queue-backed execution | Pending | single process by design |
| Resuming a crashed run from its last checkpoint | Pending | interrupted runs are marked failed |
| Physical Android/iOS device testing | Pending | needs a device or browser farm |
| Image / 3D asset generation tools | Requires external service | no provider wired |
| Unity / Unreal / Godot adapters | Pending | would sit on top of the tool registry |
| Paid API cost enforcement per provider | Partly | budget + optional prices, not per-provider billing |

## How to see the truth yourself

```bash
omniforge doctor            # environment + integrations + smoke run
omniforge integrations --verify
omniforge tools             # every tool with its permissions
curl localhost:8000/api/integrations
curl localhost:8000/api/tool-calls
```
