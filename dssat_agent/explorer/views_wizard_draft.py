"""
WizardDraft REST API.

Drives the SPA's nested-step wizard and the chat-side `experiment_wizard_step`
skill. A draft owns ordered Field and Treatment attachments and (for batch
experiments) explicit (Field, Treatment) pairs. The SPA reads/writes
`step_state` per step on Next; the chat skill writes the same row when
the user is in the chat-driven flow, so a build started in one UI can be
resumed in the other.

Endpoints
---------
* ``POST   /api/wizard-drafts/``                    create draft
* ``GET    /api/wizard-drafts/active/``             user's currently-open draft
* ``GET    /api/wizard-drafts/<id>/``               fetch full draft state
* ``PATCH  /api/wizard-drafts/<id>/``               partial update
                                                    (step_state / current_step /
                                                    fields / treatments / pairs)
* ``POST   /api/wizard-drafts/<id>/lock/``          mark a step locked
* ``POST   /api/wizard-drafts/<id>/edit/``          unlock a step + cascade-clear
                                                    everything strictly downstream
                                                    (state, fields, treatments,
                                                    pairs)
* ``POST   /api/wizard-drafts/<id>/submit/``        finalize and run

The submit endpoint replaces the old ``ExperimentSubmitAPI`` shape; legacy
``POST /api/experiment/submit/`` remains as a thin redirect that adapts
old payloads into a fresh draft and then submits.
"""

import json
import logging
import uuid

from django.http import JsonResponse, HttpResponseForbidden
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

from earthrise_agents_base.mixins import is_admin, is_agent_admin
from dssat_agent.models import (
    Field, Treatment, WizardDraft,
    WizardDraftField, WizardDraftTreatment,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _is_admin(user):
    return is_admin(user) or is_agent_admin(user, 'dssat_admin')


def _can_access(user, draft):
    return draft.user_id == user.id or _is_admin(user)


# ---------------------------------------------------------------------------
# Step ordering — used by the cascade-clear logic
# ---------------------------------------------------------------------------
#
# Step paths are dot-separated. The first segment is one of these top-level
# steps; deeper segments carry sub-step or loop indices. ``downstream_of``
# returns the set of step prefixes strictly later than the given step.

_TOP_LEVEL_ORDER = ['step1', 'step2', 'step3', 'step4', 'step5']


def _top_level(step_path):
    return (step_path or 'step1').split('.', 1)[0]


def _downstream_of(step_path):
    """Return the list of top-level step prefixes strictly later than the
    one named in ``step_path``.

    Example: ``_downstream_of('step2.location.mc')`` returns
    ``['step3', 'step4', 'step5']``.
    """
    head = _top_level(step_path)
    if head not in _TOP_LEVEL_ORDER:
        return []
    idx = _TOP_LEVEL_ORDER.index(head)
    return _TOP_LEVEL_ORDER[idx + 1:]


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _serialize_field_brief(f):
    return {
        'id': str(f.id),
        'name': f.name,
        'latitude': f.latitude,
        'longitude': f.longitude,
        'elevation': f.elevation,
        'soil_id': f.soil_profile.soil_id if f.soil_profile_id else None,
        'has_inline_soil': bool(f.inline_soil),
        'weather_source': f.weather_source,
        'location_label': f.location_label,
        # Status drives the per-card "Save / Publish" buttons on Step 5
        # so the SPA can show the right label/active state without
        # re-fetching from the field-library endpoint.
        'status': f.status,
        'is_admin_centroid': f.is_admin_centroid,
    }


def _serialize_treatment_brief(t):
    return {
        'id': str(t.id),
        'name': t.name,
        'crop_code': t.crop_code,
        'cultivar_code': t.cultivar_code,
        # See _serialize_field_brief — drives the Step 5 Save / Publish buttons.
        'status': t.status,
    }


def _serialize_draft(draft):
    fields = list(WizardDraftField.objects
                  .filter(draft=draft)
                  .select_related('field__soil_profile')
                  .order_by('ordering'))
    treatments = list(WizardDraftTreatment.objects
                      .filter(draft=draft)
                      .select_related('treatment', 'field')
                      .order_by('ordering'))
    return {
        'id': str(draft.id),
        'experiment_type': draft.experiment_type,
        'current_step': draft.current_step,
        'locked_steps': draft.locked_steps,
        'step_state': draft.step_state,
        'status': draft.status,
        'chat_id': str(draft.chat_id) if draft.chat_id else None,
        'fields': [
            {'ordering': df.ordering, **_serialize_field_brief(df.field)}
            for df in fields
        ],
        # Each treatment row carries its paired field id (or null if the
        # SPA has staged the row mid-flow before locking Step 4).
        'treatments': [
            {
                'ordering': dt.ordering,
                'field_id': str(dt.field_id) if dt.field_id else None,
                **_serialize_treatment_brief(dt.treatment),
            }
            for dt in treatments
        ],
        'created_at': draft.created_at.isoformat(),
        'updated_at': draft.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# M2M apply helpers
# ---------------------------------------------------------------------------

def _apply_field_attachments(draft, items, user):
    """Replace the draft's WizardDraftField rows with the given list.

    ``items`` is a list like ``[{'field_id': '<uuid>', 'ordering': 0}, ...]``.
    Validates ownership and existence; raises ValueError on bad input.
    """
    if items is None:
        return
    new_rows = []
    for i, item in enumerate(items):
        fid = item.get('field_id')
        if not fid:
            raise ValueError(f"items[{i}]: field_id is required")
        try:
            field = Field.objects.get(id=fid)
        except Field.DoesNotExist:
            raise ValueError(f"items[{i}]: field {fid} not found")
        if field.user_id != user.id and not _is_admin(user):
            raise ValueError(f"items[{i}]: field {fid} is not yours")
        ordering = int(item.get('ordering', i))
        new_rows.append((ordering, field))
    WizardDraftField.objects.filter(draft=draft).delete()
    WizardDraftField.objects.bulk_create([
        WizardDraftField(draft=draft, field=f, ordering=o)
        for o, f in new_rows
    ])


def _apply_treatment_attachments(draft, items, user):
    """Replace the draft's WizardDraftTreatment rows.

    Each item is ``{treatment_id, field_id?, ordering?}``. ``field_id`` is
    optional only because a draft mid-flow may stage rows before Step 4
    locks; once the lock hook fires, every row carries a field. The same
    protocol may pair with many different fields (matrix mode), but the
    same `(treatment, field)` tuple may not repeat.
    """
    if items is None:
        return
    new_rows = []
    seen = set()
    for i, item in enumerate(items):
        tid = item.get('treatment_id')
        if not tid:
            raise ValueError(f"items[{i}]: treatment_id is required")
        try:
            t = Treatment.objects.get(id=tid)
        except Treatment.DoesNotExist:
            raise ValueError(f"items[{i}]: treatment {tid} not found")
        if t.user_id != user.id and not _is_admin(user):
            raise ValueError(f"items[{i}]: treatment {tid} is not yours")
        field = None
        fid = item.get('field_id')
        if fid:
            try:
                field = Field.objects.get(id=fid)
            except Field.DoesNotExist:
                raise ValueError(f"items[{i}]: field {fid} not found")
            if field.user_id != user.id and not _is_admin(user):
                raise ValueError(f"items[{i}]: field {fid} is not yours")
        key = (str(t.id), str(field.id) if field else None)
        if key in seen:
            raise ValueError(
                f"items[{i}]: duplicate (treatment, field) pair")
        seen.add(key)
        ordering = int(item.get('ordering', i))
        new_rows.append((ordering, t, field))
    WizardDraftTreatment.objects.filter(draft=draft).delete()
    WizardDraftTreatment.objects.bulk_create([
        WizardDraftTreatment(draft=draft, treatment=t, field=f, ordering=o)
        for o, t, f in new_rows
    ])


# ---------------------------------------------------------------------------
# List + Create
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class WizardDraftListAPI(View):
    """
    POST /api/wizard-drafts/

    Create a new draft. Body: ``{experiment_type, chat_id?}``.
    Returns the new draft (no fields/treatments/pairs attached yet).
    """

    def post(self, request):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)

        exp_type = (payload.get('experiment_type') or 'single').strip()
        valid_types = {'single', 'ensemble', 'sensitivity', 'monte_carlo', 'batch'}
        if exp_type not in valid_types:
            return JsonResponse(
                {'error': f'experiment_type must be one of {sorted(valid_types)}'},
                status=400,
            )
        chat_id = payload.get('chat_id')
        if chat_id:
            try:
                chat_id = uuid.UUID(str(chat_id))
            except ValueError:
                return JsonResponse({'error': 'invalid chat_id'}, status=400)

        draft = WizardDraft.objects.create(
            user=user,
            experiment_type=exp_type,
            chat_id=chat_id,
            current_step='step1',
            locked_steps=[],
            step_state={},
        )
        return JsonResponse(
            {'success': True, 'draft': _serialize_draft(draft)}, status=201)


@method_decorator(csrf_exempt, name='dispatch')
class WizardDraftActiveAPI(View):
    """
    GET /api/wizard-drafts/active/?chat_id=<uuid>

    Returns the user's most recently-updated open draft. Use ``?chat_id=``
    to scope to a specific chat session (chat-side flow). Returns 404 with
    ``{success: true, draft: null}`` shape so the caller can decide what
    to do (start fresh vs. resume).
    """

    def get(self, request):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        qs = WizardDraft.objects.filter(user=user, status='draft')
        chat_id = request.GET.get('chat_id')
        if chat_id:
            try:
                chat_uuid = uuid.UUID(chat_id)
            except ValueError:
                return JsonResponse({'error': 'invalid chat_id'}, status=400)
            qs = qs.filter(chat_id=chat_uuid)
        draft = qs.order_by('-updated_at').first()
        if not draft:
            return JsonResponse({'success': True, 'draft': None})
        return JsonResponse({'success': True, 'draft': _serialize_draft(draft)})


# ---------------------------------------------------------------------------
# Detail / Update / Delete
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class WizardDraftDetailAPI(View):
    """
    GET    /api/wizard-drafts/<uuid:id>/
    PATCH  /api/wizard-drafts/<uuid:id>/   — partial update
    DELETE /api/wizard-drafts/<uuid:id>/   — abandon (sets status='abandoned')

    Patchable fields:
      experiment_type, current_step, step_state, locked_steps,
      fields, treatments, pairs, chat_id
    """

    def get(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            draft = WizardDraft.objects.get(id=id)
        except WizardDraft.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, draft):
            return HttpResponseForbidden('not your draft')
        return JsonResponse({'success': True, 'draft': _serialize_draft(draft)})

    def patch(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            draft = WizardDraft.objects.get(id=id)
        except WizardDraft.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, draft):
            return HttpResponseForbidden('not your draft')
        if draft.status != 'draft':
            return JsonResponse(
                {'error': f"draft is {draft.status}; cannot modify"},
                status=409,
            )
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)

        with transaction.atomic():
            if 'experiment_type' in payload:
                exp_type = payload['experiment_type']
                if exp_type not in (
                    'single', 'ensemble', 'sensitivity',
                    'monte_carlo', 'batch',
                ):
                    return JsonResponse(
                        {'error': f"invalid experiment_type {exp_type!r}"},
                        status=400,
                    )
                draft.experiment_type = exp_type
            if 'current_step' in payload:
                draft.current_step = str(payload['current_step'])
            if 'locked_steps' in payload:
                ls = payload['locked_steps']
                if not isinstance(ls, list):
                    return JsonResponse(
                        {'error': 'locked_steps must be a list'},
                        status=400,
                    )
                draft.locked_steps = [str(s) for s in ls]
            if 'step_state' in payload:
                state = payload['step_state']
                if not isinstance(state, dict):
                    return JsonResponse(
                        {'error': 'step_state must be an object'},
                        status=400,
                    )
                # PATCH semantics: shallow merge into existing step_state so
                # callers can write one step at a time without clobbering
                # the rest.
                merged = dict(draft.step_state or {})
                merged.update(state)
                draft.step_state = merged
            if 'chat_id' in payload:
                cid = payload['chat_id']
                if cid is None or cid == '':
                    draft.chat_id = None
                else:
                    try:
                        draft.chat_id = uuid.UUID(str(cid))
                    except ValueError:
                        return JsonResponse(
                            {'error': 'invalid chat_id'}, status=400)

            try:
                if 'fields' in payload:
                    _apply_field_attachments(draft, payload['fields'], user)
                if 'treatments' in payload:
                    _apply_treatment_attachments(
                        draft, payload['treatments'], user)
                # Backward-compat: accept and translate the old `pairs`
                # shape from the pre-collapse data model. Each pair becomes
                # one WizardDraftTreatment row carrying both ids.
                if 'pairs' in payload:
                    pair_items = []
                    for i, p in enumerate(payload['pairs'] or []):
                        pair_items.append({
                            'treatment_id': p.get('treatment_id'),
                            'field_id': p.get('field_id'),
                            'ordering': p.get('ordering', i),
                        })
                    if pair_items:
                        _apply_treatment_attachments(draft, pair_items, user)
            except ValueError as e:
                return JsonResponse({'error': str(e)}, status=400)

            draft.save()

        return JsonResponse({'success': True, 'draft': _serialize_draft(draft)})

    def delete(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            draft = WizardDraft.objects.get(id=id)
        except WizardDraft.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, draft):
            return HttpResponseForbidden('not your draft')
        # Soft-delete: keep the row for auditability but mark it abandoned.
        draft.status = 'abandoned'
        draft.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'success': True})


# ---------------------------------------------------------------------------
# Lock / Edit (cascade clear)
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class WizardDraftLockAPI(View):
    """
    POST /api/wizard-drafts/<uuid:id>/lock/

    Body: ``{step_path: 'step3'}`` — adds the step to ``locked_steps``.
    Idempotent.
    """

    def post(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            draft = WizardDraft.objects.get(id=id)
        except WizardDraft.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, draft):
            return HttpResponseForbidden('not your draft')
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        step_path = (payload.get('step_path') or '').strip()
        if not step_path:
            return JsonResponse(
                {'error': 'step_path is required'}, status=400)
        locked = list(draft.locked_steps or [])
        if step_path not in locked:
            locked.append(step_path)
            draft.locked_steps = locked
            draft.save(update_fields=['locked_steps', 'updated_at'])
        return JsonResponse({'success': True, 'draft': _serialize_draft(draft)})


@method_decorator(csrf_exempt, name='dispatch')
class WizardDraftEditAPI(View):
    """
    POST /api/wizard-drafts/<uuid:id>/edit/

    Body: ``{step_path: 'step2'}``. Unlocks the named step *and* every
    downstream step (in `_TOP_LEVEL_ORDER`); clears their `step_state`
    keys; detaches any field/treatment/pair attachments owned by those
    downstream steps.

    The strict cascade rule keeps the draft consistent: if the user
    re-edits Step 2, all of Steps 3-5 get cleared (fields, treatments,
    pairs, and the Step 2 state itself stays so they can edit it).
    """

    def post(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            draft = WizardDraft.objects.get(id=id)
        except WizardDraft.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, draft):
            return HttpResponseForbidden('not your draft')
        if draft.status != 'draft':
            return JsonResponse(
                {'error': f"draft is {draft.status}; cannot edit"},
                status=409,
            )
        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'invalid JSON'}, status=400)
        step_path = (payload.get('step_path') or '').strip()
        if not step_path:
            return JsonResponse(
                {'error': 'step_path is required'}, status=400)
        head = _top_level(step_path)
        if head not in _TOP_LEVEL_ORDER:
            return JsonResponse(
                {'error': f'unknown step {step_path!r}'}, status=400)

        downstream = _downstream_of(step_path)
        clear_prefixes = {head, *downstream}

        with transaction.atomic():
            # 1. Drop matching prefixes from locked_steps.
            draft.locked_steps = [
                s for s in (draft.locked_steps or [])
                if _top_level(s) not in clear_prefixes
            ]

            # 2. Clear step_state keys whose top-level prefix matches.
            new_state = {
                k: v for k, v in (draft.step_state or {}).items()
                if _top_level(k) not in downstream
            }
            # We keep the top-level head step's state so the user can edit
            # it; only strictly-downstream state is cleared.
            draft.step_state = new_state

            # 3. Detach attachments for downstream steps. Fields come from
            #    Step 2; treatment rows (which carry their own field FK
            #    after the pair-collapse) come from Step 4.
            if 'step3' in clear_prefixes:
                WizardDraftField.objects.filter(draft=draft).delete()
            if 'step4' in clear_prefixes:
                WizardDraftTreatment.objects.filter(draft=draft).delete()

            draft.current_step = head
            draft.save()

        return JsonResponse({
            'success': True,
            'cleared_prefixes': sorted(downstream),
            'draft': _serialize_draft(draft),
        })


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name='dispatch')
class WizardDraftSubmitAPI(View):
    """
    POST /api/wizard-drafts/<uuid:id>/submit/

    Resolve the draft into a run payload, enforce the 99-treatment cap,
    create the orchestrator Chat, and queue the Celery task. Implementation
    delegates to ``services.draft_submit.submit_draft`` so the wiring stays
    out of the view.
    """

    def post(self, request, id):
        user = request.user
        if not user.is_authenticated:
            return HttpResponseForbidden('authentication required')
        try:
            draft = WizardDraft.objects.get(id=id)
        except WizardDraft.DoesNotExist:
            return JsonResponse({'error': 'not found'}, status=404)
        if not _can_access(user, draft):
            return HttpResponseForbidden('not your draft')
        if draft.status != 'draft':
            return JsonResponse(
                {'error': f"draft is {draft.status}; cannot submit"},
                status=409,
            )

        try:
            from dssat_agent.services.draft_submit import submit_draft
            result = submit_draft(draft, user=user)
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        except Exception as e:
            logger.exception("Draft submit failed")
            return JsonResponse({'error': str(e)}, status=500)

        return JsonResponse({'success': True, **result})
