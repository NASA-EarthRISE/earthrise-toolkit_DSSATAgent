"""
WizardDraft -> run-pipeline submission.

Translates a WizardDraft (plus its ordered Field, Treatment, and Pair
attachments) into the legacy ``wizard_params`` + ``extracted_params`` shape
that the existing run pipeline (``services.workflow.run_full_simulation``,
``services.ensemble_service.run_ensemble``, ``services.monte_carlo_service``,
``services.batch_service``) already understands. Reusing the legacy shape
keeps the run pipeline untouched while the wizard front-end migrates to the
new ORM-backed draft model.

The submit flow:

  1. Validate that the draft is in a runnable state (has fields,
     has at least one treatment unless single).
  2. Compute the implied DSSAT treatment count and reject when > 99.
  3. Build legacy params + wizard_params, reading lat/lon/soil/weather from
     the attached Field rows and crop/cultivar/planting/management/IC/SC
     from the attached Treatment rows.
  4. Mark the draft submitted, create the orchestrator Chat (locked to the
     DSSAT agent), append the synthesized "Run experiment" message, and
     queue ``run_wizard_experiment_task``.
  5. The Celery task runs the existing pipeline; once the resulting
     ExperimentSession is created in the worker, follow-up wiring (the
     M2M back to Field/Treatment + ``ExperimentSession.draft_id``) happens
     via ``link_experiment_to_draft`` called from the worker.

Treatment-count math per experiment type
----------------------------------------

  * single        — 1 (must have exactly 1 field, 1 treatment)
  * ensemble      — len(treatments)  (1 field, N treatments)
  * sensitivity   — len(treatments)  (1 field, N generated treatments;
                                       the SPA's Cartesian generator
                                       produces the explicit list)
  * monte_carlo   — len(fields)      (N fields, 1 template treatment;
                                       per-field in-situ planting resolved
                                       at run time inside the MC service)
  * batch         — len(pairs)       (explicit field x treatment matrix)
"""

from __future__ import annotations

import datetime
import logging
import os
from typing import Dict, List, Optional, Tuple

from django.db import transaction

from dssat_agent.models import (
    Field, Treatment, WizardDraft,
    WizardDraftField, WizardDraftTreatment,
)

logger = logging.getLogger(__name__)

MAX_TREATMENTS = 99  # DSSATBatch hard cap (see DSSATTools/batch.py:147).


# ===========================================================================
# Public API
# ===========================================================================

def submit_draft(draft: WizardDraft, *, user) -> Dict:
    """Translate, validate, and dispatch the draft.

    Returns ``{chat_id, redirect_url, treatment_count, experiment_type}``.
    Raises ``ValueError`` for validation failures (HTTP 400 territory).
    """
    if draft.status != 'draft':
        raise ValueError(f"draft is {draft.status}, cannot submit")

    fields = _ordered_fields(draft)
    # `dt_rows` is the unified list of `(treatment, field)` pair rows. Each
    # row maps 1:1 to a *TREATMENTS row in the FILEX. The legacy `treatments`
    # variable below is the de-duplicated catalog of unique Treatment
    # records — kept because the existing run-pipeline translators expect
    # it. (For ensemble/sensitivity that's the same as `dt_rows` minus the
    # field; for MC it's a list of one; for batch it can repeat.)
    dt_rows = _ordered_treatment_rows(draft)
    treatments = _unique_treatments(dt_rows)

    treatment_count = _compute_treatment_count(
        draft.experiment_type, fields, dt_rows)
    if treatment_count == 0:
        raise ValueError(
            "draft has nothing to run: attach at least one Field and "
            "at least one Treatment (with a paired field).")
    if treatment_count > MAX_TREATMENTS:
        raise ValueError(
            f"draft would produce {treatment_count} treatments, exceeds "
            f"DSSAT cap of {MAX_TREATMENTS}")

    extracted_params, wizard_params = _build_legacy_params(
        draft, fields, treatments, dt_rows)

    # Snapshot draft.step_state into wizard_params so the message formatter
    # has access to the full SPA state when synthesizing the user-visible
    # summary, even for fields the legacy shape doesn't carry.
    wizard_params['_draft_id'] = str(draft.id)
    wizard_params['_draft_step_state'] = dict(draft.step_state or {})

    chat_id, redirect_url = _create_chat_and_queue(
        params=extracted_params,
        wizard_params=wizard_params,
        user=user,
        draft=draft,
    )

    with transaction.atomic():
        draft.status = 'submitted'
        if not draft.chat_id:
            import uuid as _uuid
            draft.chat_id = _uuid.UUID(chat_id)
        draft.save(update_fields=['status', 'chat_id', 'updated_at'])

    return {
        'chat_id': chat_id,
        'redirect_url': redirect_url,
        'treatment_count': treatment_count,
        'experiment_type': draft.experiment_type,
    }


def link_experiment_to_draft(experiment_id, draft_id):
    """Associate a freshly-created ExperimentSession with the source draft.

    Called from the Celery worker after the run completes so admin /
    library views can trace back from a result to the wizard build that
    produced it.
    """
    from dssat_agent.models import (
        ExperimentSession, ExperimentSessionField, ExperimentSessionTreatment,
    )

    try:
        draft = WizardDraft.objects.get(id=draft_id)
        experiment = ExperimentSession.objects.get(id=experiment_id)
    except (WizardDraft.DoesNotExist, ExperimentSession.DoesNotExist):
        logger.warning(
            "link_experiment_to_draft: draft=%s or experiment=%s missing",
            draft_id, experiment_id)
        return

    experiment.draft = draft
    experiment.save(update_fields=['draft'])

    fields = _ordered_fields(draft)
    # Treatment association uses the unique-treatment list derived from the
    # draft's pair rows via ``_ordered_treatment_rows(draft)`` — the same path
    # the wizard submit flow uses above.
    dt_rows = _ordered_treatment_rows(draft)
    treatments = _unique_treatments(dt_rows)
    ExperimentSessionField.objects.bulk_create([
        ExperimentSessionField(experiment=experiment, field=f, ordering=i)
        for i, f in enumerate(fields)
    ])
    ExperimentSessionTreatment.objects.bulk_create([
        ExperimentSessionTreatment(experiment=experiment, treatment=t, ordering=i)
        for i, t in enumerate(treatments)
    ])


# ===========================================================================
# Internal — load draft attachments
# ===========================================================================

def _ordered_fields(draft):
    return [
        df.field for df in (
            WizardDraftField.objects
            .filter(draft=draft)
            .select_related('field__soil_profile')
            .order_by('ordering')
        )
    ]


def _ordered_treatment_rows(draft):
    """Each WizardDraftTreatment row is one (Field, Treatment) pair = one
    FILEX *TREATMENTS row. Returned in attachment order."""
    return list(
        WizardDraftTreatment.objects
        .filter(draft=draft)
        .select_related('treatment', 'field__soil_profile')
        .order_by('ordering')
    )


def _unique_treatments(dt_rows):
    """Distinct Treatment records, preserving first-seen order."""
    seen = set()
    out = []
    for dt in dt_rows:
        if dt.treatment_id in seen:
            continue
        seen.add(dt.treatment_id)
        out.append(dt.treatment)
    return out


# ===========================================================================
# Internal — count + validation
# ===========================================================================

def _compute_treatment_count(exp_type, fields, dt_rows):
    """Count of FILEX *TREATMENTS rows the run will produce.

    Each ``WizardDraftTreatment`` row is one (field, treatment) pair, so
    treatment_count is just ``len(dt_rows)`` — but we keep the per-type
    structural validation to give the user a clearer error than "missing
    rows".
    """
    n = len(dt_rows)
    unique_treatment_ids = {dt.treatment_id for dt in dt_rows}
    unique_field_ids = {dt.field_id for dt in dt_rows if dt.field_id}

    if exp_type == 'single':
        if n != 1 or len(unique_treatment_ids) != 1 or len(unique_field_ids) != 1:
            raise ValueError(
                "single experiment requires exactly 1 (field, treatment) pair")
    elif exp_type in ('ensemble', 'sensitivity'):
        if len(unique_field_ids) > 1:
            raise ValueError(
                f"{exp_type} expects 1 field across all treatments "
                f"(got {len(unique_field_ids)})")
        if not n:
            raise ValueError(f"{exp_type} requires at least one treatment")
    elif exp_type == 'monte_carlo':
        if len(unique_treatment_ids) > 1:
            raise ValueError(
                "monte_carlo expects exactly 1 template treatment "
                f"(got {len(unique_treatment_ids)})")
        if not n:
            raise ValueError("monte_carlo requires at least one paired field")
    elif exp_type == 'batch':
        if not n:
            raise ValueError("batch requires at least one (field, treatment) pair")
    else:
        raise ValueError(f"unknown experiment_type: {exp_type!r}")
    return n


# ===========================================================================
# Internal — translate to legacy shape
# ===========================================================================

def _field_to_dict(field: Field) -> Dict:
    """Project a Field row into the per-field block the legacy pipeline reads."""
    out = {
        'latitude': field.latitude,
        'longitude': field.longitude,
        'elevation': field.elevation,
        'weather_source': field.weather_source,
        'location_name': field.location_label or None,
    }
    if field.soil_profile_id:
        out['soil_id'] = field.soil_profile.soil_id
    elif field.inline_soil:
        out['inline_soil'] = field.inline_soil
    return {k: v for k, v in out.items() if v is not None}


def _treatment_to_dict(t: Treatment, year: Optional[int] = None,
                        override_pdate: Optional[str] = None) -> Dict:
    """Project a Treatment row into the legacy shared+per-treatment dict.

    Treatments are stored DOY/DAP-canonical (year-less): planting is a
    Julian day, management events are days-after-planting integers, and
    simulation_controls carries ``start_offset_days`` instead of an absolute
    ``start_date``. This translator materialises absolute dates at submit
    time using the supplied ``year`` (typically from
    ``draft.step_state['step1'].year``); when ``year`` is missing we leave
    DOY/DAP intact so the legacy run-pipeline can fail loudly instead of
    silently planting in 1970.

    ``override_pdate`` (when supplied) bypasses normal DOY+year
    materialisation and uses the explicit absolute date instead. The
    wizard's Step 4 lock pre-resolves Auto-mode (in-situ raster) planting
    dates per (treatment, field) pair and stores them in
    ``step_state.step4.resolved_planting_dates`` — the build-* helpers
    pass that resolved date in here so the run pipeline doesn't have to
    re-run the lookup at submit time.
    """
    out = {
        'crop_code': t.crop_code,
        'crop_name': t.name,  # display label
        'cultivar_code': t.cultivar_code or None,
        'dssat_model': t.dssat_model or None,
    }
    p = t.planting or {}

    # Resolve planting date — explicit override (Auto-mode resolved
    # value) wins over the planting block's own DOY.
    if override_pdate:
        pdate = override_pdate
    else:
        pdate = _resolve_planting_date(p, year)
    if pdate is not None:
        out['planting_date'] = pdate

    for legacy_key, json_key in (
        ('plant_population', 'ppop'),
        ('row_spacing', 'plrs'),
        ('planting_method', 'plme'),
    ):
        v = p.get(json_key, p.get(legacy_key))
        if v is not None:
            out[legacy_key] = v

    if t.harvest:
        out['harvest'] = _materialise_harvest(t.harvest, pdate)
    if t.initial_conditions:
        out['initial_conditions'] = t.initial_conditions
    if t.simulation_controls:
        out['simulation_controls'] = _materialise_simulation_controls(
            t.simulation_controls, pdate)
    if t.fertilizer:
        out['fertilizer'] = _materialise_events(t.fertilizer, pdate, 'fdap', 'fdate')
    if t.irrigation:
        out['irrigation'] = _materialise_irrigation(t.irrigation, pdate)
    if t.residue:
        out['residue'] = _materialise_events(t.residue, pdate, 'rdap', 'rdate')
    if t.chemical:
        out['chemical'] = _materialise_events(t.chemical, pdate, 'cdap', 'cdate')
    if t.tillage:
        out['tillage'] = _materialise_events(t.tillage, pdate, 'tdap', 'tdate')
    if t.mow:
        out['mow'] = t.mow  # forage-only; left untouched
    return {k: v for k, v in out.items() if v is not None}


def _resolve_planting_date(planting: Dict, year: Optional[int]) -> Optional[str]:
    """Return ``YYYY-MM-DD`` for the planting event, preferring an explicit
    absolute ``pdate``/``planting_date`` when present, then materialising
    from ``pdoy`` + ``year``."""
    explicit = planting.get('pdate') or planting.get('planting_date')
    if explicit:
        return str(explicit)
    pdoy = planting.get('pdoy')
    if pdoy is not None and year is not None:
        try:
            return _date_from_doy(int(year), int(pdoy)).isoformat()
        except (ValueError, OverflowError):
            return None
    return None


def _date_from_doy(year: int, doy: int) -> datetime.date:
    """Convert a (year, day-of-year) pair into a date. Clamps DOY to the
    valid range so an off-by-one on the wizard side doesn't crash submit."""
    doy = max(1, min(doy, 366))
    return datetime.date(year, 1, 1) + datetime.timedelta(days=doy - 1)


def _shift_date(pdate_iso: Optional[str], days: int) -> Optional[str]:
    if not pdate_iso:
        return None
    try:
        d = datetime.date.fromisoformat(pdate_iso)
    except (ValueError, TypeError):
        return None
    return (d + datetime.timedelta(days=int(days))).isoformat()


def _materialise_events(events: List[Dict], pdate_iso: Optional[str],
                        dap_key: str, date_key: str) -> List[Dict]:
    """Convert each ``{<dap_key>: int}`` event to ``{<date_key>: 'YYYY-MM-DD'}``.

    Events that already carry an explicit date pass through unchanged. We
    leave the DAP integer in the payload too (alongside the materialised
    date) so debuggers can verify the conversion downstream."""
    out = []
    for ev in (events or []):
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        ev = dict(ev)
        if not ev.get(date_key) and dap_key in ev and ev[dap_key] is not None:
            ev[date_key] = _shift_date(pdate_iso, ev[dap_key])
        out.append(ev)
    return out


def _materialise_irrigation(irr, pdate_iso):
    """Irrigation is either {method: automatic, ...}, {method: fixed, events:[]},
    or a bare event list. Materialise dates inside its event sub-list."""
    if isinstance(irr, list):
        return _materialise_events(irr, pdate_iso, 'idap', 'idate')
    if isinstance(irr, dict):
        out = dict(irr)
        if 'events' in out and isinstance(out['events'], list):
            out['events'] = _materialise_events(
                out['events'], pdate_iso, 'idap', 'idate')
        return out
    return irr


def _materialise_harvest(h, pdate_iso):
    """Harvest may carry ``option: 'on_date'`` with a date, ``option: 'dap'``
    with an int, or no date at all (option=maturity). Resolve to a usable
    shape for the legacy pipeline."""
    if not isinstance(h, dict):
        return h
    out = dict(h)
    opt = out.get('option')
    if opt == 'dap' and out.get('dap') is not None and pdate_iso:
        out['date'] = _shift_date(pdate_iso, out['dap'])
    return out


def _materialise_simulation_controls(sc, pdate_iso):
    """Compute an absolute ``start_date`` from ``start_offset_days`` (default
    -30 days from planting) when no explicit start_date is set."""
    if not isinstance(sc, dict):
        return sc
    out = dict(sc)
    if not out.get('start_date') and not out.get('sdate'):
        offset = out.get('start_offset_days', -30)
        if pdate_iso:
            shifted = _shift_date(pdate_iso, offset)
            if shifted:
                out['start_date'] = shifted
                out['sdate'] = shifted
    return out


def _build_legacy_params(draft, fields, treatments, dt_rows) -> Tuple[Dict, Dict]:
    """Return ``(extracted_params, wizard_params)`` in the legacy shape.

    Translators read from `dt_rows` (the unified pair list); for the simpler
    cardinalities they pull the unique field / unique treatment for free.
    The experiment ``year`` (top-level Step 1 selection) drives the
    DOY/DAP -> absolute-date materialisation inside `_treatment_to_dict`.

    ``resolved`` is the per-pair Auto-mode planting-date dict pre-computed
    by the SPA at Step 4 lock time. Each value is
    ``{date: 'YYYY-MM-DD', doy: int, source, lat, lon}`` keyed by
    ``"<treatment_id>:<field_id>"``. The build-* helpers below pass the
    matching ``date`` into ``_treatment_to_dict`` as ``override_pdate``.
    """
    exp_type = draft.experiment_type
    year = _draft_year(draft)
    resolved = ((draft.step_state or {}).get('step4') or {}) \
        .get('resolved_planting_dates') or {}
    if exp_type == 'single':
        dt = dt_rows[0]
        return _build_single(dt.field, dt.treatment, year,
                             resolved=resolved)
    if exp_type == 'ensemble':
        return _build_ensemble(dt_rows[0].field, treatments, year,
                               resolved=resolved, dt_rows=dt_rows)
    if exp_type == 'sensitivity':
        return _build_sensitivity(draft, dt_rows[0].field, treatments, year,
                                  resolved=resolved, dt_rows=dt_rows)
    if exp_type == 'monte_carlo':
        return _build_monte_carlo(
            draft, [dt.field for dt in dt_rows], treatments[0], year,
            resolved=resolved, dt_rows=dt_rows)
    if exp_type == 'batch':
        return _build_batch(dt_rows, year, resolved=resolved)
    raise ValueError(f"unknown experiment_type: {exp_type!r}")


def _resolved_key(treatment_id, field_id) -> str:
    return f"{treatment_id}:{field_id}"


def _resolved_pdate(resolved: Dict, treatment_id, field_id) -> Optional[str]:
    """Pick the resolved Auto-mode planting date for a (treatment, field)
    pair, or None if the pair isn't an Auto-mode entry."""
    if not resolved:
        return None
    entry = resolved.get(_resolved_key(treatment_id, field_id))
    if not entry:
        return None
    return entry.get('date')


def _draft_year(draft) -> Optional[int]:
    step1 = (draft.step_state or {}).get('step1') or {}
    y = step1.get('year')
    try:
        return int(y) if y is not None else None
    except (TypeError, ValueError):
        return None


def _build_single(field: Field, treatment: Treatment, year: Optional[int],
                   resolved=None):
    field_d = _field_to_dict(field)
    override = _resolved_pdate(resolved or {}, treatment.id, field.id)
    trt_d = _treatment_to_dict(treatment, year=year, override_pdate=override)
    flat = {**field_d, **trt_d, 'experiment_type': 'single'}
    extracted = _extract_summary(flat)
    return extracted, flat


def _build_ensemble(field: Field, treatments, year: Optional[int],
                     resolved=None, dt_rows=None):
    """Map to the legacy {experiment_type: 'ensemble', base: ..., treatments: [...]} shape."""
    field_d = _field_to_dict(field)
    first = treatments[0]
    first_override = _resolved_pdate(resolved or {}, first.id, field.id)
    base = {**field_d, **_treatment_to_dict(
        first, year=year, override_pdate=first_override)}
    base.pop('crop_name', None)  # legacy ensemble doesn't need this in base

    treatment_dicts = []
    for t in treatments:
        # Each ensemble treatment is paired with the single field, so the
        # override key is (t.id, field.id).
        override = _resolved_pdate(resolved or {}, t.id, field.id)
        td = _treatment_to_dict(t, year=year, override_pdate=override)
        td['name'] = t.name
        treatment_dicts.append(td)

    payload = {
        'experiment_type': 'ensemble',
        'base': base,
        'treatments': treatment_dicts,
    }
    extracted = _extract_summary(base)
    extracted['experiment_type'] = 'ensemble'
    return extracted, payload


def _build_sensitivity(draft, field: Field, treatments, year: Optional[int],
                        resolved=None, dt_rows=None):
    extracted, payload = _build_ensemble(
        field, treatments, year, resolved=resolved, dt_rows=dt_rows)
    payload['experiment_type'] = 'sensitivity'
    # The sensitivity sweep config is stored under step_state['step4']['sensitivity'],
    # not as a flat 'step4.sensitivity' key.
    step4 = (draft.step_state or {}).get('step4') or {}
    payload['sensitivity'] = step4.get('sensitivity') or {}
    extracted['experiment_type'] = 'sensitivity'
    return extracted, payload


def _build_monte_carlo(draft, fields, template: Treatment, year: Optional[int],
                        resolved=None, dt_rows=None):
    """Map to the legacy {experiment_type: 'monte_carlo', ...} shape.

    Step 2's lock hook materialises the grid into one ``Field`` row per
    sampled point and Step 3 fills in per-field soil + elevation, so the
    submit payload carries the explicit per-point list under ``fields``.
    The MC service consumes that list directly (skipping its own
    grid-generation + sampling pass) which is what lets per-field choices
    flow through to DSSAT.

    Spatial config (center/bbox/admin + density knobs) is still passed
    through for downstream metadata (and so a draft re-submitted after the
    SPA gains the ability to materialise on-demand still has the geometry
    around).
    """
    step2 = (draft.step_state or {}).get('step2', {}) or {}
    step3 = (draft.step_state or {}).get('step3', {}) or {}
    template_d = _treatment_to_dict(template, year=year)

    field_dicts = []
    for f in (fields or []):
        fd = _field_to_dict(f)
        # MC has 1 template treatment × N fields, so per-pair Auto-mode
        # resolution lands on each Field record. The MC service reads
        # `pdate` off the field dict and overrides the shared template
        # planting date for that point.
        per_pair = _resolved_pdate(resolved or {}, template.id, f.id)
        if per_pair:
            fd['pdate'] = per_pair
        field_dicts.append(fd)
    # MC takes one weather source for all fields; Step 3 wrote the same
    # value to every Field, but defend the lookup by falling back to step3.
    shared_weather = (
        (field_dicts[0].get('weather_source') if field_dicts else None)
        or step3.get('weather_source')
    )

    payload = {
        'experiment_type': 'monte_carlo',
        # Spatial config — kept for metadata / debugging; the MC service
        # will use `fields` (below) as the canonical sample list.
        'spatial_mode': step2.get('spatial_mode'),
        'center': step2.get('center'),
        'radius_km': step2.get('radius_km'),
        'bbox': step2.get('bbox'),
        'admin_name': step2.get('admin_name'),
        'admin_level': step2.get('admin_level'),
        'admin_country': step2.get('admin_country'),
        'admin_parent_admin1': step2.get('admin_parent_admin1'),
        'grid_spacing': step2.get('grid_spacing'),
        'n_points': step2.get('n_points') or step2.get('nens'),
        'nens': step2.get('nens') or len(field_dicts),
        'sampling_strategy': step2.get('sampling_strategy'),
        'weather_source': shared_weather,
        # Per-field in-situ planting toggle (Step 4 MC config). Resolved
        # at run time by the MC service.
        'in_situ_selections': step3.get('in_situ_selections') or {},
        # Explicit per-point Field list — the MC service prefers this over
        # regenerating the grid, so per-field soil/elev set in Step 3 ends
        # up in DSSAT.
        'fields': field_dicts,
        # Template treatment is flattened so the legacy MC builder finds
        # its planting + management blocks at the top level.
        **template_d,
    }
    # Surface a representative location/soil/elev at the top level so
    # legacy summary helpers (and ConversationMemory.confirmed_params) have
    # something to show. The per-point values still drive the actual run
    # via the `fields` list above.
    if field_dicts:
        rep = field_dicts[0]
        for k in ('latitude', 'longitude', 'elevation', 'soil_id', 'inline_soil'):
            if rep.get(k) is not None:
                payload.setdefault(k, rep[k])

    # Surface a representative ``planting_date`` at the top level. The MC
    # service drives per-point planting from each ``fields[i].pdate`` (so
    # Auto-mode treatments still resolve correctly), but
    # ``run_full_simulation``'s pre-build phase (location resolution +
    # weather prep) reads ``params.get('planting_date')`` to decide the
    # weather window. Without a top-level value, ``prepare_weather``
    # reports "Invalid planting date:" and the run aborts before the MC
    # service ever sees the per-field dates. Prefer:
    #   1. The template's resolved date (Fixed-Date mode), if any.
    #   2. The first field's per-pair Auto-mode resolved date, if any.
    if 'planting_date' not in payload:
        first_pdate = None
        for fd in field_dicts:
            pd = fd.get('pdate')
            if pd:
                first_pdate = pd
                break
        if first_pdate:
            payload['planting_date'] = first_pdate

    payload = {k: v for k, v in payload.items() if v is not None}

    extracted = _extract_summary(payload)
    extracted['experiment_type'] = 'monte_carlo'
    return extracted, payload


def _build_batch(dt_rows, year, resolved=None):
    """Map K (Field, Treatment) draft-treatment rows into a batch payload.

    Each row becomes one fully-resolved per-pair params dict; the run
    pipeline (``_handle_run_batch`` + ``run_batch_async`` +
    ``create_batch_children_from_pairs``) creates one child
    ``ExperimentSession`` per dict. No shared template flattening — the
    matrix model where different protocols apply to different fields is
    represented natively.
    """
    pair_params = []
    for dt in dt_rows:
        f, t = dt.field, dt.treatment
        if f is None or t is None:
            continue
        override = _resolved_pdate(resolved or {}, t.id, f.id)
        merged = {
            **_field_to_dict(f),
            **_treatment_to_dict(t, year=year, override_pdate=override),
            # Each child runs as a single experiment in the legacy pipeline.
            'experiment_type': 'single',
        }
        # Surface a per-pair label so the batch detail page reads cleanly.
        if f.location_label:
            merged['location_name'] = f.location_label
        elif f.name:
            merged['location_name'] = f.name
        pair_params.append(merged)

    if not pair_params:
        raise ValueError(
            "batch draft has no usable (field, treatment) pairs — "
            "check the matrix on Step 4.")

    payload = {
        'experiment_type': 'batch',
        'sub_experiment_type': 'single',
        'pair_params': pair_params,
    }
    extracted = _extract_summary(pair_params[0])
    extracted['experiment_type'] = 'batch'
    return extracted, payload


def _extract_summary(flat: Dict) -> Dict:
    """Project the flat run params into the small dict used for ConversationMemory.confirmed_params."""
    sc = flat.get('simulation_controls') or {}
    summary = {
        'crop_code': flat.get('crop_code'),
        'crop_name': flat.get('crop_name'),
        'cultivar_code': flat.get('cultivar_code'),
        'latitude': flat.get('latitude'),
        'longitude': flat.get('longitude'),
        'elevation': flat.get('elevation'),
        'planting_date': flat.get('planting_date'),
        'plant_population': flat.get('plant_population'),
        'row_spacing': flat.get('row_spacing'),
        'soil_id': flat.get('soil_id'),
        'inline_soil': flat.get('inline_soil'),
        'weather_source': flat.get('weather_source'),
        'experiment_type': flat.get('experiment_type'),
        'fertilizer': flat.get('fertilizer') or None,
        'irrigation': flat.get('irrigation') or None,
        'harvest': flat.get('harvest') or None,
        'initial_conditions': flat.get('initial_conditions') or None,
        'num_years': sc.get('num_years', 1),
    }
    summary = {k: v for k, v in summary.items() if v is not None}
    if summary.get('latitude') and not summary.get('location_name'):
        summary['location_name'] = (
            f"{summary['latitude']}, {summary.get('longitude')}")
    return summary


# ===========================================================================
# Internal — chat creation + Celery dispatch (shared with legacy endpoint)
# ===========================================================================

def _create_chat_and_queue(*, params: Dict, wizard_params: Dict,
                           user, draft: Optional[WizardDraft] = None
                           ) -> Tuple[str, str]:
    """Create the orchestrator Chat, append the wizard message, queue Celery.

    Shared between the new ``submit_draft`` flow and the legacy
    ``ExperimentSubmitAPI`` so both paths produce the same chat shape.
    """
    from earthrise_agents_base.models import Chat, Message
    from earthrise_agents_base.agent.chat_agent import ConversationMemory
    from dssat_agent.tasks import run_wizard_experiment_task
    # Local import to avoid circular dep at module load (views_api imports
    # this file too via the WizardDraftSubmitAPI delegate).
    from dssat_agent.explorer.views_api import _format_wizard_submission_message

    # Resolve DSSAT agent label so the chat can be locked to it.
    try:
        from earthrise_agents_base.agent.discovery import discover_agents
        dssat_label = next(
            (label for label, meta in discover_agents().items()
             if meta.get('app_label') == 'dssat_agent'),
            None,
        )
    except Exception:
        dssat_label = None

    memory = ConversationMemory()
    memory.confirmed_params = params
    memory.wizard_params = wizard_params

    crop_label = (
        params.get('crop_name')
        or params.get('crop_code')
        or 'Experiment')

    chat = Chat.objects.create(
        title=f'Wizard: {crop_label}',
        agent_state={'wizard_params': wizard_params,
                     'draft_id': str(draft.id) if draft else None},
        agent_memory=memory.to_dict(),
        enabled_agents=[dssat_label] if dssat_label else [],
        agents_locked=bool(dssat_label),
    )

    # Synthesize the structured user message (same helper the legacy
    # submit endpoint already uses).
    user_content = _format_wizard_submission_message(wizard_params, params)
    Message.objects.create(
        chat=chat,
        message_type='user',
        content=user_content,
        status='completed',
    )
    assistant_msg = Message.objects.create(
        chat=chat,
        message_type='assistant',
        content='',
        status='pending',
    )

    # Persist user message into chat memory so later turns see it.
    mem = chat.agent_memory or {}
    mem_messages = list(mem.get('messages') or [])
    mem_messages.append({'role': 'user', 'content': user_content})
    mem['messages'] = mem_messages
    chat.agent_memory = mem
    chat.is_processing = True
    chat.save(update_fields=['is_processing', 'agent_memory'])

    user_id = user.id if user and user.is_authenticated else None
    task = run_wizard_experiment_task.delay(
        str(chat.id),
        str(assistant_msg.id),
        params,
        wizard_params,
        user_id,
    )
    assistant_msg.task_id = task.id
    assistant_msg.save(update_fields=['task_id'])

    subpath = os.environ.get('SUBPATH', '')
    base_path = f'/{subpath}' if subpath else ''
    redirect_url = f'{base_path}/chats/{chat.id}/'
    return str(chat.id), redirect_url
