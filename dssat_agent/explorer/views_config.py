"""
DSSAT configuration views — user Preferences and admin System Config.

Both pages are tabbed: general preferences, simulation options, management
fallbacks, per-crop defaults. System Config adds a Curated Wizard Dropdowns
tab for configuring curated dropdowns (backed by crop-wide CropModel rows).

The preferences page writes `DSSATConfig` and `CropDefault` rows with
`user=request.user`. The system config page writes the same models with
`user=NULL` (system defaults that users inherit unless they override).

API endpoints follow a per-scope pattern:
    /dssat/api/config/preferences/         → user scope
    /dssat/api/config/preferences/?scope=system  → system scope (admin only)
and so on for sim-options, fallbacks, crop-default, curated.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from earthrise_agents_base.mixins import AgentAdminMixin, is_admin, is_agent_admin
from dssat_agent.models import (
    DSSATConfig, CropDefault, CropModel, StoredSoilProfile,
)
from dssat_agent.services.config import invalidate_cache
from dssat_agent.services.crop_service import list_crops

logger = logging.getLogger(__name__)


# =============================================================================
# Page views
# =============================================================================

@method_decorator(ensure_csrf_cookie, name='dispatch')
class DSSATPreferencesView(View):
    """Per-user DSSAT preferences page (all authenticated users)."""

    def get(self, request):
        return render(request, 'dssat_agent/explorer/preferences.html',
                      _page_context(request.user, is_system=False))


@method_decorator(ensure_csrf_cookie, name='dispatch')
class DSSATSystemConfigView(AgentAdminMixin, View):
    """System-level DSSAT configuration page (dssat_admin + global admins)."""

    agent_admin_group = 'dssat_admin'

    def get(self, request):
        return render(request, 'dssat_agent/explorer/system_config.html',
                      _page_context(request.user, is_system=True))


def _page_context(user, is_system):
    """Build template context shared by both preferences and system config pages."""
    scope_user = None if is_system else user

    # Load current config values (user row first, then system fallback)
    def load_value(key):
        return _read_config(key, scope_user)

    # Load user's crops-of-interest (from existing CropDefault rows)
    crop_defaults = CropDefault.objects.filter(user=scope_user).order_by('crop_code')
    configured_crops = list(crop_defaults.values_list('crop_code', flat=True))

    # Fetch available option lists for dropdowns
    try:
        all_crops = list_crops()
    except Exception:
        all_crops = []
    available_crops = [
        {
            'code': c.get('code'),
            'name': c.get('name') or c.get('code'),
            'crop_group': c.get('crop_group') or '',
        }
        for c in all_crops if c.get('code')
    ]

    soil_profiles = list(
        StoredSoilProfile.objects.all()
        .order_by('soil_id')
        .values('soil_id', 'name', 'source')[:500]
    )

    return {
        'is_system': is_system,
        # Tab 1: General
        'configured_crops_json': json.dumps(configured_crops),
        'default_weather_source': load_value('default_weather_source') or 'power',
        'default_soil_id': load_value('default_soil_id') or '',
        'default_num_years': load_value('default_num_years') or 1,
        # Tab 2: Sim options
        'sim_water': load_value('sim_water') or 'Y',
        'sim_nitro': load_value('sim_nitro') or 'Y',
        'sim_phosphorus': load_value('sim_phosphorus') or 'N',
        'sim_potassium': load_value('sim_potassium') or 'N',
        'output_grout': load_value('output_grout') or 'Y',
        'output_caout': load_value('output_caout') or 'Y',
        'output_waout': load_value('output_waout') or 'Y',
        'output_niout': load_value('output_niout') or 'Y',
        'output_miout': load_value('output_miout') or 'N',
        'output_diout': load_value('output_diout') or 'N',
        # Tab 3: Management fallbacks are loaded by JS via
        # GET /dssat/api/config/fallbacks/ — no template vars needed here.
        # Tab 4: My crops
        'configured_crops': configured_crops,
        # Reference data
        'available_crops_json': json.dumps(available_crops),
        'soil_profiles': soil_profiles,
    }


def _read_config(key, user):
    """Read a DSSATConfig value for a specific user (or system if user=None)."""
    try:
        row = DSSATConfig.objects.get(user=user, key=key)
        return row.value
    except DSSATConfig.DoesNotExist:
        return None


# =============================================================================
# API endpoints
# =============================================================================

def _check_scope_permission(request, scope):
    """Raise 403 if saving system scope without admin rights."""
    if scope != 'system':
        return None
    if not (is_admin(request.user) or is_agent_admin(request.user, 'dssat_admin')):
        return JsonResponse({'error': 'Permission denied'}, status=403)
    return None


def _scope_user(request, scope):
    """Return the User instance (or None) to attach to saved rows."""
    return None if scope == 'system' else request.user


def _upsert_config(user, key, value):
    """Create or update a DSSATConfig row."""
    if value is None or value == '':
        # Empty → delete the override (fall back to parent scope)
        DSSATConfig.objects.filter(user=user, key=key).delete()
        return
    DSSATConfig.objects.update_or_create(
        user=user, key=key,
        defaults={'value': value},
    )


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ConfigSaveAPI(View):
    """Read (GET) or save (POST) a set of DSSATConfig rows in one request.

    URL shape: `<section>/` where section is one of
      - preferences  (general: default_weather_source, default_soil_id, default_num_years)
      - sim-options  (sim_water, sim_nitro, ..., output_grout, ...)
      - fallbacks    (fallback_fertilizer, fallback_irrigation, ...)

    POST body: {"key1": "value1", "key2": "value2", ...}
    GET returns: {"success": true, "data": {"key1": value, ...}}
    Query: ?scope=system (admin only) to read/write system defaults.
    """

    # Whitelist of config keys per section (prevents arbitrary key injection).
    # Fallback keys store JSON values (event lists / dicts) shaped by the
    # Management Practices widget — see dssat_agent/static/dssat_agent/js/
    # management_practices.js.
    ALLOWED_KEYS = {
        'preferences': {
            'default_weather_source', 'default_soil_id', 'default_num_years',
        },
        'sim-options': {
            'sim_water', 'sim_nitro', 'sim_phosphorus', 'sim_potassium',
            'output_grout', 'output_caout', 'output_waout', 'output_niout',
            'output_miout', 'output_diout',
        },
        'fallbacks': {
            'fallback_fertilizer', 'fallback_irrigation', 'fallback_harvest',
            'fallback_tillage', 'fallback_chemical', 'fallback_residue',
        },
    }

    def get(self, request, section):
        allowed = self.ALLOWED_KEYS.get(section)
        if not allowed:
            return JsonResponse({'error': f'Unknown section: {section}'}, status=400)

        scope = request.GET.get('scope', 'user')
        user = _scope_user(request, scope)
        data = {key: _read_config(key, user) for key in allowed}
        return JsonResponse({'success': True, 'data': data})

    def post(self, request, section):
        allowed = self.ALLOWED_KEYS.get(section)
        if not allowed:
            return JsonResponse({'error': f'Unknown section: {section}'}, status=400)

        scope = request.GET.get('scope', 'user')
        denied = _check_scope_permission(request, scope)
        if denied:
            return denied

        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        # For management fallbacks, reject payloads that still carry absolute
        # dates — stored defaults must be DAP-keyed (see
        # validate_default_management).
        if section == 'fallbacks':
            errors = _validate_fallback_payload(data)
            if errors:
                return JsonResponse(
                    {'error': 'Invalid management fallback', 'issues': errors},
                    status=400,
                )

        user = _scope_user(request, scope)
        saved = []
        for key, value in data.items():
            if key not in allowed:
                logger.warning("Rejected unknown config key: %s", key)
                continue
            value = _coerce_value(key, value)
            _upsert_config(user, key, value)
            saved.append(key)

        invalidate_cache(user=user)
        return JsonResponse({'success': True, 'saved': saved})


_FALLBACK_KEY_TO_SECTION = {
    'fallback_fertilizer': 'fertilizer',
    'fallback_irrigation': 'irrigation',
    'fallback_harvest': 'harvest',
    'fallback_tillage': 'tillage',
    'fallback_chemical': 'chemical',
    'fallback_residue': 'residue',
}


def _validate_fallback_payload(data):
    """Reshape fallback_* keys into a single dict and validate as defaults."""
    from dssat_agent.services.validation import validate_default_management

    payload = {}
    for fallback_key, section_key in _FALLBACK_KEY_TO_SECTION.items():
        if fallback_key in data and data[fallback_key] not in (None, '', [], {}):
            payload[section_key] = data[fallback_key]
    return validate_default_management(payload)


def _coerce_value(key, value):
    """Coerce a raw form string to the right native type for storage.

    Scalar numeric keys (currently only ``default_num_years``) are cast to
    int. Fallback keys hold JSON values (lists/dicts) and pass through
    unchanged — the whitelist already constrains what can be stored.
    """
    if value == '' or value is None:
        return None
    if key == 'default_num_years':
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    return value


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ConfigResetAPI(View):
    """Remove a single user-level override so the user falls back to system default."""

    def post(self, request, key):
        scope = request.GET.get('scope', 'user')
        denied = _check_scope_permission(request, scope)
        if denied:
            return denied
        user = _scope_user(request, scope)
        deleted, _ = DSSATConfig.objects.filter(user=user, key=key).delete()
        invalidate_cache(user=user)
        return JsonResponse({'success': True, 'deleted': deleted})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class CropDefaultAPI(View):
    """Read / create / update / delete a CropDefault row for a (user, crop_code).

    GET    → return the row's data (or empty defaults if not yet created)
    POST   → upsert
    DELETE → remove the row
    Query: ?scope=system (admin only) to write system-level row (user=NULL).
    """

    def get(self, request, crop_code):
        scope = request.GET.get('scope', 'user')
        user = _scope_user(request, scope)
        try:
            row = CropDefault.objects.get(user=user, crop_code=crop_code.upper())
            data = _serialize_crop_default(row)
        except CropDefault.DoesNotExist:
            data = _empty_crop_default(crop_code.upper())
        return JsonResponse({'success': True, 'data': data})

    def post(self, request, crop_code):
        scope = request.GET.get('scope', 'user')
        denied = _check_scope_permission(request, scope)
        if denied:
            return denied

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        user = _scope_user(request, scope)
        crop_code = crop_code.upper()

        # Reject any management blob that still carries absolute dates —
        # stored per-crop defaults must be DAP-keyed.
        from dssat_agent.services.validation import validate_default_management
        defaults_errors = validate_default_management({
            k: payload.get(k) for k in (
                'fertilizer', 'irrigation', 'harvest',
                'tillage', 'chemical', 'residue',
            )
            if payload.get(k) not in (None, '', [], {})
        })
        if defaults_errors:
            return JsonResponse(
                {'error': 'Invalid crop defaults', 'issues': defaults_errors},
                status=400,
            )

        row, _ = CropDefault.objects.update_or_create(
            user=user, crop_code=crop_code,
            defaults={
                'cultivar_code': (payload.get('cultivar_code') or '')[:6],
                'plant_population': _num_or_none(payload.get('plant_population')),
                'row_spacing': _num_or_none(payload.get('row_spacing')),
                'planting_method': (payload.get('planting_method') or '')[:10],
                'fertilizer': payload.get('fertilizer') or None,
                'irrigation': payload.get('irrigation') or None,
                'harvest': payload.get('harvest') or None,
                'tillage': payload.get('tillage') or None,
                'chemical': payload.get('chemical') or None,
                'residue': payload.get('residue') or None,
            },
        )
        invalidate_cache(user=user)

        # Soft-warn on ppop/plrs outside the crop's typical ranges so the
        # UI can surface the warning without blocking the save.
        from dssat_agent.services.validation import validate_planting_ranges
        range_warnings = validate_planting_ranges(
            {
                'ppop': row.plant_population,
                'plrs': row.row_spacing,
            },
            crop_code,
        )
        return JsonResponse({
            'success': True,
            'data': _serialize_crop_default(row),
            'warnings': range_warnings,
        })

    def delete(self, request, crop_code):
        scope = request.GET.get('scope', 'user')
        denied = _check_scope_permission(request, scope)
        if denied:
            return denied
        user = _scope_user(request, scope)
        CropDefault.objects.filter(user=user, crop_code=crop_code.upper()).delete()
        invalidate_cache(user=user)
        return JsonResponse({'success': True})


def _num_or_none(value):
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialize_crop_default(row):
    return {
        'crop_code': row.crop_code,
        'cultivar_code': row.cultivar_code,
        'plant_population': row.plant_population,
        'row_spacing': row.row_spacing,
        'planting_method': row.planting_method,
        'fertilizer': row.fertilizer,
        'irrigation': row.irrigation,
        'harvest': row.harvest,
        'tillage': row.tillage,
        'chemical': row.chemical,
        'residue': row.residue,
    }


def _empty_crop_default(crop_code):
    return {
        'crop_code': crop_code,
        'cultivar_code': '',
        'plant_population': None,
        'row_spacing': None,
        'planting_method': '',
        'fertilizer': None,
        'irrigation': None,
        'harvest': None,
        'tillage': None,
        'chemical': None,
        'residue': None,
    }


# =============================================================================
# Curated wizard dropdowns (admin only)
# =============================================================================

@method_decorator(ensure_csrf_cookie, name='dispatch')
class CuratedCropOptionsAPI(View):
    """Read / save the curated dropdown fields on a CropModel crop-wide row.

    Backed by the (crop_code, dssat_model='') row of CropModel. URL is kept
    under ``/dssat/api/config/curated/<crop>/`` for compatibility with
    existing JS.

    Admin-only.

    POST body: {
        "planting_methods": ["R", "T"],
        "fertilizer_materials": ["FE005", "FE010"],
        ...
    }
    """

    FIELDS = [
        'planting_methods', 'fertilizer_materials', 'fertilizer_applications',
        'irrigation_methods', 'chemical_materials', 'chemical_applications',
        'residue_materials', 'harvest_components',
    ]

    def get(self, request, crop_code):
        if not (is_admin(request.user) or is_agent_admin(request.user, 'dssat_admin')):
            return JsonResponse({'error': 'Permission denied'}, status=403)
        row = CropModel.objects.filter(
            crop_code=crop_code.upper(), dssat_model=''
        ).first()
        if row is None:
            data = {f: [] for f in self.FIELDS}
        else:
            data = {f: getattr(row, f) or [] for f in self.FIELDS}
        return JsonResponse({'success': True, 'crop_code': crop_code.upper(), 'data': data})

    def post(self, request, crop_code):
        if not (is_admin(request.user) or is_agent_admin(request.user, 'dssat_admin')):
            return JsonResponse({'error': 'Permission denied'}, status=403)
        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        defaults = {}
        for f in self.FIELDS:
            val = payload.get(f, [])
            if isinstance(val, list):
                defaults[f] = [str(c) for c in val if c]

        row, _ = CropModel.objects.update_or_create(
            crop_code=crop_code.upper(),
            dssat_model='',
            defaults=defaults,
        )
        return JsonResponse({'success': True, 'crop_code': row.crop_code})

    def delete(self, request, crop_code):
        """Clear the curation fields on the crop-wide CropModel row.

        Does NOT delete the whole row (which may carry other crop-wide data
        like spacing ranges); only resets the dropdown-curation fields.
        """
        if not (is_admin(request.user) or is_agent_admin(request.user, 'dssat_admin')):
            return JsonResponse({'error': 'Permission denied'}, status=403)
        CropModel.objects.filter(
            crop_code=crop_code.upper(), dssat_model=''
        ).update(**{f: [] for f in self.FIELDS})
        return JsonResponse({'success': True})
