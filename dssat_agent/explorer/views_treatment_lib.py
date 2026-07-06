"""
Treatment library REST API.

CRUD over re-usable Treatment protocol bundles (per-treatment FILEX factor
levels: cultivar, planting, harvest, IC, simulation controls, and the
optional management blocks).

Auth: user-scoped same as the Field library.
"""

import json
import logging

from django.db.models import Q
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from earthrise_agents_base.mixins import is_admin, is_agent_admin
from dssat_agent.models import Treatment

logger = logging.getLogger(__name__)

# Statuses considered "in the library" — wizard-created rows default to
# 'used' which is intentionally hidden from the library browser.
LIBRARY_STATUSES = ('saved', 'published')


# ---------------------------------------------------------------------------
# Auth helpers (mirror views_field_lib so the two stay symmetrical)
# ---------------------------------------------------------------------------

def _is_admin(user):
    return is_admin(user) or is_agent_admin(user, 'dssat_admin')


def _can_access(user, treatment):
    return treatment.user_id == user.id or _is_admin(user)


def _can_read(user, treatment):
    """Read access also includes ``published`` rows from any user."""
    if not user.is_authenticated:
        return False
    if _can_access(user, treatment):
        return True
    return treatment.status == 'published'


# JSONField sections that pass through verbatim.
_JSON_SECTIONS = (
    'planting', 'harvest', 'initial_conditions', 'simulation_controls',
    'fertilizer', 'irrigation', 'residue', 'chemical', 'tillage', 'mow',
)


def _serialize_treatment(t, viewing_user=None):
    return {
        'id': str(t.id),
        'name': t.name,
        'description': t.description,
        'crop_code': t.crop_code,
        'cultivar_code': t.cultivar_code,
        'dssat_model': t.dssat_model,
        'planting': t.planting,
        'harvest': t.harvest,
        'initial_conditions': t.initial_conditions,
        'simulation_controls': t.simulation_controls,
        'fertilizer': t.fertilizer,
        'irrigation': t.irrigation,
        'residue': t.residue,
        'chemical': t.chemical,
        'tillage': t.tillage,
        'mow': t.mow,
        'status': t.status,
        'is_owner': bool(viewing_user and t.user_id == viewing_user.id),
        'created_at': t.created_at.isoformat(),
        'updated_at': t.updated_at.isoformat(),
    }


def _apply_payload(treatment, payload, user):
    # See views_field_lib._apply_payload — UUIDField defaults make `pk`
    # truthy even before save(), so we test `_state.adding` instead.
    is_new = treatment._state.adding
    name = payload.get('name')
    if not name and is_new:
        return False, "name is required"
    if name is not None:
        treatment.name = str(name)

    if 'description' in payload:
        treatment.description = str(payload.get('description') or '')

    if 'crop_code' in payload:
        treatment.crop_code = str(payload.get('crop_code') or '').upper()
    elif is_new:
        return False, "crop_code is required"

    if 'cultivar_code' in payload:
        treatment.cultivar_code = str(payload.get('cultivar_code') or '')
    if 'dssat_model' in payload:
        treatment.dssat_model = str(payload.get('dssat_model') or '').upper()

    for key in _JSON_SECTIONS:
        if key in payload:
            v = payload.get(key)
            if key in ('planting', 'simulation_controls') and v is None:
                return False, f"{key} cannot be null"
            setattr(treatment, key, v)

    if 'status' in payload:
        s = payload.get('status')
        valid = {c[0] for c in Treatment.STATUS_CHOICES}
        if s not in valid:
            return False, f"status must be one of {sorted(valid)}"
        treatment.status = s

    if is_new:
        if not treatment.planting:
            treatment.planting = {}
        if not treatment.simulation_controls:
            treatment.simulation_controls = {}
        treatment.user = user

    return True, None


# ---------------------------------------------------------------------------
# List + Create
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class TreatmentListAPI(View):
    """
    GET  /api/treatments/        — list this user's treatments (admins see all).
    POST /api/treatments/        — create a new treatment.

    Query params (GET):
      ?q=<string>           name/description substring match
      ?crop_code=<str>      filter by crop_code (e.g. MZ)
      ?limit, ?offset       pagination
    """

    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')

        # Library browser is the same for everyone (including admins):
        # the user's own saved + published rows plus everyone else's
        # published rows. Wizard-attached 'used' rows are hidden — they
        # appear via the wizard, not the library.
        qs = Treatment.objects.filter(
            Q(user=user, status__in=LIBRARY_STATUSES)
            | Q(status='published')
        )

        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q) | Q(description__icontains=q)
            )
        crop_code = (request.GET.get('crop_code') or '').strip().upper()
        if crop_code:
            qs = qs.filter(crop_code=crop_code)

        try:
            limit = int(request.GET.get('limit', 50))
            offset = int(request.GET.get('offset', 0))
        except ValueError:
            return JsonResponse({'error': 'limit/offset must be integers'}, status=400)
        limit = max(1, min(limit, 200))

        total = qs.count()
        items = list(qs[offset:offset + limit])
        return JsonResponse({
            'success': True,
            'count': total,
            'limit': limit,
            'offset': offset,
            'treatments': [_serialize_treatment(t, viewing_user=user) for t in items],
        })

    def post(self, request):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)

        t = Treatment()
        ok, err = _apply_payload(t, payload, user)
        if not ok:
            return JsonResponse({'error': err}, status=400)
        try:
            t.save()
        except Exception as e:
            logger.exception("Treatment create failed")
            return JsonResponse({'error': str(e)}, status=500)
        return JsonResponse({'success': True, 'treatment': _serialize_treatment(t, viewing_user=user)}, status=201)


# ---------------------------------------------------------------------------
# Detail / Update / Delete
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class TreatmentDetailAPI(View):
    """
    GET    /api/treatments/<uuid:id>/
    PATCH  /api/treatments/<uuid:id>/
    DELETE /api/treatments/<uuid:id>/
    """

    def get(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            t = Treatment.objects.get(id=id)
        except Treatment.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_read(user, t):
            return HttpResponseForbidden('not your treatment')
        return JsonResponse({'success': True, 'treatment': _serialize_treatment(t, viewing_user=user)})

    def patch(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            t = Treatment.objects.get(id=id)
        except Treatment.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, t):
            return HttpResponseForbidden('not your treatment')
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        ok, err = _apply_payload(t, payload, user)
        if not ok:
            return JsonResponse({'error': err}, status=400)
        t.save()
        return JsonResponse({'success': True, 'treatment': _serialize_treatment(t, viewing_user=user)})

    def delete(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            t = Treatment.objects.get(id=id)
        except Treatment.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, t):
            return HttpResponseForbidden('not your treatment')
        try:
            t.delete()
        except Exception as e:
            return JsonResponse(
                {'error': f'cannot delete: {e}'}, status=409)
        return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Explorer pages
# ---------------------------------------------------------------------------

class TreatmentListView(View):
    def get(self, request):
        if not request.user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        return render(
            request,
            'dssat_agent/explorer/treatments_list.html',
            {'agent_name': 'DSSAT'},
        )


class TreatmentDetailView(View):
    def get(self, request, treatment_id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            t = Treatment.objects.get(id=treatment_id)
        except Treatment.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_read(user, t):
            return HttpResponseForbidden('not your treatment')
        return render(
            request,
            'dssat_agent/explorer/treatment_detail.html',
            {
                'agent_name': 'DSSAT',
                'treatment': t,
                'treatment_json': json.dumps(_serialize_treatment(t, viewing_user=user)),
            },
        )
