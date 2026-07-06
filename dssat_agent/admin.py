from django.contrib import admin
from .models import (
    StoredSoilProfile, StoredSoilLayer, ExperimentSession, SimulationResult,
    CropModel,
    Field, Treatment, WizardDraft,
    WizardDraftField, WizardDraftTreatment,
    ExperimentSessionField, ExperimentSessionTreatment,
)


class StoredSoilLayerInline(admin.TabularInline):
    model = StoredSoilLayer
    extra = 0
    ordering = ['order']
    fields = ['order', 'slb', 'slll', 'sdul', 'ssat', 'srgf', 'sbdm', 'sloc', 'slcl', 'slsi']


@admin.register(StoredSoilProfile)
class StoredSoilProfileAdmin(admin.ModelAdmin):
    list_display = ['soil_id', 'name', 'source', 'country', 'soil_classification', 'layer_count']
    list_filter = ['source', 'country', 'soil_classification']
    search_fields = ['soil_id', 'name']
    inlines = [StoredSoilLayerInline]

    def layer_count(self, obj):
        return obj.layers.count()
    layer_count.short_description = 'Layers'


class SimulationResultInline(admin.TabularInline):
    model = SimulationResult
    extra = 0
    readonly_fields = ['completed_at', 'summary']
    fields = ['completed_at', 'summary']


@admin.register(ExperimentSession)
class ExperimentSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'status', 'crop_code', 'cultivar_code', 'created_at']
    list_filter = ['status', 'crop_code']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [SimulationResultInline]


@admin.register(SimulationResult)
class SimulationResultAdmin(admin.ModelAdmin):
    list_display = ['id', 'experiment', 'completed_at']
    readonly_fields = ['id', 'completed_at']


@admin.register(CropModel)
class CropModelAdmin(admin.ModelAdmin):
    list_display = [
        'crop_code', 'dssat_model', 'display_name', 'crop_group',
        'num_harvest_stages', 'num_harvs_modes',
    ]
    list_filter = ['crop_group', 'dssat_model']
    search_fields = ['crop_code', 'dssat_model', 'display_name']
    fieldsets = (
        ('Identity', {
            'fields': ('crop_code', 'dssat_model', 'display_name', 'crop_group'),
            'description': "Empty dssat_model means this row holds crop-wide "
                           "fields (dropdown curations, spacing ranges). "
                           "Set dssat_model (e.g. 'MZCER') for model-specific "
                           "harvest-stage and harvs-mode settings.",
        }),
        ('Dropdown Curations (crop-wide)', {
            'fields': (
                'planting_methods', 'fertilizer_materials',
                'fertilizer_applications', 'irrigation_methods',
                'chemical_materials', 'chemical_applications',
                'residue_materials', 'harvest_components',
            ),
            'classes': ('collapse',),
        }),
        ('Validation Ranges (crop-wide)', {
            'fields': (
                'plant_population_range', 'row_spacing_range',
                'planting_depth_range',
            ),
            'description': 'JSON dicts like {"min": 4, "max": 10, '
                           '"typical": 7, "unit": "plants/m^2"}',
        }),
        ('Harvest (model-specific)', {
            'fields': ('harvest_stages', 'supported_harvs_modes'),
        }),
        ('Other defaults (model-specific)', {
            'fields': ('default_cultivar_code', 'supported_management'),
        }),
        ('Notes', {'fields': ('notes',)}),
    )

    def num_harvest_stages(self, obj):
        return len(obj.harvest_stages or [])
    num_harvest_stages.short_description = 'Stages'

    def num_harvs_modes(self, obj):
        return len(obj.supported_harvs_modes or [])
    num_harvs_modes.short_description = 'Modes'


# ---------------------------------------------------------------------------
# Wizard library admin
# ---------------------------------------------------------------------------

@admin.register(Field)
class FieldAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'latitude', 'longitude',
                    'weather_source', 'soil_profile', 'updated_at']
    list_filter = ['weather_source', 'is_admin_centroid']
    search_fields = ['name', 'location_label', 'admin_unit']
    raw_id_fields = ['user', 'soil_profile']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(Treatment)
class TreatmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'crop_code', 'cultivar_code',
                    'dssat_model', 'updated_at']
    list_filter = ['crop_code', 'dssat_model']
    search_fields = ['name', 'description']
    raw_id_fields = ['user']
    readonly_fields = ['id', 'created_at', 'updated_at']


class WizardDraftFieldInline(admin.TabularInline):
    model = WizardDraftField
    extra = 0
    raw_id_fields = ['field']


class WizardDraftTreatmentInline(admin.TabularInline):
    model = WizardDraftTreatment
    extra = 0
    raw_id_fields = ['treatment', 'field']
    fields = ['ordering', 'treatment', 'field']


@admin.register(WizardDraft)
class WizardDraftAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'experiment_type', 'status',
                    'current_step', 'updated_at']
    list_filter = ['status', 'experiment_type']
    search_fields = ['id', 'user__username', 'chat_id']
    raw_id_fields = ['user']
    readonly_fields = ['id', 'created_at', 'updated_at']
    inlines = [WizardDraftFieldInline, WizardDraftTreatmentInline]
