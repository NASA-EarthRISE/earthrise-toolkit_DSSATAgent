from django.urls import path
from . import views

app_name = 'data_agent'

# API routes — mounted under /data/ via data_agent/urls.py → /data/api/...
# (The old redundant 'api/data/...' prefix was stripped; full URLs become
# /data/api/sources/, /data/api/query/, etc.)
api_urlpatterns = [
    path('api/sources/',      views.DataSourcesAPI.as_view(),           name='api_data_sources'),
    path('api/check/',        views.DataAvailabilityCheckAPI.as_view(), name='api_data_check'),
    path('api/check-remote/', views.DataRemoteCheckAPI.as_view(),       name='api_data_check_remote'),
    path('api/query/',        views.DataQueryAPI.as_view(),             name='api_data_query'),
    path('api/point-query/',  views.DataPointQueryAPI.as_view(),        name='api_data_point_query'),
    path('api/dates/',        views.DataDatesAPI.as_view(),             name='api_data_dates'),
    path('api/zonal-stats/',  views.DataZonalStatsAPI.as_view(),        name='api_data_zonal_stats'),
    path('api/map/tiles/<str:source>/<str:variable>/<str:date>/<int:z>/<int:x>/<int:y>.png',
         views.TileView.as_view(), name='api_map_tile'),
    path('api/map/admin/', views.AdminBoundariesAPI.as_view(), name='api_map_admin'),
    path('api/map/dates/', views.MapSourceDatesAPI.as_view(), name='api_map_dates'),
]

# Page routes — only served under /data/ prefix.
page_urlpatterns = [
    path('', views.ExplorerIndexView.as_view(), name='explorer_index'),
    path('data-availability/', views.DataExplorerView.as_view(), name='data_availability'),
    path('map/', views.MapExplorerView.as_view(), name='explorer_map'),
]

urlpatterns = page_urlpatterns + api_urlpatterns
