# Branding & UI overrides

`earthrise_agents_base` ships a domain-neutral UI. A **product shell** app
(e.g. `dssat_chat_agent`) rebrands it without touching the framework, using
three mechanisms: template shadowing, override blocks, and the
`CUSTOM_AGENT_NAME` setting. Nothing below requires editing views or URLs.

## How overrides resolve — template shadowing

The product shell app is listed **before** `earthrise_agents_base` in
`INSTALLED_APPS`, so any template it places at the same path **shadows** the
framework's copy in Django's template loader. The framework uses a
private-base pattern (a `_..._page.html` skeleton holds the real markup with
`{% block %}`s; a thin same-named template extends it neutrally) so a product
can override a page by extending the *same private skeleton* and filling only
the blocks it cares about — no markup duplication, no recursion.

Reference implementation: `dssat_chat_agent/templates/`.

## 1. Override blocks

### Shell chrome — `shell/base.html`
`earthrise_agents_base/templates/shell/_base_skeleton.html` exposes:

| Block | Purpose | Neutral default |
|-------|---------|-----------------|
| `product_css` | Inject a product stylesheet / design-token overrides (loaded after the framework CSS, so it wins the cascade) | empty |
| `branding_footer` | Product footer line | empty |
| `head_globals` | Product `<script>` globals in `<head>` (arbitrary product JS config; agent accent colors are **not** set here — see §3) | empty |
| `extra_css` / `extra_js` | Per-page assets | empty |

### Chat page — `earthrise_agents_base/_chat_page.html`
| Block | Neutral default |
|-------|-----------------|
| `agent_type_icon` | 💬 (chat) / 📚 (knowledge) |
| `welcome_logo` | 💬 |
| `welcome_subtitle` | "Your AI-powered assistant" |
| `welcome_features` | (empty) |
| `chat_placeholder` | "Ask me anything..." |

### Home page — `earthrise_agents_base/_home_page.html`
| Block | Neutral default |
|-------|-----------------|
| `home_logo` | 💬 |

A product shell overrides these by creating
`<product>/templates/earthrise_agents_base/chat.html` (and `home.html`) that
`{% extends "earthrise_agents_base/_chat_page.html" %}` and fills the blocks.
See `dssat_chat_agent/templates/earthrise_agents_base/chat.html`.

## 2. Design tokens (`product_css`)

The framework defines all colors, radii, shadows, and fonts as CSS custom
properties in
`earthrise_agents_base/static/earthrise_agents_base/css/tokens.css` `:root`
(loaded before `chat.css`, which only *consumes* the tokens via `var(--…)` and
declares no `:root` block). Every stylesheet consumes these via `var(--…)`, so a product rebrands
the **entire** UI by redeclaring tokens in a `:root` block inside the CSS it
loads via `product_css` (loaded last → wins the cascade). Overridable tokens:

| Token | Meaning |
|-------|---------|
| `--color-primary` | links, borders, accents |
| `--color-action` | secondary/interactive accent |
| `--color-hover` | button hover, alerts |
| `--color-dark` | header / modal-header bar |
| `--color-text` / `--color-text-meta` | body / muted text |
| `--color-bg` / `--color-surface` | page background / cards |
| `--color-border` / `--color-focus` | dividers / focus rings |
| `--radius`, `--shadow-card`, `--shadow-menu`, `--container` | shape/elevation |
| `--font-body`, `--font-heading` | typography |

(Legacy aliases like `--primary-color`, `--card-bg`, `--message-user-bg` map
onto the above, so overriding the base token cascades everywhere.)

Example `product_css` payload:
```css
:root {
    --color-primary: #6d28d9;   /* rebrand to purple */
    --font-heading: 'Georgia', serif;
}
```

## 3. Agent accent colors + icons (`window.AGENT_VISUALS`)

Tool-trace / plan rows accent each agent by color and icon. These are **not**
hand-authored in a template — the skeleton emits
`window.AGENT_VISUALS = {{ agent_visuals_json|safe }}`
(`templates/shell/_base_skeleton.html`), and the values are assembled by
`context_processors.py` from each sub-agent's `AppConfig` attributes
`agent_color` / `agent_icon`, keyed by `agent_label`. The framework pre-seeds only
the platform `chat` entry (`_PLATFORM_AGENT_VISUALS`); `data_agent`,
`knowledge_agent`, `dssat_agent`, etc. supply their own colors/icons on their
AppConfigs.

So to give a new sub-agent an accent color, set `agent_color` / `agent_icon` on
its `AppConfig` (see `data_agent/apps.py`). To override the entire map from
settings, define `AGENT_VISUALS` in `settings.py` (a dict of
`agent_label → {"color": ..., "icon": ...}`), which `context_processors.py` uses
in place of the platform default.

## 4. Product name

`CUSTOM_AGENT_NAME` (setting / `CUSTOM_AGENT_NAME` env var) sets the `<title>`,
nav brand, and `agent_name` context variable. No template edit needed.

## Worked example — rebrand to "Acme"

1. Create an `acme_chat_agent` app; list it **before** `earthrise_agents_base`
   in `INSTALLED_APPS`.
2. `acme_chat_agent/templates/shell/base.html`:
   ```django
   {% extends "shell/_base_skeleton.html" %}
   {% load static %}
   {% block product_css %}<link rel="stylesheet" href="{% static 'acme_chat_agent/css/acme-tokens.css' %}">{% endblock %}
   {% block branding_footer %}<footer>Powered by Acme</footer>{% endblock %}
   ```
3. `acme_chat_agent/templates/earthrise_agents_base/chat.html`:
   ```django
   {% extends "earthrise_agents_base/_chat_page.html" %}
   {% block welcome_logo %}🚀{% endblock %}
   {% block welcome_subtitle %}Acme's research assistant{% endblock %}
   {% block chat_placeholder %}Ask Acme anything...{% endblock %}
   ```
4. Set `CUSTOM_AGENT_NAME=Acme`. Done — the framework and sub-agents are
   untouched.
