import uuid
from django.conf import settings
from django.db import models


class StoredSoilProfile(models.Model):
    """Persistent soil profile record, mirrors DSSATTools SoilProfile structure."""

    SOURCE_CHOICES = [
        ('default', 'Default (bundled SOIL.SOL)'),
        ('custom', 'Custom (user-created)'),
        ('imported', 'Imported (.SOL file)'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='soil_profiles',
        help_text="Creator of custom/imported profiles; NULL for bundled soils",
    )
    soil_id = models.CharField(max_length=10, unique=True, help_text="DSSAT 10-char soil code")
    name = models.CharField(max_length=128, blank=True, default="")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default='custom')
    country = models.CharField(max_length=64, blank=True, default="")
    site = models.CharField(max_length=64, blank=True, default="")
    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    soil_classification = models.CharField(max_length=8, blank=True, default="")
    scs_family = models.CharField(max_length=128, blank=True, default="")

    # Required surface parameters
    salb = models.FloatField(help_text="Albedo, fraction")
    slu1 = models.FloatField(help_text="Evaporation limit, mm")
    sldr = models.FloatField(help_text="Drainage rate, fraction/day")
    slro = models.FloatField(help_text="Runoff curve number")
    slnf = models.FloatField(help_text="Mineralization factor, 0-1")
    slpf = models.FloatField(help_text="Fertility factor, 0-1")

    # Optional method codes
    scom = models.CharField(max_length=8, blank=True, default="")
    smhb = models.CharField(max_length=8, blank=True, default="")
    smpx = models.CharField(max_length=8, blank=True, default="")
    smke = models.CharField(max_length=8, blank=True, default="")

    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sol_file = models.TextField(null=True, blank=True,
        help_text="Pre-generated DSSAT .SOL file content")

    class Meta:
        db_table = 'simulation_storedsoilprofile'
        ordering = ['soil_id']

    def __str__(self):
        return f"{self.soil_id} - {self.name}"


class StoredSoilLayer(models.Model):
    """Single layer within a soil profile."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(
        StoredSoilProfile, on_delete=models.CASCADE, related_name='layers'
    )
    order = models.IntegerField(help_text="Layer ordering (0-based)")

    # Required hydraulic parameters
    slb = models.FloatField(help_text="Depth, cm")
    slll = models.FloatField(help_text="Lower limit, cm3/cm3")
    sdul = models.FloatField(help_text="Drained upper limit, cm3/cm3")
    ssat = models.FloatField(help_text="Saturation, cm3/cm3")
    srgf = models.FloatField(help_text="Root growth factor, 0-1")
    sbdm = models.FloatField(help_text="Bulk density, g/cm3")
    sloc = models.FloatField(help_text="Organic carbon, %")

    # Optional hydraulic
    ssks = models.FloatField(null=True, blank=True, help_text="Sat. hydraulic conductivity")
    slmh = models.CharField(max_length=8, blank=True, default="")

    # Texture
    slcl = models.FloatField(null=True, blank=True, help_text="Clay %")
    slsi = models.FloatField(null=True, blank=True, help_text="Silt %")
    slcf = models.FloatField(null=True, blank=True, help_text="Coarse fraction %")

    # Chemistry tier 1
    slni = models.FloatField(null=True, blank=True, help_text="Total N %")
    slhw = models.FloatField(null=True, blank=True, help_text="pH in water")
    slhb = models.FloatField(null=True, blank=True, help_text="pH in buffer")
    scec = models.FloatField(null=True, blank=True, help_text="CEC, cmol/kg")
    sadc = models.FloatField(null=True, blank=True, help_text="Anion adsorption coeff")

    # Chemistry tier 2
    slpx = models.FloatField(null=True, blank=True)
    slpt = models.FloatField(null=True, blank=True)
    slpo = models.FloatField(null=True, blank=True)
    caco3 = models.FloatField(null=True, blank=True)
    slal = models.FloatField(null=True, blank=True)
    slfe = models.FloatField(null=True, blank=True)
    slmn = models.FloatField(null=True, blank=True)
    slbs = models.FloatField(null=True, blank=True)
    slpa = models.FloatField(null=True, blank=True)
    slpb = models.FloatField(null=True, blank=True)
    slke = models.FloatField(null=True, blank=True)
    slmg = models.FloatField(null=True, blank=True)
    slna = models.FloatField(null=True, blank=True)
    slsu = models.FloatField(null=True, blank=True)
    slec = models.FloatField(null=True, blank=True)
    slca = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = 'simulation_storedsoillayer'
        ordering = ['profile', 'order']
        unique_together = [('profile', 'order')]

    def __str__(self):
        return f"{self.profile.soil_id} layer {self.order} ({self.slb} cm)"


class StoredCropFile(models.Model):
    """Pre-generated DSSAT .CUL and .ECO file content for each crop."""

    crop_code = models.CharField(max_length=2)
    dssat_model = models.CharField(max_length=10, blank=True, default="",
        help_text="DSSAT simulation model (e.g. MZCER, SCCAN). Blank = default model.")
    crop_name = models.CharField(max_length=100)
    cul_file = models.TextField(help_text="Pre-generated DSSAT .CUL file content")
    eco_file = models.TextField(null=True, blank=True,
        help_text="Pre-generated DSSAT .ECO file content")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_storedcropfile'
        unique_together = [('crop_code', 'dssat_model')]

    def __str__(self):
        return f"{self.crop_code} - {self.crop_name}"


class BatchExperiment(models.Model):
    """Parent container for multi-location batch experiments.

    Owns multiple child ExperimentSession records, one per location.
    Each child reuses existing single/ensemble/monte_carlo execution.
    """

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('partially_completed', 'Partially Completed'),
        ('failed', 'Failed'),
    ]

    LOCATION_MODE_CHOICES = [
        ('admin_boundary', 'Admin Boundary'),
        ('points', 'Points List'),
        ('bbox_grid', 'Bounding Box Grid'),
    ]

    SUB_EXPERIMENT_TYPE_CHOICES = [
        ('single', 'Single'),
        ('ensemble', 'Ensemble'),
        ('monte_carlo', 'Monte Carlo'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='batch_experiments',
    )
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default='queued')
    sub_experiment_type = models.CharField(
        max_length=16, choices=SUB_EXPERIMENT_TYPE_CHOICES, default='single',
        help_text="Which experiment type to run at each location",
    )
    location_selection_mode = models.CharField(
        max_length=16, choices=LOCATION_MODE_CHOICES,
        help_text="How batch locations were specified",
    )
    location_config = models.JSONField(
        help_text="Spatial selection parameters (admin_parent, child_level, points, bbox, etc.)",
    )
    template_params = models.JSONField(
        help_text="Shared experiment config (crop, planting, management) applied at every location",
    )
    aggregate_summary = models.JSONField(null=True, blank=True,
        help_text="Cross-location aggregated results (yield stats, per-location table)",
    )
    total_locations = models.IntegerField(default=0)
    completed_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)

    chat_id = models.UUIDField(null=True, blank=True, db_index=True,
        help_text="UUID of the orchestrator chat session that created this batch")
    label = models.CharField(max_length=200, blank=True, default="",
        help_text="Human-readable label for the batch experiment")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_batchexperiment'
        ordering = ['-created_at']

    def __str__(self):
        return f"Batch {self.id} [{self.status}] ({self.total_locations} locations)"


class ExperimentSession(models.Model):
    """Tracks experiment construction and execution state."""

    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('building', 'Building'),
        ('ready', 'Ready'),
        ('running', 'Running'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

    EXPERIMENT_TYPE_CHOICES = [
        ('single', 'Single'),
        ('ensemble', 'Ensemble'),
        ('monte_carlo', 'Monte Carlo'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='experiments',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='building')
    experiment_type = models.CharField(
        max_length=16, choices=EXPERIMENT_TYPE_CHOICES, default='single'
    )

    crop_code = models.CharField(max_length=2, blank=True, default="")
    cultivar_code = models.CharField(max_length=6, blank=True, default="")
    dssat_model = models.CharField(max_length=10, blank=True, default="",
        help_text="DSSAT simulation model (e.g. MZCER, SCCAN). Blank = default for crop.")

    soil_profile = models.ForeignKey(
        StoredSoilProfile, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='experiments'
    )
    inline_soil = models.JSONField(null=True, blank=True)

    planting = models.JSONField(null=True, blank=True)
    fertilizer = models.JSONField(null=True, blank=True)
    irrigation = models.JSONField(null=True, blank=True)
    harvest = models.JSONField(null=True, blank=True)
    initial_conditions = models.JSONField(null=True, blank=True)
    residue = models.JSONField(null=True, blank=True)
    chemical = models.JSONField(null=True, blank=True)
    tillage = models.JSONField(null=True, blank=True)
    mow = models.JSONField(null=True, blank=True)
    simulation_controls = models.JSONField(null=True, blank=True)
    weather_data = models.JSONField(null=True, blank=True)
    weather_station = models.JSONField(null=True, blank=True)
    field_params = models.JSONField(null=True, blank=True)
    validation_errors = models.JSONField(null=True, blank=True)

    raw_params = models.JSONField(null=True, blank=True)
    aggregate_summary = models.JSONField(null=True, blank=True,
        help_text="Ensemble aggregate or Monte Carlo quantiles")

    # Optional link to orchestrator chat session (generic UUID, no FK to chat app)
    chat_id = models.UUIDField(null=True, blank=True, db_index=True,
        help_text="UUID of the orchestrator chat session that created this experiment")
    label = models.CharField(max_length=200, blank=True, default="",
        help_text="Human-readable label for the experiment")

    # Batch experiment support
    batch = models.ForeignKey(
        BatchExperiment, on_delete=models.CASCADE,
        null=True, blank=True, related_name='children',
        help_text="Parent batch experiment (null for standalone experiments)",
    )
    location_label = models.CharField(max_length=200, blank=True, default="",
        help_text="Location name for batch children (e.g. 'Autauga County')")

    # Wizard draft + library linkage (set when the experiment originated from
    # a WizardDraft submission; nullable for legacy / direct-API experiments).
    draft = models.ForeignKey(
        'WizardDraft', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='experiments',
        help_text="The wizard draft this experiment was submitted from",
    )
    fields = models.ManyToManyField(
        'Field', through='ExperimentSessionField',
        blank=True, related_name='experiments',
    )
    treatments = models.ManyToManyField(
        'Treatment', through='ExperimentSessionTreatment',
        blank=True, related_name='experiments',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_experimentsession'
        ordering = ['-created_at']

    def __str__(self):
        return f"Experiment {self.id} ({self.crop_code}/{self.cultivar_code}) [{self.status}]"


class SimulationResult(models.Model):
    """Stores output from a completed DSSAT simulation run."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        ExperimentSession, on_delete=models.CASCADE, related_name='results'
    )

    summary = models.JSONField(default=dict, blank=True)
    plant_growth = models.JSONField(null=True, blank=True)
    soil_water = models.JSONField(null=True, blank=True)
    soil_organic = models.JSONField(null=True, blank=True)
    soil_nitrogen = models.JSONField(null=True, blank=True)
    weather_output = models.JSONField(null=True, blank=True)
    overview = models.TextField(blank=True, default="")
    stdout = models.TextField(blank=True, default="")
    dssat_files = models.JSONField(null=True, blank=True,
        help_text="Captured DSSAT input and output file content")

    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'simulation_simulationresult'
        ordering = ['-completed_at']

    def __str__(self):
        return f"Result {self.id} for {self.experiment_id}"


class DSSATEcotype(models.Model):
    """Ecotype parameters for a specific crop model.

    Bundled DSSAT ecotypes are seeded from .ECO files.  Users can create
    custom ecotypes or clone existing ones for calibration.
    """

    SOURCE_CHOICES = [
        ('dssat', 'DSSAT Bundled'),
        ('custom', 'Custom'),
        ('cloned', 'Cloned'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='ecotypes',
        help_text="Creator of custom/cloned ecotypes; NULL for bundled",
    )
    ecotype_code = models.CharField(max_length=6)
    ecotype_name = models.CharField(max_length=64, blank=True, default="")
    crop_code = models.CharField(max_length=2, db_index=True)
    dssat_model = models.CharField(max_length=10, db_index=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default='custom')
    cloned_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='clones',
    )
    params = models.JSONField(
        default=dict,
        help_text="Ecotype parameters as {name: value} dict. Schema varies by crop model.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_dssatecotype'
        unique_together = [('ecotype_code', 'dssat_model', 'source')]
        indexes = [models.Index(fields=['crop_code', 'dssat_model'])]

    def __str__(self):
        return f"{self.ecotype_code} ({self.dssat_model}) [{self.source}]"


class DSSATCultivar(models.Model):
    """Cultivar parameters for a specific crop model.

    Bundled DSSAT cultivars are seeded from .CUL files.  Users can create
    custom cultivars or clone existing ones for calibration.
    """

    SOURCE_CHOICES = [
        ('dssat', 'DSSAT Bundled'),
        ('custom', 'Custom'),
        ('cloned', 'Cloned'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cultivars',
        help_text="Creator of custom/cloned cultivars; NULL for bundled",
    )
    cultivar_code = models.CharField(max_length=6)
    cultivar_name = models.CharField(max_length=64, blank=True, default="")
    crop_code = models.CharField(max_length=2, db_index=True)
    dssat_model = models.CharField(max_length=10, db_index=True)
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default='custom')
    cloned_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='clones',
    )
    ecotype = models.ForeignKey(
        DSSATEcotype, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='cultivars',
    )
    params = models.JSONField(
        default=dict,
        help_text="Cultivar parameters as {name: value} dict. Schema varies by crop model.",
    )
    validation_status = models.CharField(
        max_length=16, blank=True, default="",
        choices=[('', 'Not validated'), ('valid', 'Valid'), ('invalid', 'Invalid')],
    )
    validation_result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_dssatcultivar'
        unique_together = [('cultivar_code', 'dssat_model', 'source')]
        indexes = [
            models.Index(fields=['crop_code', 'dssat_model']),
            models.Index(fields=['source']),
        ]

    def __str__(self):
        return f"{self.cultivar_code} {self.cultivar_name} ({self.dssat_model}) [{self.source}]"


class SoilTypeRasterLegend(models.Model):
    """
    Lookup table mapping pixel values from a categorical soil-type raster
    to DSSAT soil_id strings + descriptions.

    Loaded by dssat_agent/startup/reference_rasters.py from a CSV legend
    or (future) embedded GDAL Raster Attribute Table. The soil_code field
    is intentionally NOT a ForeignKey to StoredSoilProfile so the legend
    can be loaded independently of soil seeding (e.g. partially seeded
    states during dev). Cross-reference verification logs orphans.
    """
    raster_dataset_name = models.CharField(max_length=100, db_index=True)
    band_value = models.IntegerField()
    soil_code = models.CharField(max_length=10)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'simulation_soiltyperasterlegend'
        unique_together = [('raster_dataset_name', 'band_value')]
        indexes = [
            models.Index(fields=['raster_dataset_name', 'soil_code']),
        ]

    def __str__(self):
        return f"{self.raster_dataset_name}#{self.band_value} -> {self.soil_code}"


class ExternalSoilLookupCache(models.Model):
    """
    Coordinate-keyed cache for SoilGrids 2.0 and SSURGO lookup responses.

    Coordinates are rounded to ~5km grid (0.05 deg) so nearby experiments
    share the same cache row. TTL is enforced at read time by comparing
    fetched_at to a per-source max age.
    """
    SOURCE_CHOICES = [
        ('soilgrids', 'SoilGrids 2.0'),
        ('ssurgo', 'SSURGO'),
    ]
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    lat_rounded = models.FloatField()
    lon_rounded = models.FloatField()
    response_json = models.JSONField()
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'simulation_externalsoillookupcache'
        unique_together = [('source', 'lat_rounded', 'lon_rounded')]
        indexes = [
            models.Index(fields=['source', 'fetched_at']),
        ]

    def __str__(self):
        return f"{self.source}@({self.lat_rounded},{self.lon_rounded})"


# =========================================================================
# Configuration models
# =========================================================================

class DSSATConfig(models.Model):
    """System + user configuration for DSSAT agent.

    Rows with user=NULL are system defaults (admin-only).
    Rows with user set are per-user overrides.
    Lookup order: user row -> system row -> hardcoded fallback.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='dssat_configs',
    )
    key = models.CharField(max_length=100)
    value = models.JSONField()
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_dssatconfig'
        unique_together = [('user', 'key')]

    def __str__(self):
        owner = self.user.username if self.user else 'SYSTEM'
        return f"DSSATConfig({owner}): {self.key}={self.value}"


class CropDefault(models.Model):
    """System + user per-crop experiment defaults.

    Rows with user=NULL are admin-configured system defaults.
    Rows with user set are per-user overrides.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        null=True, blank=True, related_name='crop_defaults',
    )
    crop_code = models.CharField(max_length=2)
    cultivar_code = models.CharField(max_length=6, blank=True, default="")
    plant_population = models.FloatField(null=True, blank=True,
        help_text="Default plant population (plants/m^2)")
    row_spacing = models.FloatField(null=True, blank=True,
        help_text="Default row spacing (cm)")
    planting_method = models.CharField(max_length=10, blank=True, default="",
        help_text="Default planting method DSSAT code")
    fertilizer = models.JSONField(null=True, blank=True,
        help_text="Default fertilizer schedule")
    irrigation = models.JSONField(null=True, blank=True,
        help_text="Default irrigation config")
    harvest = models.JSONField(null=True, blank=True,
        help_text="Default harvest config")
    tillage = models.JSONField(null=True, blank=True,
        help_text="Default tillage config")
    chemical = models.JSONField(null=True, blank=True,
        help_text="Default chemical applications")
    residue = models.JSONField(null=True, blank=True,
        help_text="Default residue management")

    class Meta:
        db_table = 'simulation_cropdefault'
        unique_together = [('user', 'crop_code')]

    def __str__(self):
        owner = self.user.username if self.user else 'SYSTEM'
        return f"CropDefault({owner}): {self.crop_code}"


class CropModel(models.Model):
    """Per-(crop, DSSAT model) admin policy + UI-facing metadata.

    Supersedes CuratedCropOptions. Two kinds of rows exist:

    * Rows with ``dssat_model=''`` hold **crop-wide** fields that don't
      depend on which DSSAT model variant is used: dropdown curations,
      plant/row spacing ranges, display name, crop group.
    * Rows with a specific ``dssat_model`` (e.g. ``'MZCER'``, ``'MZIXM'``)
      hold **model-specific** fields: growth-stage vocabulary parsed from
      GRSTAGE.CDE, supported harvs modes, default cultivar.

    ``get_crop_model(crop_code, dssat_model)`` reads the model-specific
    row first and falls back to the crop-wide row so callers can treat
    the model as a single lookup.
    """
    crop_code = models.CharField(max_length=2, db_index=True)
    dssat_model = models.CharField(
        max_length=5, blank=True, default='',
        help_text="Empty = crop-wide row. Set (e.g. 'MZCER') for model-specific.",
    )

    # Display
    display_name = models.CharField(max_length=64, blank=True, default="")
    crop_group = models.CharField(
        max_length=32, blank=True, default="",
        help_text='cereal | legume | tuber | forage | fiber | tree',
    )

    # Crop-wide dropdown curations (stored on the crop-wide CropModel row)
    planting_methods = models.JSONField(default=list, blank=True,
        help_text="Array of DSSAT planting method codes")
    fertilizer_materials = models.JSONField(default=list, blank=True,
        help_text="Array of fertilizer material codes")
    fertilizer_applications = models.JSONField(default=list, blank=True,
        help_text="Array of fertilizer application method codes")
    irrigation_methods = models.JSONField(default=list, blank=True,
        help_text="Array of irrigation method codes")
    chemical_materials = models.JSONField(default=list, blank=True,
        help_text="Array of chemical material codes")
    chemical_applications = models.JSONField(default=list, blank=True,
        help_text="Array of chemical application method codes")
    residue_materials = models.JSONField(default=list, blank=True,
        help_text="Array of residue material codes")
    harvest_components = models.JSONField(default=list, blank=True,
        help_text="Array of hcom codes (harvest component, e.g. H/C/L)")

    # Crop-wide: validation ranges ({min, max, typical, unit})
    plant_population_range = models.JSONField(null=True, blank=True,
        help_text="{min, max, typical, unit} for plants/m^2")
    row_spacing_range = models.JSONField(null=True, blank=True,
        help_text="{min, max, typical, unit} for cm")
    planting_depth_range = models.JSONField(null=True, blank=True,
        help_text="{min, max, typical, unit} for cm")

    # Model-specific: harvest metadata (parsed from GRSTAGE.CDE)
    harvest_stages = models.JSONField(default=list, blank=True,
        help_text='[{code:"GS005", name:"R6", description:"..."}]')
    supported_harvs_modes = models.JSONField(default=list, blank=True,
        help_text="Subset of ['auto','maturity','on_date','growth_stage','dap']")

    # Model-specific: other defaults
    default_cultivar_code = models.CharField(max_length=6, blank=True, default="")
    supported_management = models.JSONField(default=list, blank=True,
        help_text="Subset of ['fertilizer','irrigation','harvest','tillage','chemical','residue']")

    notes = models.TextField(blank=True, default="",
        help_text="Admin notes (citations, overrides, known caveats)")

    class Meta:
        db_table = 'simulation_cropmodel'
        unique_together = [('crop_code', 'dssat_model')]
        indexes = [models.Index(fields=['crop_code'])]

    def __str__(self):
        if self.dssat_model:
            return f"CropModel: {self.crop_code}/{self.dssat_model}"
        return f"CropModel: {self.crop_code} (crop-wide)"


# ---------------------------------------------------------------------------
# Wizard library: Field, Treatment, WizardDraft
# ---------------------------------------------------------------------------
#
# These three models replace the flat JSON-blob wizard payload that used to
# live in Chat.agent_state. They give the wizard:
#
#   - server-side draft persistence (survives refresh, supports SPA <-> chat
#     hand-off via /dssat/api/wizard-drafts/active/)
#   - reusable Field and Treatment records that show up in the
#     /explorer/fields/ and /explorer/treatments/ libraries
#   - explicit step-lock and cascade-clear semantics enforced at the API
#     boundary
#
# At submission time, the run pipeline walks the draft's attached Fields and
# Treatments (or batch (field, treatment) pairs) to construct the per-row
# DSSATBatch input. The DSSATBatch identity-dedup then collapses shared
# blocks into single FILEX rows and expands varying ones into multiple rows.


class Field(models.Model):
    """A re-usable named location with weather source and soil binding.

    Maps to one row in FILEX `*FIELDS` (factor `FL`). Two `Field` records
    that resolve to the same `(weather, soil)` pair collapse into a single
    FILEX row at run time via DSSATBatch's identity dedup.

    ``status`` controls library visibility:
       * ``used``      — wizard-attached only; not shown in the library
                         browser. New rows default here.
       * ``saved``     — owner explicitly clicked "Save for Future Use".
                         Visible to the owner only.
       * ``published`` — owner explicitly clicked "Publish".
                         Visible to all users.
    """

    STATUS_CHOICES = [
        ('used', 'Used'),
        ('saved', 'Saved'),
        ('published', 'Published'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='fields',
        help_text="Owner; required because Fields are user-scoped library items.",
    )
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True, default="")

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='used',
        help_text="Library visibility — see class docstring.",
    )

    latitude = models.FloatField()
    longitude = models.FloatField()
    elevation = models.FloatField(null=True, blank=True,
        help_text="Meters; nullable so DSSAT receives -99 if unknown.")

    soil_profile = models.ForeignKey(
        StoredSoilProfile, on_delete=models.PROTECT,
        null=True, blank=True, related_name='used_in_fields',
    )
    inline_soil = models.JSONField(null=True, blank=True,
        help_text="Inline soil definition (estimated-from-texture path); "
                  "either soil_profile OR inline_soil should be set.")

    weather_source = models.CharField(max_length=64,
        help_text="Raster source id (e.g. nasa_power, era5). The "
                  "actual WSTA/.WTH file is generated per-(lat,lon) at run time.")

    location_label = models.CharField(max_length=200, blank=True, default="",
        help_text="Display label, e.g. 'Autauga County, AL'.")
    is_admin_centroid = models.BooleanField(default=False,
        help_text="True for batch fields auto-generated from state -> county "
                  "centroid expansion.")
    admin_unit = models.CharField(max_length=200, blank=True, default="",
        help_text="The admin2 unit name when is_admin_centroid is True.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_field'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'name']),
        ]

    def __str__(self):
        return f"Field '{self.name}' ({self.latitude:.3f}, {self.longitude:.3f})"


class Treatment(models.Model):
    """A re-usable named treatment protocol bundle.

    One Treatment maps to one row in FILEX `*TREATMENTS`. Carries every
    per-treatment factor: cultivar (CU), planting (MP), harvest (MH),
    initial conditions (IC), simulation controls (SM), and the optional
    management blocks (MF, MI, MR, MC, MT, plus Mow for forage).

    Field (FL) is intentionally NOT stored on Treatment — fields are paired
    with treatments in WizardDraftField + WizardDraftTreatment / WizardDraftPair
    so the same protocol can run at multiple locations.

    ``status`` controls library visibility (same semantics as ``Field``):
    used / saved / published.
    """

    STATUS_CHOICES = [
        ('used', 'Used'),
        ('saved', 'Saved'),
        ('published', 'Published'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='treatments',
    )
    name = models.CharField(max_length=200)
    description = models.CharField(max_length=500, blank=True, default="")

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default='used',
        help_text="Library visibility — see class docstring.",
    )

    crop_code = models.CharField(max_length=2)
    cultivar_code = models.CharField(max_length=6, blank=True, default="")
    dssat_model = models.CharField(max_length=10, blank=True, default="")

    planting = models.JSONField(default=dict,
        help_text="{pdate, ppop, plme, plrs, pldp, ...} -- FILEX MP")
    harvest = models.JSONField(null=True, blank=True,
        help_text="FILEX MH; null when HARVS=M (auto at maturity)")
    initial_conditions = models.JSONField(null=True, blank=True,
        help_text="FILEX IC; null = use SOIL.SOL defaults")
    simulation_controls = models.JSONField(default=dict,
        help_text="{sdate, num_years, num_reps, options:{water,nitrogen,co2}, "
                  "management:{plant,irrig,ferti,resid,harvs}, ...} -- FILEX SM")

    fertilizer = models.JSONField(null=True, blank=True, help_text="FILEX MF event list")
    irrigation = models.JSONField(null=True, blank=True, help_text="FILEX MI: events or auto config")
    residue = models.JSONField(null=True, blank=True, help_text="FILEX MR")
    chemical = models.JSONField(null=True, blank=True, help_text="FILEX MC")
    tillage = models.JSONField(null=True, blank=True, help_text="FILEX MT")
    mow = models.JSONField(null=True, blank=True, help_text="Forage MOW; null otherwise")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_treatment'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'name']),
            models.Index(fields=['user', 'crop_code']),
        ]

    def __str__(self):
        return f"Treatment '{self.name}' ({self.crop_code}/{self.cultivar_code})"


class WizardDraft(models.Model):
    """A single in-progress experiment build, shared by SPA and chat wizard.

    The SPA persists per-step on Next; the chat wizard reads/writes the same
    row via the experiment_wizard_step skill so a build started in one UI
    can be resumed in the other.
    """

    EXPERIMENT_TYPE_CHOICES = [
        ('single', 'Single'),
        ('ensemble', 'Ensemble'),
        ('sensitivity', 'Sensitivity'),
        ('monte_carlo', 'Monte Carlo'),
        ('batch', 'Batch'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('abandoned', 'Abandoned'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='wizard_drafts',
    )
    experiment_type = models.CharField(
        max_length=16, choices=EXPERIMENT_TYPE_CHOICES, default='single',
    )
    current_step = models.CharField(max_length=64, blank=True, default='step1',
        help_text="Step path the user is currently on, e.g. 'step3.field[2]'.")
    locked_steps = models.JSONField(default=list, blank=True,
        help_text="List of step paths that have been completed and locked. "
                  "Re-editing requires unlock + cascade clear of downstream.")
    step_state = models.JSONField(default=dict, blank=True,
        help_text="Full draft payload, namespaced by step path: "
                  "{'step1': {...}, 'step2': {...}, 'step3': {...}, "
                  "'step4': {...}, 'step4.protocols': [...], ...}")

    fields = models.ManyToManyField(
        Field, through='WizardDraftField', blank=True,
        related_name='draft_uses',
    )
    treatments = models.ManyToManyField(
        Treatment, through='WizardDraftTreatment', blank=True,
        related_name='draft_uses',
    )

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='draft')
    chat_id = models.UUIDField(null=True, blank=True, db_index=True,
        help_text="Set when the draft was started or attached from a chat session.")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'simulation_wizarddraft'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['chat_id']),
        ]

    def __str__(self):
        return f"WizardDraft {self.id} [{self.experiment_type}/{self.status}]"


class WizardDraftField(models.Model):
    """Through table: ordered list of Fields attached to a draft (Step 2/3)."""
    draft = models.ForeignKey(WizardDraft, on_delete=models.CASCADE,
        related_name='draft_fields')
    field = models.ForeignKey(Field, on_delete=models.PROTECT,
        related_name='draft_field_uses')
    ordering = models.PositiveIntegerField()

    class Meta:
        db_table = 'simulation_wizarddraft_field'
        ordering = ['ordering']
        unique_together = [('draft', 'field')]


class WizardDraftTreatment(models.Model):
    """Through table: each row is one DSSAT treatment in the draft.

    A row is the ``(draft, treatment, field)`` triple — the FILEX
    ``*TREATMENTS`` row's tuple of factor-level pointers, made concrete:
    the ``treatment`` carries the per-protocol bundle (CU, MP, MH, IC, SC,
    MF, MI, MR, MC, MT) and the ``field`` carries the geo binding (FL).

    ``field`` is nullable so the SPA can stage rows mid-flow before the
    field is decided; the lock hook always populates it before submit.

    Cardinality per experiment type:
      single        — 1 row
      ensemble      — N rows, all sharing the same field
      sensitivity   — N rows, all sharing the same field (generated)
      monte_carlo   — N rows, all sharing the same template treatment
      batch         — K rows, one per checked cell of the matrix; the same
                       `(treatment, field)` pair never appears twice.
    """
    draft = models.ForeignKey(WizardDraft, on_delete=models.CASCADE,
        related_name='draft_treatments')
    treatment = models.ForeignKey(Treatment, on_delete=models.PROTECT,
        related_name='draft_treatment_uses')
    field = models.ForeignKey(Field, on_delete=models.PROTECT,
        related_name='draft_treatment_field_uses',
        null=True, blank=True,
        help_text='The Field this treatment is paired with. Optional only '
                  'for in-progress drafts; the lock hook populates it.')
    ordering = models.PositiveIntegerField()

    class Meta:
        db_table = 'simulation_wizarddraft_treatment'
        ordering = ['ordering']
        # The same protocol may pair with many different fields (matrix
        # mode), but the same (field, treatment) tuple never repeats in
        # one draft — that would be a duplicate FILEX row.
        unique_together = [('draft', 'treatment', 'field')]


class ExperimentSessionField(models.Model):
    """Through table: which Fields a submitted experiment ran at."""
    experiment = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE,
        related_name='session_fields')
    field = models.ForeignKey(Field, on_delete=models.PROTECT,
        related_name='session_field_uses')
    ordering = models.PositiveIntegerField()

    class Meta:
        db_table = 'simulation_experimentsession_field'
        ordering = ['ordering']
        unique_together = [('experiment', 'field')]


class ExperimentSessionTreatment(models.Model):
    """Through table: which Treatments a submitted experiment ran with."""
    experiment = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE,
        related_name='session_treatments')
    treatment = models.ForeignKey(Treatment, on_delete=models.PROTECT,
        related_name='session_treatment_uses')
    ordering = models.PositiveIntegerField()

    class Meta:
        db_table = 'simulation_experimentsession_treatment'
        ordering = ['ordering']
        unique_together = [('experiment', 'treatment')]
