# Privacy notice (template)

This is a template for whoever operates an OmniForge deployment. Fill in the
bracketed fields; do not publish it unedited. It describes what the software does,
not what any particular operator does with it.

## What the software stores

| Data | Where | Why |
|---|---|---|
| Account email, name, role | `users` table | authentication and access control |
| Password hash (PBKDF2-SHA256, salted) | `users` table | login; the password itself is never stored |
| Session tokens (hashed) and API keys (hashed) | `sessions`, `api_keys` | keeping you signed in |
| TOTP secret and hashed backup codes | `mfa`, `mfa_backup_codes` | second factor |
| Runs: goal text, plan, task outputs | `runs`, `tasks` | so you can read and replay work |
| Tool calls, audit entries | `tool_calls`, `audit` | traceability and security review |
| Uploaded files and generated artifacts | `data/uploads`, `data/artifacts` | the deliverables you asked for |
| Encrypted secrets | `secrets` (Fernet ciphertext) | credentials the deployment needs |
| Usage: tokens and cost per run | `usage` | cost reporting and quotas |

## What leaves the machine

- Prompts and context go to the model provider you configure (OpenAI, Anthropic,
  Gemini, a local runtime, or any OpenAI-compatible endpoint). That provider's own
  terms apply to that traffic.
- If a webhook URL is configured, a summary of each finished run is POSTed there.
- If email is configured, reset and alert messages go through your SMTP server.
- Nothing else is transmitted by default.

## Your controls

- Delete your account: removes the account, its sessions and its API keys.
- Delete project files, memories, secrets and documents through the API.
- Export everything: portable project export, `/api/audit`, `/api/billing/usage`.
- Retention: purge expired sessions; back up or delete the data directory at will.

## Operator details (fill in)

- Operator: [name]
- Contact: [email]
- Hosting location: [region]
- Retention period: [e.g. 90 days]
- Model provider(s) in use: [list]
