from django.urls import path
from . import (
    views, views_experiment, views_api, views_crop_model,
    views_field_lib, views_treatment_lib, views_wizard_draft,
)

# Namespace is owned by `dssat_agent/urls.py` (the top-level aggregator).
# This module is included by that aggregator without its own app_name.

# ---------------------------------------------------------------------------
# API routes — resolve under /dssat/api/... when this module is included at
# the /dssat/ mount. All subagent APIs live under their module prefix (no
# root-level /api/ mount). Adding a new endpoint here makes it available at
# /dssat/api/<path>/ automatically.
# ---------------------------------------------------------------------------
api_urlpatterns = [
    # Soils
    path('api/soils/', views_api.SoilsAPI.as_view(), name='api_soils'),
    path('api/soils/estimate/', views.SoilEstimateAPI.as_view(), name='api_soil_estimate'),
    path('api/soils/<str:soil_id>/', views_api.SoilDetailAPI.as_view(), name='api_soil_detail'),
    # Crops & cultivars
    path('api/crops/', views_api.CropsAPI.as_view(), name='api_crops'),
    path('api/cultivars/<str:crop_code>/', views_api.CultivarsAPI.as_view(), name='api_cultivars'),
    path('api/cultivars/<str:crop_code>/schema/', views_api.CultivarSchemaAPI.as_view(), name='api_cultivar_schema'),
    path('api/cultivars/<str:crop_code>/create/', views_api.CultivarCreateAPI.as_view(), name='api_cultivar_create'),
    path('api/cultivars/<str:crop_code>/<uuid:cultivar_id>/', views_api.CultivarDetailAPI.as_view(), name='api_cultivar_detail'),
    path('api/cultivars/<str:crop_code>/<uuid:cultivar_id>/clone/', views_api.CultivarCloneAPI.as_view(), name='api_cultivar_clone'),
    path('api/cultivars/<str:crop_code>/<uuid:cultivar_id>/validate/', views_api.CultivarValidateAPI.as_view(), name='api_cultivar_validate'),
    # Ecotypes
    path('api/ecotypes/<str:crop_code>/', views_api.EcotypeListAPI.as_view(), name='api_ecotype_list'),
    path('api/ecotypes/<str:crop_code>/create/', views_api.EcotypeCreateAPI.as_view(), name='api_ecotype_create'),
    path('api/ecotypes/<str:crop_code>/<uuid:ecotype_id>/', views_api.EcotypeDetailAPI.as_view(), name='api_ecotype_detail'),
    # Codes
    path('api/codes/<str:category>/', views_api.CodesAPI.as_view(), name='api_codes'),
    # Crop model metadata (harvest stages, ranges, curated dropdowns)
    path('api/crop-model/<str:crop_code>/',
         views_crop_model.CropModelAPI.as_view(), name='api_crop_model'),
    # Experiment management
    path('api/experiment/submit/', views_api.ExperimentSubmitAPI.as_view(), name='api_experiment_submit'),
    path('api/experiment/validate/', views_api.ExperimentValidateAPI.as_view(), name='api_experiment_validate'),
    path('api/experiment/ensemble/', views_api.ExperimentEnsembleAPI.as_view(), name='api_experiment_ensemble'),
    path('api/experiment/weather-dates/', views_api.WeatherDatesAPI.as_view(), name='api_weather_dates'),
    path('api/admin-units/', views_api.AdminUnitsAPI.as_view(), name='api_admin_units'),
    path('api/admin-units/centroid/', views_api.AdminCentroidAPI.as_view(), name='api_admin_centroid'),
    path('api/elevation/', views_api.ElevationAPI.as_view(), name='api_elevation'),
    path('api/mc/preview-grid/', views_api.MCPreviewGridAPI.as_view(), name='api_mc_preview_grid'),
    # Experiment file access
    path('api/experiments/<uuid:experiment_id>/files/<str:file_type>/<str:file_key>/',
         views_experiment.ExperimentFileAPI.as_view(), name='experiment_file'),
    path('api/experiments/<uuid:experiment_id>/files/zip/',
         views_experiment.ExperimentFilesZipAPI.as_view(), name='experiment_files_zip'),
    path('api/experiments/<uuid:experiment_id>/results/<uuid:result_id>/files/<str:file_type>/<str:file_key>/',
         views_experiment.ExperimentFileAPI.as_view(), name='experiment_result_file'),
    path('api/experiments/<uuid:experiment_id>/results/<uuid:result_id>/files/zip/',
         views_experiment.ExperimentFilesZipAPI.as_view(), name='experiment_result_files_zip'),
    # In-situ reference data lookups
    path('api/insitu/sources/', views_api.InSituSourcesAPI.as_view(), name='api_insitu_sources'),
    path('api/insitu/preview/', views_api.InSituPreviewAPI.as_view(), name='api_insitu_preview'),
    path('api/insitu/coverage/', views_api.InSituCoverageAPI.as_view(), name='api_insitu_coverage'),
    # Resolve planting dates per-point against an in-situ raster.
    # Step 4 calls this on Next to materialise Auto-mode planting dates
    # for every (treatment, field) pair before the lock commits.
    path('api/planting-dates/resolve/',
         views_api.ResolvePlantingDatesAPI.as_view(),
         name='api_planting_dates_resolve'),

    # Field library
    path('api/fields/',
         views_field_lib.FieldListAPI.as_view(), name='api_field_list'),
    path('api/fields/<uuid:id>/',
         views_field_lib.FieldDetailAPI.as_view(), name='api_field_detail'),

    # Treatment library
    path('api/treatments/',
         views_treatment_lib.TreatmentListAPI.as_view(), name='api_treatment_list'),
    path('api/treatments/<uuid:id>/',
         views_treatment_lib.TreatmentDetailAPI.as_view(), name='api_treatment_detail'),

    # Wizard drafts
    path('api/wizard-drafts/',
         views_wizard_draft.WizardDraftListAPI.as_view(), name='api_wizard_draft_list'),
    path('api/wizard-drafts/active/',
         views_wizard_draft.WizardDraftActiveAPI.as_view(), name='api_wizard_draft_active'),
    path('api/wizard-drafts/<uuid:id>/',
         views_wizard_draft.WizardDraftDetailAPI.as_view(), name='api_wizard_draft_detail'),
    path('api/wizard-drafts/<uuid:id>/lock/',
         views_wizard_draft.WizardDraftLockAPI.as_view(), name='api_wizard_draft_lock'),
    path('api/wizard-drafts/<uuid:id>/edit/',
         views_wizard_draft.WizardDraftEditAPI.as_view(), name='api_wizard_draft_edit'),
    path('api/wizard-drafts/<uuid:id>/submit/',
         views_wizard_draft.WizardDraftSubmitAPI.as_view(), name='api_wizard_draft_submit'),
]

# ---------------------------------------------------------------------------
# Page routes — only served under /explorer/ prefix.
# ---------------------------------------------------------------------------
page_urlpatterns = [
    # =========================================================================
    # Explorer: Soils (Full CRUD)
    # =========================================================================
    path('soils/', views.SoilListView.as_view(), name='explorer_soils'),
    path('soils/create/', views.SoilCreateView.as_view(), name='soil_create'),
    path('soils/create-from-texture/', views.SoilFromTextureView.as_view(), name='soil_from_texture'),
    path('soils/<str:soil_id>/', views.SoilDetailView.as_view(), name='soil_detail'),
    path('soils/<str:soil_id>/add-layer/', views.SoilAddLayerView.as_view(), name='soil_add_layer'),
    path('soils/<str:soil_id>/layer/<int:layer_idx>/edit/',
         views.SoilEditLayerView.as_view(), name='soil_edit_layer'),
    path('soils/<str:soil_id>/layer/<int:layer_idx>/delete/',
         views.SoilDeleteLayerView.as_view(), name='soil_delete_layer'),
    path('soils/<str:soil_id>/download/sol/',
         views.SoilDownloadSOLView.as_view(), name='soil_download_sol'),

    # =========================================================================
    # Explorer: Crops (cultivars + ecotypes)
    # =========================================================================
    path('crops/', views.CropListView.as_view(), name='explorer_crops'),
    path('crops/<str:crop_code>/', views.CropDetailView.as_view(), name='crop_detail'),
    # Cultivar CRUD
    path('crops/<str:crop_code>/create-cultivar/',
         views.CropCultivarCreateView.as_view(), name='crop_cultivar_create'),
    path('crops/<str:crop_code>/cultivar/<uuid:cultivar_id>/edit/',
         views.CropCultivarEditView.as_view(), name='crop_cultivar_edit'),
    path('crops/<str:crop_code>/cultivar/<str:cultivar_code>/',
         views.CropCultivarDetailView.as_view(), name='crop_cultivar_detail'),
    # Ecotype CRUD
    path('crops/<str:crop_code>/create-ecotype/',
         views.CropEcotypeCreateView.as_view(), name='crop_ecotype_create'),
    path('crops/<str:crop_code>/ecotype/<uuid:ecotype_id>/edit/',
         views.CropEcotypeEditView.as_view(), name='crop_ecotype_edit'),
    path('crops/<str:crop_code>/ecotype/<str:ecotype_code>/',
         views.CropEcotypeDetailView.as_view(), name='crop_ecotype_detail'),
    # Downloads
    path('crops/<str:crop_code>/download/cul/',
         views.CropDownloadCULView.as_view(), name='crop_download_cul'),
    path('crops/<str:crop_code>/download/eco/',
         views.CropDownloadECOView.as_view(), name='crop_download_eco'),

    # =========================================================================
    # Explorer: Experiments
    # =========================================================================
    path('experiments/',
         views_experiment.ExperimentListView.as_view(), name='experiment_list'),
    path('experiments/<uuid:experiment_id>/',
         views_experiment.ExperimentDetailView.as_view(), name='experiment_detail'),
    path('chats/<uuid:chat_id>/experiments/',
         views_experiment.ExperimentListView.as_view(), name='chat_experiment_list'),

    # Batch experiments
    path('batch/<uuid:batch_id>/',
         views_experiment.BatchDetailView.as_view(), name='batch_detail'),

    # Experiment file access
    path('api/experiments/<uuid:experiment_id>/files/<str:file_type>/<str:file_key>/',
         views_experiment.ExperimentFileAPI.as_view(), name='experiment_file'),
    path('api/experiments/<uuid:experiment_id>/files/zip/',
         views_experiment.ExperimentFilesZipAPI.as_view(), name='experiment_files_zip'),
    path('api/experiments/<uuid:experiment_id>/results/<uuid:result_id>/files/<str:file_type>/<str:file_key>/',
         views_experiment.ExperimentFileAPI.as_view(), name='experiment_result_file'),
    path('api/experiments/<uuid:experiment_id>/results/<uuid:result_id>/files/zip/',
         views_experiment.ExperimentFilesZipAPI.as_view(), name='experiment_result_files_zip'),

    # =========================================================================
    # Explorer: DSSAT Codes
    # =========================================================================
    path('codes/', views.CodesListView.as_view(), name='explorer_codes'),
    path('codes/<str:category>/', views.CodesDetailView.as_view(), name='codes_detail'),

    # =========================================================================
    # Explorer: Wizard library (Fields, Treatments)
    # =========================================================================
    path('fields/',
         views_field_lib.FieldListView.as_view(), name='explorer_fields'),
    path('fields/<uuid:field_id>/',
         views_field_lib.FieldDetailView.as_view(), name='field_detail'),
    path('treatments/',
         views_treatment_lib.TreatmentListView.as_view(), name='explorer_treatments'),
    path('treatments/<uuid:treatment_id>/',
         views_treatment_lib.TreatmentDetailView.as_view(), name='treatment_detail'),
]

# Combined — page routes + API routes both live under the /dssat/ mount.
urlpatterns = page_urlpatterns + api_urlpatterns
