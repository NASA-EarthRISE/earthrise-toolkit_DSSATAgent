"""CropModel lookup helpers.

CropModel rows come in two flavors:
  * ``(crop_code, '')`` — crop-wide: dropdown curations, spacing ranges,
    display name, crop group, harvest component codes.
  * ``(crop_code, <dssat_model>)`` — model-specific: growth-stage
    vocabulary, supported harvs modes, default cultivar.

``get_crop_model()`` is the canonical read path: it tries the model-specific
row first and transparently falls back to the crop-wide row so callers
don't have to chain two queries.
"""

from dssat_agent.models import CropModel


def get_crop_model(crop_code, dssat_model=None):
    """Return the best-matching CropModel row, or ``None`` if nothing matches.

    Resolution order:
      1. ``(crop_code, dssat_model)`` — model-specific row (if dssat_model set)
      2. ``(crop_code, '')``          — crop-wide row
      3. ``None``
    """
    if not crop_code:
        return None
    qs = CropModel.objects.filter(crop_code=crop_code.upper())
    if dssat_model:
        row = qs.filter(dssat_model=dssat_model.upper()).first()
        if row:
            return row
    return qs.filter(dssat_model='').first()


def get_crop_model_pair(crop_code, dssat_model=None):
    """Return ``(model_specific, crop_wide)`` — either side may be ``None``.

    Use when a caller needs fields from both rows (e.g. crop-wide
    spacing ranges plus model-specific harvest stages).
    """
    if not crop_code:
        return None, None
    qs = CropModel.objects.filter(crop_code=crop_code.upper())
    model_row = None
    if dssat_model:
        model_row = qs.filter(dssat_model=dssat_model.upper()).first()
    crop_row = qs.filter(dssat_model='').first()
    return model_row, crop_row


def serialize_for_api(model_row, crop_row, crop_code=None):
    """Merge the two rows into a single dict shaped for the wizard UI.

    Model-specific fields come from ``model_row`` and fall through to
    ``crop_row`` only if the model-specific row is missing. Crop-wide
    fields are always read from ``crop_row``.

    When ``model_row`` is None and ``crop_code`` is provided, aggregates
    harvest stages from ALL model-specific rows for that crop (deduped by
    code) so contexts without a specific model (e.g. per-crop defaults)
    still get a useful stage dropdown.
    """
    row = model_row or crop_row
    if row is None:
        return None

    def _get(obj, name, default=None):
        return getattr(obj, name, default) if obj else default

    return {
        'crop_code': row.crop_code,
        'dssat_model': _get(model_row, 'dssat_model', ''),
        'display_name': _get(crop_row, 'display_name', '') or _get(model_row, 'display_name', ''),
        'crop_group': _get(crop_row, 'crop_group', '') or _get(model_row, 'crop_group', ''),

        # Curated dropdowns (crop-wide)
        'planting_methods':        _get(crop_row, 'planting_methods', []) or [],
        'fertilizer_materials':    _get(crop_row, 'fertilizer_materials', []) or [],
        'fertilizer_applications': _get(crop_row, 'fertilizer_applications', []) or [],
        'irrigation_methods':      _get(crop_row, 'irrigation_methods', []) or [],
        'chemical_materials':      _get(crop_row, 'chemical_materials', []) or [],
        'chemical_applications':   _get(crop_row, 'chemical_applications', []) or [],
        'residue_materials':       _get(crop_row, 'residue_materials', []) or [],
        'harvest_components':      _get(crop_row, 'harvest_components', []) or [],

        # Validation ranges (crop-wide)
        'plant_population_range': _get(crop_row, 'plant_population_range'),
        'row_spacing_range':      _get(crop_row, 'row_spacing_range'),
        'planting_depth_range':   _get(crop_row, 'planting_depth_range'),

        # Model-specific
        'harvest_stages':         _get(model_row, 'harvest_stages', []) or _aggregate_stages(model_row, crop_code),
        'supported_harvs_modes':  _get(model_row, 'supported_harvs_modes', []) or [],
        'default_cultivar_code':  _get(model_row, 'default_cultivar_code', '') or '',
        'supported_management':   (_get(model_row, 'supported_management', None)
                                    or _get(crop_row, 'supported_management', None)
                                    or []),
    }


def _aggregate_stages(model_row, crop_code):
    """When no model-specific row exists, union stages across all the crop's
    model-specific rows so per-crop-defaults UI still gets a dropdown.

    Dedupes by ``code``; first description wins for duplicate codes.
    """
    if model_row is not None or not crop_code:
        return []
    seen = {}
    qs = CropModel.objects.filter(crop_code=crop_code.upper()).exclude(dssat_model='')
    for row in qs:
        for stage in (row.harvest_stages or []):
            code = stage.get('code')
            if code and code not in seen:
                seen[code] = stage
    return list(seen.values())
