# Template conventions

## The `shell/base.html` override pattern

**Problem.** The framework needs to define a base HTML skeleton
(nav, `<head>`, common blocks). Product apps need to override
branding on it (title, footer, CSS tokens). Sub-agents need to
extend the same skeleton without knowing which product they're
running under.

**Solution.** Three templates, one file per app, using Django's
template loader precedence:

```
earthrise_agents_base/templates/shell/_base_skeleton.html   # the actual template
earthrise_agents_base/templates/shell/base.html             # default entry (extends _base_skeleton)
dssat_chat_agent/templates/shell/base.html                  # branded entry (extends _base_skeleton)
```

**How it resolves.** Django's app template loader walks
`INSTALLED_APPS` in order. Sub-agent templates extend
`"shell/base.html"` — Django resolves that string against every app's
`templates/shell/base.html`, first match wins.

Product shell apps that want to brand the chrome list themselves
**before** `earthrise_agents_base` in `INSTALLED_APPS`. Their
`shell/base.html` wins, and it internally extends the shared
`shell/_base_skeleton.html` (which no product shadows). No infinite
recursion, no code duplication.

Deployments that ship no product shell just get the framework's
`shell/base.html` — unbranded but functional.

## Sub-agent template rule

Every sub-agent explorer page starts with:

```django
{% extends "shell/base.html" %}
```

Never `earthrise_agents_base/base.html`, never a product-specific path.
This is the entire framework/product decoupling: sub-agents know only
one template name.

## Blocks the skeleton provides

- `{% block title %}` — page title (defaults to `{{ agent_name }}`)
- `{% block body_class %}` — CSS class on `<body>`
- `{% block body %}` — main page content
- `{% block extra_css %}` — page-specific `<link>` tags
- `{% block product_css %}` — product-level design tokens
- `{% block extra_js %}` — page-specific `<script>` tags
- `{% block sitewide_feedback %}` — sitewide widget (defaults to feedback)
- `{% block branding_footer %}` — product branding line

Products override `product_css` and `branding_footer` most often.

## Anti-patterns

- **Don't extend a product template from a sub-agent.**
  ```django
  {# BAD — couples the sub-agent to a specific product #}
  {% extends "dssat_chat_agent/base.html" %}
  ```
  Sub-agents must not know product names.

- **Don't override the skeleton in a product.**
  ```django
  {# BAD — infinite recursion if a product's shell/base.html extends
     shell/base.html #}
  {% extends "shell/base.html" %}
  ```
  Products extend `shell/_base_skeleton.html` instead.

- **Don't hard-code branding in the skeleton.**
  Any string that changes per deployment lives in a block, not
  literal text.

## Adding a new template block

If a sub-agent needs a hook a product might want to override, add it
to `_base_skeleton.html` as `{% block <name> %}{% endblock %}` and
document it here. Blocks are cheap; opinions are expensive.
