"""
Cultivar and ecotype CRUD service.

Provides DB-backed cultivar/ecotype management, the bridge between Django
models and DSSATTools Crop instances, and DSSAT binary validation.
"""

import logging
import math

from DSSATTools.base.partypes import NumberType, DescriptionType
from DSSATTools.crop_registry import (
    CROP_MODEL_CLASSES, CROP_CODE_MODELS, get_crop_class,
    get_model_display_name,
)

from dssat_agent.models import DSSATCultivar, DSSATEcotype
from .crop_service import CROP_NAMES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parameter extraction (from DSSATTools Crop instances → JSON-safe dicts)
# ---------------------------------------------------------------------------

def _to_json_safe(val):
    """Convert a DSSATTools typed value to a JSON-serializable Python type."""
    if val is None:
        return None
    if hasattr(val, 'item'):
        val = val.item()
    if isinstance(val, float):
        if math.isnan(val) or math.isinf(val):
            return None
        return val
    if isinstance(val, (int, bool)):
        return val
    if isinstance(val, str):
        s = val.strip()
        return s if s and s != '-99' else None
    if hasattr(val, 'tolist'):
        return val.tolist()
    return str(val)


def extract_cultivar_params(crop_instance, cul_dtypes):
    """Extract cultivar parameters as a JSON-serializable dict.

    Skips the ``eco#`` key (handled via FK) and the name/expno fields
    (stored as dedicated model fields).
    """
    params = {}
    skip = {'eco#', 'vrname', 'var-name', 'expno', 'exp#'}
    for name in cul_dtypes:
        if name in skip:
            continue
        try:
            params[name] = _to_json_safe(crop_instance[name])
        except (KeyError, TypeError):
            pass
    return params


def extract_ecotype_params(eco_obj, eco_dtypes):
    """Extract ecotype parameters as a JSON-serializable dict.

    Skips the name field (stored as dedicated model field).
    """
    params = {}
    skip = {'econame', 'eco-name'}
    for name in eco_dtypes:
        if name in skip:
            continue
        try:
            params[name] = _to_json_safe(eco_obj[name])
        except (KeyError, TypeError):
            pass
    return params


def _get_cultivar_name(crop_instance, cul_dtypes):
    """Extract the human-readable cultivar name from the instance."""
    for key in ('vrname', 'var-name'):
        if key in cul_dtypes:
            try:
                val = str(crop_instance[key]).strip()
                if val and val != '-99':
                    return val
            except (KeyError, TypeError):
                pass
    return ""


def _get_ecotype_name(eco_obj, eco_dtypes):
    """Extract the human-readable ecotype name from the instance."""
    for key in ('econame', 'eco-name'):
        if key in eco_dtypes:
            try:
                val = str(eco_obj[key]).strip()
                if val and val != '-99':
                    return val
            except (KeyError, TypeError):
                pass
    return ""


# ---------------------------------------------------------------------------
# Schema introspection (for dynamic forms)
# ---------------------------------------------------------------------------

def get_cultivar_schema(crop_code, dssat_model=None):
    """Return a JSON-friendly schema describing cultivar + ecotype fields."""
    cls = get_crop_class(crop_code, dssat_model)
    smodel = dssat_model or cls.smodel

    def _field_defs(dtypes, fmts, skip_keys):
        fields = []
        for name in dtypes:
            if name in skip_keys:
                continue
            dtype = dtypes[name]
            fmt = fmts.get(name, '')
            field = {'name': name, 'label': name.upper()}
            if dtype is NumberType:
                field['type'] = 'number'
                # Extract precision from format like '>5.3f'
                if '.' in fmt:
                    try:
                        field['precision'] = int(fmt.split('.')[1].rstrip('f'))
                    except (IndexError, ValueError):
                        field['precision'] = 1
                else:
                    field['precision'] = 0
            else:
                field['type'] = 'text'
                # Extract max width
                try:
                    w = int(fmt.lstrip('.<>').split('.')[0])
                    field['max_length'] = w
                except (ValueError, IndexError):
                    field['max_length'] = 16
            fields.append(field)
        return fields

    cul_fields = _field_defs(
        cls.cul_dtypes, cls.cul_pars_fmt,
        skip_keys={'eco#', 'vrname', 'var-name', 'expno', 'exp#'},
    )

    eco_fields = []
    has_ecotype = bool(cls.eco_dtypes)
    if has_ecotype:
        eco_fields = _field_defs(
            cls.eco_dtypes, cls.eco_pars_fmt,
            skip_keys={'econame', 'eco-name'},
        )

    # Available ecotypes for the dropdown
    ecotypes = list(
        DSSATEcotype.objects.filter(dssat_model=smodel)
        .values('id', 'ecotype_code', 'ecotype_name', 'source')
        .order_by('source', 'ecotype_code')
    )
    # UUID → string for JSON
    for e in ecotypes:
        e['id'] = str(e['id'])

    return {
        'crop_code': crop_code,
        'dssat_model': smodel,
        'crop_name': CROP_NAMES.get(crop_code, crop_code),
        'model_name': get_model_display_name(smodel),
        'cultivar_fields': cul_fields,
        'ecotype_fields': eco_fields,
        'has_ecotype': has_ecotype,
        'available_ecotypes': ecotypes,
    }


# ---------------------------------------------------------------------------
# DB queries (list / detail)
# ---------------------------------------------------------------------------

def list_cultivars_db(crop_code, dssat_model=None, source=None):
    """List cultivars from DB for a crop code / model."""
    qs = DSSATCultivar.objects.filter(crop_code=crop_code.upper())
    if dssat_model:
        qs = qs.filter(dssat_model=dssat_model.upper())
    if source:
        qs = qs.filter(source=source)
    qs = qs.select_related('ecotype').order_by('source', 'cultivar_code')

    cultivars = []
    for c in qs:
        cultivars.append({
            'id': str(c.id),
            'code': c.cultivar_code,
            'name': c.cultivar_name,
            'ecotype': c.ecotype.ecotype_code if c.ecotype else '',
            'source': c.source,
            'validation_status': c.validation_status,
        })

    return {
        'crop_code': crop_code.upper(),
        'crop_name': CROP_NAMES.get(crop_code.upper(), crop_code),
        'dssat_model': dssat_model or '',
        'cultivars': cultivars,
    }


def get_cultivar_detail_db(cultivar_id):
    """Get full cultivar + ecotype params from DB."""
    try:
        c = DSSATCultivar.objects.select_related('ecotype').get(id=cultivar_id)
    except DSSATCultivar.DoesNotExist:
        return {'error': f'Cultivar {cultivar_id} not found'}

    result = {
        'id': str(c.id),
        'crop_code': c.crop_code,
        'dssat_model': c.dssat_model,
        'crop_name': CROP_NAMES.get(c.crop_code, c.crop_code),
        'cultivar_code': c.cultivar_code,
        'cultivar_name': c.cultivar_name,
        'source': c.source,
        'validation_status': c.validation_status,
        'validation_result': c.validation_result,
        'cultivar_params': c.params,
    }

    if c.ecotype:
        result['ecotype_code'] = c.ecotype.ecotype_code
        result['ecotype_name'] = c.ecotype.ecotype_name
        result['ecotype_params'] = c.ecotype.params
    else:
        result['ecotype_code'] = ''
        result['ecotype_params'] = {}

    return result


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _validate_ecotype_code_unique(ecotype_code, dssat_model, exclude_id=None):
    """Check that an ecotype code doesn't clash with any existing record
    for the same model, regardless of source."""
    qs = DSSATEcotype.objects.filter(
        ecotype_code=ecotype_code, dssat_model=dssat_model)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        existing = qs.first()
        raise ValueError(
            f"Ecotype code '{ecotype_code}' already exists for model "
            f"'{dssat_model}' (source: {existing.source}, "
            f"name: {existing.ecotype_name}). Choose a different code."
        )


def create_ecotype(crop_code, dssat_model, ecotype_code, ecotype_name,
                   params, source='custom'):
    """Create a new custom ecotype."""
    crop_code = crop_code.upper()
    dssat_model = dssat_model.upper()
    _validate_ecotype_code_unique(ecotype_code, dssat_model)
    return DSSATEcotype.objects.create(
        crop_code=crop_code,
        dssat_model=dssat_model,
        ecotype_code=ecotype_code,
        ecotype_name=ecotype_name,
        source=source,
        params=params,
    )


def update_ecotype(ecotype_id, **kwargs):
    """Update a custom/cloned ecotype."""
    e = DSSATEcotype.objects.get(id=ecotype_id)
    if e.source == 'dssat':
        raise ValueError("Cannot modify DSSAT bundled ecotypes. Clone it first.")
    new_code = kwargs.get('ecotype_code')
    if new_code and new_code != e.ecotype_code:
        _validate_ecotype_code_unique(new_code, e.dssat_model, exclude_id=e.id)
    for field in ('ecotype_code', 'ecotype_name', 'params'):
        if field in kwargs and kwargs[field] is not None:
            setattr(e, field, kwargs[field])
    e.save()
    return e


def _validate_cultivar_code_unique(cultivar_code, dssat_model, exclude_id=None):
    """Check that a cultivar code doesn't clash with any existing record
    for the same model, regardless of source."""
    qs = DSSATCultivar.objects.filter(
        cultivar_code=cultivar_code, dssat_model=dssat_model)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    if qs.exists():
        existing = qs.first()
        raise ValueError(
            f"Cultivar code '{cultivar_code}' already exists for model "
            f"'{dssat_model}' (source: {existing.source}, "
            f"name: {existing.cultivar_name}). Choose a different code."
        )


def create_cultivar(crop_code, dssat_model, cultivar_code, cultivar_name,
                    params, ecotype_id=None, source='custom'):
    """Create a new custom cultivar.

    Raises ValueError if the cultivar code already exists for this model
    or the crop model requires an ecotype but none is provided.
    """
    crop_code = crop_code.upper()
    dssat_model = dssat_model.upper()

    # Validate code uniqueness across all sources
    _validate_cultivar_code_unique(cultivar_code, dssat_model)

    # Validate ecotype requirement
    cls = get_crop_class(crop_code, dssat_model)
    eco = None
    if ecotype_id:
        eco = DSSATEcotype.objects.get(id=ecotype_id)
    elif cls.eco_dtypes:
        raise ValueError(
            f"Ecotype is required for {crop_code}/{dssat_model}. "
            f"Select an existing ecotype or create a custom one."
        )

    c = DSSATCultivar.objects.create(
        crop_code=crop_code,
        dssat_model=dssat_model,
        cultivar_code=cultivar_code,
        cultivar_name=cultivar_name,
        source=source,
        ecotype=eco,
        params=params,
    )
    return c


def update_cultivar(cultivar_id, **kwargs):
    """Update a custom/cloned cultivar. Rejects updates to DSSAT bundled."""
    c = DSSATCultivar.objects.get(id=cultivar_id)
    if c.source == 'dssat':
        raise ValueError("Cannot modify DSSAT bundled cultivars. Clone it first.")
    # Validate code uniqueness if code is being changed
    new_code = kwargs.get('cultivar_code')
    if new_code and new_code != c.cultivar_code:
        _validate_cultivar_code_unique(new_code, c.dssat_model, exclude_id=c.id)
    for field in ('cultivar_code', 'cultivar_name', 'params', 'ecotype_id'):
        if field in kwargs and kwargs[field] is not None:
            setattr(c, field, kwargs[field])
    c.save()
    return c


def delete_cultivar(cultivar_id):
    """Delete a custom/cloned cultivar. Rejects deletion of DSSAT bundled."""
    c = DSSATCultivar.objects.get(id=cultivar_id)
    if c.source == 'dssat':
        raise ValueError("Cannot delete DSSAT bundled cultivars.")
    c.delete()


def clone_cultivar(source_id, new_code, new_name='', clone_ecotype=False):
    """Clone a cultivar (and optionally its ecotype).

    Raises ValueError if the new code already exists for this model.
    """
    src = DSSATCultivar.objects.select_related('ecotype').get(id=source_id)

    # Validate code uniqueness
    _validate_cultivar_code_unique(new_code, src.dssat_model)

    new_eco = src.ecotype
    if clone_ecotype and src.ecotype:
        new_eco = DSSATEcotype.objects.create(
            ecotype_code=new_code[:6],
            ecotype_name=f"Clone of {src.ecotype.ecotype_name}"[:64],
            crop_code=src.crop_code,
            dssat_model=src.dssat_model,
            source='cloned',
            cloned_from=src.ecotype,
            params=dict(src.ecotype.params),
        )

    new_cul = DSSATCultivar.objects.create(
        cultivar_code=new_code,
        cultivar_name=new_name or f"Clone of {src.cultivar_name}"[:64],
        crop_code=src.crop_code,
        dssat_model=src.dssat_model,
        source='cloned',
        cloned_from=src,
        ecotype=new_eco,
        params=dict(src.params),
    )
    return new_cul


# ---------------------------------------------------------------------------
# Bridge: DB record → DSSATTools Crop instance
# ---------------------------------------------------------------------------

def build_crop_from_db(cultivar_record):
    """Create a DSSATTools Crop instance with params overridden from DB.

    Uses the first available file-based cultivar as a template (for the
    file header scaffolding that ``_write_cul()`` / ``_write_eco()`` need),
    then overrides all parameter values from the DB record.
    """
    cls = get_crop_class(cultivar_record.crop_code, cultivar_record.dssat_model)

    # Find a template cultivar that instantiates without error
    crop = None
    for tc in cls.cultivar_list():
        try:
            crop = cls(tc)
            break
        except Exception:
            continue
    if crop is None:
        raise RuntimeError(
            f"No template cultivar available for {cultivar_record.dssat_model}"
        )

    # Override cultivar params from DB
    for name, value in cultivar_record.params.items():
        if name in cls.cul_dtypes and name != 'eco#' and value is not None:
            crop[name] = value

    # Override cultivar name
    for name_key in ('vrname', 'var-name'):
        if name_key in cls.cul_dtypes:
            crop[name_key] = cultivar_record.cultivar_name
            break

    # Override ecotype params from DB
    if cultivar_record.ecotype and cls.eco_dtypes:
        for name, value in cultivar_record.ecotype.params.items():
            if name in cls.eco_dtypes and value is not None:
                try:
                    crop['eco#'][name] = value
                except (KeyError, TypeError):
                    pass

    return crop


def resolve_cultivar(crop_code, cultivar_code, dssat_model=None):
    """Resolve a cultivar code to a DSSATTools Crop instance.

    Checks DB for custom/cloned records first, falls back to file-based
    instantiation for DSSAT bundled cultivars.
    """
    crop_code = crop_code.upper()
    dm = dssat_model.upper() if dssat_model else None

    # Check DB for custom/cloned cultivars
    qs = DSSATCultivar.objects.filter(
        cultivar_code=cultivar_code,
        crop_code=crop_code,
        source__in=('custom', 'cloned'),
    )
    if dm:
        qs = qs.filter(dssat_model=dm)
    record = qs.select_related('ecotype').first()

    if record:
        return build_crop_from_db(record)

    # Fall back to file-based instantiation (DSSAT bundled or no DB)
    cls = get_crop_class(crop_code, dm)
    return cls(cultivar_code)


# ---------------------------------------------------------------------------
# DSSAT binary validation
# ---------------------------------------------------------------------------

def validate_cultivar_with_dssat(cultivar_id):
    """Run a minimal DSSAT simulation to verify cultivar/ecotype params.

    Constructs a synthetic experiment (generic soil, synthetic weather,
    default planting) and runs DSSAT. If the binary produces output
    without crashing, the cultivar is marked ``valid``; otherwise
    ``invalid`` with the error details.

    Returns
    -------
    dict
        ``{"status": "valid"|"invalid"|"error", "message": "...", ...}``
    """
    from datetime import date, timedelta
    from DSSATTools.run import DSSAT
    from DSSATTools.weather import WeatherStation, WeatherRecord
    from DSSATTools.soil import SoilProfile, SoilLayer
    from DSSATTools.filex import (
        Planting, Field,
        SimulationControls, SCGeneral, SCOptions, SCMethods,
        SCManagement, SCOutputs,
    )
    from .crop_service import CROP_GROWTH_DURATIONS

    try:
        record = DSSATCultivar.objects.select_related('ecotype').get(id=cultivar_id)
    except DSSATCultivar.DoesNotExist:
        return {'status': 'error', 'message': f'Cultivar {cultivar_id} not found'}

    # 0. Ensure ecotype is assigned if crop requires one
    cls = get_crop_class(record.crop_code, record.dssat_model)
    if cls.eco_dtypes and not record.ecotype_id:
        # Auto-assign default ecotype for validation
        default_eco = DSSATEcotype.objects.filter(
            dssat_model=record.dssat_model, source='dssat'
        ).first()
        if default_eco:
            record.ecotype = default_eco
            record.save(update_fields=['ecotype'])
        else:
            result = {'status': 'error',
                      'message': 'No ecotype assigned and no default available.'}
            _save_validation(record, result)
            return result

    # 1. Build Crop object from DB
    try:
        crop_obj = build_crop_from_db(record)
    except Exception as e:
        result = {'status': 'error', 'message': f'Failed to build crop object: {e}'}
        _save_validation(record, result)
        return result

    # 2. Build minimal soil — generic loam, 3 layers
    try:
        layers = [
            SoilLayer(slb=15, slll=0.170, sdul=0.310, ssat=0.430,
                      srgf=1.00, sbdm=1.30, sloc=1.50),
            SoilLayer(slb=45, slll=0.170, sdul=0.310, ssat=0.430,
                      srgf=0.60, sbdm=1.35, sloc=0.80),
            SoilLayer(slb=90, slll=0.170, sdul=0.310, ssat=0.430,
                      srgf=0.20, sbdm=1.40, sloc=0.30),
        ]
        soil = SoilProfile(
            table=layers, name='VALIDATION',
            salb=0.13, slu1=9.0, sldr=0.60,
            slro=73.0, slnf=1.0, slpf=1.0,
        )
    except Exception as e:
        result = {'status': 'error', 'message': f'Failed to build soil: {e}'}
        _save_validation(record, result)
        return result

    # 3. Build synthetic weather — 2 years of daily records
    try:
        pdate = date(2020, 6, 15)
        growth_days = CROP_GROWTH_DURATIONS.get(record.crop_code, 210)
        wth_start = pdate - timedelta(days=60)
        wth_end = pdate + timedelta(days=growth_days + 60)
        total_days = (wth_end - wth_start).days

        records = []
        for i in range(total_days):
            d = wth_start + timedelta(days=i)
            records.append(WeatherRecord(
                date=d, srad=18.0, tmax=30.0, tmin=20.0, rain=3.0,
            ))
        weather = WeatherStation(
            table=records, lat=10.0, long=-80.0, elev=100,
        )
    except Exception as e:
        result = {'status': 'error', 'message': f'Failed to build weather: {e}'}
        _save_validation(record, result)
        return result

    # 4. Build field + planting + simulation controls
    try:
        field = Field(
            id_field='VALD0001', wsta=weather, id_soil=soil,
            xcrd=-80.0, ycrd=10.0, elev=100,
        )
        planting = Planting(
            pdate=pdate, ppop=7.0, ppoe=7.0, plrs=75.0,
            pldp=5.0, plme='S',
        )
        sim_start = pdate - timedelta(days=30)
        sim_controls = SimulationControls(
            general=SCGeneral(sdate=sim_start, nyers=1),
            options=SCOptions(water='Y', nitro='Y'),
            methods=SCMethods(wther='M', incon='M', light='E',
                              evapo='R', infil='R', photo='C',
                              hydro='R', nswit='1', mesom='P',
                              mesev='R', mesol='1'),
            management=SCManagement(plant='R', irrig='N', ferti='N',
                                    resid='N', harvs='M'),
            outputs=SCOutputs(fname='N', ovvew='Y', sumry='Y',
                              grout='N', caout='N', waout='N',
                              niout='N', miout='N', diout='N',
                              vbose='Y', chout='N', opout='N'),
        )
    except Exception as e:
        result = {'status': 'error', 'message': f'Failed to build experiment: {e}'}
        _save_validation(record, result)
        return result

    # 5. Run DSSAT
    dssat = DSSAT()
    try:
        summary = dssat.run_treatment(
            field=field,
            cultivar=crop_obj,
            planting=planting,
            simulation_controls=sim_controls,
            verbose=False,
        )
        # Check if DSSAT produced meaningful output
        stdout = ''
        if hasattr(dssat, 'output') and dssat.output:
            stdout = str(dssat.output)[:2000]

        if summary is not None and len(summary) > 0:
            result = {
                'status': 'valid',
                'message': 'DSSAT simulation completed successfully.',
                'summary_keys': list(summary.columns) if hasattr(summary, 'columns') else [],
            }
        else:
            result = {
                'status': 'invalid',
                'message': 'DSSAT ran but produced no output summary.',
                'stdout': stdout,
            }
    except Exception as e:
        stdout = ''
        try:
            if hasattr(dssat, 'output') and dssat.output:
                stdout = str(dssat.output)[:2000]
        except Exception:
            pass
        result = {
            'status': 'invalid',
            'message': f'DSSAT simulation failed: {e}',
            'stdout': stdout,
        }
    finally:
        try:
            dssat.close()
        except Exception:
            pass

    _save_validation(record, result)
    return result


def _save_validation(record, result):
    """Persist validation result to the cultivar record."""
    status = result.get('status', 'invalid')
    record.validation_status = status if status in ('valid', 'invalid') else 'invalid'
    record.validation_result = result
    record.save(update_fields=['validation_status', 'validation_result', 'updated_at'])
