# Changelog

## 0.6.0

Added
- Project-level ACLs: per-project members with roles, enforced on read, run,
  manage and export; projects without members keep the previous open behaviour.
- Per-project token quota (HTTP 402 when exhausted) and a quota endpoint.
- Failure alerts by email (`OMNIFORGE_ALERT_EMAIL`) reusing the mailer.
- Conditional workflow tasks: declarative `when` blocks (`equals`, `not_equals`,
  `in`, `exists`) that drop tasks and re-link their dependents.
- Load-test harness: `omniforge loadtest --runs N --concurrency C` with latency
  percentiles and throughput.
- Semantic retrieval: optional embedding provider, vectors stored in SQLite and a
  hybrid BM25+cosine scorer (`/api/knowledge/search?mode=semantic`).
- Plugin install from an HTTPS URL, with a signature-only mode
  (`OMNIFORGE_PLUGIN_REQUIRE_SIGNATURE`).
- PostgreSQL seam: full schema in `core/pg.py`, `store_from_url` factory and
  `/api/db` status; a live server is still required to verify the adapter.
- `docs/PRIVACY.md` and `docs/TERMS.md` operator templates.
- Tests: 238 → 264.

Changed
- `run_workflow` accepts a workflow id or row, and reports tasks dropped by
  conditions in `meta.dropped_by_condition`.
- Project responses include members and tokens used.

Fixed
- Plugin fetches map network failures to a clean 400 instead of a 500.

## 0.6.0

Added
- Password reset by email: single-use tokens, no account enumeration, sessions
  invalidated on reset; console and SMTP mail backends.
- TOTP MFA with 8 single-use backup codes; login requires the second factor when
  enrolled, with `X-MFA-Required` signalling.
- Vault key rotation (`Vault.rotate`, `omniforge vault`).
- HTTP host allowlist for `http_get` (`OMNIFORGE_HTTP_ALLOWLIST`).
- Scheduler: interval and daily schedules, `tick_once()` for tests and
  `/api/schedules` for operators, manual fire, enable/disable.
- Workflow templates: save a run's plan, replay it with `{{param}}` substitution,
  versioned rows.
- Inbound webhooks (`/api/hooks/{token}/trigger`) that start a workflow.
- Outbound webhook notifier with retries, audit rows and `/api/notifications`.
- Project files: uploads with size/type limits, SHA-256, download, delete.
- Portable project export/import (zip with project.json, artifacts, uploads).
- Agent performance scoring (`/api/agents/performance`).
- Per-agent memory with search and deletion (`/api/memory`).
- Artifact provenance with checksums and editable licences (`/api/provenance`).
- Content policy: secret/PII detection, redaction and blocked terms.
- Anthropic and Gemini adapters; multi-provider gateway with failover and latency.
- Billing: provider price table, per-run usage rows, monthly token quota (402),
  pre-flight estimate, `/api/billing/*`.
- Plugin packages: install/verify/enable/disable/remove, HMAC signing with trust
  levels, permission review, `docs/PLUGINS.md`.
- Python SDK (`omniforge/client.py`).
- Dashboard: Automation and Usage views, 102 i18n keys in English and Hindi,
  skip link, visible focus, reduced-motion support and `?lite=1`.
- CLI: `plugins`, `automation`, `usage`, `vault`.
- Tests: 175 → 238.

Changed
- `build_llm` selects a provider and wraps fallbacks in a gateway.
- Run submission checks the user's monthly quota and attributes usage to the user.
- Scheduler starts with the API lifespan (disable with `OMNIFORGE_SCHEDULER_ENABLED`).

Fixed
- Replayed workflows no longer reuse task ids from the source run.
- Inbound webhook routes are reachable without a session (the token is the secret).
- Policy rejections return 400 instead of 500.

## 0.6.0

Added
- Multi-user identity: PBKDF2 accounts, sessions, logout, password change, account
  deletion; first registration becomes owner.
- Role-based access control (viewer < member < admin < owner) enforced in middleware
  on every API route and on the WebSocket.
- Workspaces with membership roles and email invitations; projects can carry a
  workspace.
- API keys: shown once, hashed at rest, revocable, `last_used_at` tracked.
- Encrypted secrets vault (Fernet) with write-only API semantics.
- Audit log for auth events, key/secret changes, runs and control actions.
- Token-bucket rate limiting with `Retry-After`.
- Checkpoints per scheduler wave, and `POST /api/runs/{id}/resume` to continue an
  interrupted run from its last checkpoint.
- Global pause, resume, emergency stop and clear (`/api/control/*`).
- Verified backups: sqlite backup API + artifacts + SHA-256 manifest + integrity
  check; `omniforge backup/restore`.
- Run idempotency keys and per-task priority scheduling.
- `/api/monitor` and a Monitor view: control state, busy/idle agents, task counts,
  timings, tool-call failures, integration health.
- Security centre view: vault, secrets, API keys, audit log.
- Hindi/English UI (`web/i18n.js`, 73 keys) with a header toggle.
- `docs/SECURITY.md`, `docs/TROUBLESHOOTING.md`, `ROADMAP.md` (item-by-item status).
- CLI: `users`, `backup`, `restore`.
- Tests: 126 → 175.

Changed
- API middleware now resolves an identity first, then applies auth, RBAC and rate
  limits; single-key mode and open mode keep working unchanged.
- `/api/runs` accepts `priority`, `idempotency_key` and `workspace_id`.
- Task records carry `priority`; run records carry `workspace_id` and `priority`.
- Database migrates additively on startup (new columns and tables).

Fixed
- Backup verification never raises on a corrupt archive; it reports the problem.
- The vault reports unavailable (instead of storing plaintext) without cryptography.

## 0.3.0

Added
- Tool registry v2: permissions, destructive flag, approval requirement, provider,
  enabled state and I/O schema on every tool.
- Tool-call audit trail in SQLite, with `GET /api/tool-calls` and `omniforge tools`.
- Least-privilege permissions per agent role (write/exec granted by role name only).
- GitHub client with reads and writes (commit, branch, PR, issue, workflow dispatch),
  refusing to act without `GITHUB_TOKEN` instead of faking success.
- Integration registry with live verification: `GET /api/integrations`,
  `POST /api/integrations/{key}/verify`, `omniforge integrations --verify`.
- Prompt-injection defence: pattern scan, neutralisation, untrusted-data wrapping
  on every external payload (`core/guard.py`).
- Sandboxed code execution tool: isolated interpreter, rlimits, temp cwd, timeout,
  capped output; off unless `OMNIFORGE_ALLOW_CODE_EXEC=true`.
- Projects: group runs, `POST/GET /api/projects`, dashboard project selector.
- Dashboard v3: mobile-first with Chat, Projects, Departments, Agents, Skills,
  Tools/Integrations and Logs views; approvals inline; artifact downloads.
- `FEATURES.md` status report (implemented / tested / connected / pending).
- Tests: 83 → 126.

Changed
- `http_get` now returns sanitised text inside an untrusted-data block.
- The research agent's profiler skill calls a real tool (`text_stats`).
- Run records carry `project_id`; store gained `projects` and `tool_calls` tables
  with automatic column migration.
- CLI gained `integrations` and `tools`; `doctor` prints integration truth.

Fixed
- Tool denials and approval blocks are logged, not silently dropped.
- Planner routes data-profiling goals to the deterministic profiler.

## 0.2.0

Added
- Durable SQLite store (runs, tasks, events, documents) with crash marking.
- Dynamic scheduler: plan growth from delegation, deadlock detection, wall-clock cap.
- Retry policy with exponential backoff and jitter, per task.
- Review loop: heuristic critic, optional LLM critique, bounded rework with feedback.
- Agent-to-agent delegation (`ctx.spawn`) with depth limit, task cap, deduplication.
- Token and cost budget per run with a hard stop.
- Cross-run knowledge base (BM25) with retrieval injected into every prompt.
- MCP over streamable HTTP in addition to stdio.
- Optional bearer-token auth for the API and WebSocket.
- Metrics (counters, timings) as JSON and Prometheus text.
- Replay, artifact download, knowledge search and stats endpoints.
- Eval harness (`omniforge eval`) with 5 goal-level cases and explicit checks.
- CLI: `doctor`, `eval`, `replay`, `history`, `kb`, `metrics`.
- Dashboard v2, Docker, docker-compose, Makefile, GitHub Actions CI, ruff config.
- Tests: 31 → 83.

Changed
- JSON run files replaced by the SQLite store (`data/omniforge.db`).
- `run.finished` payload includes tokens and cost.
- Task records carry `review_score`, `parent_id` and `attempts`.

Fixed
- Task attempts are counted per try, not once per task.
- Missing required skill sections are a hard review failure, not a deduction.
- Stemming no longer turns "running" into "runn".
- Review no longer burns rework attempts on expected mock placeholders.

## 0.1.0

- Initial framework: orchestrator, 120 roles, 50 skills, 10 departments, message bus,
  blackboard, approvals, MCP stdio, FastAPI + dashboard, 31 tests.
