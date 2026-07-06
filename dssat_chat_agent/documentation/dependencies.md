# Dependencies

`dssat_chat_agent` is the **DSSAT product shell**: the thin app
that assembles the `earthrise_agents_base` framework and the DSSAT
sub-agents into one deployable, DSSAT-branded chat product.

It is a **pure leaf** — nothing in the monorepo imports it. Each
future product would be its own such leaf.

## Compositional, not import-based

The shell's relationship to the sub-agents is almost entirely
**compositional** rather than Python imports. It does not reach
into `dssat_agent` or `data_agent` internals at all. It integrates
by three non-import mechanisms:

1. **`INSTALLED_APPS` ordering** — it lists `earthrise_agents_base`
   and the sub-agents, and must appear **before**
   `earthrise_agents_base` so its templates win resolution (see
   `README.md`).
2. **Template shadowing** — `templates/shell/base.html` shadows the
   framework's default `shell/base.html` to inject DSSAT branding.
   This is Django template resolution, not a Python import.
3. **Data registration at `post_migrate`** — `apps.py` reads
   `data/tenants.yaml` and registers the DSSAT knowledge tenant by
   calling into `knowledge_agent`.

## Verified imports

The only first-party **Python import** from this shell is into
`knowledge_agent` (1 site), from the `post_migrate` handler that
installs the DSSAT tenant:

| Depends on | How | Purpose |
|---|---|---|
| `knowledge_agent.registry` | Python import (1 site, `apps.py` `post_migrate`) | `register_tenant(...)` / `register_source(...)` for the DSSAT tenant from `data/tenants.yaml`. |
| `earthrise_agents_base` | Compositional | Subclasses `ChatAgent`, extends `shell/base.html`; provides all chat views, models, URLs. |
| `data_agent`, `dssat_agent` | Compositional only | Listed in `INSTALLED_APPS`; reached at runtime through the framework's tool registry / A2A, **never** imported here. |
| `accounts` | Compositional | Listed in `INSTALLED_APPS`; login/authz handled by the plugin + global middleware. |

Verified first-party import counts (production code, excluding
tests/migrations):

| Target | Import sites |
|---|---|
| `knowledge_agent` | 1 |
| everything else | 0 (compositional) |

## Depended on by

**Nothing.** No app imports `dssat_chat_agent`. Sub-agents must
never import a product shell — the product decides which
sub-agents to ship, not the reverse. This is what lets the shell
be swapped (e.g. `acme_chat_agent`) without touching the framework
or any sub-agent.

## Database isolation

No cross-app database foreign keys. The shell defines no models of
its own — it uses the framework's persistence layer. There is
nothing here to isolate at the DB level.

## Dependency diagram

```
   ┌──────────────────────────────────────────────────────┐
   │              dssat_chat_agent (product shell)         │
   │  INSTALLED_APPS order · shell/base.html shadow ·      │
   │  tenants.yaml → post_migrate registration             │
   └───┬───────────────┬───────────────┬──────────────┬────┘
       │ compositional │ compositional │ 1 import     │ compositional
       ▼               ▼               ▼              ▼
 earthrise_agents_  data_agent /   knowledge_    accounts
 base (framework)   dssat_agent    agent         (plugin)
   (subclass +      (via tool      (register_
    template)        registry/A2A)  tenant)
```

## Extraction readiness

**READY (trivially).** As a leaf that nobody imports, the shell
can be replaced or duplicated per product with zero impact on the
framework or sub-agents. Building a new branded product means
cloning this app's three compositional mechanisms — see the
"Deploying without DSSAT branding" section in `README.md`.

## Related docs

- [`README.md`](README.md) — what the shell owns / does not own,
  and the mandatory `INSTALLED_APPS` order.
- [`../../earthrise_agents_base/documentation/writing_a_product_shell.md`](../../earthrise_agents_base/documentation/writing_a_product_shell.md)
  — building a product shell from scratch.
