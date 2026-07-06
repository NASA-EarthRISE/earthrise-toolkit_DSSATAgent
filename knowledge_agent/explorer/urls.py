from django.urls import path
from . import views, views_api

# Namespace is owned by `knowledge_agent/urls.py` (the top-level aggregator).

urlpatterns = [
    # Page views — strategies / documents
    path('', views.ExplorerIndexView.as_view(), name='explorer_index'),
    path('documents/', views.DocumentsView.as_view(), name='explorer_documents'),
    path('documents/<slug:source_slug>/', views.SourceDetailView.as_view(), name='explorer_source_detail'),
    path('documents/<slug:source_slug>/file/', views.SourceFileView.as_view(), name='explorer_source_file'),

    # Page views — corpus inspection (graphrag, raptor, ontology only —
    # the 3 strategies that produce a visualizable structure beyond a
    # flat chunk list).
    path('graphrag/', views.GraphRAGInspectView.as_view(), name='inspect_graphrag'),
    path('raptor/',   views.RaptorInspectView.as_view(),   name='inspect_raptor'),
    path('ontology/', views.OntologyInspectView.as_view(), name='inspect_ontology'),

    # Existing JSON API
    path('api/strategies/', views_api.StrategiesAPI.as_view(), name='api_strategies'),
    path('api/strategies/active/', views_api.ActiveStrategyAPI.as_view(), name='api_active_strategy'),
    path('api/ingest/', views_api.IngestAPI.as_view(), name='api_ingest'),
    path('api/ingest/step/', views_api.IngestStepAPI.as_view(), name='api_ingest_step'),
    path('api/ingest/status/<str:task_id>/', views_api.IngestStatusAPI.as_view(), name='api_ingest_status'),
    path('api/documents/', views_api.DocumentsAPI.as_view(), name='api_documents'),
    path('api/chunks/<slug:source_slug>/', views_api.ChunksAPI.as_view(), name='api_chunks'),
    path('api/task/', views_api.ActiveTaskAPI.as_view(), name='api_active_task'),
    path('api/readiness/', views_api.ReadinessAPI.as_view(), name='api_readiness'),

    # Per-document inspection JSON API
    path('api/source/<slug:source_slug>/contextual/',
         views_api.SourceContextualAPI.as_view(),
         name='api_source_contextual'),
    path('api/source/<slug:source_slug>/parent-child/',
         views_api.SourceParentChildAPI.as_view(),
         name='api_source_parent_child'),
    path('api/source/<slug:source_slug>/graph/',
         views_api.SourceGraphAPI.as_view(),
         name='api_source_graph'),
    path('api/source/<slug:source_slug>/raptor/',
         views_api.SourceRaptorAPI.as_view(),
         name='api_source_raptor'),
    path('api/source/<slug:source_slug>/ontology/',
         views_api.SourceOntologyAPI.as_view(),
         name='api_source_ontology'),
    path('api/source/<slug:source_slug>/query/',
         views_api.SourceQueryAPI.as_view(),
         name='api_source_query'),
    path('api/entity/<str:entity_id>/sources/',
         views_api.EntitySourcesAPI.as_view(),
         name='api_entity_sources'),

    # Corpus-wide inspection JSON API
    path('api/inspect/graphrag/',
         views_api.InspectGraphAPI.as_view(),
         name='api_inspect_graphrag'),
    path('api/inspect/raptor/',
         views_api.InspectRaptorAPI.as_view(),
         name='api_inspect_raptor'),
    path('api/inspect/ontology/',
         views_api.InspectOntologyAPI.as_view(),
         name='api_inspect_ontology'),
]
