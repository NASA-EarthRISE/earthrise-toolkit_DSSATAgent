# Dependencies

`accounts` is the **auth / identity plugin**: custom user model,
login/registration/password flows, role discovery, and the
project-wide login-required middleware. In the target
architecture it is a **plugin** that depends on the framework —
and after the authz-mixin refactor the dependency arrow points the
correct way (see below).

## What `accounts` imports

`accounts` imports only Django, its own modules, and — since the
authz mixins moved into the framework — the generic authz mixins
*from* `earthrise_agents_base`. It has **zero** imports of any
sub-agent or product shell.

Verified first-party import counts (production code, excluding
tests/migrations):

| Target | Import sites |
|---|---|
| `earthrise_agents_base` | 1 |
| `data_agent` / `dssat_agent` / `knowledge_agent` | 0 |
| `dssat_chat_agent` | 0 |

The single `earthrise_agents_base` import is
`views.py` pulling the authz mixins (`is_admin`,
`AdminRequiredMixin`, `AgentAdminMixin`, …) from
`earthrise_agents_base.mixins` — a correct plugin → framework edge.
These mixins are no longer accounts-owned; the framework owns them.

## What it provides to the rest of the platform

`accounts` is consumed by others through three surfaces, none of
which is a Python import — nothing imports `accounts` anymore:

1. **Registration / approval workflow + auth flows.** The plugin
   owns the custom user model, the login / registration /
   password-reset flows and their templates, the
   registration-approval workflow, and the user-administration UI —
   its real substance, exercised through URLs and templates rather
   than cross-app imports.
2. **`roles.yaml` discovery (data, not import).** Any app can ship
   a `roles.yaml`; the `accounts` role discovery / sync engine
   (`accounts/role_discovery.py` + the `sync_roles` command) reads
   them at startup to register roles/permissions. Sub-agents
   contribute roles this way *without* `accounts` importing them and
   *without* them importing `accounts`.
3. **`LoginRequiredMiddleware` (global config, not import).**
   `accounts.middleware.LoginRequiredMiddleware` is installed
   project-wide in `MIDDLEWARE`, so every app's pages are gated by
   it via settings — no code import required.

## Depended on by

| Consumer | How | Direction |
|---|---|---|
| All apps | `roles.yaml` discovery + global middleware | data / config only (no import) |

**No app imports `accounts`.** The former inbound Python edges
(`earthrise_agents_base` and `dssat_agent` reaching in for authz
mixins) are gone — the generic authz mixins now live in
`earthrise_agents_base.mixins`, and every consumer that needs them
imports the framework instead.

## Coupling resolved

The intended shape now holds: **`accounts` is a plugin that
depends on the framework's authz contract**, and the framework does
*not* import `accounts`. The generic authz helpers (`is_admin`,
`is_agent_admin`, `AdminRequiredMixin`, `AgentAdminMixin`,
`AgentPermissionMixin`) moved *into* `earthrise_agents_base.mixins`;
`accounts` now imports them from there (1 site in `views.py`), and
`dssat_agent` likewise imports them from the framework. Both former
wrong-direction edges are gone and all arrows point plugin/sub-agent
→ framework (see
`earthrise_agents_base/documentation/dependencies.md`).

## Database isolation

No cross-app database foreign keys point out of `accounts`. It
owns the custom user model referenced everywhere via
`settings.AUTH_USER_MODEL`; every other app's user-linked FK
targets `AUTH_USER_MODEL`, not a concrete `accounts` table name.
There are no FKs from `accounts` into any other app. This keeps
the DB layer extraction-clean.

## Dependency diagram

```
     roles.yaml (data)         MIDDLEWARE (global config)
   from every app  ─────┐        ┌──── gates every app's pages
                        ▼        ▼
              ┌───────────────────────────┐
              │          accounts          │
              │  user model · auth flows · │
              │  role discovery · mw       │
              └─────────────┬─────────────┘
                            │ 1 import  ✓ correct direction
                            ▼
              ┌───────────────────────────┐
              │   earthrise_agents_base    │
              │  (framework — owns the     │
              │   generic authz mixins)    │
              └───────────────────────────┘
```

## Extraction readiness

**READY (clean plugin).** `accounts` now depends only on the
framework: its single cross-app import pulls the authz mixins from
`earthrise_agents_base.mixins`, a correct plugin → framework edge.
Nothing imports `accounts` anymore, so there are no inbound
blockers, and the generic authz helpers it once owned live in the
framework. It is a swappable plugin whose only dependency is the
framework's authz contract.

## Related docs

- [`../../earthrise_agents_base/documentation/dependencies.md`](../../earthrise_agents_base/documentation/dependencies.md)
  — the framework that owns the authz mixins `accounts` imports.
- [`../../dssat_agent/documentation/dependencies.md`](../../dssat_agent/documentation/dependencies.md)
  — the sub-agent that imports the same framework authz mixins.
