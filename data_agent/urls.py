"""
Top-level URL aggregator for data_agent.

Mounted by the host project's `urls.py` — discovered via
`earthrise_agents_base.agent.discovery`, which reads the `url_prefix` /
`url_module` attrs on `DataAgentConfig`. Delegates to
`data_agent.explorer.urls`, which owns the `data_agent` namespace.
"""

from django.urls import path, include

urlpatterns = [
    path('', include('data_agent.explorer.urls')),
]
