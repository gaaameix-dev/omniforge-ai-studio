# Setup and troubleshooting

## First run

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
omniforge doctor          # environment, integrations, smoke run
omniforge serve           # http://127.0.0.1:8000
```

If `omniforge` is not on PATH, use `python -m omniforge.cli <command>`.

## Common problems

**`ModuleNotFoundError: omniforge`**
Run from the repository root, or install with `pip install -e .`. Example scripts
bootstrap `sys.path` themselves.

**Everything says "mock"**
That is the default. Set `OMNIFORGE_LLM_API_KEY` and `OMNIFORGE_MOCK_LLM=false`.
Mock mode is deterministic and labels its output; it is not a failure.

**`/api/*` returns 401**
`OMNIFORGE_API_KEY` is set (single-key mode) or `OMNIFORGE_AUTH_ENABLED=true`
(multi-user). Send `Authorization: Bearer <token>`. With auth enabled, create the
first account through `POST /api/auth/register` or `omniforge users --create you@example.com --password ...`.

**403 `role 'member' cannot manage`**
RBAC is working. Ask an owner/admin to mint a key, or change the role in the database.

**429 rate limited**
`OMNIFORGE_RATE_LIMIT_PER_MINUTE` / `_BURST` are too low for your client. The
response carries `Retry-After`.

**A run is stuck in `awaiting_approval`**
Approvals block until answered: `POST /api/runs/{id}/approvals/{task_id}` with
`{"approved": true}`, or the buttons in the dashboard. Cancel the run to release it.

**A run failed with "interrupted by restart"**
The process died mid-run. Resume it: `POST /api/runs/{id}/resume` (needs a
checkpoint, which every wave writes).

**`omniforge integrations` shows not_configured**
That is the honest state: no credential or no server. `--verify` performs the live
check and prints exactly what failed.

**GitHub writes fail with `IntegrationNotConfigured`**
Set `GITHUB_TOKEN` with `repo` scope. Reads work without a token but are rate-limited.

**Secrets vault says "cryptography is not installed"**
`pip install cryptography`. Without it the vault reports unavailable rather than
storing plaintext.

**Docker healthcheck fails**
The container needs `/api/health` reachable; check `OMNIFORGE_DATA_DIR` is writable
by uid 10001 and the port mapping.

**Backups**
`omniforge backup` writes `data/backups/*.zip`; `omniforge backup --verify <file>`
checks hashes and SQLite integrity; `omniforge restore --file <zip> --force` replaces
the database (a `.bak` copy is kept). Stop the service before restoring.

**Low-end Android**
The dashboard is one 45 KB page, mobile-first, with no external assets. If it feels
slow, reduce `OMNIFORGE_MAX_PARALLEL` so fewer task cards update at once.

## Where things live

| Path | What |
|---|---|
| `data/omniforge.db` | runs, tasks, events, tool calls, users, audit, checkpoints |
| `data/artifacts/<run_id>/` | per-run deliverables |
| `data/.vault.key` | vault key file (0600) when `OMNIFORGE_SECRET_KEY` is empty |
| `data/backups/` | verified archives |
