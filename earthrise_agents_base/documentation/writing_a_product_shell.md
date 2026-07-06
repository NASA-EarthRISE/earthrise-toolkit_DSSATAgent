# Writing a product shell

A **product shell** app is the assembly point for one specific chat
product. It wires framework primitives + sub-agents + branding into a
deployable Django application. Every deployment has exactly one
product shell.

`dssat_chat_agent/` is the canonical reference implementation. This
guide walks through building your own.

## What a product shell owns

- **Knowledge tenant vocabulary** — the entity/relationship types and
  document sources that shape retrieval prompts for THIS product's
  corpus
- **Branded shell/base.html** — overrides the framework's neutral shell
  with product typography, logo, footer, brand colors
- **`INSTALLED_APPS` order** — the product goes **before**
  `earthrise_agents_base` so template overrides win

> **Note:** A product shell does **not** subclass `ChatAgent`. There is no
> `allowed_skills` allowlist or `chat_agent_class` setting in the current
> codebase — the chat tool registry is built generically from all discovered
> sub-agents, and which agents participate in a turn is scoped per call via the
> `enabled_agents` argument to `evaluate()`. The reference shell
> (`dssat_chat_agent`) has no `agent.py`.

## What a product shell does NOT own

- Framework code (state machine, ReAct loop, envelopes, tool schemas)
- Sub-agent skills (each sub-agent registers its own via
  `SkillTable`)
- Web routes (the framework's URL config handles chat pages and
  generic A2A endpoints)
- Persistence models (framework owns `Chat`, `Message`,
  `MessageFeedback`)

## App layout

```
acme_chat_agent/
├── __init__.py
├── apps.py                          # post_migrate hook loads tenants.yaml
├── data/
│   ├── tenants.yaml                 # knowledge tenant vocabulary
│   └── documents/                   # source PDFs (or mount at deploy time)
├── templates/
│   └── shell/
│       └── base.html                # overrides framework's shell/base.html
├── static/
│   └── acme_chat_agent/
│       └── css/acme-tokens.css      # product design tokens
├── tests/
│   ├── test_registration.py         # tenants.yaml → register_tenant contract
│   └── test_branding.py             # verify shell override wins
└── documentation/
    ├── README.md
    ├── deployment.md
    ├── knowledge_tenant.md
    └── inherits.md
```

## Wiring, step by step

### 1. Declare the AppConfig

```python
# acme_chat_agent/apps.py
from django.apps import AppConfig
from django.db.models.signals import post_migrate

class AcmeChatAgentConfig(AppConfig):
    name = "acme_chat_agent"
    label = "acme_chat_agent"
    display_name = "Acme Chat Agent"

    def ready(self):
        post_migrate.connect(_register_knowledge_tenant, sender=self)


def _register_knowledge_tenant(sender, **kwargs):
    from acme_chat_agent.apps import _load_tenants_config
    from knowledge_agent.registry import register_tenant, register_source
    for cfg in _load_tenants_config():
        register_tenant(**cfg["tenant_kwargs"])
        for source in cfg["sources"]:
            register_source(**source)
```

The `_load_tenants_config()` helper reads `data/tenants.yaml` and
returns registration-ready dicts.

### 2. Write `tenants.yaml`

```yaml
# acme_chat_agent/data/tenants.yaml
tenants:
  - tenant_id: acme
    display_name: Acme Chat Agent
    default_strategy: hybrid
    documents_dir: documents
    domain:
      hint: Acme's product domain
      entity_types: [Widget, Gadget, Component]
      relationship_types: [contains, requires, dependsOn]
      ontology:
        namespace: http://acme.com/ontology#
        prefix: acme
```

### 3. (No ChatAgent subclass needed)

The current framework does **not** support a product `ChatAgent` subclass or an
`allowed_skills` allowlist, and there is no `EARTHRISE_AGENTS_BASE` /
`chat_agent_class` setting. `ChatAgent` is instantiated directly by the framework;
the tool registry is assembled generically from every discovered sub-agent's
`SkillTable`. If you need to limit which sub-agents answer a given turn, pass
`enabled_agents=[...]` to `agent.evaluate(...)` at call time. To keep a skill off
the chat LLM entirely, don't register it on the sub-agent's chat tool surface
(expose it only via direct A2A).

### 4. Brand the shell template

```html
{# acme_chat_agent/templates/shell/base.html #}
{% extends "shell/_base_skeleton.html" %}
{% load static %}

{% block product_css %}
  <link rel="stylesheet" href="{% static 'acme_chat_agent/css/acme-tokens.css' %}">
{% endblock %}

{% block branding_footer %}
  <footer class="acme-footer">Powered by Acme</footer>
{% endblock %}
```

**Do not** extend `shell/base.html` here — that would recurse. Extend
the framework's `_base_skeleton.html` directly. See
[`template_conventions.md`](template_conventions.md).

### 5. Order INSTALLED_APPS

```python
INSTALLED_APPS = [
    # Django built-ins
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    ...,
    "accounts",
    "acme_chat_agent",         # MUST precede earthrise_agents_base
    "earthrise_agents_base",
    # sub-agents
    "data_agent",
    "knowledge_agent",
    # ... whichever capability modules this product uses
]
```

The order matters for template loader precedence — the product's
`shell/base.html` shadows the framework's default when it appears
first.

### 6. Test the wiring

At minimum, cover:

1. **Registration hook fires** — `apps.py::ready()` calls
   `register_tenant` with the expected vocabulary
2. **Template override wins** — resolving `shell/base.html` picks up
   the product's version

See `dssat_chat_agent/tests/test_registration.py` for a reference (it covers the
tenant-registration contract and the template override — not any skill allowlist).

## Common pitfalls

- **Product imports sub-agents directly.** Bad — the product should
  declare sub-agents in `INSTALLED_APPS` and let discovery wire
  everything. If you find yourself doing `from data_agent import ...`
  in your product shell, you're probably violating the boundary.
- **Product ships web routes.** The framework provides `/chat/` and
  `/a2a/` — you don't need `urls.py` in a product shell for typical
  cases.
- **Product hardcodes DSSAT vocabulary.** If you're forking
  `dssat_chat_agent` to build a new product, replace `tenants.yaml`
  entries wholesale — don't inherit DSSAT-specific entity types.

## Deploying multiple products from the same repo

Only one product can be active at a time (only one shell should be in
`INSTALLED_APPS`). To ship a different product from the same
codebase, swap the shell + reset the DB + reconfigure
`CUSTOM_AGENT_NAME`. The framework and sub-agents don't need to
change.

## What next

- [`api_reference.md`](api_reference.md) — the `ChatAgent` construction and
  `evaluate()` entry points
- [`template_conventions.md`](template_conventions.md) — the shell
  override rules
- [`settings_reference.md`](settings_reference.md) — every setting
  the framework reads
