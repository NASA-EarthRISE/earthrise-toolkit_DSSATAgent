"""
URL patterns for DSSAT configuration API endpoints.

Mounted at `/dssat/api/config/` by the top-level `dssat_agent/urls.py`.
Companion to `dssat_agent/config_urls.py` (the HTML pages).

Namespace is inherited from the top-level aggregator (`dssat_agent`).
"""

from django.urls import path

from .explorer import views_config

urlpatterns = [
    # Save a section of DSSATConfig rows (preferences | sim-options | fallbacks)
    path('<str:section>/',
         views_config.ConfigSaveAPI.as_view(), name='api_config_save'),

    # Reset a single user override (DELETE semantics via POST for simplicity)
    path('reset/<str:key>/',
         views_config.ConfigResetAPI.as_view(), name='api_config_reset'),

    # Per-crop defaults (GET / POST / DELETE)
    path('crop-default/<str:crop_code>/',
         views_config.CropDefaultAPI.as_view(), name='api_crop_default'),

    # Curated wizard dropdowns per crop (admin only)
    path('curated/<str:crop_code>/',
         views_config.CuratedCropOptionsAPI.as_view(), name='api_curated'),
]
