"""
URL patterns for Knowledge agent configuration pages (HTML).

Mounted at `/knowledge/config/` by the top-level `knowledge_agent/urls.py`.
API routes live in `knowledge_agent/config_api_urls.py`, mounted at
`/knowledge/api/config/`.
"""

from django.urls import path

from .explorer import views_config

urlpatterns = [
    path('', views_config.KnowledgePreferencesView.as_view(), name='config_preferences'),
]
