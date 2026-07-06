"""
Batch execution support for DSSATTools.

Provides DSSATBatch class that accumulates multiple treatments and runs
them in a single DSSAT invocation using batch mode with DSSBatch.v48.
"""

import os
import sys
import shutil
import tempfile
import random
import string
import subprocess
import io
import re
import pandas as pd

from . import VERSION
from . import __file__ as module_path
from .crop import Crop
from .filex import (
    Planting, Cultivar, Harvest, InitialConditions, Fertilizer,
    SoilAnalysis, Irrigation, Residue, Chemical, Tillage, Field,
    SimulationControls, Mow, Treatment,
)
from .base.partypes import SECTION_HEADERS, TabularRecord
from .soil import SoilProfile
from .weather import WeatherStation
from .base.utils import detect_encoding
from .run import (
    BIN_PATH, DSSAT_HOME, CONFILE, TMP_BASE, CRD_PATH, SLD_PATH, STD_PATH,
    WIN_SHUTIL_KWARGS,
)

# Field tier column lists (matching Field.__init__)
FIELD_TIER1 = [
    "id_field", "wsta", "flsa", "flob", "fldt", "fldd", "flds",
    "flst", "sltx", "sldp", "id_soil", "flname"
]
FIELD_TIER2 = [
    "xcrd", "ycrd", "elev", "area", "slen", "flwr", "slas", "flhst",
    "fhdur"
]


def _format_header(key, fmt):
    """Format a column header key using its format spec."""
    if fmt == "%y%j":
        fmt = ">5"
    if fmt == "%Y%j":
        fmt = ">7"
    if fmt[0] == ".":
        leading = "."
        fmt = fmt[1:]
    else:
        leading = ""
    fmt = leading + fmt.split(".")[0]
    return format(key.upper(), fmt)


class DSSATBatch:
    """
    Accumulates multiple treatments and runs them in a single DSSAT
    batch invocation.

    Usage::

        batch = DSSATBatch()
        batch.add_treatment(field=field1, cultivar=crop, planting=planting,
                            simulation_controls=sc, fertilizer=fert)
        batch.add_treatment(field=field2, cultivar=crop, planting=planting,
                            simulation_controls=sc, fertilizer=fert)
        results = batch.run(verbose=False)
        batch.close()

    Object identity (``id()``) is used for deduplication: if the same
    Planting instance is passed to multiple treatments, it is stored as
    a single factor level. Different instances become separate levels.
    """

    def __init__(self, run_path=None):
        if not run_path:
            run_path = os.path.join(
                TMP_BASE,
                'dssatbatch' + ''.join(
                    random.choices(string.ascii_lowercase, k=8)
                )
            )
        if not os.path.exists(run_path):
            os.mkdir(run_path)
        if not os.path.exists(os.path.join(run_path, "Weather")):
            os.mkdir(os.path.join(run_path, "Weather"))
        self.run_path = run_path
        self.output_files = {}
        self._output = {}

        # Registries: id(obj) -> (1-indexed level, obj)
        self._weather_stations = {}
        self._soil_profiles = {}
        self._cultivars = {}
        self._plantings = {}
        self._sim_controls = {}
        self._fertilizers = {}
        self._irrigations = {}
        self._harvests = {}
        self._initial_conditions = {}
        self._residues = {}
        self._chemicals = {}
        self._tillages = {}

        # Field dedup: (weather_level, soil_level) -> field_level
        self._fields = {}
        self._field_objects = {}  # field_level -> Field

        # Treatment list
        self._treatments = []

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------

    def _register(self, obj, registry):
        """Register an object by identity, returning its 1-indexed level.
        Returns 0 for None (optional section not provided)."""
        if obj is None:
            return 0
        obj_id = id(obj)
        if obj_id not in registry:
            registry[obj_id] = (len(registry) + 1, obj)
        return registry[obj_id][0]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_treatment(self, field, cultivar, planting, simulation_controls,
                      harvest=None, initial_conditions=None, fertilizer=None,
                      soil_analysis=None, irrigation=None, residue=None,
                      chemical=None, tillage=None, mow=None):
        """
        Add a treatment to the batch.

        Parameters are the same as ``DSSAT.run_treatment()``.
        Returns the 1-indexed treatment number.
        """
        assert len(self._treatments) < 99, "Maximum 99 treatments"
        assert isinstance(field, Field), \
            "field must be a Field instance"
        assert issubclass(type(cultivar), Crop), \
            "cultivar must be a Crop instance"
        assert isinstance(planting, Planting), \
            "planting must be a Planting instance"
        assert isinstance(simulation_controls, SimulationControls), \
            "simulation_controls must be a SimulationControls instance"
        if harvest:
            assert isinstance(harvest, Harvest)
        if initial_conditions:
            assert isinstance(initial_conditions, InitialConditions)
        if fertilizer:
            assert isinstance(fertilizer, Fertilizer)
        if irrigation:
            assert isinstance(irrigation, Irrigation)
        if residue:
            assert isinstance(residue, Residue)
        if chemical:
            assert isinstance(chemical, Chemical)
        if tillage:
            assert isinstance(tillage, Tillage)

        # Extract sub-objects from Field
        weather = field["wsta"]
        soil = field["id_soil"]

        # Register unique objects
        w_level = self._register(weather, self._weather_stations)
        s_level = self._register(soil, self._soil_profiles)
        cu_level = self._register(cultivar, self._cultivars)
        mp_level = self._register(planting, self._plantings)
        sm_level = self._register(simulation_controls, self._sim_controls)
        mf_level = self._register(fertilizer, self._fertilizers)
        mi_level = self._register(irrigation, self._irrigations)
        mh_level = self._register(harvest, self._harvests)
        ic_level = self._register(initial_conditions,
                                  self._initial_conditions)
        mr_level = self._register(residue, self._residues)
        mc_level = self._register(chemical, self._chemicals)
        mt_level = self._register(tillage, self._tillages)

        # Field dedup by (weather, soil) pair
        field_key = (w_level, s_level)
        if field_key not in self._fields:
            fl_level = len(self._fields) + 1
            self._fields[field_key] = fl_level
            self._field_objects[fl_level] = field
        fl_level = self._fields[field_key]

        trt_num = len(self._treatments) + 1
        self._treatments.append({
            'trt': trt_num,
            'cu': cu_level,
            'fl': fl_level,
            'sa': 0,
            'ic': ic_level,
            'mp': mp_level,
            'mi': mi_level,
            'mf': mf_level,
            'mr': mr_level,
            'mc': mc_level,
            'mt': mt_level,
            'me': 0,
            'mh': mh_level,
            'sm': sm_level,
        })
        return trt_num

    def run(self, verbose=True):
        """
        Run all accumulated treatments in a single DSSAT batch invocation.

        Returns a list of dicts, one per treatment, each containing a
        ``summary`` dict with output statistics.
        """
        assert self._treatments, "No treatments added"

        # First cultivar determines crop code and model
        _, first_cultivar = next(iter(self._cultivars.values()))
        crop_code = first_cultivar.code

        # Leave SMODEL blank — DSSAT infers the model from the cultivar
        # file (.CUL) at runtime. Writing ``MZCER`` (5 chars) into the
        # fixed-width SMODEL column results in DSSAT mis-parsing it as
        # ``MZCE048`` (a non-existent model) and erroring out at IPSIM.
        # The stakeholder reference FILEX confirms this convention —
        # see data/4_AlexG/INPUTS/NSNS0000.MZX.
        for _, (_, sc) in self._sim_controls.items():
            sc["general"]["smodel"] = ""

        # Clean previous outputs
        for f in os.listdir(self.run_path):
            if f[-3:] in ('OUT', 'INP', 'INH'):
                os.remove(os.path.join(self.run_path, f))

        # Assign auto INSI codes and soil IDs, saving originals
        saved_insi = {}
        for obj_id, (level, ws) in self._weather_stations.items():
            saved_insi[obj_id] = str(ws["insi"]).strip()
            ws["insi"] = f"WS{level:02d}"

        saved_soil_names = {}
        for obj_id, (level, soil) in self._soil_profiles.items():
            saved_soil_names[obj_id] = str(soil["name"]).strip()
            soil["name"] = f"SI{level:08d}"

        try:
            self._write_soil_file()
            self._write_weather_files()
            self._write_cultivar_files()

            filex_name = f'EXPEFILE.{crop_code}X'
            filex_path = os.path.join(self.run_path, filex_name)
            with open(filex_path, 'w') as f:
                f.write(self._build_filex(crop_code))

            batch_path = os.path.join(self.run_path, 'DSSBatch.v48')
            with open(batch_path, 'w') as f:
                f.write(self._build_batch_file(filex_name))

            self._write_config(first_cultivar)

            # Run DSSAT in batch mode
            exc_args = [BIN_PATH, 'B', 'DSSBatch.v48']
            excinfo = subprocess.run(
                exc_args,
                cwd=self.run_path,
                capture_output=True,
                text=True,
                env={"DSSAT_HOME": DSSAT_HOME},
            )
            excinfo.stdout = re.sub(r"\n{2,}", "\n", excinfo.stdout)
            excinfo.stdout = re.sub(r"\n$", "", excinfo.stdout)
            self.stdout = excinfo.stdout.strip()

            if verbose:
                for line in excinfo.stdout.split("\n"):
                    sys.stdout.write(line + '\n')

            if excinfo.returncode != 0:
                error_file = os.path.join(self.run_path, "ERROR.OUT")
                if os.path.exists(error_file):
                    with open(error_file, 'r') as f:
                        error_text = f.read()
                    raise RuntimeError(
                        f"DSSAT batch execution failed:\n{error_text}"
                    )
                raise RuntimeError(
                    f"DSSAT batch execution failed: {excinfo.stderr}"
                )

            self._fetch_output()
            return self._parse_summary()

        finally:
            # Restore original INSI codes and soil names
            for obj_id, (_, ws) in self._weather_stations.items():
                if obj_id in saved_insi:
                    ws["insi"] = saved_insi[obj_id]
            for obj_id, (_, soil) in self._soil_profiles.items():
                if obj_id in saved_soil_names:
                    soil["name"] = saved_soil_names[obj_id]

    def close(self):
        """Remove the batch execution directory."""
        shutil.rmtree(self.run_path, **WIN_SHUTIL_KWARGS)

    # ------------------------------------------------------------------
    # File writers
    # ------------------------------------------------------------------

    def _write_soil_file(self):
        """Write combined SOIL.SOL with all unique soil profiles."""
        sol_path = os.path.join(self.run_path, "SOIL.SOL")
        with open(sol_path, 'w') as f:
            f.write("*SOILS: General DSSAT Soil Input File\n\n")
            for _, (_, soil) in sorted(
                self._soil_profiles.items(), key=lambda x: x[1][0]
            ):
                # _write_sol() includes its own file header; extract just
                # the profile data (everything after the 2 header lines).
                sol_text = soil._write_sol()
                lines = sol_text.split('\n')
                profile_lines = lines[2:]  # skip "*SOILS: ..." and blank
                f.write('\n'.join(profile_lines))
                if not sol_text.endswith('\n'):
                    f.write('\n')

    def _write_weather_files(self):
        """Write a .WTH file for each unique weather station."""
        for _, (_, ws) in sorted(
            self._weather_stations.items(), key=lambda x: x[1][0]
        ):
            wth_year = ws.table[0]["date"].year
            wth_len = ws.table[-1]["date"].year - wth_year + 1
            wth_filename = (
                f'{ws["insi"]}{str(wth_year)[2:]}{wth_len:02d}.WTH'
            )
            wth_path = os.path.join(
                self.run_path, "Weather", wth_filename
            )
            with open(wth_path, 'w') as f:
                f.write(ws._write_wth())

    def _write_cultivar_files(self):
        """Write .CUL and .ECO files for each unique cultivar."""
        written_spe = set()
        for _, (_, cultivar) in self._cultivars.items():
            spe_base = cultivar.spe_file[:-3]
            if spe_base in written_spe:
                continue
            written_spe.add(spe_base)

            cul_path = os.path.join(self.run_path, spe_base + "CUL")
            with open(cul_path, 'w') as f:
                f.write(cultivar._write_cul())

            if cultivar.eco_dtypes:
                eco_path = os.path.join(self.run_path, spe_base + "ECO")
                with open(eco_path, 'w') as f:
                    f.write(cultivar._write_eco())

    def _write_config(self, cultivar):
        """Write DSSATPRO configuration file."""
        config_path = os.path.join(self.run_path, CONFILE)
        with open(config_path, 'w') as f:
            f.write(
                f'WED    {os.path.join(self.run_path, "Weather")}\n'
            )
            f.write(
                f'M{cultivar.code}    {self.run_path} dscsm048 '
                f'{cultivar.smodel}{VERSION}\n'
            )
            f.write(f'CRD    {CRD_PATH}\n')
            f.write(f'PSD    {os.path.join(DSSAT_HOME, "Pest")}\n')
            f.write(f'SLD    {SLD_PATH}\n')
            f.write(f'STD    {STD_PATH}\n')

    # ------------------------------------------------------------------
    # FileX builder
    # ------------------------------------------------------------------

    def _build_filex(self, crop_code):
        """Build multi-treatment FileX string.

        DSSAT's *EXP.DETAILS* line accepts an experiment-name code that's
        exactly 8 chars + 2-letter crop = 10 chars total. The previous
        ``EXPEFILE{yy01}{crop}`` template produced 14 chars and DSSAT's
        IPEXP parser fails with error 5010 at the *CULTIVARS* line. Use
        the same compact form ``CHAT{yy01}{crop}`` (10 chars) that
        ``filex.create_filex`` uses for single-treatment runs.
        """
        _, first_sc = next(iter(self._sim_controls.values()))
        sdate = first_sc["general"]["sdate"]
        exp_name = f'CHAT{sdate.strftime("%y01")}{crop_code}'

        out = f"*EXP.DETAILS: {exp_name}\n\n"
        out += self._build_treatments_section() + "\n"
        out += self._build_cultivars_section() + "\n"
        out += self._build_fields_section() + "\n"
        out += self._build_record_section(self._plantings) + "\n"

        # Optional sections
        for registry in (
            self._initial_conditions,
            self._irrigations,
            self._fertilizers,
            self._residues,
            self._chemicals,
            self._tillages,
        ):
            section = self._build_optional_section(registry)
            if section:
                out += section + "\n"

        # Harvest (Record, not TabularRecord)
        harvest_section = self._build_optional_record_section(self._harvests)
        if harvest_section:
            out += harvest_section + "\n"

        out += self._build_simulation_controls_section()
        return out

    def _build_treatments_section(self):
        """Build the *TREATMENTS section."""
        # Use a dummy Treatment to get the column header
        dummy = Treatment(
            r=1, o=0, c=0, tname="", cu=1, fl=1, sa=0,
            ic=0, mp=1, mi=0, mf=0, mr=0, mc=0, mt=0, me=0, mh=0, sm=1,
        )
        header = ["@" + dummy.prefix.upper()]
        for key in dummy.pars_fmt:
            header.append(_format_header(key, dummy.pars_fmt[key]))

        out = SECTION_HEADERS["Treatment"] + "\n"
        out += " ".join(header) + "\n"

        for t in self._treatments:
            trt_obj = Treatment(
                r=1, o=0, c=0,
                tname=f"TRT_{t['trt']}",
                cu=t['cu'], fl=t['fl'], sa=t['sa'], ic=t['ic'],
                mp=t['mp'], mi=t['mi'], mf=t['mf'], mr=t['mr'],
                mc=t['mc'], mt=t['mt'], me=t['me'], mh=t['mh'],
                sm=t['sm'],
            )
            out += f"{t['trt']:>2d} " + trt_obj._write_row()
        return out

    def _build_cultivars_section(self):
        """Build the *CULTIVARS section."""
        out = "*CULTIVARS\n@C CR INGENO CNAME\n"
        for _, (level, cultivar) in sorted(
            self._cultivars.items(), key=lambda x: x[1][0]
        ):
            # Extract the data line from the single-level output
            section = cultivar._write_section()
            lines = [l for l in section.split('\n') if l.strip()]
            data_line = lines[-1]  # " 1 MZ IB0001 CultivarName"
            # Strip the original level prefix (" 1 ") and replace
            rest = data_line.lstrip()  # "1 MZ IB0001 ..."
            # Skip past the level number and space
            idx = 0
            while idx < len(rest) and rest[idx].isdigit():
                idx += 1
            rest = rest[idx:].lstrip()  # "MZ IB0001 ..."
            out += f"{level:>2d} {rest}\n"
        return out

    def _build_fields_section(self):
        """Build the *FIELDS section (2-tier format)."""
        sorted_fields = sorted(
            self._field_objects.items(), key=lambda x: x[0]
        )
        first_field = sorted_fields[0][1]

        out = "*FIELDS\n"

        # Tier 1
        header = ["@" + first_field.prefix.upper()]
        for key in FIELD_TIER1:
            header.append(
                _format_header(key, first_field.pars_fmt[key])
            )
        out += " ".join(header) + "\n"
        for level, field in sorted_fields:
            values = [field[key].str for key in FIELD_TIER1]
            out += f"{level:>2d} " + " ".join(values) + "\n"

        # Tier 2
        header = ["@" + first_field.prefix.upper()]
        for key in FIELD_TIER2:
            header.append(
                _format_header(key, first_field.pars_fmt[key])
            )
        out += " ".join(header) + "\n"
        for level, field in sorted_fields:
            values = [field[key].str for key in FIELD_TIER2]
            out += f"{level:>2d} " + " ".join(values) + "\n"

        return out

    def _build_record_section(self, registry):
        """Build a simple Record section with multiple levels."""
        if not registry:
            return ""
        items = sorted(registry.values(), key=lambda x: x[0])
        _, first_obj = items[0]
        cls_name = type(first_obj).__name__

        out = SECTION_HEADERS[cls_name] + "\n"

        # Column header
        header = ["@" + first_obj.prefix.upper()]
        for key in first_obj.pars_fmt:
            header.append(
                _format_header(key, first_obj.pars_fmt[key])
            )
        out += " ".join(header) + "\n"

        # Data rows
        for level, obj in items:
            out += f"{level:>2d} " + obj._write_row()

        return out

    def _build_optional_record_section(self, registry):
        """Build an optional Record section, skipping None entries."""
        filtered = {k: v for k, v in registry.items() if v[1] is not None}
        if not filtered:
            return ""
        return self._build_record_section(filtered)

    def _build_optional_section(self, registry):
        """Build an optional section (Record or TabularRecord)."""
        filtered = {k: v for k, v in registry.items() if v[1] is not None}
        if not filtered:
            return ""
        items = sorted(filtered.values(), key=lambda x: x[0])
        _, first_obj = items[0]

        if isinstance(first_obj, TabularRecord):
            return self._build_tabular_section(items)
        else:
            return self._build_record_section(filtered)

    def _build_tabular_section(self, items):
        """Build a TabularRecord section with multiple levels."""
        _, first_obj = items[0]
        cls_name = type(first_obj).__name__

        out = SECTION_HEADERS[cls_name] + "\n"

        has_top_level = len(first_obj.dtypes) > 0
        if has_top_level:
            # Top-level column header
            header = ["@" + first_obj.prefix.upper()]
            for key in first_obj.pars_fmt:
                header.append(
                    _format_header(key, first_obj.pars_fmt[key])
                )
            out += " ".join(header) + "\n"
            # Top-level data rows
            for level, obj in items:
                out += f"{level:>2d} " + obj._write_row()

        # Table header (from first object with a non-empty table)
        table_header_written = False
        for _, obj in items:
            if len(obj.table) > 0:
                table_str = obj.table._write_table().split("\n")
                out += f"@{first_obj.prefix.upper()} {table_str[0]}\n"
                table_header_written = True
                break

        if not table_header_written:
            return out

        # Table event rows, grouped by level. Format the level
        # right-justified to width 2 so the column alignment stays
        # consistent across single-digit (T1-T9) and double-digit
        # (T10+) levels. Without this, T10's row gets an extra leading
        # space, shifting FDATE/FMCD/FACD one column right and causing
        # IPFERT (and equivalent IRRIG/RESID/CHEM/TILL parsers) to fail
        # at the first multi-digit treatment.
        for level, obj in items:
            if len(obj.table) > 0:
                table_str = obj.table._write_table().split("\n")
                # Skip header line (index 0); skip trailing empty
                for row in table_str[1:]:
                    if row.strip():
                        out += f"{level:>2d} {row}\n"

        return out

    def _build_simulation_controls_section(self):
        """Build the *SIMULATION CONTROLS section.

        DSSAT's IPSIM parser expects each simulation-control level's
        subsections to be **grouped by level** (GE+OP+ME+MA+OU then
        AUTOMATIC MANAGEMENT), not grouped by subsection across all
        levels. The previous "all GE rows, then all OP rows, ..." layout
        parsed fine for a single level but tripped IPSIM error 5010 at
        the OPTIONS header line as soon as a multi-level section
        appeared (e.g. ensemble/sensitivity with per-treatment sdates).
        Reference layout: ``data/4_AlexG/INPUTS/NSNS0000.MZX``.
        """
        items = sorted(self._sim_controls.values(), key=lambda x: x[0])

        subsections = [
            ("general", "GE",
             "@N GENERAL     NYERS NREPS START SDATE RSEED "
             "SNAME.................... SMODEL"),
            ("options", "OP",
             "@N OPTIONS     WATER NITRO SYMBI PHOSP POTAS "
             "DISES  CHEM  TILL   CO2"),
            ("methods", "ME",
             "@N METHODS     WTHER INCON LIGHT EVAPO INFIL "
             "PHOTO HYDRO NSWIT MESOM MESEV MESOL"),
            ("management", "MA",
             "@N MANAGEMENT  PLANT IRRIG FERTI RESID HARVS"),
            ("outputs", "OU",
             "@N OUTPUTS     FNAME OVVEW SUMRY FROPT GROUT "
             "CAOUT WAOUT NIOUT MIOUT DIOUT VBOSE CHOUT OPOUT FMOPT"),
        ]

        am_subsections = [
            ("planting", "PL",
             "@N PLANTING    PFRST PLAST PH2OL PH2OU PH2OD PSTMX PSTMN"),
            ("irrigation", "IR",
             "@N IRRIGATION  IMDEP ITHRL ITHRU IROFF IMETH IRAMT IREFF"),
            ("nitrogen", "NI",
             "@N NITROGEN    NMDEP NMTHR NAMNT NCODE NAOFF"),
            ("residues", "RE",
             "@N RESIDUES    RIPCN RTIME RIDEP"),
            ("harvest", "HA",
             "@N HARVEST     HFRST HLAST HPCNP HPCNR"),
        ]

        out = "*SIMULATION CONTROLS\n"

        for level_idx, (level, sc) in enumerate(items):
            for sub_key, sub_code, header_line in subsections:
                out += header_line + "\n"
                out += (
                    f"{level:>2d} {sub_code}          "
                    f"{sc[sub_key]._write_row()}"
                )

            out += "\n@  AUTOMATIC MANAGEMENT\n"
            for sub_key, sub_code, header_line in am_subsections:
                out += header_line + "\n"
                out += (
                    f"{level:>2d} {sub_code}          "
                    f"{sc[sub_key]._write_row()}"
                )

            # Blank line between levels (none after the last one — the
            # caller appends a final newline).
            if level_idx < len(items) - 1:
                out += "\n"

        return out

    # ------------------------------------------------------------------
    # DSSBatch.v48
    # ------------------------------------------------------------------

    def _build_batch_file(self, filex_name):
        """Build DSSBatch.v48 content.

        DSSAT batch mode reads ``DSSBatch.v48`` to know which FILEX +
        treatment numbers to run. The expected header is the standard
        5-column form (``TRTNO RP SQ OP CO``); the previous template
        emitted a garbled ``TESSION ROESSIONMESESSION`` and only 3 of
        the 5 numeric columns, which DSSAT's IPEXP parser rejects with
        error 5010 once it advances past the FILEX header. Match the
        canonical format from ``Data/BatchFiles/*.v48``.
        """
        out = "$BATCH(SEASONAL)\n!\n"
        out += (
            "@FILEX"
            + " " * 88
            + "TRTNO     RP     SQ     OP     CO\n"
        )
        for t in self._treatments:
            out += (
                f"{filex_name:<94s}"
                f"{t['trt']:>5d}{1:>7d}{0:>7d}{0:>7d}{0:>7d}\n"
            )
        return out

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    def _fetch_output(self):
        """Read all .OUT files from the run directory."""
        for file in os.listdir(self.run_path):
            if file.endswith('.OUT'):
                fpath = os.path.join(self.run_path, file)
                encoding = detect_encoding(fpath)
                with open(fpath, 'r', encoding=encoding) as f:
                    self.output_files[file.split(".")[0]] = f.read()

    def _parse_summary(self):
        """Parse Summary.OUT into a list of per-treatment result dicts."""
        summary_text = self.output_files.get('Summary', '')
        if not summary_text:
            return []

        lines = summary_text.split('\n')
        header_idx = -1
        for i, line in enumerate(lines):
            if line.strip().startswith('@'):
                header_idx = i
                break

        if header_idx == -1:
            return []

        data_text = '\n'.join(lines[header_idx:])
        try:
            df = pd.read_csv(
                io.StringIO(data_text),
                sep=r'\s+',
                skipinitialspace=True,
            )
        except Exception:
            return []

        # Clean column names (remove leading @)
        df.columns = [
            col.lstrip('@').strip().lower() for col in df.columns
        ]

        results = []
        for _, row in df.iterrows():
            summary = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, 'item'):
                    val = val.item()
                if isinstance(val, float) and val == -99.0:
                    val = None
                summary[col] = val
            results.append({"summary": summary})

        return results

    @property
    def output_tables(self):
        if len(self._output) < 1:
            return None
        return self._output
