"""
URL patterns for Knowledge agent configuration API endpoints.

Mounted at `/knowledge/api/config/` by `knowledge_agent/urls.py`.
"""

from django.urls import path

from .explorer import views_config

urlpatterns = [
    path('preferences/',
         views_config.KnowledgePreferencesAPI.as_view(), name='api_config_preferences'),
]
