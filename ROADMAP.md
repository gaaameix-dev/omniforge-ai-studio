# Delivery status and roadmap

Rule: a line says IMPLEMENTED / TESTED only when code exists and a test exercises
it. CONNECTED means it worked against a real service in this checkout; where a
credential is missing, the line says so.

Legend: **I** implemented · **T** tested · **C** connected here · **P** partial · **N** pending

Counts at this build: 283 automated tests · 51 shipped skills · 120 agent roles ·
15 built-in tools · 10 departments · 11 dashboard views.

---

## Closed in this round (0.7.0)

Everything below was previously PENDING or PARTIAL and is now working and tested.

| Item | Status | Where |
|---|---|---|
| Password reset by email | I · T · C (console backend) · SMTP ready | `core/mailer.py`, `/api/auth/forgot`, `/api/auth/reset` |
| MFA (TOTP + backup codes) | I · T | `core/mfa.py`, `/api/auth/mfa/*` |
| Outbound alerts / notifications | I · T · C (needs a URL) | `core/notifier.py`, `/api/notifications` |
| Scheduled execution | I · T | `core/scheduler.py`, `/api/schedules` |
| Event-triggered workflows / inbound webhooks | I · T | `/api/hooks/{token}/trigger` |
| Workflow templates + pause/resume + versioning | I · T (branching still P) | `core/orchestrator.run_workflow`, `/api/workflows` |
| Plugin packages: install, verify, enable, disable, remove | I · T | `core/plugins.py`, `/api/plugins` |
| Plugin signing + trust levels + permission review | I · T | HMAC manifest signature |
| Import / export (portable project format) | I · T | `core/portable.py` |
| File uploads and downloads (size + type limits, hashes) | I · T | `core/uploads.py`, `/api/files` |
| Anthropic and Gemini adapters | I · T (needs keys) | `core/llm.py` |
| Multi-provider fallback gateway + latency tracking | I · T | `core/gateway.py`, `/api/models` |
| Provider price table + usage accounting | I · T | `core/billing.py`, `/api/billing/*` |
| Per-user monthly token quota (blocks at 402) | I · T | `Billing.check_quota` |
| Pre-flight cost estimate | I · T | `/api/billing/estimate` |
| Agent performance scoring | I · T | `store.agent_stats`, `/api/agents/performance` |
| Per-agent memory + memory API (list / search / delete) | I · T | `agent_memory`, `/api/memory` |
| Artifact provenance with checksums and licences | I · T | `provenance`, `/api/provenance` |
| Content policy: secret/PII detection, blocked terms | I · T | `core/moderation.py` |
| Vault key rotation | I · T | `Vault.rotate`, `omniforge vault` |
| HTTP host allowlist | I · T | `OMNIFORGE_HTTP_ALLOWLIST` |
| Python SDK | I · T | `omniforge/client.py` |
| Accessibility pass (skip link, focus, reduced motion, aria) | I · T (structural) | dashboard |
| Lite / low-bandwidth mode | I · T | `?lite=1` |
| Project-level ACLs | I · T | `project_members`, `api/access.py` |
| Per-project token quota | I · T | `projects.token_quota`, 402 |
| Failure alerts by email | I · T · C (console backend) | `_alert` in the orchestrator |
| Workflow conditional branching | I · T | declarative `when` blocks |
| Load-test harness | I · T | `omniforge loadtest` |
| Semantic retrieval (embeddings) | I · T (needs key) | `core/embeddings.py`, hybrid search |
| Plugin install from HTTPS URL | I · T | `/api/plugins/install-url` |
| Signature-only plugin mode | I · T | `OMNIFORGE_PLUGIN_REQUIRE_SIGNATURE` |
| PostgreSQL mirror (verified) | I · T · C | `core/pgmirror.py`, tested against PostgreSQL 16 |
| Alert rules (metric/threshold/cooldown) | I · T · C | `core/alerts.py`, `/api/alert-rules` |
| Plugin registry index | I · T | `core/plugin_registry.py`, checksum verified |
| LLM content classifier | I · T (needs key) | `LlmModerator`, off by default |
| Workspace editor + sandbox runner | I · T | dashboard Workspace view |
| Workflow builder (form-based) | I · T | dashboard Builder view |
| Privacy and terms templates | I | `docs/PRIVACY.md`, `docs/TERMS.md` |

---

## A. Identity and workspace management

| Item | Status | Notes |
|---|---|---|
| Signup / login / logout | I · T · C | PBKDF2-SHA256, hashed session tokens |
| Profiles and account settings | I · T | `/api/auth/me`, password change |
| RBAC | I · T | viewer < member < admin < owner, middleware-enforced |
| Workspaces and invitations | I · T | membership table |
| Project-level permissions | I · T | per-project members with roles, enforced on read/run/manage |
| Session management | I · T | expiry, purge, reset invalidates sessions |
| Account recovery | I · T · C | email reset link (console/SMTP backend) |
| MFA | I · T | TOTP + 8 single-use backup codes |

## B. Security and privacy

| Item | Status | Notes |
|---|---|---|
| Encrypted secrets vault | I · T · C | Fernet, key rotation, write-only API |
| No credentials in logs or frontend | I · T | asserted by tests |
| Sandboxed code execution | I · T · C | off by default; process isolation + rlimits |
| Prompt-injection protection | I · T | scan → neutralise → untrusted wrapper |
| Tool permission scopes | I · T | per-tool, per-role, denials logged |
| Secure file uploads/downloads | I · T | 10 MB cap, type allowlist, SHA-256, path-checked |
| Audit logs and security alerts | I · T · C (needs URL) | audit table + webhook notifier |
| Data deletion and retention | I · T | account deletion, session purge, retention endpoint |
| Content policy (sensitive data, blocked terms) | I · T | `core/moderation.py` |

## C. Persistent project state

| Item | Status | Notes |
|---|---|---|
| Automatic saving | I · T | every transition persisted |
| Checkpoints and snapshots | I · T | per wave, last 20 per run |
| Task and agent state persistence | I · T | tasks, scores, attempts, memory |
| Crash recovery and resume | I · T | `/api/runs/{id}/resume` |
| Version history and rollback | P | run history, replay, checkpoints; project-level versions PENDING |
| Backup and restore | I · T · C | verified archives, integrity check, `--force` guard |
| Database migrations | I · T | additive at startup |

## D. Distributed execution

| Item | Status | Notes |
|---|---|---|
| Background task queues | P | in-process scheduler + run registry; no broker |
| Worker management | N | single process by design |
| Parallel / sequential execution | I · T | dependency waves + semaphore |
| Retry policies and timeouts | I · T | backoff + jitter, per-task and per-run caps |
| Duplicate-task prevention | I · T | idempotency keys, delegated-title dedupe |
| Task cancellation | I · T | per run, plus emergency stop |
| Priority scheduling | I · T | per-task priority |
| Resource limits | I · T | parallel cap, task cap, token budget, sandbox rlimits |
| Scheduled execution | I · T | interval + daily schedules |

## E. Agent governance

| Item | Status | Notes |
|---|---|---|
| Agent-level permissions | I · T | least privilege per role |
| Execution limits | I · T | retries, rework, budget, task cap |
| Human approval | I · T | task gates + destructive-tool gate |
| Emergency stop / global pause | I · T | `/api/control/*` |
| Activity history | I · T | events, tool calls, memory, provenance |
| Policy enforcement | I · T | middleware + orchestrator + content policy |
| Safe failure and recovery | I · T | fail-fast, skipped dependents, resume |

## F. Data and knowledge governance

| Item | Status | Notes |
|---|---|---|
| Source tracking and citations | P | tool calls + provenance; per-claim citations PENDING |
| Knowledge ownership | P | documents carry `run_id`; per-user ownership PENDING |
| Import / export | I · T | portable project zip |
| Retention policies | I · T | documented + purge endpoint |
| User-controlled deletion | I · T | account, memory, files, documents |
| Access permissions | I · T | workspace roles |
| Provenance and version tracking | I · T | checksums, licence field, per-artifact rows |

## G. Testing and evaluation

| Item | Status | Notes |
|---|---|---|
| Unit / integration / e2e | I · T | 238 tests |
| Agent benchmark suite | I · T | `omniforge eval` (5 cases) |
| Regression testing | I · T | CI on Python 3.10–3.12 |
| Output quality evaluation | I · T | review critic + scores |
| Hallucination / factuality checks | P | structural heuristics only |
| Security testing | I · T | permissions, approvals, injection, vault, rate limits, policy |
| Performance testing | P | latency histograms; no load generator (PENDING) |
| Test reports and release gates | I · T | non-zero exit gates CI |

## H. Monitoring and observability

| Item | Status | Notes |
|---|---|---|
| Live dashboard | I · T | Monitor view + `/api/monitor` |
| Agent logs and traces | I · T | events, tool calls, per-task scores |
| Model/API error tracking | I · T | gateway failures, integration health |
| Token and latency tracking | I · T | per run, per model, per provider |
| Worker health | P | in-process jobs only |
| Alerts / notifications | I · T · C (needs URL) | webhook with retries and audit |
| Execution history and debugging | I · T | store, replay, resume, artifacts, provenance |

## I. Billing and resources

| Item | Status | Notes |
|---|---|---|
| Model usage tracking | I · T | per run, per model |
| Provider-wise cost | I · T | price table + prefixes, override via env |
| User / project budgets | I · T | per-run budget, per-user monthly quota, per-project quota |
| Usage quotas and alerts | I · T · C | quota enforced (402); failure alerts by email |
| Rate limits | I · T | token bucket per identity |
| Cost estimation | I · T | `/api/billing/estimate` |
| Free and paid providers | I | OpenAI-compatible incl. local Ollama, Anthropic, Gemini |
| No paid service without consent | I · T | destructive/paid gates + audit |

## J. Plugins and extensions

| Item | Status | Notes |
|---|---|---|
| Install / remove / enable / disable | I · T | `/api/plugins`, `omniforge plugins` |
| Versioning and compatibility | I · T | `requires.omniforge` check |
| Permission review before activation | I · T | operator allowlist |
| Signing and trust levels | I · T | HMAC signature → verified / unsigned |
| Updates and rollback | P | reinstall a new version; no in-place upgrade |
| Remote marketplace / registry | N | local packages only |
| Documentation | I · T | `docs/PLUGINS.md` |

## K. Import, export, developer platform

| Item | Status | Notes |
|---|---|---|
| Portable project export / import | I · T | zip with project.json, artifacts, uploads |
| Export workflows and agent definitions | I · T | workflow plans via API; plugin manifests |
| Public API + docs | I · T | OpenAPI at `/docs`, README tables |
| API keys | I · T | shown once, hashed, revocable |
| Webhooks (inbound and outbound) | I · T | triggers + notifications |
| SDK | I · T | `omniforge/client.py` (async) |
| Plugin / workflow / task APIs | I · T | all three |
| Rate limits | I · T | per identity |
| Usage dashboard | I · T | Usage view |

## L. Release, deployment, platforms

| Item | Status | Notes |
|---|---|---|
| CI pipeline | I · T | lint + tests + eval, 3 Python versions |
| Dev/test/prod environments | P | env-driven single image |
| Rollback | P | image tag + backup restore |
| Android / iOS build pipelines | N | needs signing keys and runners |
| Game engine adapters (Unity/Unreal/Godot) | N | design-only roles today |
| 3D / image / video generation | N | no provider wired; no claim made |

## M. Accessibility and localisation

| Item | Status | Notes |
|---|---|---|
| Hindi + English UI | I · T | 102 keys in both locales |
| Extensible translations | I · T | add a locale object; `dir` honoured |
| Mobile-first responsive | I · T | structural checks; no physical device test |
| Screen-reader support | P | landmarks, labels, skip link; no audit with AT |
| Keyboard accessibility | P | native controls, visible focus; no formal test |
| Contrast and font sizing | P | checked visually; not measured |
| Low-bandwidth mode | I · T | `?lite=1`, single ~55 KB page |

## N. Legal, content safety, disaster recovery

| Item | Status | Notes |
|---|---|---|
| Copyright / licence tracking | I · T | provenance licence field per artifact |
| Generated-content provenance | I · T | checksums + source run/agent |
| Content moderation | P | policy engine (secrets, PII, blocked terms); no ML classifier |
| Consent management | I · T | destructive/paid gates + audit |
| Privacy / terms documents | N | operator responsibility; template not written |
| Compliance checklist | I · T | `docs/SECURITY.md` |
| Scheduled backups | P | CLI/CI triggerable; no internal cron for backups |
| Backup integrity + restore testing | I · T | manifest hashes + integrity check + tests |
| Service health checks | I · T | `/api/health`, Docker healthcheck, `doctor` |
| Failure notifications | I · T · C (needs URL) | webhook notifier |
| Recovery objectives | P | RPO = last backup; RTO = restore time |

## O. Still open (honest list)

| Item | Why it is open |
|---|---|
| Native PostgreSQL store | mirror is verified; making PostgreSQL the primary store needs a dialect shim in `RunStore` |
| Vector embeddings | hybrid BM25 + cosine shipped; a dedicated vector DB is still open |
| Distributed workers / external queue | deliberate: in-process until load demands otherwise; the load harness shows headroom |
| Hosted plugin registry service | index format + checksum verification shipped; hosting one is an ops task |
| Node-graph workflow canvas | form-based builder shipped |
| Drag-and-drop workflow canvas | the Builder view is form-based; a node canvas is a front-end project |
| Classifier categories per deployment | model classifier shipped; category policy is fixed, not per-tenant |
| Alert routing per team | rules support email/webhook with cooldown; no per-team routing |
| 3D studio, media studio, model manager screens | need asset providers; the Workspace editor exists |
| Physical Android/iOS device testing | needs hardware or a device farm |
| Finalised privacy policy / terms | templates shipped; the operator must fill and review them |

## Next, in order

1. PostgreSQL adapter behind the existing store interface.
2. Alert rules (email/webhook) for failures, budget and quota.
3. Vector retrieval behind `KnowledgeBase.search`.
4. Remote plugin registry with signing keys.
5. Workflow branching + a visual builder.
