"""
Root URL configuration for the EarthRISEAgents project.

Subagent URLs are mounted dynamically by iterating over apps discovered via
`earthrise_agents_base.agent.discovery.discover_agents()` and reading URL-related attributes
from each app's AppConfig:

    - `url_prefix` + `url_module`      → user-facing mount (pages + APIs live
                                          under /{url_prefix}/...)
    - `a2a_url_module` + `a2a_prefix`  → machine-to-machine inter-agent endpoints

All subagent APIs live under the module prefix (e.g., `/dssat/api/...`,
`/data/api/...`, `/knowledge/api/...`). There is no root-level `/api/...`
mount — consumers must include the module prefix. This keeps namespaces
clean and avoids collisions between agents.

Adding a new subagent is plug-and-play: drop it into INSTALLED_APPS, declare
the attrs on its AppConfig, and its URLs are wired automatically.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
import os

from earthrise_agents_base.agent.discovery import discover_agents

subpath = os.environ.get("SUBPATH")
agent_clients = getattr(settings, 'AGENT_CLIENTS', {})

prefix = (subpath + '/') if subpath else ''

urlpatterns = [
    path('admin/', admin.site.urls),
    path(prefix + 'accounts/', include('accounts.urls')),
    path(prefix, include('earthrise_agents_base.urls')),
]


def _register_agents():
    """Mount each discovered subagent's URL modules based on AppConfig attrs."""
    for agent_label, meta in discover_agents().items():
        app_config = meta['app_config']
        mode = agent_clients.get(agent_label, {}).get('mode', 'embedded')

        # A2A endpoints — always mounted if declared (machine-to-machine use)
        a2a_module = getattr(app_config, 'a2a_url_module', None)
        a2a_prefix = getattr(app_config, 'a2a_prefix', None)
        if a2a_module and a2a_prefix:
            urlpatterns.append(path(f'{a2a_prefix}/', include(a2a_module)))

        # Explorer / config pages and APIs — embedded mode only.
        # The agent's top-level urls.py aggregates its explorer, wizard,
        # config pages, and config APIs. Agent-level APIs (e.g.,
        # /dssat/api/cultivars/) are exposed via the explorer's own
        # urlpatterns = page_urlpatterns + api_urlpatterns combination.
        if mode != 'embedded':
            continue

        url_prefix = getattr(app_config, 'url_prefix', None)
        url_module = getattr(app_config, 'url_module', None)
        if url_prefix and url_module:
            urlpatterns.append(path(f'{prefix}{url_prefix}/', include(url_module)))


_register_agents()

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
