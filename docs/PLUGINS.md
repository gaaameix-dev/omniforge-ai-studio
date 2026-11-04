# Plugins

A plugin is **data**: a manifest that adds prompt-driven skills and agent
templates. Nothing from a plugin is executed, so installing one cannot run code.

## Package layout

```
acme-pack.zip
├── manifest.json
└── README.md          (optional, any extra files are copied alongside)
```

```json
{
  "name": "acme-pack",
  "version": "1.0.0",
  "description": "Two skills and one agent",
  "permissions": ["net.read"],
  "requires": {"omniforge": ">=0.4.0"},
  "skills": [
    {"name": "acme_brief", "department": "research", "description": "Writes a brief",
     "min_chars": 20, "required_any": ["## Brief"]}
  ],
  "agents": [
    {"name": "Acme Analyst", "department": "research", "description": "Plugin analyst",
     "skills": ["acme_brief"], "primary_skills": ["acme_brief"],
     "permissions": ["net.read"]}
  ]
}
```

Field notes
- `skills[].department` must match one of the ten departments.
- `agents[].skills` must reference skills declared in the same manifest.
- `permissions` are checked against the operator allowlist
  (`OMNIFORGE_PLUGIN_PERMISSIONS`, default `net.read,fs.write,github.read`).
  Anything else - `code.exec`, `github.write` - is refused at install time.
- `requires.omniforge` accepts `>=`, `<=`, `==`, `>`, `<`.

## Signing

```python
from omniforge.core.plugins import sign
manifest["signature"] = sign(manifest, "your-deployment-secret")   # OMNIFORGE_SECRET_KEY
```

- valid signature → trust `verified`
- missing or wrong → trust `unsigned`, installed only when
  `OMNIFORGE_PLUGIN_ALLOW_UNSIGNED=true` (default). Set it to `false` in
  production to accept signed packages only.

## Lifecycle

```bash
omniforge plugins --install acme-pack.zip
omniforge plugins                       # list with trust + state
omniforge plugins --enable  plg_ab12cd
omniforge plugins --disable plg_ab12cd
omniforge plugins --remove  plg_ab12cd
```

The same operations are available over HTTP (`/api/plugins`, install is a multipart
POST) and in the dashboard's Automation view. Enabling registers the skills and
agents into the live registries, so they appear immediately in `/api/skills` and
`/api/agents` and can be routed to by the orchestrator. Disabling unregisters them.

## What a plugin cannot do

- run code, spawn processes, or open sockets
- raise its own permissions above the operator allowlist
- change another plugin, an existing agent, or the orchestrator
- delete data
