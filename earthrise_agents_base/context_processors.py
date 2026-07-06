"""
Template context processors for the chat app.

* ``nav_items`` — structured agent navigation data (dropdowns per embedded
  subagent, external links for remote ones).
* ``home_cards`` — per-agent gallery sections for the home page.
* ``management_items`` — splits each AppConfig's ``management_items`` into
  two buckets: ``user_menu_items`` (shown in the username dropdown) and
  ``admin_menu_items`` (shown in the Management dropdown for admins /
  permission holders).
* ``agent_visuals`` — per-agent accent color + icon (``window.AGENT_VISUALS``),
  sourced from each AppConfig's ``agent_color`` / ``agent_icon``.

All of these read exclusively from AppConfig attributes — no app names are
hardcoded here. Adding a new subagent is a matter of declaring the right
attrs on its AppConfig; this file does not need to change.
"""

import json
import logging
import os

from django.apps import apps as django_apps
from django.conf import settings as django_settings
from django.urls import reverse, NoReverseMatch

from earthrise_agents_base.agent.discovery import discover_agents

logger = logging.getLogger(__name__)

# Platform-owned visual identity for the orchestrator's own tools
# (owner_agent == 'chat') plus the neutral fallback for any agent that
# declares no color/icon. Override the whole map with a settings.AGENT_VISUALS
# dict to rebrand the orchestrator accent.
_PLATFORM_AGENT_VISUALS = {
    'chat': {'color': '#b45309', 'icon': '💬'},
}
_FALLBACK_COLOR = '#58585b'
_FALLBACK_ICON = '🔧'

_agent_visuals_json = None


def _build_agent_visuals():
    """Assemble the ``agent_label -> {color, icon}`` map once per process.

    Sourced from each discovered sub-agent's AppConfig (``agent_color`` /
    ``agent_icon``) plus the platform's own orchestrator entry — nothing is
    hardcoded in templates or JS. Keyed by ``agent_label``, which is exactly
    what the chat tool-trace stream emits as ``owner_agent``.

    Warns (once, at build time) when two agents claim the same color — they
    would be visually indistinguishable in the tool trace — or when an agent
    declares no color/icon and so falls back to the neutral default.
    """
    global _agent_visuals_json
    if _agent_visuals_json is not None:
        return _agent_visuals_json

    visuals = dict(getattr(django_settings, 'AGENT_VISUALS', None)
                   or _PLATFORM_AGENT_VISUALS)

    seen_colors = {}
    for agent_label, meta in discover_agents().items():
        app_config = meta['app_config']
        color = getattr(app_config, 'agent_color', None)
        icon = getattr(app_config, 'agent_icon', None)
        if not color:
            logger.warning(
                "Agent %r declares no agent_color; using fallback %s",
                agent_label, _FALLBACK_COLOR,
            )
        if not icon:
            logger.warning(
                "Agent %r declares no agent_icon; using fallback %s",
                agent_label, _FALLBACK_ICON,
            )
        if color and color in seen_colors:
            logger.warning(
                "Agent %r shares agent_color %s with %r; their tool-trace "
                "rows will look identical", agent_label, color,
                seen_colors[color],
            )
        elif color:
            seen_colors[color] = agent_label
        visuals[agent_label] = {
            'color': color or _FALLBACK_COLOR,
            'icon': icon or _FALLBACK_ICON,
        }

    _agent_visuals_json = json.dumps(visuals)
    return _agent_visuals_json


def agent_visuals(request):
    """Inject ``agent_visuals_json`` for the chat UI's ``window.AGENT_VISUALS``
    — per-agent accent color + icon, all sourced from AppConfig (see
    ``_build_agent_visuals``)."""
    return {'agent_visuals_json': _build_agent_visuals()}


def agent_name(request):
    """Inject the deployment's display name as ``agent_name``.

    Driven by the ``CUSTOM_AGENT_NAME`` env var (set in the project's settings)
    and falls back to "ChatAgent". Use ``{{ agent_name }}`` in templates
    wherever the product name appears — page titles, nav brand, emails, etc.
    """
    return {'agent_name': getattr(django_settings, 'CUSTOM_AGENT_NAME', 'ChatAgent')}


def subpath_prefix(request):
    """Inject `subpath_prefix` for JS/templates to build deployment-aware URLs.

    When the app is deployed under a subpath (e.g., ``www.example.com/subpath/``),
    all URLs must be prefixed with ``/subpath``. This processor reads the
    ``SUBPATH`` env var and returns the leading-slash prefix, or empty string
    when running at root.

    Templates can use ``{{ subpath_prefix }}`` directly, or inject it as
    ``window.SUBPATH`` for JS use. Any JS that constructs URLs should prepend
    ``window.SUBPATH`` to module-absolute paths (e.g. ``/product/api/...``).
    """
    sp = os.environ.get('SUBPATH', '').strip('/')
    return {'subpath_prefix': ('/' + sp) if sp else ''}


def nav_items(request):
    """Inject `agents_nav` (list of dropdowns for subagent pages) into template context."""
    agent_clients = getattr(django_settings, 'AGENT_CLIENTS', {})
    agents_nav = []

    for agent_label, meta in discover_agents().items():
        app_config = meta['app_config']
        config = agent_clients.get(agent_label, {})
        mode = config.get('mode', 'embedded')
        display = getattr(app_config, 'display_name', None) or agent_label.title()

        resolved_items = []
        if mode == 'embedded':
            for item in meta.get('nav_items', []):
                try:
                    url = reverse(item['url_name'])
                except NoReverseMatch:
                    continue
                resolved_items.append({
                    'label': item['label'],
                    'url': url,
                })

        agents_nav.append({
            'label': display,
            'agent_label': agent_label,
            'mode': mode,
            'remote_url': config.get('url', ''),
            'nav_items': resolved_items,
        })

    return {'agents_nav': agents_nav}


def home_cards(request):
    """Aggregate home-page card entries declared by every embedded subagent,
    grouped into one section per agent so the home template can render each
    agent as its own scrollable row.

    Each agent's AppConfig may expose a ``home_cards`` list with entries
    like::

        {'label': 'Data Browser',
         'url_name': 'foo_agent:explorer_bar',
         'icon': '📊',
         'description': "Browse this agent's available data.",
         'color': 'primary'}   # primary | purple | teal | green

    Returns a list of ``{label, agent_label, cards: [...]}`` sections in
    the order agents are discovered. Agents with zero resolvable cards
    are omitted. Entries whose ``url_name`` fails to resolve are silently
    skipped (mirrors the behavior of ``nav_items``).
    """
    agent_clients = getattr(django_settings, 'AGENT_CLIENTS', {})
    sections = []

    for agent_label, meta in discover_agents().items():
        app_config = meta['app_config']
        mode = agent_clients.get(agent_label, {}).get('mode', 'embedded')
        if mode != 'embedded':
            continue
        display = getattr(app_config, 'display_name', None) or agent_label.title()

        cards = []
        for card in getattr(app_config, 'home_cards', []) or []:
            try:
                url = reverse(card['url_name'])
            except NoReverseMatch:
                continue
            cards.append({
                'label':       card['label'],
                'url':         url,
                'icon':        card.get('icon', ''),
                'description': card.get('description', ''),
                'color':       card.get('color', 'primary'),
            })

        if cards:
            sections.append({
                'label':       display,
                'agent_label': agent_label,
                'cards':       cards,
            })

    return {'home_card_sections': sections}


def management_items(request):
    """Split each AppConfig's ``management_items`` into two buckets for
    the nav bar:

    * ``user_menu_items``  — ``visibility='auth'`` items, shown in the
      per-user dropdown on the username. Includes ``agent_label`` and
      ``agent_display`` so the template can group entries under each
      agent's name (e.g. "Foo", "Knowledge", "Account").
    * ``admin_menu_items`` — ``visibility='admin'`` or
      ``visibility='permission'`` items, shown in the Management
      dropdown (admins/developers only).

    Each app's AppConfig declares entries like::

        management_items = [
            {'label': 'My Account',
             'url_name': 'accounts:profile',
             'section': 'account',
             'visibility': 'auth'},
            ...
        ]

    Visibility is evaluated against ``request.user``. Entries whose
    ``url_name`` fails to resolve are silently skipped (mirrors
    ``nav_items``).
    """
    if not request.user.is_authenticated:
        return {'user_menu_items': [], 'admin_menu_items': []}

    # Imported lazily here (kept from when this lived in accounts); harmless.
    # Generic authz helper, now framework-owned.
    from earthrise_agents_base.mixins import is_admin

    user_items = []
    admin_items = []

    for app_config in django_apps.get_app_configs():
        defs = getattr(app_config, 'management_items', []) or []
        if not defs:
            continue
        agent_label = (
            getattr(app_config, 'agent_label', None)
            or app_config.label
        )
        agent_display = (
            getattr(app_config, 'display_name', None)
            or agent_label.title()
        )
        for item_def in defs:
            if not _is_item_visible(item_def, request.user, is_admin):
                continue
            try:
                url = reverse(item_def['url_name'])
            except NoReverseMatch:
                continue
            item = {
                'label': item_def['label'],
                'url': url,
                'section': item_def.get('section', 'other'),
                'agent_label': agent_label,
                'agent_display': agent_display,
            }
            visibility = item_def.get('visibility', 'auth')
            if visibility == 'auth':
                user_items.append(item)
            else:
                admin_items.append(item)

    return {
        'user_menu_items': user_items,
        'admin_menu_items': admin_items,
    }


def _is_item_visible(item_def, user, is_admin_fn):
    visibility = item_def.get('visibility', 'auth')

    if visibility == 'auth':
        return True

    if visibility == 'admin':
        return is_admin_fn(user)

    if visibility == 'permission':
        if user.is_superuser:
            return True
        perm = item_def.get('permission')
        return bool(perm) and user.has_perm(perm)

    return False
