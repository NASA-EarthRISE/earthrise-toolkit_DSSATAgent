"""
Soil profile CRUD, texture estimation, and .SOL import/export.
"""

import logging
import os

from django.db import transaction

from dssat_agent.models import StoredSoilProfile, StoredSoilLayer
from DSSATTools.soil import SoilProfile, SoilLayer, estimate_from_texture

logger = logging.getLogger(__name__)

# All layer fields that map between DB model and DSSATTools SoilLayer
LAYER_FIELDS = [
    'slb', 'slll', 'sdul', 'ssat', 'srgf', 'sbdm', 'sloc', 'ssks',
    'slmh', 'slcl', 'slsi', 'slcf', 'slni', 'slhw', 'slhb', 'scec',
    'sadc', 'slpx', 'slpt', 'slpo', 'caco3', 'slal', 'slfe', 'slmn',
    'slbs', 'slpa', 'slpb', 'slke', 'slmg', 'slna', 'slsu', 'slec', 'slca',
]

REQUIRED_LAYER_FIELDS = ['slb', 'slll', 'sdul', 'ssat', 'srgf', 'sbdm', 'sloc']


def _dssat_soil_name(soil_id):
    # DSSATTools asserts the profile name is exactly 10 chars (fixed-width
    # .SOL column). Many ingested IDs lost trailing spaces during parsing;
    # restore them here at the boundary. Pad with spaces — that's what
    # DSSAT's .SOL convention expects, and matches the catalog's external
    # references. The strip-then-restore round-trip in DSSATBatch.run() is
    # handled by the auto-pad monkey-patch installed at module load time
    # (see ``_install_soil_name_autopad`` below).
    sid = (soil_id or '').strip()
    return sid[:10] if len(sid) >= 10 else sid.ljust(10)


def _install_soil_name_autopad():
    """Make ``DSSATTools.soil.SoilProfile`` tolerate short ``name`` values
    by auto-padding to 10 chars on assignment.

    DSSAT's batch runner (``DSSATBatch.run()``) saves the original soil
    name via ``str(soil["name"]).strip()`` before swapping in a synthetic
    ``SI########`` code for the duration of the run, then restores the
    stripped value in its ``finally`` block. If our space-padded name
    enters that round-trip, strip() shortens it below 10 chars and the
    restore triggers ``SoilProfile.__setitem__``'s ``len == 10``
    assertion. We can't keep the padding through ``str.strip``, so we
    instead make the setter tolerant: any short ``name`` is right-padded
    with spaces back up to 10 before the original assertion runs."""
    try:
        from DSSATTools.soil import SoilProfile
    except Exception:  # DSSATTools missing in some environments
        return
    if getattr(SoilProfile, '_soil_name_autopad_installed', False):
        return
    original_setitem = SoilProfile.__setitem__

    def patched_setitem(self, key, value):
        if key == "name" and isinstance(value, str) and len(value) < 10:
            value = value.ljust(10)
        return original_setitem(self, key, value)

    SoilProfile.__setitem__ = patched_setitem
    SoilProfile._soil_name_autopad_installed = True


_install_soil_name_autopad()


def _usda_texture_class(clay, silt):
    """Derive USDA soil texture class from clay and silt percentages.

    Returns a short code (e.g. "CL", "SIL", "S") or "" if inputs are missing.
    """
    if clay is None or silt is None:
        return ""
    sand = 100.0 - clay - silt
    if sand < 0:
        sand = 0.0

    if clay >= 40 and silt < 40 and sand < 45:
        return "C"
    if clay >= 27 and clay < 40 and sand > 20 and sand <= 45:
        return "CL"
    if clay >= 27 and clay < 40 and sand <= 20:
        return "SICL"
    if clay >= 40 and silt >= 40:
        return "SIC"
    if clay >= 35 and sand > 45:
        return "SC"
    if clay >= 20 and clay < 35 and silt < 28 and sand > 45:
        return "SCL"
    if silt >= 80 and clay < 12:
        return "SI"
    if silt >= 50 and clay >= 12 and clay < 27:
        return "SIL"
    if silt >= 50 and silt < 80 and clay < 12:
        return "SIL"
    if clay >= 7 and clay < 27 and silt >= 28 and silt < 50 and sand <= 52:
        return "L"
    if clay >= 7 and clay < 20 and sand > 52:
        return "SL"
    if clay < 7 and silt < 50 and sand > 70 and sand <= 90:
        return "LS"
    if sand > 90:
        return "S"
    return "L"  # fallback


def _profile_to_dict(profile, include_layers=True):
    """Convert a StoredSoilProfile to a JSON-serializable dict."""
    layers_qs = profile.layers.all()
    num_layers = layers_qs.count()

    # Derive texture from the top layer's clay/silt
    texture = ""
    top_layer = layers_qs.order_by('order').first()
    if top_layer:
        texture = _usda_texture_class(top_layer.slcl, top_layer.slsi)

    data = {
        'soil_id': profile.soil_id,
        'name': profile.name,
        'source': profile.source,
        'country': profile.country,
        'site': profile.site,
        'lat': profile.lat,
        'lon': profile.lon,
        'texture': texture,
        'num_layers': num_layers,
        'soil_classification': profile.soil_classification,
        'scs_family': profile.scs_family,
        'salb': profile.salb,
        'slu1': profile.slu1,
        'sldr': profile.sldr,
        'slro': profile.slro,
        'slnf': profile.slnf,
        'slpf': profile.slpf,
        'scom': profile.scom,
        'smhb': profile.smhb,
        'smpx': profile.smpx,
        'smke': profile.smke,
    }
    if include_layers:
        data['layers'] = [_layer_to_dict(l) for l in layers_qs]
    return data


def _layer_to_dict(layer):
    """Convert a StoredSoilLayer to a JSON-serializable dict."""
    data = {'order': layer.order}
    for f in LAYER_FIELDS:
        val = getattr(layer, f, None)
        if val is not None and val != '':
            data[f] = val
    return data


def _regenerate_sol_file(soil_id):
    """Regenerate and persist the .SOL file content for a soil profile."""
    try:
        dssat_profile = profile_to_dssat(soil_id)
        content = dssat_profile._write_sol()
        StoredSoilProfile.objects.filter(soil_id=soil_id).update(sol_file=content)
    except (ValueError, Exception):
        StoredSoilProfile.objects.filter(soil_id=soil_id).update(sol_file=None)


def list_soils(source=None, search=None, country=None,
               classification=None, page=1, page_size=50):
    """Paginated soil profiles list."""
    qs = StoredSoilProfile.objects.all()

    if source and source != 'all':
        qs = qs.filter(source=source)
    if search:
        qs = qs.filter(
            models_Q(soil_id__icontains=search) |
            models_Q(name__icontains=search)
        )
    if country:
        qs = qs.filter(country__icontains=country)
    if classification:
        qs = qs.filter(soil_classification=classification.upper())

    total = qs.count()
    offset = (page - 1) * page_size
    profiles = qs[offset:offset + page_size]

    return {
        'total': total,
        'page': page,
        'page_size': page_size,
        'profiles': [_profile_to_dict(p, include_layers=False) for p in profiles],
    }


def models_Q(**kwargs):
    """Helper to avoid circular import of Q."""
    from django.db.models import Q
    return Q(**kwargs)


def get_soil_profile(soil_id):
    """Get a full soil profile with layers."""
    try:
        profile = StoredSoilProfile.objects.prefetch_related('layers').get(soil_id=soil_id)
        return _profile_to_dict(profile)
    except StoredSoilProfile.DoesNotExist:
        return {"error": f"Soil profile '{soil_id}' not found"}


@transaction.atomic
def create_soil_profile(params):
    """Create a new soil profile (no layers yet)."""
    soil_id = params.get('soil_id', '').strip()
    if not soil_id:
        return {"error": "soil_id is required"}
    if len(soil_id) > 10:
        return {"error": "soil_id must be at most 10 characters"}

    if StoredSoilProfile.objects.filter(soil_id=soil_id).exists():
        return {"error": f"Soil profile '{soil_id}' already exists"}

    required = ['salb', 'slu1', 'sldr', 'slro', 'slnf', 'slpf']
    for f in required:
        if params.get(f) is None:
            return {"error": f"{f} is required"}

    profile = StoredSoilProfile.objects.create(
        soil_id=soil_id,
        name=params.get('name', ''),
        source='custom',
        country=params.get('country', ''),
        site=params.get('site', ''),
        lat=params.get('lat'),
        lon=params.get('lon'),
        soil_classification=params.get('classification', ''),
        scs_family=params.get('scs_family', ''),
        salb=params['salb'],
        slu1=params['slu1'],
        sldr=params['sldr'],
        slro=params['slro'],
        slnf=params['slnf'],
        slpf=params['slpf'],
        scom=params.get('scom', ''),
        smhb=params.get('smhb', ''),
        smpx=params.get('smpx', ''),
        smke=params.get('smke', ''),
    )
    return _profile_to_dict(profile)


@transaction.atomic
def create_soil_from_texture(params):
    """
    Create soil profile with layers auto-estimated from texture.

    Each layer needs: depth, clay_pct, silt_pct.
    Optionally: organic_carbon, bulk_density.
    """
    soil_id = params.get('soil_id', '').strip()
    if not soil_id:
        return {"error": "soil_id is required"}

    if StoredSoilProfile.objects.filter(soil_id=soil_id).exists():
        return {"error": f"Soil profile '{soil_id}' already exists"}

    layers_input = params.get('layers', [])
    if not layers_input:
        return {"error": "At least one layer is required"}

    # Surface params with defaults
    salb = params.get('salb', 0.13)
    slu1 = params.get('slu1', 6.0)
    sldr = params.get('sldr', 0.60)
    slro = params.get('slro', 73.0)
    slnf = params.get('slnf', 1.0)
    slpf = params.get('slpf', 1.0)

    profile = StoredSoilProfile.objects.create(
        soil_id=soil_id,
        name=params.get('name', ''),
        source='custom',
        country=params.get('country', ''),
        site=params.get('site', ''),
        lat=params.get('lat'),
        lon=params.get('lon'),
        soil_classification=params.get('classification', ''),
        scs_family=params.get('scs_family', ''),
        salb=salb, slu1=slu1, sldr=sldr, slro=slro, slnf=slnf, slpf=slpf,
        scom=params.get('scom', ''),
        smhb=params.get('smhb', ''),
        smpx=params.get('smpx', ''),
        smke=params.get('smke', ''),
    )

    for i, layer_in in enumerate(layers_input):
        clay_pct = layer_in.get('clay_pct')
        silt_pct = layer_in.get('silt_pct')
        depth = layer_in.get('depth')

        if clay_pct is None or silt_pct is None or depth is None:
            profile.delete()
            return {"error": f"Layer {i}: depth, clay_pct, and silt_pct are required"}

        oc = layer_in.get('organic_carbon')
        bd = layer_in.get('bulk_density')

        # Estimate hydraulic properties
        estimated = estimate_from_texture(
            slcl=clay_pct,
            slsi=silt_pct,
            sbdm=bd,
            sloc=oc,
        )

        # Root growth factor: decreases with depth
        srgf = max(1.0 - (depth / 200.0), 0.05)

        StoredSoilLayer.objects.create(
            profile=profile,
            order=i,
            slb=depth,
            slll=estimated.get('slll', 0.1),
            sdul=estimated.get('sdul', 0.2),
            ssat=estimated.get('ssat', 0.4),
            srgf=round(srgf, 3),
            sbdm=bd or estimated.get('sbdm') or 1.4,
            sloc=oc or 0.5,
            ssks=estimated.get('ssks'),
            slcl=clay_pct,
            slsi=silt_pct,
        )

    _regenerate_sol_file(soil_id)

    return _profile_to_dict(
        StoredSoilProfile.objects.prefetch_related('layers').get(pk=profile.pk)
    )


@transaction.atomic
def add_soil_layer(soil_id, layer_params):
    """Add a layer to an existing profile."""
    try:
        profile = StoredSoilProfile.objects.get(soil_id=soil_id)
    except StoredSoilProfile.DoesNotExist:
        return {"error": f"Soil profile '{soil_id}' not found"}

    for f in REQUIRED_LAYER_FIELDS:
        if layer_params.get(f) is None:
            return {"error": f"{f} is required for a soil layer"}

    existing_count = profile.layers.count()

    kwargs = {'profile': profile, 'order': existing_count}
    for f in LAYER_FIELDS:
        if f in layer_params and layer_params[f] is not None:
            kwargs[f] = layer_params[f]

    StoredSoilLayer.objects.create(**kwargs)

    _regenerate_sol_file(soil_id)

    return _profile_to_dict(
        StoredSoilProfile.objects.prefetch_related('layers').get(pk=profile.pk)
    )


@transaction.atomic
def update_soil_layer(soil_id, layer_index, updated_params):
    """Update an existing layer."""
    try:
        profile = StoredSoilProfile.objects.get(soil_id=soil_id)
    except StoredSoilProfile.DoesNotExist:
        return {"error": f"Soil profile '{soil_id}' not found"}

    try:
        layer = profile.layers.get(order=layer_index)
    except StoredSoilLayer.DoesNotExist:
        return {"error": f"Layer index {layer_index} not found for {soil_id}"}

    for f in LAYER_FIELDS:
        if f in updated_params:
            setattr(layer, f, updated_params[f])
    layer.save()

    _regenerate_sol_file(soil_id)

    return _profile_to_dict(
        StoredSoilProfile.objects.prefetch_related('layers').get(pk=profile.pk)
    )


@transaction.atomic
def remove_soil_layer(soil_id, layer_index):
    """Remove a layer and re-order remaining layers."""
    try:
        profile = StoredSoilProfile.objects.get(soil_id=soil_id)
    except StoredSoilProfile.DoesNotExist:
        return {"error": f"Soil profile '{soil_id}' not found"}

    try:
        layer = profile.layers.get(order=layer_index)
    except StoredSoilLayer.DoesNotExist:
        return {"error": f"Layer index {layer_index} not found for {soil_id}"}

    layer.delete()

    # Re-order remaining layers
    for i, remaining in enumerate(profile.layers.order_by('order')):
        if remaining.order != i:
            remaining.order = i
            remaining.save(update_fields=['order'])

    _regenerate_sol_file(soil_id)

    return _profile_to_dict(
        StoredSoilProfile.objects.prefetch_related('layers').get(pk=profile.pk)
    )


def estimate_soil_properties(clay_pct, silt_pct, bulk_density=None, organic_carbon=None):
    """Estimate hydraulic properties without persistence."""
    try:
        result = estimate_from_texture(
            slcl=clay_pct,
            slsi=silt_pct,
            sbdm=bulk_density,
            sloc=organic_carbon,
        )
        return {
            "slll": result.get('slll'),
            "sdul": result.get('sdul'),
            "ssat": result.get('ssat'),
            "ssks": result.get('ssks'),
            "sbdm": result.get('sbdm'),
            "input": {
                "clay_pct": clay_pct,
                "silt_pct": silt_pct,
                "bulk_density": bulk_density,
                "organic_carbon": organic_carbon,
            },
        }
    except Exception as e:
        return {"error": f"Estimation failed: {e}"}


def profile_to_dssat(soil_id):
    """Convert a stored profile to a DSSATTools SoilProfile object."""
    try:
        profile = StoredSoilProfile.objects.prefetch_related('layers').get(soil_id=soil_id)
    except StoredSoilProfile.DoesNotExist:
        raise ValueError(f"Soil profile '{soil_id}' not found")

    layers = []
    for layer in profile.layers.order_by('order'):
        kwargs = {}
        for f in LAYER_FIELDS:
            val = getattr(layer, f, None)
            if val is not None and val != '':
                kwargs[f] = val
        layers.append(SoilLayer(**kwargs))

    if not layers:
        raise ValueError(f"Soil profile '{soil_id}' has no layers")

    return SoilProfile(
        name=_dssat_soil_name(profile.soil_id),
        table=layers,
        salb=profile.salb,
        slu1=profile.slu1,
        sldr=profile.sldr,
        slro=profile.slro,
        slnf=profile.slnf,
        slpf=profile.slpf,
        soil_data_source=profile.source,
        soil_clasification=profile.soil_classification or None,
        soil_series_name=profile.name or None,
        site=profile.site or None,
        country=profile.country or None,
        lat=profile.lat,
        long=profile.lon,
        scs_family=profile.scs_family or None,
        scom=profile.scom or None,
        smhb=profile.smhb or None,
        smpx=profile.smpx or None,
        smke=profile.smke or None,
    )


def dict_to_dssat_soil(inline_soil):
    """Convert an inline soil JSON dict to a DSSATTools SoilProfile object."""
    layers_data = inline_soil.get('layers', inline_soil.get('table', []))
    layers = []
    for layer_data in layers_data:
        kwargs = {}
        for f in LAYER_FIELDS:
            if f in layer_data and layer_data[f] is not None:
                kwargs[f] = layer_data[f]
        layers.append(SoilLayer(**kwargs))

    if not layers:
        raise ValueError("inline_soil must have at least one layer")

    return SoilProfile(
        name=_dssat_soil_name(inline_soil.get('soil_id', inline_soil.get('name', 'CUSTOM0001'))),
        table=layers,
        salb=inline_soil.get('salb', 0.13),
        slu1=inline_soil.get('slu1', 6.0),
        sldr=inline_soil.get('sldr', 0.60),
        slro=inline_soil.get('slro', 73.0),
        slnf=inline_soil.get('slnf', 1.0),
        slpf=inline_soil.get('slpf', 1.0),
        soil_clasification=inline_soil.get('soil_classification') or None,
        soil_series_name=inline_soil.get('name') or None,
        scom=inline_soil.get('scom') or None,
        smhb=inline_soil.get('smhb') or None,
        smpx=inline_soil.get('smpx') or None,
        smke=inline_soil.get('smke') or None,
    )
