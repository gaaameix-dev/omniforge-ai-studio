# What is not done yet

Honest backlog for OmniForge AI Studio 1.1.0. Every line was checked against the
code at this commit.

Legend **Needs**: `key` = paid credential · `hardware` = device or farm · `ops` =
hosted service/registry · `decision` = a product choice · `—` = just work.
Size: **S** < 1 day · **M** a few days · **L** a week+.

Baseline: 51/51 skills deterministic · 120 agent roles · 16 tools · 149 HTTP routes ·
14 dashboard views · 28 CLI commands · 513 tests.

---

## Closed in 1.1.0 (was on this list)

| Was | Item | What changed |
|---|---|---|
| A6 | Drag-to-arrange canvas | Builder graph nodes are draggable (pointer events); positions kept while you edit |
| B1 | Settings coverage | 24 runtime-tunable keys (was 11), including `http_allowlist`, `cors_origins`, `run_timeout`, `alert_on_failure`, `debate_judge` |
| B2 | Accessibility | `aria-live` on the live output and event log, `role="status"`, labelled nav, visible focus ring |
| B4 | Backup scheduling | `job: "backup"` schedules create a verified archive on their own tick |
| B5 | Alert routing | Rules carry `severity` (info/warning/critical) and quiet hours; critical bypasses quiet hours; per-team routes |
| B6 | Knowledge import | `/api/knowledge/import-file` accepts `.txt/.md/.csv/.json/.zip` with per-file skip reasons |
| B8 | Debate judge | A model picks the winning candidate when `DEBATE_JUDGE` is on; review score remains the fallback |
| B9 | Review rubric | Skills carry `min_words` and `must_contain`; missing rubric terms are critical |
| B10 | Delegation budget | Delegation is refused once 90% of the run budget is spent |
| B11 | Streaming | The handoff summary streams too (`run.token`); WS client reconnects |
| B12 | Plugin trust | Ed25519 public-key signatures plus a revocation list enforced at install |
| B14 | Cost hard stop | The provider cap is checked inside the run loop, so a run stops mid-flight |
| B16 | CLI | `run --watch` streams tokens and events; `omniforge completions bash|zsh` |

## A. Never built — blocked by a provider, hardware or a decision

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| A1 | Video generation | `/api/media` reports `video_generation: false` | Adapter to a video model, job polling, artifact handling | key | M |
| A2 | Music generation | `music_generation: false` | Adapter to a music model plus licence metadata | key | M |
| A3 | 3D model generation | `model_3d_generation: false` | Text/image-to-mesh provider or a Blender headless pipeline, plus a glTF validator | key / hardware | L |
| A4 | Real engine import/build | `core/engines.py` writes text skeletons | Engine CLI invocation, asset import, build + log parsing | hardware | L |
| A5 | Physical Android/iOS testing | CSS-verified only | Device or browser farm in CI | hardware / key | M |
| A7 | External broker (Redis/SQS) | Workers claim from the database | Broker integration; only needed beyond one database | decision | M |
| A8 | Multi-region / HA | Single process, one database | Replication, leader election, failover, LB config | ops | L |
| A9 | Per-tenant isolation | Shared tables with `workspace_id` | Schema-per-tenant or row-level security, plus migrations | decision | L |

## B. Built, but limited

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| B3 | Mobile layout | 6 media queries, mobile-first CSS, safe areas not handled | Real-device checks (see A5) and iOS safe-area insets | hardware | S |
| B7 | Retrieval | Hybrid BM25 + cosine over SQLite vectors | A vector database, ANN index, re-embedding on model change | decision | M |
| B13 | Marketplace | Index + package export + revocations | A hosted index with review, ratings and per-project pinning | ops | M |
| B15 | Observability | Prometheus text, JSON monitor, per-task events | OpenTelemetry spans, log shipping config, SLO dashboards | decision | M |
| B17 | Settings coverage, remaining | 24 of 74 keys are tunable | The rest are credentials and paths that should stay env-only | — | — |

## C. Code ready — waiting on credentials or infrastructure

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| C1 | Live model | Adapters tested against mock transports | A provider key, then one smoke run per provider | key | S |
| C2 | Image + speech | Endpoints implemented, tested with mocks | `OMNIFORGE_MEDIA_API_KEY`; verify one real image and clip | key | S |
| C3 | Embeddings | Hybrid search implemented, 409 without a key | `OMNIFORGE_EMBEDDING_API_KEY`, then re-embed | key | S |
| C4 | GitHub writes | Commit/branch/PR/issue/workflow tools | A token with `repo` scope on a scratch repository | key | S |
| C5 | MCP servers | stdio + HTTP clients, fake-server tests | Point at a real MCP server and record its tool list | ops | S |
| C6 | Email | Console backend, SMTP client implemented | SMTP credentials + deliverability check | key | S |
| C7 | Code sandbox | Implemented, off by default | A decision to enable it; a container if you need a real boundary | decision | M |
| C8 | Docker image | Dockerfile + compose in repo | Build in a Docker-enabled environment and push | ops | S |
| C9 | PyPI package | `pyproject.toml` + release workflow | `PYPI_API_TOKEN` secret, then tag `v1.1.0` | ops | S |

## D. Not requested, commonly asked for next

| # | Item | Why it is not there | Needs | Size |
|---|---|---|---|---|
| D1 | SSO / OIDC login | Local accounts only | decision | M |
| D2 | SAML / SCIM | Enterprise-only need | decision | L |
| D3 | Stripe billing | Usage is metered internally; no invoicing | key | M |
| D4 | Native mobile app | The web UI is responsive; no shell | — | L |
| D5 | Offline UI queue | Runs need a live API connection | — | S |
| D6 | Non-English artifacts | UI is EN/HI; artifacts are English | — | M |
| D7 | Data residency controls | One database, one region | decision | L |
| D8 | Fine-tuning pipeline | Training plan generator exists, no pipeline | key | L |

## E. Deliberate non-goals

- Autonomous deployment to production (destructive tools need an explicit approval flag).
- A general chat assistant.
- Model hosting.
- A security boundary in the sandbox (process isolation with rlimits, documented as such).

---

## How to check this list yourself

```bash
omniforge doctor                 # environment, integrations, smoke run
omniforge integrations --verify  # live checks per service
curl localhost:8000/api/media    # which media modalities exist
curl localhost:8000/api/settings # what is tunable at runtime
curl localhost:8000/api/plugins/revocations
omniforge eval                   # 5 goal-level cases
pytest -q                        # 513 tests
```
