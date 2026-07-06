"""
URL patterns for DSSAT configuration pages (HTML).

Mounted at `/dssat/config/` by the top-level `dssat_agent/urls.py`.
API routes live in a sibling module `dssat_agent/config_api_urls.py`,
mounted at `/dssat/api/config/` for consistency with the rest of the
agent's APIs (all at `/dssat/api/...`).

Namespace is inherited from the top-level aggregator (`dssat_agent`).
"""

from django.urls import path

from .explorer import views_config

urlpatterns = [
    path('', views_config.DSSATPreferencesView.as_view(), name='config_preferences'),
    path('system/', views_config.DSSATSystemConfigView.as_view(), name='config_system'),
]
