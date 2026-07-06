"""
Top-level URL aggregator for knowledge_agent.

Mounted by the host project's `urls.py` — discovered via
`earthrise_agents_base.agent.discovery`, which reads the `url_prefix` /
`url_module` attrs on `KnowledgeAgentConfig`. Owns the `knowledge_agent`
namespace so explorer + config pages + config APIs share it.

URL layout under the mount prefix (e.g., `/knowledge/`):
    /knowledge/                    → strategies dashboard, documents, etc.
    /knowledge/api/...             → explorer APIs (strategies, ingest, documents, etc.)
    /knowledge/config/             → config page (preferences)
    /knowledge/api/config/...      → config APIs
"""

from django.urls import path, include

app_name = 'knowledge_agent'

urlpatterns = [
    path('', include('knowledge_agent.explorer.urls')),                # explorer pages + APIs
    path('config/', include('knowledge_agent.config_urls')),           # config pages
    path('api/config/', include('knowledge_agent.config_api_urls')),   # config APIs
]
