from django.contrib import admin
from .models import RasterDataset, DataFetchJob


@admin.register(RasterDataset)
class RasterDatasetAdmin(admin.ModelAdmin):
    list_display = (
        'dataset_name', 'dataset_subtype', 'tds_product_name',
        'tds_region', 'tds_spatial_resolution', 'is_pipeline_enabled',
    )
    list_filter = ('is_pipeline_enabled', 'is_pipeline_active', 'dataset_subtype')
    search_fields = ('dataset_name', 'tds_product_name')


@admin.register(DataFetchJob)
class DataFetchJobAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'raster_dataset', 'start_date', 'end_date',
        'status', 'progress_pct', 'created_at',
    )
    list_filter = ('status', 'raster_dataset')
    readonly_fields = ('id', 'created_at', 'updated_at')
