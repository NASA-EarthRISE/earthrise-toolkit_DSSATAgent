"""
Top-level URL aggregator for dssat_agent.

Mounted by the host project's `urls.py` — discovered via
`earthrise_agents_base.agent.discovery`, which reads the `url_prefix` /
`url_module` attrs on `DssatAgentConfig`. Sub-modules (explorer, wizard,
config pages, config APIs) are collected here so the single `dssat_agent`
namespace spans all of them.

URL layout under the mount prefix (e.g., `/dssat/`):
    /dssat/                     → explorer (soils, crops, experiments, codes)
    /dssat/api/...              → explorer APIs (cultivars, codes, soils, etc.)
    /dssat/experiment/          → wizard
    /dssat/config/              → config pages (preferences + system)
    /dssat/api/config/...       → config APIs
"""

from django.urls import path, include

app_name = 'dssat_agent'

urlpatterns = [
    path('', include('dssat_agent.explorer.urls')),               # explorer pages + APIs
    path('experiment/', include('dssat_agent.wizard.urls')),       # experiment wizard
    path('config/', include('dssat_agent.config_urls')),           # config pages
    path('api/config/', include('dssat_agent.config_api_urls')),   # config APIs
]
