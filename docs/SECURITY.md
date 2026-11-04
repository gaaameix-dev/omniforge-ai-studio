# Security checklist

Operator-facing checklist. Every line states what the code does today; nothing here
is aspirational.

## Identity

- [x] Passwords hashed with PBKDF2-HMAC-SHA256, 210k iterations, per-user salt
- [x] Session tokens random, stored as SHA-256 hashes, expiring (`SESSION_TTL_HOURS`)
- [x] API keys shown once, stored as hashes, revocable, `last_used_at` tracked
- [x] Role ladder enforced on every `/api` route (viewer < member < admin < owner)
- [x] Account deletion removes the account, its sessions and its keys
- [ ] Password reset by email (PENDING: needs a mailer)
- [ ] MFA (PENDING)

## Secrets

- [x] Vault encrypts values (Fernet) before they reach SQLite
- [x] Master key from `OMNIFORGE_SECRET_KEY`, else a 0600 key file
- [x] API returns secret **names only**, never values
- [x] Secrets never written to logs, events or prompts
- [ ] Key rotation command (PENDING: re-encrypt under a new master key)

## Execution

- [x] Per-tool permissions; per-role grants; denials logged
- [x] Destructive tools require an explicit run flag plus audit entry
- [x] Code execution off by default; isolated interpreter, rlimits, timeout, output cap
- [x] Prompt-injection scanning, neutralisation and untrusted-data wrapping
- [x] Tool-call audit trail (tool, agent, ok/failed, duration, error)
- [ ] Container/gVisor isolation for `code_exec` (PENDING: host-level sandbox)

## Network and API

- [x] Optional bearer auth for API and WebSocket
- [x] Token-bucket rate limiting per identity, 429 + `Retry-After`
- [x] Artifact download path-checked against traversal
- [x] CORS allowlist configurable, empty by default
- [ ] Outbound allowlist for `http_get` (PENDING: host allowlist per deployment)

## Data

- [x] Durable store with additive migrations
- [x] Verified backups (SHA-256 manifest + `PRAGMA integrity_check`)
- [x] Restore refuses to overwrite without `--force`, keeps a `.bak`
- [x] Retention: expired sessions purgeable, audit exportable
- [ ] Per-user quotas (PENDING)

## Reporting a problem

This is a self-hosted framework: report issues to whoever operates your deployment.
Do not paste secrets, tokens or database dumps into an issue.
