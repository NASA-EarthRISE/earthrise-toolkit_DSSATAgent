# dssat_chat_agent

The **DSSAT product shell**. Thin Django app that assembles the
`earthrise_agents_base` framework with the DSSAT sub-agents into the
DSSAT-branded chat product deployed at this project.

## What it owns

- `data/tenants.yaml` — DSSAT knowledge tenant vocabulary and document
  sources
- `apps.py` — post-migrate hook that reads tenants.yaml and calls
  `knowledge_agent.registry.register_tenant()` + `register_source()`
- `templates/shell/base.html` — the DSSAT-branded template that shadows
  the framework's default `shell/base.html` (INSTALLED_APPS ordering
  makes this app's version win template resolution)
- `static/dssat_chat_agent/css/dssat-tokens.css` — DSSAT design tokens
  layered on top of the framework's neutral tokens

## What it does NOT own

- No framework code
- No sub-agent skills (dssat_agent, data_agent, knowledge_agent each
  ship their own)
- No web views (uses the framework's URL routes)
- No models (uses the framework's persistence layer)

## Assembly rules

The DSSAT chat product exists at `dssat_chat_project/` (Django project
directory) and the following `INSTALLED_APPS` order is **mandatory**:

```python
INSTALLED_APPS = [
    # Django built-ins...
    "accounts",
    "dssat_chat_agent",       # MUST precede earthrise_agents_base
    "earthrise_agents_base",  # framework
    "data_agent",             # capability module
    "dssat_agent",            # capability module
    "knowledge_agent",        # capability module
]
```

**Why the order matters.** `dssat_chat_agent` must precede
`earthrise_agents_base` so its `templates/shell/base.html` shadows the
framework's default in template resolution. If the order is reversed,
DSSAT branding disappears.

## Deploying without DSSAT branding

To build a differently branded product:

1. Create a new app (e.g. `acme_chat_agent`) modeled after this one:
   - `data/tenants.yaml` with your domain vocabulary
   - `apps.py` reading tenants.yaml and calling `register_tenant`
   - `templates/shell/base.html` with your branding
2. Replace `dssat_chat_agent` with your app in `INSTALLED_APPS`
3. That's it — sub-agents and framework unchanged

See [`inherits.md`](inherits.md) for the exact template/model
inheritance diagram.

## Related docs

- [`inherits.md`](inherits.md) — inheritance map
- [`deployment.md`](deployment.md) — env vars + startup checklist
- [`knowledge_tenant.md`](knowledge_tenant.md) — how tenants.yaml maps
  to `KnowledgeTenant` / `KnowledgeSource` rows
- Framework: [`../../earthrise_agents_base/documentation/`](../../earthrise_agents_base/documentation/)
