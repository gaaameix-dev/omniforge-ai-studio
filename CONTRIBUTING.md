# Contributing

Thanks for looking. This is a small, opinionated codebase; these rules keep it that way.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q            # 264 tests, no API key needed
ruff check .
omniforge doctor
omniforge eval
```

## Ground rules

1. **No claim without a test.** If a feature has no test exercising it, it is not done.
2. **No fake success.** An integration that is not configured must report
   `not_configured`; never return a stub that looks like a real result.
3. **Do not duplicate systems.** Skills, agents, tools, plugins, workflows and
   schedules already exist - extend them instead of adding a parallel mechanism.
4. **Preserve behaviour.** Existing endpoints and their payloads stay compatible.
5. **Security defaults stay closed.** New tools declare `permissions`; anything
   destructive sets `destructive=True` and needs an approval.
6. **Update the docs you changed** - `README.md`, `FEATURES.md`, `ROADMAP.md`,
   `CHANGELOG.md`, and `docs/` where relevant.

## Where things go

| Change | Location |
|---|---|
| New capability an agent can use | `omniforge/skills/` (add to `library.py`, handler in `handlers.py`) |
| New role | `omniforge/agents/departments.py` |
| New external tool | `omniforge/tools/` + register in `builtin.py` |
| Policy, persistence, scheduling | `omniforge/core/` |
| HTTP surface | `omniforge/api/*_routes.py` |
| UI | `web/index.html` + `web/i18n.js` (add both languages) |

## Pull requests

- One concern per PR, with tests and a changelog line.
- `ruff check .` and `pytest -q` must pass; CI runs both on Python 3.10-3.12.
- Describe what changed and what you verified, not what you intended.

## Reporting a security issue

Do not open a public issue. Contact the maintainer directly and give a minimal
reproduction. Please do not test against deployments you do not own.
