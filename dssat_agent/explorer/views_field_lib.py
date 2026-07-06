"""
Field library REST API.

CRUD over re-usable Field records (lat/lon/elevation + soil + weather source).
The wizard saves a Field per location; the library page lets users
rename/delete/list.

Auth: a Field is user-scoped. Admins (Django superusers, agent admins) can
read any user's fields; everyone else can only read/write their own.
"""

import json
import logging
import uuid

from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from earthrise_agents_base.mixins import is_admin, is_agent_admin
from dssat_agent.models import Field, StoredSoilProfile

# Statuses considered "in the library" — wizard-created rows default to
# 'used' which is intentionally hidden from the library browser.
LIBRARY_STATUSES = ('saved', 'published')

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _is_admin(user):
    return is_admin(user) or is_agent_admin(user, 'dssat_admin')


def _can_access(user, field):
    """User can access if they own the field or are an admin."""
    return field.user_id == user.id or _is_admin(user)


def _can_read(user, field):
    """Read access also includes ``published`` rows from any user — that's
    the whole point of Publish."""
    if not user.is_authenticated:
        return False
    if _can_access(user, field):
        return True
    return field.status == 'published'


def _serialize_field(f, viewing_user=None):
    return {
        'id': str(f.id),
        'name': f.name,
        'description': f.description,
        'latitude': f.latitude,
        'longitude': f.longitude,
        'elevation': f.elevation,
        'soil_id': f.soil_profile.soil_id if f.soil_profile_id else None,
        'has_inline_soil': bool(f.inline_soil),
        'weather_source': f.weather_source,
        'location_label': f.location_label,
        'is_admin_centroid': f.is_admin_centroid,
        'admin_unit': f.admin_unit,
        'status': f.status,
        'is_owner': bool(viewing_user and f.user_id == viewing_user.id),
        'created_at': f.created_at.isoformat(),
        'updated_at': f.updated_at.isoformat(),
    }


def _apply_payload(field, payload, user, allow_user_change=False):
    """Apply request body fields to a Field instance (mutating).

    Validates required fields. Resolves ``soil_id`` (string DSSAT code) to
    a ``StoredSoilProfile`` FK. Returns ``(ok, error_message_or_None)``.

    Note: Field.id has ``default=uuid.uuid4`` which Django evaluates at
    instantiation, so ``field.pk`` is *already set* even on a brand-new
    unsaved Field. We use ``_state.adding`` to distinguish "not yet saved"
    from "loaded from DB".
    """
    is_new = field._state.adding
    name = payload.get('name')
    if not name and is_new:
        return False, "name is required"
    if name is not None:
        field.name = str(name)

    if 'description' in payload:
        field.description = str(payload.get('description') or '')

    if 'latitude' in payload:
        try:
            field.latitude = float(payload['latitude'])
        except (TypeError, ValueError):
            return False, "latitude must be a number"
    elif is_new:
        return False, "latitude is required"

    if 'longitude' in payload:
        try:
            field.longitude = float(payload['longitude'])
        except (TypeError, ValueError):
            return False, "longitude must be a number"
    elif is_new:
        return False, "longitude is required"

    if 'elevation' in payload:
        v = payload['elevation']
        if v is None or v == '':
            field.elevation = None
        else:
            try:
                field.elevation = float(v)
            except (TypeError, ValueError):
                return False, "elevation must be a number or null"

    if 'soil_id' in payload:
        soil_id = payload.get('soil_id')
        if soil_id:
            try:
                field.soil_profile = StoredSoilProfile.objects.get(soil_id=soil_id)
            except StoredSoilProfile.DoesNotExist:
                return False, f"soil profile '{soil_id}' not found"
        else:
            field.soil_profile = None

    if 'inline_soil' in payload:
        field.inline_soil = payload.get('inline_soil') or None

    if 'weather_source' in payload:
        field.weather_source = str(payload.get('weather_source') or '')
    elif is_new:
        return False, "weather_source is required"

    if 'location_label' in payload:
        field.location_label = str(payload.get('location_label') or '')
    if 'is_admin_centroid' in payload:
        field.is_admin_centroid = bool(payload.get('is_admin_centroid'))
    if 'admin_unit' in payload:
        field.admin_unit = str(payload.get('admin_unit') or '')

    if 'status' in payload:
        s = payload.get('status')
        valid = {c[0] for c in Field.STATUS_CHOICES}
        if s not in valid:
            return False, f"status must be one of {sorted(valid)}"
        field.status = s

    if is_new:
        field.user = user

    return True, None


# ---------------------------------------------------------------------------
# List + Create
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class FieldListAPI(View):
    """
    GET  /api/fields/        — list this user's fields (admins see all).
    POST /api/fields/        — create a new field.

    Query params (GET):
      ?q=<string>     filter by name/location_label substring
      ?limit=<int>    page size, default 50
      ?offset=<int>   offset
    """

    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')

        # Library browser is the same for everyone (including admins):
        # the user's own saved + published rows plus everyone else's
        # published rows. Wizard-attached 'used' rows never appear here
        # — they're only reachable via the wizard itself. Admins who
        # need to see "used" or other-user rows can hit the API
        # directly with a status query or use Django admin.
        qs = Field.objects.filter(
            Q(user=user, status__in=LIBRARY_STATUSES)
            | Q(status='published')
        )

        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(location_label__icontains=q)
            )

        try:
            limit = int(request.GET.get('limit', 50))
            offset = int(request.GET.get('offset', 0))
        except ValueError:
            return JsonResponse({'error': 'limit/offset must be integers'}, status=400)
        limit = max(1, min(limit, 200))

        total = qs.count()
        fields = list(qs.select_related('soil_profile')[offset:offset + limit])
        return JsonResponse({
            'success': True,
            'count': total,
            'limit': limit,
            'offset': offset,
            'fields': [_serialize_field(f, viewing_user=user) for f in fields],
        })

    def post(self, request):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)

        f = Field()
        ok, err = _apply_payload(f, payload, user)
        if not ok:
            return JsonResponse({'error': err}, status=400)
        try:
            f.save()
        except Exception as e:
            logger.exception("Field create failed")
            return JsonResponse({'error': str(e)}, status=500)
        return JsonResponse({'success': True, 'field': _serialize_field(f, viewing_user=user)}, status=201)


# ---------------------------------------------------------------------------
# Detail / Update / Delete
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class FieldDetailAPI(View):
    """
    GET    /api/fields/<uuid:id>/   — fetch (any user can read a published row)
    PATCH  /api/fields/<uuid:id>/   — partial update (owner / admin only)
    DELETE /api/fields/<uuid:id>/   — delete (owner / admin only;
                                      PROTECT will block if used in a draft/run)
    """

    def get(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            f = Field.objects.select_related('soil_profile').get(id=id)
        except Field.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_read(user, f):
            return HttpResponseForbidden('not your field')
        return JsonResponse({'success': True,
                              'field': _serialize_field(f, viewing_user=user)})

    def patch(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            f = Field.objects.get(id=id)
        except Field.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, f):
            return HttpResponseForbidden('not your field')
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        ok, err = _apply_payload(f, payload, user)
        if not ok:
            return JsonResponse({'error': err}, status=400)
        f.save()
        return JsonResponse({'success': True,
                              'field': _serialize_field(f, viewing_user=user)})

    def delete(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            f = Field.objects.get(id=id)
        except Field.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, f):
            return HttpResponseForbidden('not your field')
        try:
            f.delete()
        except Exception as e:
            # PROTECT FK from WizardDraftField / WizardDraftPair / ExperimentSessionField
            return JsonResponse(
                {'error': f'cannot delete: {e}'}, status=409)
        return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Explorer pages (server-rendered shell; JS fetches data via the APIs above)
# ---------------------------------------------------------------------------

class FieldListView(View):
    """Render /explorer/fields/ — Field library landing page."""

    def get(self, request):
        if not request.user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        return render(
            request,
            'dssat_agent/explorer/fields_list.html',
            {'agent_name': 'DSSAT'},
        )


class FieldDetailView(View):
    """Render /explorer/fields/<uuid>/ — single-field detail/edit page."""

    def get(self, request, field_id):
        if not request.user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            field = Field.objects.select_related('soil_profile').get(id=field_id)
        except Field.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_read(request.user, field):
            return HttpResponseForbidden('not your field')
        return render(
            request,
            'dssat_agent/explorer/field_detail.html',
            {
                'agent_name': 'DSSAT',
                'field': field,
                'field_json': json.dumps(_serialize_field(field)),
            },
        )
