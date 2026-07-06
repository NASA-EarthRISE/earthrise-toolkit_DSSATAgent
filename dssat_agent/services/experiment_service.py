"""
Experiment building and execution service.

Builds DSSATTools objects from JSON params, runs DSSAT simulations,
and parses output.
"""

import logging
import math
import os
from datetime import date, datetime

from DSSATTools.run import DSSAT
from DSSATTools.filex import (
    Planting, Harvest, Field, Cultivar,
    InitialConditions, InitialConditionsLayer,
    Fertilizer, FertilizerEvent,
    Irrigation, IrrigationEvent,
    Residue, ResidueEvent,
    Chemical, ChemicalEvent,
    Tillage, TillageEvent,
    Mow, MowEvent,
    SimulationControls, SCGeneral, SCOptions, SCMethods,
    SCManagement, SCOutputs, AMIrrigation,
    create_filex,
)

from .crop_service import CROP_CLASSES, get_crop_class
from .cultivar_service import resolve_cultivar
from .soil_service import profile_to_dssat, dict_to_dssat_soil
from .weather_service import build_weather_station, check_weather_coverage, resolve_weather
from .validation import validate_experiment, _parse_date

logger = logging.getLogger(__name__)


def _safe_float(val):
    """Convert to float, returning None for missing/invalid values."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or f == -99 or f == -99.0) else f
    except (ValueError, TypeError):
        return None


# Extensions of input files DSSATTools writes into run_path before invoking
# the binary. The dict is keyed by basename so the UI shows the real generated
# filename (e.g. NSNS0000.MZX, SOIL.SOL, MZCER048.CUL).
_INPUT_FILE_EXTS = {'.SOL', '.CUL', '.ECO', '.MOW'}


def _read_input_files_from_run_dir(run_path, input_dict):
    """
    Walk a DSSAT run directory and copy every input file's content into
    ``input_dict`` keyed by its basename. Shared by single-run and batch-run
    capture paths.
    """
    if not run_path or not os.path.isdir(run_path):
        return

    def _read(path):
        try:
            with open(path, 'r') as f:
                input_dict[os.path.basename(path)] = f.read()
        except Exception as e:
            logger.warning("Failed to read input file %s: %s", path, e)

    for entry in os.listdir(run_path):
        full = os.path.join(run_path, entry)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(entry)[1].upper()
        # FileX: 4-char extension ending in X (e.g. .MZX, .WHX, .RIX, .SBX)
        if len(ext) == 4 and ext.endswith('X'):
            _read(full)
        elif ext in _INPUT_FILE_EXTS:
            _read(full)

    # Weather files live in run_path/Weather/<insi><yy><len>.WTH
    weather_dir = os.path.join(run_path, 'Weather')
    if os.path.isdir(weather_dir):
        for entry in os.listdir(weather_dir):
            if entry.upper().endswith('.WTH'):
                _read(os.path.join(weather_dir, entry))


def capture_batch_input_files(batch, reference_crops=None):
    """
    Capture input files from a completed DSSATBatch run.

    DSSATBatch writes one shared multi-treatment FileX (EXPEFILE.<crop>X),
    one combined SOIL.SOL with all unique factor-level profiles, one .CUL +
    .ECO per unique species, and one .WTH per unique weather station — all
    into ``batch.run_path``. We read them straight off disk after batch.run()
    succeeds, before batch.close() removes the directory. Returns a dict
    keyed by basename, suitable for inclusion in dssat_files['input'].

    ``reference_crops`` — optional iterable of DSSATTools Crop instances
    whose ``.SPE`` files should also be captured (the DSSAT binary reads
    these from the symlinked Genotype dir, so they never land in
    ``run_path``). ``GRSTAGE.CDE`` is always captured when any reference
    crop is provided.
    """
    inputs = {}
    run_path = getattr(batch, 'run_path', None)
    _read_input_files_from_run_dir(run_path, inputs)
    if reference_crops:
        for crop in reference_crops:
            _capture_reference_files(crop, inputs)
    return inputs


def _capture_input_files(dssat, input_dict):
    """
    Read DSSAT input files from the run directory after a successful single-
    treatment run. Each entry is keyed by its actual basename (e.g.
    "NSNS0000.MZX") so the UI surfaces the real filename.
    """
    run_path = getattr(dssat, 'run_path', None)
    if not run_path or not os.path.isdir(run_path):
        logger.warning("Cannot capture input files: run_path missing")
        return
    _read_input_files_from_run_dir(run_path, input_dict)


def _capture_reference_files(crop, input_dict):
    """
    Add the .SPE (species parameters) and GRSTAGE.CDE (phenological stage
    codes) files to the captured input dict.

    These two files are NOT written to the DSSAT run directory — the binary
    reads them directly from the symlinked ``Genotype/`` and
    ``StandardData/`` dirs under ``$DSSAT_HOME``. Capturing them from their
    canonical source paths makes the experiment record self-contained:
    users can see exactly which species coefficients and stage codes the
    run consumed without needing to introspect the vendored DSSAT tree.
    """
    # .SPE path comes from the DSSATTools Crop instance (set when the crop
    # class is constructed). Format: ``.../Data/Genotype/MZCER048.SPE``.
    spe_path = getattr(crop, 'spe_path', None)
    if spe_path and os.path.isfile(spe_path):
        try:
            with open(spe_path, 'r') as f:
                input_dict[os.path.basename(spe_path)] = f.read()
        except Exception as e:
            logger.warning("Failed to capture .SPE file %s: %s", spe_path, e)

    # GRSTAGE.CDE lives alongside DETAIL.CDE in the vendored tree.
    try:
        from DSSATTools import __file__ as _dssat_path
        grstage = os.path.join(
            os.path.dirname(_dssat_path),
            'dssat-csm-os', 'Data', 'GRSTAGE.CDE',
        )
        if os.path.isfile(grstage):
            with open(grstage, 'r') as f:
                input_dict['GRSTAGE.CDE'] = f.read()
    except Exception as e:
        logger.warning("Failed to capture GRSTAGE.CDE: %s", e)


def _build_simulation_controls(params):
    """Build SimulationControls from JSON dict."""
    from dssat_agent.services.config import get_config, resolve_user
    _user = resolve_user(params.get('user_id'))

    sc = params.get('simulation_controls', {})

    # SDATE precedence:
    #   1. Explicit sdate from simulation_controls
    #   2. ICDAT from initial_conditions (so DSSAT applies the IC values
    #      directly at simulation start, the strict-convention case)
    #   3. None (caller / DSSATTools defaults will handle it)
    sdate = _parse_date(sc.get('sdate'))
    if sdate is None:
        ic = params.get('initial_conditions') or {}
        if ic.get('icdat'):
            sdate = _parse_date(ic['icdat'])
    nyers = sc.get('nyers', 1)
    nreps = sc.get('nreps', 1)
    start = sc.get('start', 'S')
    rseed = sc.get('rseed', 2150)

    general = SCGeneral(
        sdate=sdate,
        nyers=nyers,
        nreps=nreps,
        start=start,
        rseed=rseed,
    )

    # Options — config-driven defaults
    opts = sc.get('options', {})
    options = SCOptions(
        water=opts.get('water', get_config('sim_water', 'Y', user=_user)),
        nitro=opts.get('nitro', get_config('sim_nitro', 'Y', user=_user)),
        symbi=opts.get('symbi', 'N'),
        phosp=opts.get('phosp', get_config('sim_phosphorus', 'N', user=_user)),
        potas=opts.get('potas', get_config('sim_potassium', 'N', user=_user)),
        dises=opts.get('dises', 'N'),
        chem=opts.get('chem', 'N'),
        till=opts.get('till', 'N'),
        co2=opts.get('co2', 'M'),
    )

    # Methods
    meth = sc.get('methods', {})
    methods = SCMethods(
        wther=meth.get('wther', 'M'),
        incon=meth.get('incon', 'M'),
        light=meth.get('light', 'E'),
        evapo=meth.get('evapo', 'R'),
        infil=meth.get('infil', 'R'),
        photo=meth.get('photo', 'C'),
        hydro=meth.get('hydro', 'R'),
        nswit=meth.get('nswit', '1'),
        mesom=meth.get('mesom', 'P'),
        mesev=meth.get('mesev', 'R'),
        mesol=meth.get('mesol', '1'),
    )

    # Management — auto-detect modes from params. DSSAT's MANAGEMENT
    # codes (per Management/Fert_Place.for and InputModule/IPSIM.for):
    #   R = Reported, FILEX events fire on absolute YYDDD dates
    #   D = Default, FILEX events fire on DAP (days-after-planting)
    #   F = Forced/fixed automatic application
    #   A = Automatic, model decides based on stress thresholds
    #   N = None, section skipped entirely
    # Our event writers emit YYDDD-format dates (e.g. ``24104``) in the
    # *FERTILIZERS / *IRRIGATION / *RESIDUES sections, so the matching
    # mode is R. The previous default of D told DSSAT to interpret
    # ``24104`` as "DAP=24104" (well past harvest), and the section
    # never fired — sensitivity sweeps over fertilizer rate produced
    # identical near-zero yields because no fertilizer was applied to
    # any treatment. Switch the auto-derivation to R, leaving an
    # explicit ``mgmt.ferti`` override intact for users who really
    # do want DAP semantics.
    #   irrigation events present → R (or A for the "automatic" method)
    #   fertilizer events present → R
    #   residue events present    → R (else N)
    mgmt = sc.get('management', {})
    irrig_config = params.get('irrigation')
    irrig_mode = mgmt.get('irrig', 'N')
    if isinstance(irrig_config, dict) and irrig_config.get('method') == 'automatic':
        irrig_mode = 'A'
    elif isinstance(irrig_config, dict) and irrig_config.get('method') == 'fixed':
        irrig_mode = 'R'
    elif isinstance(irrig_config, list) and irrig_config:
        irrig_mode = 'R'

    fert_events = params.get('fertilizer')
    ferti_mode = mgmt.get('ferti')
    if not ferti_mode:
        ferti_mode = 'R' if fert_events else 'N'

    resid_events = params.get('residue')
    resid_mode = mgmt.get('resid')
    if not resid_mode:
        resid_mode = 'R' if resid_events else 'N'

    # Translate harvest.option from the wizard/prefs UI into DSSAT's `harvs`
    # SimulationControls code:
    #   'auto'         → 'A'  (automatic, at maturity — no HARVEST section)
    #   'maturity'     → 'M'  (harvest at physiological maturity)
    #   'on_date'      → 'R'  (actual date in the HARVEST section)
    #   'growth_stage' → 'G'  (HSTG in the HARVEST section)
    #   'dap'          → 'R'  (HDATE computed as pdate + N days by _build_harvest)
    # Legacy values 'fixed_date' / 'reported_date' are treated as 'on_date'.
    # Explicit mgmt.harvs takes precedence if passed.
    harvest_cfg = params.get('harvest') or {}
    option = harvest_cfg.get('option') if isinstance(harvest_cfg, dict) else None
    if mgmt.get('harvs'):
        harvs = mgmt['harvs']
    elif option == 'maturity':
        harvs = 'M'
    elif option == 'growth_stage':
        harvs = 'G'
    elif option in ('on_date', 'dap', 'fixed_date', 'reported_date'):
        harvs = 'R'
    else:
        harvs = 'A'

    management = SCManagement(
        plant=mgmt.get('plant', 'R'),
        irrig=irrig_mode,
        ferti=ferti_mode,
        resid=resid_mode,
        harvs=harvs,
    )

    # Outputs — config-driven defaults
    outs = sc.get('outputs', {})
    outputs = SCOutputs(
        fname=outs.get('fname', 'N'),
        ovvew=outs.get('ovvew', 'Y'),
        sumry=outs.get('sumry', 'Y'),
        fropt=outs.get('fropt', 1),
        grout=outs.get('grout', get_config('output_grout', 'Y', user=_user)),
        caout=outs.get('caout', get_config('output_caout', 'Y', user=_user)),
        waout=outs.get('waout', get_config('output_waout', 'Y', user=_user)),
        niout=outs.get('niout', get_config('output_niout', 'Y', user=_user)),
        miout=outs.get('miout', get_config('output_miout', 'N', user=_user)),
        diout=outs.get('diout', get_config('output_diout', 'N', user=_user)),
        vbose=outs.get('vbose', 'Y'),
        chout=outs.get('chout', 'N'),
        opout=outs.get('opout', 'N'),
        fmopt=outs.get('fmopt', 'A'),
    )

    # Automatic management irrigation section
    am_irrigation = None
    if isinstance(irrig_config, dict) and irrig_config.get('method') == 'automatic':
        am_kwargs = {}
        if irrig_config.get('threshold') is not None:
            am_kwargs['ithrl'] = float(irrig_config['threshold'])
        if irrig_config.get('efficiency') is not None:
            am_kwargs['ireff'] = float(irrig_config['efficiency']) / 100.0
        am_irrigation = AMIrrigation(**am_kwargs)

    return SimulationControls(
        general=general,
        options=options,
        methods=methods,
        management=management,
        outputs=outputs,
        irrigation=am_irrigation,
    )


def _build_planting(params):
    """Build Planting from JSON dict."""
    p = params.get('planting', {})
    kwargs = {
        'pdate': _parse_date(p['pdate']),
        'ppop': float(p['ppop']),
        'plrs': float(p['plrs']),
    }
    if p.get('ppoe') is not None:
        kwargs['ppoe'] = float(p['ppoe'])
    if p.get('plds'):
        kwargs['plds'] = p['plds']
    if p.get('plme'):
        kwargs['plme'] = p['plme']
    if p.get('pldp') is not None:
        kwargs['pldp'] = float(p['pldp'])
    if p.get('plwt') is not None:
        kwargs['plwt'] = float(p['plwt'])
    if p.get('page') is not None:
        kwargs['page'] = float(p['page'])
    if p.get('penv') is not None:
        kwargs['penv'] = float(p['penv'])
    if p.get('plph') is not None:
        kwargs['plph'] = float(p['plph'])
    if p.get('sprl') is not None:
        kwargs['sprl'] = float(p['sprl'])
    if p.get('plrd') is not None:
        kwargs['plrd'] = float(p['plrd'])
    if p.get('edate'):
        kwargs['edate'] = _parse_date(p['edate'])

    return Planting(**kwargs)


def _resolve_event_date(ev, date_key, dap_key, pdate, label):
    """Resolve a management-event date.

    Inline user input carries an absolute date under ``date_key`` (e.g.
    ``fdate``); stored defaults carry a non-negative integer under
    ``dap_key`` (``fdap``) that resolves against the planting date. The
    absolute key wins when both are present. Returns ``None`` and logs a
    warning when neither path can produce a date.
    """
    from datetime import timedelta as _td

    raw_date = ev.get(date_key)
    if raw_date:
        d = _parse_date(raw_date)
        if d is not None:
            return d

    dap = ev.get(dap_key)
    if dap is not None:
        if pdate is None:
            logger.warning(
                "%s event has %s=%s but no planting date is available; skipping.",
                label, dap_key, dap,
            )
            return None
        try:
            dap_int = int(dap)
        except (TypeError, ValueError):
            logger.warning("%s event has invalid %s=%r; skipping.", label, dap_key, dap)
            return None
        if dap_int < 0:
            logger.warning(
                "%s event has negative %s=%d; skipping.", label, dap_key, dap_int,
            )
            return None
        return pdate + _td(days=dap_int)

    logger.warning(
        "%s event has neither %s nor %s; skipping.", label, date_key, dap_key,
    )
    return None


def _build_fertilizer(events, user_id=None, planting_params=None):
    """Build Fertilizer from event list.

    When ``events`` is empty/None, falls back to the user's/system's
    ``fallback_fertilizer`` DSSATConfig value (a full event list).
    Per-event field fallbacks (fmcd, facd, fdep) then resolve through
    DSSATConfig (user override → system default → hardcoded) when the
    event doesn't provide them.

    Events may carry either an absolute ``fdate`` (inline user input,
    where the planting date is known) or a ``fdap`` integer (stored
    defaults, resolved here from ``planting_params['pdate']``). Events
    that supply neither are skipped with a warning.
    """
    from .config import get_config, resolve_user
    _user = resolve_user(user_id)

    if not events:
        events = get_config('fallback_fertilizer', None, user=_user)
    if not events:
        return None

    pdate = _parse_date((planting_params or {}).get('pdate'))

    fert_events = []
    for ev in events:
        fdate = _resolve_event_date(ev, 'fdate', 'fdap', pdate, 'fertilizer')
        if fdate is None:
            continue
        kwargs = {
            'fdate': fdate,
            'fmcd': ev.get('fmcd') or get_config('fert_material', 'FE005', user=_user),
            'facd': ev.get('facd') or get_config('fert_application', 'AP002', user=_user),
            'fdep': float(ev.get('fdep') if ev.get('fdep') is not None
                          else get_config('fert_depth', 10, user=_user)),
            'famn': float(ev.get('famn', 0)),
        }
        for opt in ('famp', 'famk', 'famc', 'famo'):
            if ev.get(opt) is not None:
                kwargs[opt] = float(ev[opt])
        if ev.get('focd'):
            kwargs['focd'] = ev['focd']

        fert_events.append(FertilizerEvent(**kwargs))

    if not fert_events:
        return None
    return Fertilizer(table=fert_events)


def _build_irrigation(params, user_id=None, planting_params=None):
    """Build Irrigation from event list or dict.

    When ``params`` is empty, falls back to the user's/system's
    ``fallback_irrigation`` DSSATConfig value. Per-event `irop` and
    top-level `efir` / `idep` also flow through DSSATConfig when not
    explicitly provided.

    Fixed-schedule events may carry either an absolute ``idate`` (inline
    user input) or an ``idap`` integer (stored defaults), resolved here
    against ``planting_params['pdate']``.
    """
    from .config import get_config, resolve_user
    _user = resolve_user(user_id)

    if not params:
        params = get_config('fallback_irrigation', None, user=_user)
    if not params:
        return None

    # Handle both list of events and dict with table + top-level params
    if isinstance(params, list):
        events = params
        top_params = {}
    else:
        events = params.get('events', params.get('table', []))
        top_params = params

    if not events:
        return None

    default_irop = get_config('irrig_method', None, user=_user)
    pdate = _parse_date((planting_params or {}).get('pdate'))

    irr_events = []
    for ev in events:
        idate = _resolve_event_date(ev, 'idate', 'idap', pdate, 'irrigation')
        if idate is None:
            continue
        kwargs = {
            'idate': idate,
            'irval': float(ev['irval']),
        }
        irop = ev.get('irop') or default_irop
        if irop:
            kwargs['irop'] = irop
        irr_events.append(IrrigationEvent(**kwargs))

    if not irr_events:
        return None
    irr_kwargs = {'table': irr_events}

    efir = top_params.get('efir')
    if efir is None:
        efir = get_config('irrig_efficiency', None, user=_user)
    if efir is not None:
        irr_kwargs['efir'] = float(efir)

    idep = top_params.get('idep')
    if idep is None:
        idep = get_config('irrig_depth', None, user=_user)
    if idep is not None:
        irr_kwargs['idep'] = float(idep)

    if top_params.get('ithr') is not None:
        irr_kwargs['ithr'] = float(top_params['ithr'])

    return Irrigation(**irr_kwargs)


def _build_harvest(params, user_id=None, planting_params=None):
    """Build Harvest from JSON dict.

    Accepts the wizard/prefs shape (``{option, date, stage, dap, hcom, ...}``)
    or the raw DSSAT shape (``{hdate, hstg, ...}``). When ``params`` is empty,
    falls back to the user's/system's ``fallback_harvest`` DSSATConfig value.

    Option → HARVEST-section behavior:
      * ``auto`` / ``maturity`` (or empty)
          Returns None; no HARVEST section. ``harvs='A'`` / ``harvs='M'`` is
          set in ``_build_simulation_controls``.
      * ``on_date`` (aliases: ``fixed_date``, ``reported_date``)
          HDATE = ``params['date']``, HARVS='R'.
      * ``growth_stage``
          HSTG = ``params['stage']`` (a GSxxx code), HARVS='G'. HDATE is
          still required by the FileX grammar but the binary ignores it
          for harvs='G' — we use pdate as a filler if no date is given.
      * ``dap``
          HDATE computed as ``pdate + params['dap']`` days, HARVS='R'.
          Functionally identical to 'on_date' for single-season runs.
    """
    from datetime import timedelta
    from .config import get_config, resolve_user
    _user = resolve_user(user_id)

    # Fallback to the user's/system's configured fallback_harvest if nothing
    # was supplied on the experiment. Apply before shape-normalization.
    if not params:
        params = get_config('fallback_harvest', None, user=_user) or None
    if not params:
        return None

    option = params.get('option') if isinstance(params, dict) else None

    # 'auto' and 'maturity' do not need a HARVEST section — the mode is
    # carried entirely by SCManagement.harvs in _build_simulation_controls.
    if option in ('auto', 'maturity') or option == '':
        return None

    hdate = None
    if option == 'dap':
        dap = params.get('dap')
        if dap is None:
            return None
        planting = planting_params or {}
        pdate_raw = planting.get('pdate')
        pdate = _parse_date(pdate_raw)
        if pdate is None:
            return None
        try:
            hdate = pdate + timedelta(days=int(dap))
        except (TypeError, ValueError):
            return None
    elif option == 'growth_stage':
        # harvs='G': HDATE is required by the FileX grammar but the binary
        # reads HSTG. Use pdate as a safe filler if the UI didn't supply one.
        hdate_raw = params.get('hdate') or params.get('date')
        if hdate_raw:
            hdate = _parse_date(hdate_raw)
        else:
            planting = planting_params or {}
            hdate = _parse_date(planting.get('pdate'))
    else:
        # 'on_date', legacy 'fixed_date'/'reported_date', or raw DSSAT shape.
        hdate_raw = params.get('hdate') or params.get('date')
        if not hdate_raw:
            return None
        hdate = _parse_date(hdate_raw)

    if hdate is None:
        return None

    kwargs = {'hdate': hdate}

    # HSTG: from wizard 'stage' (preferred) or raw 'hstg'.
    stage = params.get('stage') or params.get('hstg')
    if stage:
        kwargs['hstg'] = stage

    if params.get('hsize'):
        kwargs['hsize'] = params['hsize']

    hcom = params.get('hcom') or get_config('harvest_component', None, user=_user)
    if hcom:
        kwargs['hcom'] = hcom

    _config_defaults = {
        'hpc': get_config('harvest_product_pct', None, user=_user),
        'hbpc': get_config('harvest_byproduct_pct', None, user=_user),
    }
    for opt in ('hpc', 'hbpc'):
        val = params.get(opt)
        if val is None:
            val = _config_defaults[opt]
        if val is not None:
            kwargs[opt] = float(val)

    return Harvest(**kwargs)


def _build_initial_conditions(params):
    """Build InitialConditions from JSON dict."""
    if not params:
        return None

    kwargs = {'pcr': params.get('pcr', 'MZ')}
    if params.get('icdat'):
        kwargs['icdat'] = _parse_date(params['icdat'])
    for opt in ('icrt', 'icnd', 'icrn', 'icre', 'icwd', 'icres', 'icren', 'icrep', 'icrip', 'icrid'):
        if params.get(opt) is not None:
            kwargs[opt] = float(params[opt])

    layers = params.get('layers', params.get('table', []))
    if layers:
        ic_layers = []
        for lyr in layers:
            lkw = {'icbl': float(lyr['icbl']), 'sh2o': float(lyr['sh2o'])}
            if lyr.get('snh4') is not None:
                lkw['snh4'] = float(lyr['snh4'])
            if lyr.get('sno3') is not None:
                lkw['sno3'] = float(lyr['sno3'])
            ic_layers.append(InitialConditionsLayer(**lkw))
        kwargs['table'] = ic_layers

    return InitialConditions(**kwargs)


def _build_residue(events, user_id=None, planting_params=None):
    """Build Residue from event list.

    When ``events`` is empty, falls back to ``fallback_residue`` from
    DSSATConfig. Per-event `rcod`, `rdep`, `ramt` also resolve through
    DSSATConfig when not set on the event.

    Events may carry either ``rdate`` (inline) or ``rdap`` (defaults).
    """
    from .config import get_config, resolve_user
    _user = resolve_user(user_id)

    if not events:
        events = get_config('fallback_residue', None, user=_user)
    if not events:
        return None

    default_rcod = get_config('residue_material', None, user=_user)
    default_rdep = get_config('residue_depth', None, user=_user)
    default_ramt = get_config('residue_amount', None, user=_user)
    pdate = _parse_date((planting_params or {}).get('pdate'))

    res_events = []
    for ev in events:
        rdate = _resolve_event_date(ev, 'rdate', 'rdap', pdate, 'residue')
        if rdate is None:
            continue
        kwargs = {'rdate': rdate}

        rcod = ev.get('rcod') or default_rcod
        if rcod:
            kwargs['rcod'] = rcod

        # rmet has no config default — per-event only
        if ev.get('rmet'):
            kwargs['rmet'] = ev['rmet']

        # Numeric fields: event value > config default
        _numeric_defaults = {
            'ramt': default_ramt,
            'rdep': default_rdep,
        }
        for opt in ('ramt', 'resn', 'resp', 'resk', 'rinp', 'rdep'):
            val = ev.get(opt)
            if val is None:
                val = _numeric_defaults.get(opt)
            if val is not None:
                kwargs[opt] = float(val)

        res_events.append(ResidueEvent(**kwargs))
    if not res_events:
        return None
    return Residue(table=res_events)


def _build_chemical(events, user_id=None, planting_params=None):
    """Build Chemical from event list.

    When ``events`` is empty, falls back to ``fallback_chemical`` from
    DSSATConfig. Per-event `chcod`, `chme`, `chdep` also resolve through
    DSSATConfig.

    Events may carry either ``cdate`` (inline) or ``cdap`` (defaults).
    """
    from .config import get_config, resolve_user
    _user = resolve_user(user_id)

    if not events:
        events = get_config('fallback_chemical', None, user=_user)
    if not events:
        return None

    default_chcod = get_config('chemical_material', None, user=_user)
    default_chme = get_config('chemical_method', None, user=_user)
    default_chdep = get_config('chemical_depth', None, user=_user)
    pdate = _parse_date((planting_params or {}).get('pdate'))

    chem_events = []
    for ev in events:
        cdate = _resolve_event_date(ev, 'cdate', 'cdap', pdate, 'chemical')
        if cdate is None:
            continue
        kwargs = {'cdate': cdate}

        chcod = ev.get('chcod') or default_chcod
        if chcod:
            kwargs['chcod'] = chcod

        chme = ev.get('chme') or default_chme
        if chme:
            kwargs['chme'] = chme

        if ev.get('chtarget'):
            kwargs['chtarget'] = ev['chtarget']

        # Numeric fields
        if ev.get('chamt') is not None:
            kwargs['chamt'] = float(ev['chamt'])

        chdep = ev.get('chdep')
        if chdep is None:
            chdep = default_chdep
        if chdep is not None:
            kwargs['chdep'] = float(chdep)

        chem_events.append(ChemicalEvent(**kwargs))
    if not chem_events:
        return None
    return Chemical(table=chem_events)


def _build_tillage(events, user_id=None, planting_params=None):
    """Build Tillage from event list.

    When ``events`` is empty, falls back to ``fallback_tillage`` from
    DSSATConfig. Per-event `timpl`, `tdep` also resolve through DSSATConfig.

    Events may carry either ``tdate`` (inline) or ``tdap`` (defaults).
    """
    from .config import get_config, resolve_user
    _user = resolve_user(user_id)

    if not events:
        events = get_config('fallback_tillage', None, user=_user)
    if not events:
        return None

    default_timpl = get_config('tillage_implement', None, user=_user)
    default_tdep = get_config('tillage_depth', None, user=_user)
    pdate = _parse_date((planting_params or {}).get('pdate'))

    till_events = []
    for ev in events:
        tdate = _resolve_event_date(ev, 'tdate', 'tdap', pdate, 'tillage')
        if tdate is None:
            continue
        kwargs = {'tdate': tdate}

        timpl = ev.get('timpl') or default_timpl
        if timpl:
            kwargs['timpl'] = timpl

        tdep = ev.get('tdep')
        if tdep is None:
            tdep = default_tdep
        if tdep is not None:
            kwargs['tdep'] = float(tdep)

        till_events.append(TillageEvent(**kwargs))
    if not till_events:
        return None
    return Tillage(table=till_events)


def _build_mow(events, user_id=None):
    """Build Mow from event list.

    Mow has no configurable fallbacks currently — `user_id` is accepted
    for signature consistency with the other management builders.
    """
    if not events:
        return None
    mow_events = []
    for ev in events:
        kwargs = {'mdate': _parse_date(ev['mdate'])}
        for opt in ('mowamt', 'rsplf', 'mession'):
            if ev.get(opt) is not None:
                kwargs[opt] = float(ev[opt]) if opt != 'mession' else ev[opt]
        mow_events.append(MowEvent(**kwargs))
    return Mow(table=mow_events)


def _df_to_records(df):
    """Convert a DataFrame to list of JSON-serializable records."""
    if df is None or df.empty:
        return None

    # Reset index to include date columns
    df = df.reset_index(drop=False) if df.index.name else df.copy()

    records = []
    for _, row in df.iterrows():
        record = {}
        for col in df.columns:
            val = row[col]
            if hasattr(val, 'isoformat'):
                record[col] = val.isoformat()
            elif hasattr(val, 'item'):
                record[col] = val.item()
            elif isinstance(val, float) and (math.isnan(val) or val == -99.0):
                record[col] = None
            else:
                record[col] = val
        records.append(record)
    return records


def run_simulation(params, progress_callback=None):
    """
    Build and run a DSSAT simulation from JSON parameters.

    Parameters
    ----------
    params : dict
        Full experiment parameters.
    progress_callback : callable, optional
        Called as ``progress_callback(stage, title, description)`` at each
        major build step.  Ignored when *None*.

    Returns simulation results or an error/missing_data status.
    """
    def _progress(stage, title, description=''):
        if progress_callback:
            try:
                progress_callback(stage, title, description)
            except Exception:
                pass

    # 1. Validate
    _progress('validating', 'Validating Parameters', 'Checking experiment configuration...')
    errors, warnings = validate_experiment(params)
    if errors:
        return {"status": "validation_error", "errors": errors, "warnings": warnings}

    crop_code = params['crop_code'].upper()
    cultivar_code = params['cultivar_code']
    dssat_model = params.get('dssat_model', '').upper() or None

    # 2. Build soil profile
    _progress('building_soil', 'Building Soil Profile', 'Constructing soil layers...')
    try:
        if params.get('soil_id'):
            soil = profile_to_dssat(params['soil_id'])
        elif params.get('inline_soil'):
            soil = dict_to_dssat_soil(params['inline_soil'])
        else:
            return {"status": "error", "errors": ["No soil data provided"]}
    except Exception as e:
        return {"status": "error", "errors": [f"Soil error: {e}"]}

    # 3. Build weather
    _progress('building_weather', 'Processing Weather Data', 'Loading weather records...')
    try:
        weather_data = resolve_weather(params)
    except ValueError as e:
        return {"status": "error", "errors": [str(e)]}

    # Check coverage
    sc = params.get('simulation_controls', {})
    sdate = _parse_date(sc.get('sdate'))
    nyers = sc.get('nyers', 1)

    coverage = check_weather_coverage(weather_data, sdate, nyers)
    if not coverage.get('complete', False):
        return {
            "status": "missing_data",
            "missing_count": coverage.get('total_missing', 0),
            "missing_dates": coverage.get('missing_dates', []),
            "weather_start_needed": coverage.get('weather_start_needed'),
            "weather_end_needed": coverage.get('weather_end_needed'),
            "data_start": coverage.get('data_start'),
            "data_end": coverage.get('data_end'),
        }

    try:
        weather = build_weather_station(weather_data)
    except Exception as e:
        return {"status": "error", "errors": [f"Weather error: {e}"]}

    # 4. Build field
    field_params = params.get('field', {})
    field = Field(
        id_field=field_params.get('id_field', 'SIMU0001'),
        wsta=weather,
        id_soil=soil,
        xcrd=field_params.get('lon') or field_params.get('longitude'),
        ycrd=field_params.get('lat') or field_params.get('latitude'),
        elev=field_params.get('elev') or field_params.get('elevation'),
        flsa=field_params.get('flsa'),
        flob=field_params.get('flob'),
        fldt=field_params.get('fldt'),
        fldd=field_params.get('fldd'),
        flds=field_params.get('flds'),
    )

    # 5. Build remaining FileX sections
    _progress('building_experiment', 'Building Experiment', 'Assembling crop, cultivar, management sections...')
    try:
        _user_id = params.get('user_id')
        cultivar = resolve_cultivar(crop_code, cultivar_code, dssat_model)
        sim_controls = _build_simulation_controls(params)
        planting = _build_planting(params)
        harvest = _build_harvest(
            params.get('harvest'), user_id=_user_id,
            planting_params=params.get('planting'),
        )
        initial_conditions = _build_initial_conditions(params.get('initial_conditions'))
        _planting_params = params.get('planting')
        fertilizer = _build_fertilizer(
            params.get('fertilizer'), user_id=_user_id,
            planting_params=_planting_params,
        )
        irrigation = _build_irrigation(
            params.get('irrigation'), user_id=_user_id,
            planting_params=_planting_params,
        )
        residue = _build_residue(
            params.get('residue'), user_id=_user_id,
            planting_params=_planting_params,
        )
        chemical = _build_chemical(
            params.get('chemical'), user_id=_user_id,
            planting_params=_planting_params,
        )
        tillage = _build_tillage(
            params.get('tillage'), user_id=_user_id,
            planting_params=_planting_params,
        )
        mow = _build_mow(params.get('mow'), user_id=_user_id)
    except Exception as e:
        return {"status": "error", "errors": [f"Experiment build error: {e}"]}

    # 5b. Initialize file capture container. Inputs are captured post-run from
    # dssat.run_path (where DSSATTools writes them as part of run_treatment),
    # so we get byte-for-byte the same files DSSAT actually consumed rather
    # than a re-derivation via private _write_* helpers.
    dssat_files = {'input': {}, 'output': {}}

    # 6. Run DSSAT
    _progress('running', 'Running DSSAT', 'Executing crop simulation model...')
    dssat = DSSAT()
    try:
        summary = dssat.run_treatment(
            field=field,
            cultivar=cultivar,
            planting=planting,
            simulation_controls=sim_controls,
            harvest=harvest,
            initial_conditions=initial_conditions,
            fertilizer=fertilizer,
            irrigation=irrigation,
            residue=residue,
            chemical=chemical,
            tillage=tillage,
            mow=mow,
            verbose=False,
        )

        # 7. Parse output
        _progress('parsing', 'Parsing Results', 'Extracting output tables...')
        result = {
            "status": "completed",
            "summary": _clean_summary(summary),
            "warnings": warnings,
        }

        # Output tables
        if hasattr(dssat, 'output_tables') and dssat.output_tables:
            tables = dssat.output_tables
            if 'PlantGro' in tables:
                result['plant_growth'] = _df_to_records(tables['PlantGro'])
            if 'SoilWat' in tables:
                result['soil_water'] = _df_to_records(tables['SoilWat'])
            if 'SoilOrg' in tables:
                result['soil_organic'] = _df_to_records(tables['SoilOrg'])
            if 'SoilNi' in tables:
                result['soil_nitrogen'] = _df_to_records(tables['SoilNi'])
            if 'Weather' in tables:
                result['weather_output'] = _df_to_records(tables['Weather'])

        # Overview text
        if hasattr(dssat, 'output_files') and dssat.output_files:
            result['overview'] = dssat.output_files.get('OVERVIEW', '')
            for key, content in dssat.output_files.items():
                if isinstance(content, str):
                    dssat_files['output'][key] = content

        # Capture input files from disk (DSSATTools wrote them as part of
        # run_treatment, so they are guaranteed to exist and exactly match
        # what DSSAT consumed). See DSSATTools/run.py:212-247 for the writes.
        _capture_input_files(dssat, dssat_files['input'])
        # Reference files (.SPE, GRSTAGE.CDE) live in the symlinked Data
        # tree — copy them in so the experiment record is self-contained.
        _capture_reference_files(cultivar, dssat_files['input'])

        result['dssat_files'] = dssat_files
        logger.info("dssat_files captured: input=%s, output=%s",
                     list(dssat_files.get('input', {}).keys()),
                     list(dssat_files.get('output', {}).keys()))

        return result

    except Exception as e:
        logger.exception("DSSAT simulation failed")
        # Capture whatever DSSAT wrote before crashing — input files
        # (FILEX, SOIL.SOL, .CUL, .WTH) and any partial output files
        # (WARNING.OUT, ERROR.OUT, OVERVIEW.OUT). Without this we lose
        # the FILEX immediately when the run dir is cleaned up, making
        # IPSIM/IPFERT/IPSOIL errors near-impossible to diagnose.
        try:
            _capture_input_files(dssat, dssat_files['input'])
        except Exception:
            logger.warning("Failed to capture inputs after DSSAT failure",
                           exc_info=True)
        try:
            _capture_reference_files(cultivar, dssat_files['input'])
        except Exception:
            logger.warning("Failed to capture reference files after DSSAT failure",
                           exc_info=True)
        try:
            run_path = getattr(dssat, 'run_path', None)
            if run_path and os.path.isdir(run_path):
                for fname in os.listdir(run_path):
                    if fname.upper().endswith(('.OUT', '.LST')):
                        try:
                            with open(os.path.join(run_path, fname)) as fh:
                                dssat_files['output'][fname.rsplit('.', 1)[0]] = fh.read()
                        except Exception:
                            pass
        except Exception:
            logger.warning("Failed to capture outputs after DSSAT failure",
                           exc_info=True)

        stdout = dssat_files['output'].get('OVERVIEW', '')
        return {
            "status": "failed",
            "errors": [str(e)],
            "stdout": stdout,
            "warnings": warnings,
            "dssat_files": dssat_files,
        }
    finally:
        try:
            dssat.close()
        except Exception:
            pass


def validate_experiment_only(params):
    """Validate without running. Returns validation results."""
    errors, warnings = validate_experiment(params)

    # Also try building DSSATTools objects to catch construction errors
    build_errors = []

    if not errors:
        try:
            _build_simulation_controls(params)
        except Exception as e:
            build_errors.append(f"SimulationControls: {e}")

        try:
            _build_planting(params)
        except Exception as e:
            build_errors.append(f"Planting: {e}")

        _validate_user_id = params.get('user_id')
        _validate_planting = params.get('planting')
        if params.get('fertilizer'):
            try:
                _build_fertilizer(
                    params['fertilizer'], user_id=_validate_user_id,
                    planting_params=_validate_planting,
                )
            except Exception as e:
                build_errors.append(f"Fertilizer: {e}")

        if params.get('irrigation'):
            try:
                _build_irrigation(
                    params['irrigation'], user_id=_validate_user_id,
                    planting_params=_validate_planting,
                )
            except Exception as e:
                build_errors.append(f"Irrigation: {e}")

        crop_code = params.get('crop_code', '').upper()
        cultivar_code = params.get('cultivar_code', '')
        v_dssat_model = params.get('dssat_model', '').upper() or None
        try:
            resolve_cultivar(crop_code, cultivar_code, v_dssat_model)
        except KeyError:
            pass  # crop_code validation handles unknown codes
        except Exception as e:
            build_errors.append(f"Crop/Cultivar: {e}")

        if params.get('soil_id'):
            try:
                profile_to_dssat(params['soil_id'])
            except Exception as e:
                build_errors.append(f"Soil profile: {e}")
        elif params.get('inline_soil'):
            try:
                dict_to_dssat_soil(params['inline_soil'])
            except Exception as e:
                build_errors.append(f"Inline soil: {e}")

    all_errors = errors + build_errors

    return {
        "valid": len(all_errors) == 0,
        "errors": all_errors,
        "warnings": warnings,
    }


def _yyddd_to_date(val):
    """Parse a DSSAT YYYYDDD-format date (e.g. ``2024202`` for day-of-year
    202 in 2024) into a ``datetime.date``. Returns None for missing/-99.
    Accepts int/float/str."""
    from datetime import date as _date, timedelta as _td
    if val is None:
        return None
    try:
        v = int(round(float(val)))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    year = v // 1000
    doy = v % 1000
    if year < 1900 or year > 2100 or doy < 1 or doy > 366:
        return None
    try:
        return _date(year, 1, 1) + _td(days=doy - 1)
    except (ValueError, OverflowError):
        return None


def _dap_from(target_yyddd, planting_yyddd):
    """Days-after-planting between two YYYYDDD-format dates. DSSAT's
    Summary.OUT records dates (MDAT/ADAT/EDAT/PDAT) in YYYYDDD form and
    does not emit separate ``mat``/``flo`` integer DAP fields. Compute
    DAP here so downstream artifacts can keep using the conventional
    field names without each call site re-parsing the date format."""
    p = _yyddd_to_date(planting_yyddd)
    t = _yyddd_to_date(target_yyddd)
    if p is None or t is None:
        return None
    delta = (t - p).days
    return delta if delta >= 0 else None


def _clean_summary(summary):
    """Clean the summary dict, converting -99 to None and deriving
    standard ``flo``/``mat`` (days-after-planting integers) from the
    raw ``adat``/``mdat``/``pdat`` YYYYDDD dates DSSAT actually outputs.
    Existing callers expect ``flo``/``mat`` for chart/key-result
    rendering — we backfill them here so a single conversion point
    keeps every artifact builder consistent."""
    if not summary:
        return {}
    cleaned = {}
    for k, v in summary.items():
        cleaned[k] = _safe_float(v) if isinstance(v, (int, float)) else v
    # Backfill DAP-form phenology stages from YYYYDDD dates.
    pdat = cleaned.get('pdat')
    if cleaned.get('flo') is None:
        flo = _dap_from(cleaned.get('adat'), pdat)
        if flo is not None:
            cleaned['flo'] = flo
    if cleaned.get('mat') is None:
        mat = _dap_from(cleaned.get('mdat'), pdat)
        if mat is not None:
            cleaned['mat'] = mat
    if cleaned.get('emergence_dap') is None:
        emerg = _dap_from(cleaned.get('edat'), pdat)
        if emerg is not None:
            cleaned['emergence_dap'] = emerg
    return cleaned
