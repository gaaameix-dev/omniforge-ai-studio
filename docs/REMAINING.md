# What is not done yet

Honest backlog for OmniForge AI Studio 1.0.1. Every line was checked against the
code at this commit; nothing here is a guess.

Legend for **Needs**: `key` = a paid credential · `hardware` = a device or farm ·
`ops` = a hosted service or registry · `decision` = a product choice you must make ·
`—` = nothing external, just work.

Size: **S** ≈ under a day · **M** ≈ a few days · **L** ≈ a week or more.

Current baseline: 51/51 skills deterministic · 120 agent roles · 16 tools ·
145 HTTP routes · 14 dashboard views · 26 CLI commands · 496 tests · 83% coverage.

---

## A. Never built — blocked by a provider, hardware or a decision

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| A1 | Video generation | `/api/media` reports `video_generation: false` | An adapter to a video model (Runway/Veo-class), job polling, artifact handling | key | M |
| A2 | Music generation | `music_generation: false` | An adapter to a music model (Suno/ElevenLabs-music class) and licence metadata | key | M |
| A3 | 3D model generation | `model_3d_generation: false` | A text/image-to-mesh provider, or a Blender headless pipeline; plus a glTF validator | key / hardware | L |
| A4 | Real engine import/build | `core/engines.py` writes text skeletons only | Unity/Unreal/Godot CLI invocation, asset import, build + log parsing | hardware | L |
| A5 | Physical Android/iOS testing | Layout is CSS-verified only | A device or a browser/device farm (BrowserStack-class) in CI | hardware / key | M |
| A6 | Drag-to-arrange workflow canvas | Read-only SVG dependency graph | A node editor with drag, snapping and edge drawing | — | M |
| A7 | External broker (Redis/SQS) | Workers claim from the database | Broker integration; only worth it beyond one database | decision | M |
| A8 | Multi-region / HA deployment | Single process + one database | Replication, leader election, health-based failover, load balancer config | ops | L |
| A9 | Per-tenant isolation (schema per tenant) | Shared tables with `workspace_id` | Tenant-scoped schemas or row-level security, plus migrations | decision | L |

## B. Built, but limited

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| B1 | Settings screen | 11 of 74 settings are tunable at runtime | The other 63 are env-only (keys, paths, DB URL, CORS, sandbox limits) — most should stay env-only, but a few (budgets, alert thresholds, CORS) could move | — | M |
| B2 | Accessibility | Skip link, aria labels, focus styles, reduced motion | Screen-reader walkthrough of every view, contrast audit, keyboard-only run of the run/approve flow | — | M |
| B3 | Mobile layout | 6 media queries, mobile-first CSS | Real-device checks (see A5); safe-area insets on iOS | hardware | S |
| B4 | Backup scheduling | Backups are CLI/API only | A `backup` job kind in the scheduler + retention policy | — | S |
| B5 | Alert routing | Per-team email/webhook | Per-severity routing, quiet hours, deduplication across runs | — | M |
| B6 | Knowledge import | Bulk JSON import (200 docs) | Directory/zip/CSV import, source tagging, re-index endpoint | — | S |
| B7 | Retrieval | Hybrid BM25 + cosine over SQLite vectors | A real vector database, ANN index, and re-embedding on model change | decision | M |
| B8 | Debate | N candidates, best reviewed wins | A judge model that scores candidates on criteria, and cost accounting per candidate | key | S |
| B9 | Review loop | Heuristic critic + optional LLM critique | Rubric per skill, human feedback capture, quality trend reporting | — | M |
| B10 | Delegation | Depth ≤ 2, capped, deduplicated | Dynamic specialist creation, budget inheritance per subtree | — | M |
| B11 | Streaming | `agent.token` events for the model path | Streaming for the plan/synthesis calls, and reconnect-after-drop in the UI | — | S |
| B12 | Plugin trust | HMAC signing with the deployment key | Public-key signatures, revocation list, publisher identity | decision | M |
| B13 | Marketplace | Index + package export | A hosted index with review, ratings, and version pinning per project | ops | M |
| B14 | Cost control | Per-run budget, monthly quota, per-provider caps | Hard stop mid-run when a provider cap is crossed (today it stops before the next run) | — | S |
| B15 | Observability | Prometheus text + JSON monitor | Traces (OpenTelemetry spans per task/tool), log shipping config, SLO dashboards | decision | M |
| B16 | CLI | 26 commands, 22 smoke tests | Interactive TUI, `--watch` streaming output, shell completions | — | S |

## C. Code ready — waiting on credentials or infrastructure

| # | Item | Current state | What is missing | Needs | Size |
|---|---|---|---|---|---|
| C1 | Live model | Mock + deterministic planners work; adapters tested against mock transports | A real key for OpenAI/Anthropic/Gemini, then one smoke run per provider | key | S |
| C2 | Image + speech | Adapters and endpoints implemented, tests use mock transports | `OMNIFORGE_MEDIA_API_KEY`; verify one real image and one real clip | key | S |
| C3 | Embeddings | Hybrid search implemented, 409 without a key | `OMNIFORGE_EMBEDDING_API_KEY`, then re-embed the corpus | key | S |
| C4 | GitHub writes | Commit/branch/PR/issue/workflow tools implemented | A token with `repo` scope; verify against a scratch repository | key | S |
| C5 | MCP servers | stdio + HTTP clients, tested against a fake server | Point it at a real MCP server and record the tool list | ops | S |
| C6 | Email | Console backend works; SMTP client implemented | SMTP credentials, then a deliverability check | key | S |
| C7 | Code sandbox | Implemented, off by default | A decision to enable it, and a container/VM if you need a real security boundary | decision | M |
| C8 | Docker image | Dockerfile + compose in the repo | Build in a Docker-enabled environment, push to a registry | ops | S |
| C9 | PyPI package | `pyproject.toml` ready, release workflow written | `PYPI_API_TOKEN` secret, then tag `v1.0.1` | ops | S |

## D. Not requested, commonly asked for next

| # | Item | Why it is not there | Needs | Size |
|---|---|---|---|---|
| D1 | SSO / OIDC login | Local accounts only today | decision | M |
| D2 | SAML / SCIM provisioning | Enterprise-only need | decision | L |
| D3 | Billing integration (Stripe) | Usage is metered internally; no invoicing | key | M |
| D4 | Mobile app for the dashboard | The web UI is responsive; no native shell | — | L |
| D5 | Offline queue for the UI | Runs require a live connection to the API | — | S |
| D6 | Multi-language output documents | UI is EN/HI; artifacts are English | — | M |
| D7 | Data residency controls | One database, one region | decision | L |
| D8 | Fine-tuning pipeline | Training plan generator exists, no pipeline | key | L |

## E. Deliberate non-goals

- **Autonomous code deployment to production.** Destructive tools need an explicit
  approval flag; nothing deploys itself.
- **A general chat assistant.** OmniForge plans and executes tasks; it is not a chat UI.
- **Model hosting.** It calls providers; it does not serve weights.
- **A security boundary in the sandbox.** `core/sandbox.py` is process isolation with
  rlimits, documented as such. Use a container or VM if you need a real boundary.

---

## How to check this list yourself

```bash
omniforge doctor                 # environment, integrations, smoke run
omniforge integrations --verify  # live checks for every service
curl localhost:8000/api/media    # which media modalities exist
curl localhost:8000/api/settings # what is tunable at runtime
curl localhost:8000/api/workers  # queue mode state
omniforge eval                   # 5 goal-level cases
pytest -q                        # 496 tests
```

If a line here says "works", there is a test for it. If it says "needs key", the code
path exists and returns `not_configured` until a credential is present.
