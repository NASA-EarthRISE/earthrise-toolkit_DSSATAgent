"""
Ensemble simulation service.

Orchestrates batch/spatial runs using DSSATBatch. Supports spatial
ensembles (varying weather/soil), management comparisons (varying
planting/fertilizer/irrigation), and sensitivity analysis (varying any
single parameter).

The input uses a base + treatments override pattern: ``base`` holds
shared defaults, each entry in ``treatments`` is a partial override.
Treatment-level values replace base-level values via shallow merge.
"""

import logging
import math
import json
import os
from datetime import date, datetime

from DSSATTools.batch import DSSATBatch
from DSSATTools.filex import Field

from .crop_service import CROP_CLASSES, get_crop_class
from .cultivar_service import resolve_cultivar
from .soil_service import profile_to_dssat, dict_to_dssat_soil
from .weather_service import (
    build_weather_station,
    check_weather_coverage,
    validate_weather_records,
    resolve_weather,
)
from .validation import validate_experiment, _parse_date
from .experiment_service import (
    _build_simulation_controls,
    _build_planting,
    _build_fertilizer,
    _build_irrigation,
    _build_harvest,
    _build_initial_conditions,
    _build_residue,
    _build_chemical,
    _build_tillage,
    _build_mow,
    _clean_summary,
    _safe_float,
    capture_batch_input_files,
)

logger = logging.getLogger(__name__)

# All parameter keys that participate in base/treatment merge
ALL_PARAM_KEYS = [
    'crop_code', 'cultivar_code', 'simulation_controls', 'planting',
    'weather_data', 'soil_id', 'inline_soil', 'field',
    'fertilizer', 'irrigation', 'harvest', 'initial_conditions',
    'residue', 'chemical', 'tillage', 'mow',
]


def _merge_treatment(base, treatment):
    """Shallow merge: treatment keys override base keys.

    Special case: the ``field`` dict is deep-merged so that a treatment
    can override individual field keys (e.g. lat/lon) while inheriting
    the rest from base.
    """
    merged = {}
    for key in ALL_PARAM_KEYS:
        if key in treatment:
            merged[key] = treatment[key]
        elif key in base:
            merged[key] = base[key]
    # Deep-merge dicts where partial overrides are common (field +
    # planting). Without this, a protocol that only overrides
    # ``planting.pdate`` would otherwise wipe out ``ppop``/``plrs``
    # inherited from base.
    if 'field' in base or 'field' in treatment:
        merged['field'] = {
            **base.get('field', {}),
            **treatment.get('field', {}),
        }
    if 'planting' in base or 'planting' in treatment:
        merged['planting'] = {
            **(base.get('planting') or {}),
            **(treatment.get('planting') or {}),
        }
    if 'simulation_controls' in base or 'simulation_controls' in treatment:
        merged['simulation_controls'] = {
            **(base.get('simulation_controls') or {}),
            **(treatment.get('simulation_controls') or {}),
        }
    return merged


def _cache_key(value):
    """Create a hashable cache key from a JSON-serializable value."""
    return json.dumps(value, sort_keys=True, default=str)


def _split_run_records(text):
    """Split a DSSAT batch output file (PlantGro.OUT / SoilWat.OUT /
    Weather.OUT / etc.) into one list of records per treatment run.

    DSSAT batch mode concatenates all treatment outputs into a single
    file, demarcating each treatment with a ``*RUN N`` header line.
    Each run section then has a ``@YEAR DOY DAS DAP ...`` header
    followed by space-delimited data rows. This helper returns a list
    indexed by (run_number - 1) where each entry is the records list
    that single-experiment runs would store under
    ``SimulationResult.plant_growth`` — same shape as ``_df_to_records``
    in ``experiment_service``.
    """
    if not text:
        return []
    import io
    import math
    try:
        import pandas as pd
    except Exception:
        return []
    runs = []
    section_lines = None  # accumulating lines for the current run
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith('*RUN'):
            if section_lines is not None:
                runs.append(section_lines)
            section_lines = []
            continue
        if section_lines is not None:
            section_lines.append(line)
    if section_lines is not None:
        runs.append(section_lines)
    if not runs:
        return []

    out = []
    for sec in runs:
        # Find the @YEAR (or @ at col 1) header inside the section.
        header_idx = next(
            (i for i, ln in enumerate(sec)
             if ln.lstrip().startswith('@')),
            None,
        )
        if header_idx is None:
            out.append([])
            continue
        # Read the header + data rows. Skip blank lines after the data.
        body = [sec[header_idx]]
        for ln in sec[header_idx + 1:]:
            if not ln.strip():
                break
            body.append(ln)
        try:
            df = pd.read_csv(
                io.StringIO('\n'.join(body)),
                sep=r'\s+',
                skipinitialspace=True,
            )
        except Exception:
            out.append([])
            continue
        df.columns = [c.lstrip('@').strip() for c in df.columns]
        records = []
        for _, row in df.iterrows():
            rec = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, 'item'):
                    val = val.item()
                if isinstance(val, float) and (
                        math.isnan(val) or val == -99.0):
                    rec[col] = None
                else:
                    rec[col] = val
            records.append(rec)
        out.append(records)
    return out


def run_ensemble(params):
    """
    Run multiple DSSAT treatments in a single batch execution.

    Parameters
    ----------
    params : dict
        {
            "base": { ... shared defaults ... },
            "treatments": [
                {},                 # empty = use base as-is
                {"planting": {...}},  # override just planting
                ...
            ]
        }

    Returns
    -------
    dict
        {
            "status": "completed",
            "treatment_count": N,
            "treatments": [ {"summary": {...}}, ... ],
            "aggregate": { "mean_yield": ..., ... },
            "warnings": [...]
        }
    """
    base = params.get('base', {})
    treatments_overrides = params.get('treatments', [])

    if not treatments_overrides:
        return {
            "status": "error",
            "errors": ["treatments array is required and must not be empty"],
        }

    if len(treatments_overrides) > 99:
        return {
            "status": "error",
            "errors": ["Maximum 99 treatments allowed"],
        }

    # ----------------------------------------------------------------
    # 1. Merge each treatment with base
    # ----------------------------------------------------------------
    merged_treatments = []
    for i, trt_override in enumerate(treatments_overrides):
        merged = _merge_treatment(base, trt_override or {})
        merged_treatments.append(merged)

    # ----------------------------------------------------------------
    # 2. Validate every merged treatment
    # ----------------------------------------------------------------
    all_errors = []
    all_warnings = []

    for i, m in enumerate(merged_treatments):
        tag = f"treatment[{i}]"

        # Basic required fields
        if not m.get('crop_code'):
            all_errors.append(f"{tag}: crop_code is required")
        if not m.get('cultivar_code'):
            all_errors.append(f"{tag}: cultivar_code is required")
        if not m.get('simulation_controls'):
            all_errors.append(f"{tag}: simulation_controls is required")
        if not m.get('planting'):
            all_errors.append(f"{tag}: planting is required")
        if not m.get('weather_data') and not m.get('weather_source'):
            all_errors.append(f"{tag}: weather_data or weather_source is required")
        if not m.get('soil_id') and not m.get('inline_soil'):
            all_errors.append(f"{tag}: soil_id or inline_soil is required")

        # Run full validation (same as single-treatment)
        errors, warnings = validate_experiment(m)
        all_errors.extend(f"{tag}: {e}" for e in errors)
        all_warnings.extend(f"{tag}: {w}" for w in warnings)

        # Validate weather records
        wd = m.get('weather_data', {})
        recs = wd.get('records', [])
        if recs:
            rec_errors = validate_weather_records(recs)
            all_errors.extend(f"{tag}: {e}" for e in rec_errors)

    if all_errors:
        return {
            "status": "validation_error",
            "errors": all_errors,
            "warnings": all_warnings,
        }

    # ----------------------------------------------------------------
    # 3. Build DSSATTools objects per treatment
    # ----------------------------------------------------------------
    # Caches for deduplication (same param dict -> same object)
    cultivar_cache = {}   # (crop_code, cultivar_code) -> Crop obj
    soil_cache = {}       # soil_id -> SoilProfile obj
    weather_cache = {}    # cache_key -> WeatherStation obj
    sc_cache = {}         # cache_key -> SimulationControls obj
    planting_cache = {}   # cache_key -> Planting obj
    fert_cache = {}
    irrig_cache = {}
    harvest_cache = {}
    ic_cache = {}
    residue_cache = {}
    chemical_cache = {}
    tillage_cache = {}
    mow_cache = {}

    treatment_objects = []

    for i, m in enumerate(merged_treatments):
        tag = f"treatment[{i}]"
        crop_code = m['crop_code'].upper()
        cultivar_code = m['cultivar_code']
        e_dssat_model = m.get('dssat_model', '').upper() or None

        try:
            # Cultivar
            cu_key = (crop_code, cultivar_code, e_dssat_model)
            if cu_key not in cultivar_cache:
                cultivar_cache[cu_key] = resolve_cultivar(crop_code, cultivar_code, e_dssat_model)
            cultivar = cultivar_cache[cu_key]

            # Simulation controls
            sc_key = _cache_key(m.get('simulation_controls', {}))
            if sc_key not in sc_cache:
                sc_cache[sc_key] = _build_simulation_controls(m)
            sim_controls = sc_cache[sc_key]

            # Planting
            pl_key = _cache_key(m.get('planting', {}))
            if pl_key not in planting_cache:
                planting_cache[pl_key] = _build_planting(m)
            planting = planting_cache[pl_key]

            # Weather -- resolve from inline data or PostGIS rasters
            try:
                wd = resolve_weather(
                    m,
                    lat=m.get('field', {}).get('lat') or m.get('field', {}).get('latitude'),
                    lon=m.get('field', {}).get('lon') or m.get('field', {}).get('longitude'),
                )
            except ValueError as e:
                return {"status": "error", "errors": [f"{tag} weather: {e}"]}

            w_key = _cache_key(wd)
            if w_key not in weather_cache:
                # Check coverage
                sc_params = m.get('simulation_controls', {})
                sdate = _parse_date(sc_params.get('sdate'))
                nyers = sc_params.get('nyers', 1)
                coverage = check_weather_coverage(wd, sdate, nyers)
                if not coverage.get('complete', False):
                    return {
                        "status": "missing_data",
                        "treatment_index": i,
                        "missing_count": coverage.get('total_missing', 0),
                        "missing_dates": coverage.get(
                            'missing_dates', []
                        ),
                        "weather_start_needed": coverage.get(
                            'weather_start_needed'
                        ),
                        "weather_end_needed": coverage.get(
                            'weather_end_needed'
                        ),
                        "data_start": coverage.get('data_start'),
                        "data_end": coverage.get('data_end'),
                    }
                weather_cache[w_key] = build_weather_station(wd)
            weather = weather_cache[w_key]

            # Soil
            if m.get('soil_id'):
                s_key = m['soil_id']
                if s_key not in soil_cache:
                    soil_cache[s_key] = profile_to_dssat(s_key)
                soil = soil_cache[s_key]
            else:
                inline = m['inline_soil']
                s_key = _cache_key(inline)
                if s_key not in soil_cache:
                    soil_cache[s_key] = dict_to_dssat_soil(inline)
                soil = soil_cache[s_key]

            # Field
            field_params = m.get('field', {})
            field = Field(
                id_field=field_params.get(
                    'id_field', f'EN{i:06d}'
                ),
                wsta=weather,
                id_soil=soil,
                xcrd=field_params.get('lon'),
                ycrd=field_params.get('lat'),
                elev=field_params.get('elev'),
                flsa=field_params.get('flsa'),
                flob=field_params.get('flob'),
                fldt=field_params.get('fldt'),
                fldd=field_params.get('fldd'),
                flds=field_params.get('flds'),
            )

            # Optional management sections.
            # user_id is constant across treatments (from the ensemble base),
            # so passing it to the builder without adding it to the cache key
            # is safe — the cache is local to this run_ensemble() invocation.
            m_user_id = m.get('user_id')

            def _cached_build(cache, key_data, builder, data, pass_user_id=True):
                if data is None:
                    return None
                ck = _cache_key(key_data)
                if ck not in cache:
                    if pass_user_id:
                        cache[ck] = builder(data, user_id=m_user_id)
                    else:
                        cache[ck] = builder(data)
                return cache[ck]

            fertilizer = _cached_build(
                fert_cache, m.get('fertilizer'),
                _build_fertilizer, m.get('fertilizer')
            )
            irrigation = _cached_build(
                irrig_cache, m.get('irrigation'),
                _build_irrigation, m.get('irrigation')
            )
            harvest = _cached_build(
                harvest_cache, m.get('harvest'),
                _build_harvest, m.get('harvest')
            )
            # _build_initial_conditions has no config fallbacks — no user_id needed
            initial_conditions = _cached_build(
                ic_cache, m.get('initial_conditions'),
                _build_initial_conditions, m.get('initial_conditions'),
                pass_user_id=False,
            )
            residue = _cached_build(
                residue_cache, m.get('residue'),
                _build_residue, m.get('residue')
            )
            chemical = _cached_build(
                chemical_cache, m.get('chemical'),
                _build_chemical, m.get('chemical')
            )
            tillage = _cached_build(
                tillage_cache, m.get('tillage'),
                _build_tillage, m.get('tillage')
            )
            mow = _cached_build(
                mow_cache, m.get('mow'),
                _build_mow, m.get('mow')
            )

            treatment_objects.append({
                'field': field,
                'cultivar': cultivar,
                'planting': planting,
                'sim_controls': sim_controls,
                'fertilizer': fertilizer,
                'irrigation': irrigation,
                'harvest': harvest,
                'initial_conditions': initial_conditions,
                'residue': residue,
                'chemical': chemical,
                'tillage': tillage,
                'mow': mow,
            })

        except Exception as e:
            logger.exception(f"Failed to build {tag}")
            return {
                "status": "error",
                "errors": [f"{tag}: build error: {e}"],
                "warnings": all_warnings,
            }

    # ----------------------------------------------------------------
    # 4. Accumulate into DSSATBatch
    # ----------------------------------------------------------------
    # Note: per-treatment input file capture has been removed. DSSATBatch
    # writes a single multi-treatment FileX (EXPEFILE.<crop>X) plus shared
    # SOIL.SOL / *.CUL / *.ECO / Weather/*.WTH files into batch.run_path,
    # and that is what DSSAT actually consumes. We capture from disk after
    # batch.run() succeeds, not via per-treatment create_filex() loops which
    # produced fictional single-treatment artifacts that didn't match the
    # real run.
    batch = DSSATBatch()
    try:
        for trt in treatment_objects:
            batch.add_treatment(
                field=trt['field'],
                cultivar=trt['cultivar'],
                planting=trt['planting'],
                simulation_controls=trt['sim_controls'],
                fertilizer=trt.get('fertilizer'),
                irrigation=trt.get('irrigation'),
                harvest=trt.get('harvest'),
                initial_conditions=trt.get('initial_conditions'),
                residue=trt.get('residue'),
                chemical=trt.get('chemical'),
                tillage=trt.get('tillage'),
                mow=trt.get('mow'),
            )

        # ----------------------------------------------------------------
        # 5. Run
        # ----------------------------------------------------------------
        results = batch.run(verbose=False)

        # ----------------------------------------------------------------
        # 6. Capture input + output files from batch.run_path
        # ----------------------------------------------------------------
        # Inputs: read the real multi-treatment FileX, SOIL.SOL, .CUL, .ECO,
        # and .WTH files that DSSAT actually consumed. These are shared
        # across all treatments in this batch. The .SPE and GRSTAGE.CDE
        # files are captured from their canonical source paths via the
        # reference_crops argument (one Crop per unique cultivar).
        _ref_crops = {id(t['cultivar']): t['cultivar'] for t in treatment_objects}.values()
        shared_inputs = capture_batch_input_files(batch, reference_crops=_ref_crops)

        output_files = {}
        if hasattr(batch, 'output_files') and batch.output_files:
            for key, content in batch.output_files.items():
                if isinstance(content, str):
                    output_files[key] = content

        # Each treatment result gets the same shared input bundle attached.
        # Storage shape choice: duplication. Cheap to optimize later by
        # lifting to a parent table; the simplification is worth it now.
        # Also coerce NaN/-99 sentinels in each treatment summary to None
        # (PostgreSQL JSONB rejects NaN literals) — single's path does this
        # via ``_clean_summary`` and ensemble must too.
        for r in results:
            if r.get('summary'):
                r['summary'] = _clean_summary(r['summary'])
            r['dssat_files'] = {
                'input': dict(shared_inputs),
                'output': output_files,
            }

        # Parse per-treatment time-series tables (PlantGro/SoilWat/etc)
        # out of the merged batch output files. ``DSSATBatch._fetch_output``
        # only reads the raw .OUT text into ``output_files``; it never
        # parses per-run sections. Without this step every treatment's
        # ``plant_growth`` is an empty list and the ensemble crop-growth
        # overlay drops out of the chart gallery.
        per_run_tables = {
            'plant_growth':  _split_run_records(output_files.get('PlantGro')),
            'soil_water':    _split_run_records(output_files.get('SoilWat')),
            'soil_organic':  _split_run_records(output_files.get('SoilOrg')),
            'soil_nitrogen': _split_run_records(output_files.get('SoilNi')),
            'weather_output': _split_run_records(output_files.get('Weather')),
        }
        for i, r in enumerate(results):
            for key, by_run in per_run_tables.items():
                if not by_run:
                    continue
                # ``_split_run_records`` returns a 1-indexed list keyed by
                # RUN number; treatment ``i`` lines up with run ``i+1``.
                if i < len(by_run) and by_run[i]:
                    r[key] = by_run[i]

        # ----------------------------------------------------------------
        # 8. Aggregate and return
        # ----------------------------------------------------------------
        aggregate = _compute_aggregate(results)

        # Attach treatment labels + the override fields so downstream
        # artifact builders can describe what each treatment varied
        # ("best treatment used 30 kg N/ha at 4 applications, gap 20 d").
        # Only carries the user-specified knobs (label/name/fertilizer/
        # planting/cultivar/etc.) — not the merged base.
        for i, r in enumerate(results):
            if i < len(treatments_overrides):
                override = treatments_overrides[i] or {}
                if 'label' in override:
                    r['label'] = override['label']
                elif override.get('name'):
                    r['label'] = override['name']
                elif not r.get('label'):
                    r['label'] = f'Treatment {i + 1}'
                # Store overrides verbatim. Strip 'base' / 'treatments'
                # safety in case the dict was mis-shaped.
                clean = {k: v for k, v in override.items()
                         if k not in ('base', 'treatments') and v is not None}
                if clean:
                    r['overrides'] = clean

        return {
            "status": "completed",
            "treatment_count": len(results),
            "treatments": results,
            "aggregate": aggregate,
            "warnings": all_warnings,
        }

    except Exception as e:
        logger.exception("DSSAT batch execution failed")
        # Capture whatever DSSAT wrote before crashing — input files
        # (FILEX, SOIL.SOL, .CUL, .WTH) and any partial output files
        # (WARNING.OUT, ERROR.OUT, OVERVIEW.OUT). Without this we can't
        # diagnose IPSIM/IPFERT/IPSOIL errors after the fact because
        # ``DSSATBatch.close()`` deletes the run dir in ``finally``.
        failure_inputs = {}
        try:
            _ref_crops = {id(t['cultivar']): t['cultivar']
                          for t in treatment_objects}.values()
            failure_inputs = capture_batch_input_files(
                batch, reference_crops=_ref_crops,
            )
        except Exception:
            logger.warning("Failed to capture inputs after DSSAT failure",
                           exc_info=True)
        failure_outputs = {}
        try:
            run_path = getattr(batch, 'run_path', None)
            if run_path and os.path.isdir(run_path):
                for fname in os.listdir(run_path):
                    if fname.upper().endswith(('.OUT', '.LST')):
                        try:
                            with open(os.path.join(run_path, fname)) as fh:
                                failure_outputs[fname.rsplit('.', 1)[0]] = fh.read()
                        except Exception:
                            pass
        except Exception:
            logger.warning("Failed to capture outputs after DSSAT failure",
                           exc_info=True)

        stdout = failure_outputs.get('OVERVIEW', '')
        return {
            "status": "failed",
            "errors": [str(e)],
            "stdout": stdout,
            "warnings": all_warnings,
            "dssat_files": {
                "input": failure_inputs,
                "output": failure_outputs,
            },
        }
    finally:
        try:
            batch.close()
        except Exception:
            pass


def _compute_aggregate(results):
    """Compute aggregate statistics across treatments."""
    if not results:
        return {}

    # Extract yield values (hwam = harvested weight at maturity).
    # Maturity is measured in days-after-planting (``mat``), which
    # ``_clean_summary`` derives from ``mdat`` - ``pdat``. The previous
    # code averaged the raw ``mdat`` YYYYDDD date integer (~2,024,202),
    # producing nonsensical "mean maturity" values in the millions.
    yields = []
    maturities = []

    for r in results:
        s = r.get('summary', {})
        hwam = s.get('hwam')
        if hwam is not None:
            yields.append(float(hwam))
        mat = s.get('mat')
        if mat is not None:
            maturities.append(float(mat))

    agg = {}

    if yields:
        agg['mean_yield'] = round(sum(yields) / len(yields), 1)
        agg['min_yield'] = round(min(yields), 1)
        agg['max_yield'] = round(max(yields), 1)
        if len(yields) > 1:
            mean = sum(yields) / len(yields)
            variance = sum((y - mean) ** 2 for y in yields) / (
                len(yields) - 1
            )
            agg['std_yield'] = round(math.sqrt(variance), 1)
        else:
            agg['std_yield'] = 0.0
        agg['yield_count'] = len(yields)

    if maturities:
        agg['mean_maturity'] = round(
            sum(maturities) / len(maturities), 0
        )

    return agg
