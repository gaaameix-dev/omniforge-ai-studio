# Feature status

Legend: **Implemented** (code exists and runs) · **Tested** (covered by
`pytest -q`) · **Connected** (worked against a real service in this checkout;
"needs key" means the path is real but there is no credential here).

Single source of truth: `omniforge doctor`, `GET /api/integrations`,
`GET /api/media` and `GET /api/db` report the same state at runtime.

Numbers at this build: **496 tests**, 83% line coverage, 136 routes, 13 dashboard
views, 126 i18n keys (en/hi), 5/5 eval cases.

## Core orchestration

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Master orchestrator (plan → route → gate → execute → merge) | Yes | Yes | Yes | `core/orchestrator.py` |
| LLM planning with JSON schema + validation | Yes | Yes | Needs key | mock mode uses the deterministic planner |
| Deterministic fallback planner | Yes | Yes | Yes | keyword routing |
| Plan repair (unknown dept/skill, cycles) | Yes | Yes | Yes | invalid plans fall back |
| Dependency waves / dynamic scheduler | Yes | Yes | Yes | tolerates plan growth |
| Intelligent routing (dept → skill → agent) | Yes | Yes | Yes | scored, 120 roles |
| Parallel execution with a concurrency cap | Yes | Yes | Yes | `MAX_PARALLEL` |
| Retry with exponential backoff + jitter | Yes | Yes | Yes | per task |
| Review loop + bounded rework | Yes | Yes | Yes | heuristic critic, LLM critique opt-in |
| **Multi-agent debate / voting** | Yes | Yes | Yes | `DEBATE_CANDIDATES` > 1 runs N candidates, keeps the best reviewed |
| Agent-to-agent delegation | Yes | Yes | Yes | depth ≤ 2, capped, deduped |
| Human approval gates | Yes | Yes | Yes | approve / reject / resume |
| Final synthesis / handoff summary | Yes | Yes | Yes | written to artifacts |
| Wall-clock run timeout | Yes | Yes | Yes | `RUN_TIMEOUT` |
| Fail-fast on permanent failure | Yes | Yes | Yes | dependents skipped |
| Priority scheduling / idempotency keys | Yes | Yes | Yes | per task / per run |
| **Queue mode (distributed workers)** | Yes | Yes | Yes | `WORKER_MODE=true` + `omniforge worker`; atomic lease claim, expired leases requeued |

## Agents, skills, departments

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| 120 agent role templates | Yes | Yes | Yes | skills, tools, permissions, prompt |
| 51 skills, all with deterministic implementations | Yes | Yes | Yes | `skills/generators.py`; no model needed for any skill |
| Prompt path for custom skills | Yes | Yes | Needs key | `PREFER_GENERATORS=false` uses the model instead |
| Modular skill registration | Yes | Yes | Yes | `skills.register(Skill(...))` |
| Role-level permission grants | Yes | Yes | Yes | least privilege per role |
| Custom roles from bundled skills | Yes | Yes | Yes | tested with "VR Game Engineer" |
| Agent performance scoring | Yes | Yes | Yes | `/api/agents/performance` |
| Per-agent memory + API | Yes | Yes | Yes | search, delete |

## Communication and state

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Shared message bus | Yes | Yes | Yes | pub/sub, history, listeners |
| Live event stream (WebSocket) | Yes | Yes | Yes | history then stream |
| Blackboard | Yes | Yes | Yes | upstream results visible |
| **SQLite store** | Yes | Yes | Yes | default backend |
| **PostgreSQL store** | Yes | Yes | Yes | `DB_URL=postgresql://…`; verified against PostgreSQL 16 |
| PostgreSQL mirror | Yes | Yes | Yes | `omniforge db --sync` for BI/reporting |
| Checkpoints + crash resume | Yes | Yes | Yes | per wave, `/api/runs/{id}/resume` |
| Verified backups + restore | Yes | Yes | Yes | manifest, integrity check, `--force` guard |
| Cross-run knowledge base (BM25) | Yes | Yes | Yes | indexed after every run |
| **Hybrid retrieval (BM25 + embeddings)** | Yes | Yes | Needs key | `?mode=semantic` |
| Projects + per-project ACLs + quotas | Yes | Yes | Yes | `api/access.py` |

## Tools and integrations

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Tool registry with permissions and schemas | Yes | Yes | Yes | `/api/tools` |
| Tool-call audit trail | Yes | Yes | Yes | `/api/tool-calls` |
| Destructive tools need approval | Yes | Yes | Yes | run flag `allow_destructive` |
| Integration status with live verification | Yes | Yes | Yes | `omniforge integrations --verify` |
| HTTP fetch (sanitised, untrusted block) | Yes | Yes | Yes (public) | optional host allowlist |
| GitHub reads + writes | Yes | Yes | Needs key | commit, branch, PR, issue, workflow |
| MCP over stdio and HTTP | Yes | Yes | Yes (local) | tested with a fake server |
| Sandboxed code execution | Yes | Yes | Off by default | process isolation + rlimits |
| **Image generation** | Yes | Yes | Needs key | OpenAI-compatible `/images/generations` |
| **Unity / Unreal / Godot scaffolding** | Yes | Yes | Yes | text project skeletons; nothing is compiled |
| Video / audio / 3D model generation | No | No | No | no provider wired — not claimed |

## Automation

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Schedules (interval / daily) | Yes | Yes | Yes | manual fire + tick endpoint |
| Workflow templates + replay | Yes | Yes | Yes | `{{param}}` substitution |
| **Conditional tasks** | Yes | Yes | Yes | declarative `when` (equals/in/not_equals/exists) |
| Inbound webhooks | Yes | Yes | Yes | token is the credential |
| Outbound notifications | Yes | Yes | Needs URL | retries + audit rows |
| Alert rules (metric/threshold/cooldown) | Yes | Yes | Yes | email + webhook |
| Failure alerts by email | Yes | Yes | Console backend | `ALERT_EMAIL` |
| Plugin packages + signing | Yes | Yes | Yes | verified / unsigned trust |
| Plugin install from URL | Yes | Yes | Needs URL | signature-only mode available |
| Plugin registry index | Yes | Yes | Needs URL | checksum verified |
| Remote marketplace service | No | No | No | format shipped; hosting is an ops task |

## Identity, security, cost

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| Accounts, sessions, logout | Yes | Yes | Yes | PBKDF2, hashed tokens, expiry |
| RBAC (viewer/member/admin/owner) | Yes | Yes | Yes | middleware-enforced |
| Workspaces and invitations | Yes | Yes | Yes | membership table |
| Password reset by email | Yes | Yes | Console backend | single-use tokens, no enumeration |
| TOTP MFA + backup codes | Yes | Yes | Yes | login gate |
| API keys | Yes | Yes | Yes | shown once, hashed, revocable |
| Encrypted secrets vault | Yes | Yes | Yes | Fernet, rotation |
| Audit log | Yes | Yes | Yes | auth + privileged actions |
| Rate limiting | Yes | Yes | Yes | token bucket, 429 |
| Content policy (secrets/PII/terms) | Yes | Yes | Yes | redaction + blocking |
| LLM content classifier | Yes | Yes | Needs key | off by default, fails open |
| Prompt-injection defence | Yes | Yes | Yes | scan → neutralise → wrap |
| Token budget + monthly quota | Yes | Yes | Yes | 402 on exhaustion |
| **Per-provider cost caps** | Yes | Yes | Yes | `PROVIDER_BUDGETS=openai:5,anthropic:10` |
| Usage, pricing, estimates | Yes | Yes | Yes | `/api/billing/*` |

## Surface

| Feature | Implemented | Tested | Connected | Notes |
|---|---|---|---|---|
| FastAPI service (136 routes) | Yes | Yes | Yes | OpenAPI at `/docs` |
| Prometheus metrics | Yes | Yes | Yes | `/metrics` |
| Dashboard, 13 views | Yes | Yes | Yes | chat, projects, departments, agents, skills, tools, workspace, builder, automation, monitor, usage, security, logs |
| Mobile-first + lite mode | Yes | Partly | Yes | `?lite=1`; not tested on a physical device |
| Hindi + English UI | Yes | Yes | Yes | 126 keys, toggle |
| Accessibility pass | Yes | Partly | Yes | skip link, focus, reduced motion |
| CLI (26 commands) | Yes | Yes | Yes | 22 subprocess smoke tests |
| Python SDK | Yes | Yes | Yes | `omniforge/client.py` |
| Dockerfile + compose + CI | Yes | Lint/CI config only | Not built here | no Docker daemon in this sandbox |

## Still not built (and why)

| Item | Status | Why |
|---|---|---|
| Speech (TTS) generation | Yes | Yes | Needs key | `/api/media/speech`, OpenAI-compatible |
| Video / music / 3D model generation | Not built | needs providers (Runway/Suno/Blender-class); wiring one is a new integration, not a flag |
| Plugin marketplace index + export | Yes | Yes | Yes | `/api/plugins/index` and `/api/plugins/{id}/export` (self-hostable) |
| Workflow dependency graph | Yes | Yes (structure) | Yes | SVG graph in the Builder view; drag-to-arrange not built |
| Physical Android/iOS device testing | Not done | needs hardware or a device farm; the layout is verified structurally only |
| Per-team alert routing | Yes | Yes | Yes | `alert_routes`, `/api/alert-routes` |
| Token-by-token streaming | Yes | Yes | Yes | `agent.token` events, live box in the chat view |
| Single-task cancellation | Yes | Yes | Yes | `/api/runs/{id}/tasks/{task_id}/cancel` |
| Expert-level prose from generators | Not built | generators produce structured first drafts (specs, tables, code skeletons), not domain-expert judgement |
| Bulk knowledge import | Yes | Yes | Yes | `/api/knowledge/import`, up to 200 documents |
| Settings screen (DB-backed config) | Yes | Yes | Yes | `/api/settings`, 11 tunable keys, applies live |
| Docker image / PyPI release | Configured | Workflow only | Not published | `.github/workflows/release.yml` builds the wheel and pushes to GHCR on a tag; publishing needs registry secrets |

## Backlog

Everything still missing is listed with the exact blocker and a size estimate in
[`docs/REMAINING.md`](docs/REMAINING.md). That file is the single backlog: never built,
built-but-limited, code-ready-waiting-on-a-credential, and deliberate non-goals.

## How to check all of this yourself

```bash
omniforge doctor                      # environment + integrations + smoke run
omniforge eval                        # 5 goal-level cases
omniforge integrations --verify       # live service checks
omniforge db                          # which database backend is active
curl localhost:8000/api/media         # media capability truth
curl localhost:8000/api/workers       # queue mode state
curl localhost:8000/api/billing/providers
pytest -q                             # 356 tests
```
