"""
Data Agent models.

Contains:
- RasterDataset: Defines a raster data source configuration (drives all ETL behavior).
  Combined sources are now regular RasterDataset entries with ``source_groups``
  in ``granule_info``.
- VectorDataset: Defines a vector data source (admin boundaries, etc.).
- DataFetchJob: Tracks a weather data fetch request through the ETL pipeline.
- Combined sources are represented as regular RasterDataset entries
  carrying ``source_groups`` in ``granule_info``.
"""

import uuid
from django.db import models


class RasterDataset(models.Model):
    """
    Defines a raster data source configuration. The dataset_information JSONField
    drives all ETL behavior (download URLs, variable mappings, subroutines).
    """
    uuid = models.CharField(
        default=uuid.uuid4, editable=True, max_length=40,
        primary_key=True, auto_created=True,
    )

    dataset_name = models.CharField(
        'Human Readable Dataset Short Name',
        max_length=90, blank=False, default='Unknown Dataset Name',
    )

    dataset_subtype = models.CharField(
        'Dataset Subtype',
        max_length=90, blank=False, default='Unknown_Dataset_Subtype',
        help_text='Used by the pipeline to select which sub type script logic is used.',
    )

    is_pipeline_enabled = models.BooleanField(
        default=False,
        help_text='Is this ETL Dataset enabled for pipeline processing?',
    )

    is_pipeline_active = models.BooleanField(
        default=False,
        help_text='Is an ETL job currently running for this dataset?',
    )

    merge_only = models.BooleanField(
        default=False,
        help_text='Skip standard ETL and proceed straight to merging NC4 files?',
    )

    tds_product_name = models.CharField(
        '(TDS) Product Name', max_length=90, blank=False,
        default='UNKNOWN_PRODUCT_NAME',
    )

    tds_region = models.CharField(
        '(TDS) Region', max_length=90, blank=False,
        default='UNKNOWN_REGION',
    )

    tds_spatial_resolution = models.CharField(
        '(TDS) Spatial Resolution', max_length=90, blank=False,
        default='UNKNOWN_SPATIAL_RESOLUTION',
    )

    tds_temporal_resolution = models.CharField(
        '(TDS) Temporal Resolution', max_length=90, blank=False,
        default='UNKNOWN_TEMPORAL_RESOLUTION',
    )

    temp_working_dir = models.TextField(
        '(Path) Local temp working directory', default='',
    )

    final_load_dir = models.TextField(
        '(Path) Local NC4 directory', default='',
    )

    fast_directory_path = models.TextField(
        '(Path) Merge Directory', default='',
    )

    nc4_verification_path = models.TextField(
        '(Path) NC4 Verification', default='', blank=True,
    )

    merged_verification_path = models.TextField(
        '(Path) Merged Verification', default='', blank=True,
    )

    late_after = models.IntegerField(default=0, help_text='Duration in days')

    number = models.IntegerField(default=0, help_text='Datatype number')

    merge_type = models.CharField(
        'Merge Type', default='None', blank=True, max_length=10,
        help_text='Monthly, Yearly, or None.',
    )

    dataset_information = models.JSONField(
        'NC4 Attributes Data', default=dict, blank=True,
        help_text='JSON configuration driving all ETL behavior.',
    )

    DATASET_TYPE_CHOICES = [
        ('time_series', 'Time Series'),
        ('static', 'Static'),
    ]

    DATA_CATEGORY_CHOICES = [
        ('observational', 'Observational'),
        ('forecast', 'Forecast'),
        ('observational_and_forecast', 'Observational & Forecast'),
    ]

    DATA_TYPE_CHOICES = [
        ('raster', 'Raster'),
        ('point', 'Point'),
    ]

    dataset_type = models.CharField(
        max_length=20, choices=DATASET_TYPE_CHOICES,
        null=True, blank=True,
        help_text='Time-series (has temporal dimension) or static (fixed in time).',
    )
    data_category = models.CharField(
        max_length=30, choices=DATA_CATEGORY_CHOICES,
        default='observational',
    )
    data_type = models.CharField(
        max_length=10, choices=DATA_TYPE_CHOICES,
        default='raster',
    )

    def __str__(self):
        return f'{self.dataset_name} - {self.dataset_subtype}'

    class Meta:
        db_table = 'dataagent_raster_dataset'
        verbose_name = 'Raster Dataset'
        verbose_name_plural = 'Raster Datasets'


class VectorDataset(models.Model):
    """
    Defines a vector data source (e.g., admin boundaries from GADM).
    Each record represents one logical dataset at a specific level.
    """

    LEVEL_CHOICES = [
        (0, 'Countries'),
        (1, 'States'),
        (2, 'Counties'),
    ]

    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100)
    level = models.IntegerField(choices=LEVEL_CHOICES, null=True, blank=True)
    source_url = models.TextField(blank=True, default='')
    schema_name = models.CharField(max_length=100, default='dataagent')
    table_name = models.CharField(max_length=100, default='admin')
    is_loaded = models.BooleanField(default=False)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.label} (level={self.level})'

    class Meta:
        db_table = 'dataagent_vector_dataset'
        verbose_name = 'Vector Dataset'
        verbose_name_plural = 'Vector Datasets'


class DataFetchJob(models.Model):
    """Tracks a weather data fetch request through the ETL pipeline."""

    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('downloading', 'Downloading'),
        ('transforming', 'Transforming'),
        ('loading', 'Loading to PostGIS'),
        ('combining', 'Combining Datasets'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    raster_dataset = models.ForeignKey(
        RasterDataset, on_delete=models.CASCADE, related_name='fetch_jobs',
    )
    schema_name = models.CharField(max_length=100, default='dataagent')

    # Bounding box
    bbox_west = models.FloatField()
    bbox_south = models.FloatField()
    bbox_east = models.FloatField()
    bbox_north = models.FloatField()

    start_date = models.DateField()
    end_date = models.DateField()

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)
    progress_pct = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    dates_loaded = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return (
            f'{self.raster_dataset.dataset_name} | '
            f'{self.start_date} to {self.end_date} | {self.status}'
        )

    class Meta:
        db_table = 'dataagent_data_fetch_job'
        ordering = ['-created_at']


class CategoricalRasterLegend(models.Model):
    """
    Lookup table mapping pixel values from any categorical raster to
    human-readable code + label pairs.

    Generic — supports soil types, land cover, crop types, etc. The
    ``raster_dataset_name`` links to ``RasterDataset.dataset_subtype``.
    Populated by the reference raster loader at startup from a CSV legend
    or (future) embedded GDAL Raster Attribute Table.
    """
    raster_dataset_name = models.CharField(max_length=100, db_index=True)
    band_value = models.IntegerField()
    code = models.CharField(max_length=20, blank=True)
    label = models.TextField(blank=True)

    class Meta:
        db_table = 'dataagent_categorical_raster_legend'
        unique_together = [('raster_dataset_name', 'band_value')]
        indexes = [
            models.Index(fields=['raster_dataset_name', 'code']),
        ]

    def __str__(self):
        return f"{self.raster_dataset_name}#{self.band_value} → {self.code}"


class DataConfig(models.Model):
    """System + user configuration for Data agent.

    Same pattern as the other agents' Config models: user=NULL is system default,
    user set is per-user override.
    """
    user = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE,
        null=True, blank=True, related_name='data_configs',
    )
    key = models.CharField(max_length=100)
    value = models.JSONField()
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'dataagent_config'
        unique_together = [('user', 'key')]

    def __str__(self):
        owner = self.user.username if self.user else 'SYSTEM'
        return f"DataConfig({owner}): {self.key}={self.value}"
