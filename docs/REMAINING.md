# What is not done yet

Honest backlog for OmniForge AI Studio 1.2.0. Every line was checked against the
code at this commit.

Legend **Needs**: `key` = paid credential · `hardware` = device or farm · `ops` =
hosted service/registry · `decision` = a product choice · `—` = just work.
Size: **S** < 1 day · **M** a few days · **L** a week+.

Baseline: 51/51 skills deterministic · 120 agent roles · 16 tools · 157 HTTP routes ·
14 dashboard views · 29 CLI commands · 537 tests.

---

## Closed in 1.2.0

| Was | Item | What changed |
|---|---|---|
| A1 | Video generation | `core/media_jobs.py` job-API adapter + `POST /api/media/job?kind=video`; tested against a mock provider |
| A2 | Music generation | same adapter, `kind=music` |
| A3 | 3D generation | same adapter with a glTF/GLB validator before the asset is stored |
| A4 | Engine build | `core/engine_build.py` runs a configured engine CLI with timeout and log capture; `GET /api/engines/availability`, `POST /api/engines/build` |
| A7 | External broker | `core/broker.py`: database broker (default) + Redis broker with leases and expired-lease requeue; worker uses it when `OMNIFORGE_BROKER_URL` is set |
| A8 | HA building block | Leader lease (`core/leader.py`, one conditional UPDATE) gates the scheduler; `GET /api/ready` for a load balancer |
| A9 | Per-tenant isolation | Schema-per-tenant for PostgreSQL (`core/tenancy.py`, `OMNIFORGE_TENANT`, `/api/tenants`), tested against a real server |
| B13 | Marketplace hosting | `omniforge plugins --index --base-url` prints a ready-to-host index; export endpoint already existed |
| B15 | Observability | Span tracer with per-task durations and `/api/traces`, optional OpenTelemetry bridge (`OMNIFORGE_OTEL`) |
| D1 | SSO / OIDC | Generic OIDC login with PKCE (`core/oidc.py`, `/api/auth/oidc/login|callback`) |
| D5 | Offline UI queue | Dashboard queues an instruction in `localStorage` when offline and flushes it on reconnect |

Closed in 1.1.0: review rubric, mid-run cost stop, delegation budget, backup
schedules, import formats, alert severity/quiet hours, Ed25519 plugin signing +
revocation, debate judge, streamed summary, 24 tunable settings, CLI watch and
completions, draggable graph, accessibility pass.

## A. Never built — still blocked

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| A5 | Physical Android/iOS testing | CSS + structural checks only | A device or browser farm in CI; the dashboard is responsive but untested on hardware | hardware / key | M |
| A6 | Engine *runs* without the engine | Build command wired, availability reported | An installed Unity/Unreal/Godot to actually compile a scaffold | hardware | L |
| A10 | Multi-region replication | Leader lease + readiness done | Database replication, cross-region failover, traffic policy | ops | L |

## B. Built, but limited

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| B3 | Mobile layout | 6 media queries, mobile-first CSS | Safe-area insets and real-device verification (see A5) | hardware | S |
| B7 | Retrieval | Hybrid BM25 + cosine over SQLite vectors | A vector database with an ANN index; re-embedding on model change | decision | M |
| B18 | Tracing depth | Task-level spans | Tool-level and LLM-call spans, sampling, an OTel collector config | — | S |
| B19 | Settings coverage | 24 of 78 keys are tunable | The rest are credentials and paths that should stay env-only | — | — |

## C. Code ready — waiting on credentials or infrastructure

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| C1 | Live model | Adapters tested against mock transports | A provider key, then one smoke run per provider (`omniforge verify`) | key | S |
| C2 | Image + speech | Endpoints implemented, mock-tested | `OMNIFORGE_MEDIA_API_KEY`; one real image and clip | key | S |
| C3 | Video / music / 3D | Job adapter + validator, mock-tested | `OMNIFORGE_MEDIA_JOB_URL` pointing at a shim over your provider | key | S |
| C4 | Embeddings | Hybrid search implemented | `OMNIFORGE_EMBEDDING_API_KEY`, then re-embed | key | S |
| C5 | GitHub writes | Tools implemented | A token with `repo` scope on a scratch repo | key | S |
| C6 | MCP servers | stdio + HTTP clients, fake-server tests | Point at a real MCP server | ops | S |
| C7 | Email | Console backend, SMTP client | SMTP credentials | key | S |
| C8 | Code sandbox | Implemented, off by default | Enable it; a container if you need a real boundary | decision | M |
| C9 | Redis broker | Adapter tested with fakeredis | A Redis instance (`OMNIFORGE_BROKER_URL`) | ops | S |
| C10 | Docker image | Dockerfile + compose | Build in a Docker-enabled environment, push | ops | S |
| C11 | PyPI package | Release workflow written | `PYPI_API_TOKEN`, then tag `v1.2.0` | ops | S |

## D. Not requested, commonly asked for next

| # | Item | Why it is not there | Needs | Size |
|---|---|---|---|---|
| D2 | SAML / SCIM | OIDC covers most SSO; SCIM is enterprise provisioning | decision | L |
| D3 | Stripe billing | Usage is metered internally; no invoicing | key | M |
| D4 | Native mobile app | The web UI is responsive; no shell | — | L |
| D6 | Non-English artifacts | UI is EN/HI; generators and prompts write English | — | M |
| D7 | Data residency controls | Tenant schemas exist; region pinning is infra | decision | L |
| D8 | Fine-tuning pipeline | Training plan generator exists, no pipeline | key | L |

## E. Deliberate non-goals

- Autonomous deployment to production (destructive tools need an explicit approval).
- A general chat assistant.
- Model hosting.
- A security boundary in the sandbox (process isolation with rlimits, documented).

---

## How to check this list yourself

```bash
omniforge doctor                 # environment, integrations, smoke run
omniforge verify                 # one live check per configured provider
omniforge integrations --verify
curl localhost:8000/api/media    # which media modalities exist
curl localhost:8000/api/ready    # readiness for a load balancer
curl localhost:8000/api/traces   # span summary
omniforge eval && pytest -q      # 5 cases, 537 tests
```
