"""
Management command to seed soil profiles from the bundled SOIL.SOL file.
"""

import logging
import os
import re

from django.core.management.base import BaseCommand
from django.db import transaction

from dssat_agent.models import StoredSoilProfile, StoredSoilLayer

logger = logging.getLogger(__name__)

# Path to bundled SOIL.SOL
DEFAULT_SOL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'SOIL.SOL'
)


class Command(BaseCommand):
    help = 'Load soil profiles from SOIL.SOL into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file', type=str, default=DEFAULT_SOL_PATH,
            help='Path to SOIL.SOL file'
        )
        parser.add_argument(
            '--skip-existing', action='store_true',
            help='Skip profiles that already exist in the database'
        )
        parser.add_argument(
            '--clear', action='store_true',
            help='Clear all default profiles before loading'
        )

    def handle(self, *args, **options):
        sol_path = options['file']
        skip_existing = options['skip_existing']
        clear = options['clear']

        if not os.path.exists(sol_path):
            self.stderr.write(f"SOIL.SOL not found at {sol_path}")
            self.stderr.write("Skipping soil seeding. You can download SOIL.SOL from "
                              "https://github.com/DSSAT/dssat-csm-data")
            return

        if clear:
            count = StoredSoilProfile.objects.filter(source='default').delete()[0]
            self.stdout.write(f"Cleared {count} default profiles")

        profiles = parse_sol_file(sol_path)
        self.stdout.write(f"Parsed {len(profiles)} profiles from {sol_path}")

        created = 0
        skipped = 0

        for profile_data in profiles:
            soil_id = profile_data['soil_id']

            if skip_existing and StoredSoilProfile.objects.filter(soil_id=soil_id).exists():
                skipped += 1
                continue

            try:
                _create_profile_from_parsed(profile_data)
                created += 1
                try:
                    from dssat_agent.services.soil_service import _regenerate_sol_file
                    _regenerate_sol_file(soil_id)
                except Exception:
                    pass
            except Exception as e:
                logger.warning("Failed to create profile %s: %s", soil_id, e)

        self.stdout.write(
            self.style.SUCCESS(f"Created {created} profiles, skipped {skipped}")
        )


def _safe_float(val):
    """Convert string to float, returning None for missing values."""
    if val is None or val.strip() in ('', '-99', '-99.', '-99.0', '-99.00'):
        return None
    try:
        f = float(val.strip())
        return None if f == -99.0 else f
    except ValueError:
        return None


def _safe_str(val):
    """Clean a string value."""
    if val is None:
        return ''
    val = val.strip()
    return '' if val in ('-99', '-99.') else val


def parse_sol_file(filepath):
    """
    Parse a DSSAT SOIL.SOL file into a list of profile dicts.

    Each dict has keys: soil_id, name, country, site, lat, lon,
    soil_classification, scs_family, salb, slu1, sldr, slro, slnf, slpf,
    scom, smhb, smpx, smke, layers (list of dicts).
    """
    with open(filepath, 'r') as f:
        content = f.read()

    profiles = []
    # Split on profile headers: lines starting with *
    sections = re.split(r'\n(?=\*)', content)

    for section in sections:
        lines = section.strip().split('\n')
        if not lines or not lines[0].startswith('*'):
            continue

        header = lines[0]
        # Extract soil_id from header: *IBMZ910214  ...
        parts = header[1:].split(None, 1)
        if not parts:
            continue
        soil_id = parts[0][:10]

        profile_data = {
            'soil_id': soil_id,
            'name': parts[1].strip() if len(parts) > 1 else '',
            'country': '',
            'site': '',
            'lat': None,
            'lon': None,
            'soil_classification': '',
            'scs_family': '',
            'salb': 0.13,
            'slu1': 6.0,
            'sldr': 0.60,
            'slro': 73.0,
            'slnf': 1.0,
            'slpf': 1.0,
            'scom': '',
            'smhb': '',
            'smpx': '',
            'smke': '',
            'layers': [],
        }

        # Parse remaining lines
        i = 1
        while i < len(lines):
            line = lines[i]

            if line.startswith('@SITE'):
                # Next line has site info
                if i + 1 < len(lines):
                    i += 1
                    data_line = lines[i]
                    profile_data['country'] = _safe_str(data_line[0:12])
                    profile_data['site'] = _safe_str(data_line[12:24]) if len(data_line) > 12 else ''
                    lat_str = data_line[24:32].strip() if len(data_line) > 24 else ''
                    lon_str = data_line[32:40].strip() if len(data_line) > 32 else ''
                    profile_data['lat'] = _safe_float(lat_str)
                    profile_data['lon'] = _safe_float(lon_str)
                    scs = data_line[40:].strip() if len(data_line) > 40 else ''
                    if scs:
                        profile_data['soil_classification'] = scs[:5].strip()
                        profile_data['scs_family'] = scs[5:].strip() if len(scs) > 5 else ''

            elif line.startswith('@  SCOM'):
                # Next line has surface parameters
                if i + 1 < len(lines):
                    i += 1
                    data_line = lines[i]
                    # Fixed-width fields: SCOM(6) SALB(6) SLU1(6) SLDR(6) SLRO(6) SLNF(6) SLPF(6) SMHB(6) SMPX(6) SMKE(6)
                    profile_data['scom'] = _safe_str(data_line[0:6])
                    profile_data['salb'] = _safe_float(data_line[6:12]) or 0.13
                    profile_data['slu1'] = _safe_float(data_line[12:18]) or 6.0
                    profile_data['sldr'] = _safe_float(data_line[18:24]) or 0.60
                    profile_data['slro'] = _safe_float(data_line[24:30]) or 73.0
                    profile_data['slnf'] = _safe_float(data_line[30:36]) or 1.0
                    profile_data['slpf'] = _safe_float(data_line[36:42]) or 1.0
                    profile_data['smhb'] = _safe_str(data_line[42:48]) if len(data_line) > 42 else ''
                    profile_data['smpx'] = _safe_str(data_line[48:54]) if len(data_line) > 48 else ''
                    profile_data['smke'] = _safe_str(data_line[54:60]) if len(data_line) > 54 else ''

            elif line.startswith('@  SLB'):
                # DSSAT profiles have TWO layer header blocks sharing the
                # same "@  SLB" prefix:
                #   (a) Physical/hydraulic block: columns SLMH SLLL SDUL SSAT ...
                #   (b) Chemical block:          columns SLPX SLPT SLPO CACO3 ...
                #
                # We only parse the physical block (which is the one that
                # feeds StoredSoilLayer's required fields). The chemical
                # block is currently not stored — its data lines are parsed
                # by matching their slb depth onto the existing layers so
                # chemical fields are preserved when present, but the primary
                # parse happens from the physical block.
                is_physical = 'SLLL' in line.upper()
                is_chemical = (not is_physical) and ('SLPX' in line.upper())

                if is_physical:
                    i += 1
                    order = 0
                    while i < len(lines) and not lines[i].startswith('@') and not lines[i].startswith('*'):
                        layer_line = lines[i]
                        if not layer_line.strip():
                            i += 1
                            continue
                        layer = _parse_layer_line(layer_line)
                        if layer:
                            layer['order'] = order
                            profile_data['layers'].append(layer)
                            order += 1
                        i += 1
                    continue  # Don't increment i again
                elif is_chemical:
                    # Merge chemical fields into matching physical layers by slb.
                    i += 1
                    while i < len(lines) and not lines[i].startswith('@') and not lines[i].startswith('*'):
                        chem_line = lines[i]
                        if chem_line.strip():
                            _merge_chemical_layer_line(profile_data['layers'], chem_line)
                        i += 1
                    continue
                # Unrecognized @  SLB variant: skip its data block to avoid
                # corrupting the physical layers.
                i += 1
                while i < len(lines) and not lines[i].startswith('@') and not lines[i].startswith('*'):
                    i += 1
                continue

            i += 1

        if profile_data['layers']:
            profiles.append(profile_data)

    return profiles


def _parse_layer_line(line):
    """Parse a single soil layer data line (fixed-width format)."""
    if len(line) < 18:
        return None

    # Standard DSSAT .SOL layer format:
    # SLB(6) SLMH(5 left-aligned) SLLL(6) SDUL(6) SSAT(6) SRGF(6) SSKS(6)
    # SBDM(6) SLOC(6) SLCL(6) SLSI(6) SLCF(6) SLNI(6) SLHW(6) SLHB(6)
    # SCEC(6) SADC(6)
    #
    # We split on whitespace for most fields, but the SLMH column (master
    # horizon code like 'A', 'Bt', 'Btgv1') is a 5-char left-aligned text
    # field that may run directly into the next numeric column with no
    # separating space — e.g. "Btgv10.081" actually means SLMH='Btgv1' and
    # SLLL=0.081. We detect that case below and split the merged token.
    import re
    fields = line.split()
    if not fields:
        return None

    try:
        slb = float(fields[0])
    except (ValueError, IndexError):
        return None

    # Detect and un-merge a collided SLMH + SLLL token. SLMH is a 5-char
    # left-aligned text field; when a horizon code fills all 5 chars (e.g.
    # 'Btgv1', 'E/Btx', '2Btg1', '2A/Eb'), the next numeric column SLLL is
    # written directly after with no whitespace separator, so split() yields
    # a single token like 'Btgv10.081' or '2Btg10.302'. We detect this by
    # noting that fields[1] is longer than 5 chars AND the first 5 chars
    # contain at least one letter (so it's not a pure number being mis-split).
    if len(fields) > 1 and len(fields[1]) > 5:
        head = fields[1][:5]
        tail = fields[1][5:]
        if any(c.isalpha() for c in head):
            try:
                float(tail)
                fields = [fields[0], head, tail] + fields[2:]
            except ValueError:
                pass  # tail doesn't parse cleanly; leave token as-is

    layer = {'slb': slb}

    # Map positional fields
    field_names = [
        'slb', 'slmh', 'slll', 'sdul', 'ssat', 'srgf', 'ssks', 'sbdm', 'sloc',
        'slcl', 'slsi', 'slcf', 'slni', 'slhw', 'slhb', 'scec', 'sadc',
    ]

    for j, fname in enumerate(field_names):
        if j >= len(fields):
            break
        if fname == 'slmh':
            layer[fname] = _safe_str(fields[j])
        else:
            layer[fname] = _safe_float(fields[j])

    # Ensure required fields have non-None defaults. ``dict.setdefault`` won't
    # override an existing None value (which _safe_float returns for -99), so
    # we have to explicitly check.
    required_defaults = [
        ('slll', 0.1),
        ('sdul', 0.2),
        ('ssat', 0.4),
        ('srgf', 1.0),
        ('sbdm', 1.4),
        ('sloc', 0.5),
    ]
    for fname, default in required_defaults:
        if layer.get(fname) is None:
            layer[fname] = default

    return layer


def _merge_chemical_layer_line(layers, line):
    """
    Parse a line from a chemical (SLPX/SLPT/...) layer block and merge its
    fields into the matching physical layer by ``slb`` depth.
    """
    if len(line) < 6:
        return
    fields = line.split()
    if not fields:
        return
    try:
        slb = float(fields[0])
    except ValueError:
        return

    # Find the physical layer with the matching depth.
    match = None
    for lyr in layers:
        if lyr.get('slb') == slb:
            match = lyr
            break
    if match is None:
        return

    # Column positions in the chemical block (per the standard DSSAT format).
    chemical_field_names = [
        'slb', 'slpx', 'slpt', 'slpo', 'caco3', 'slal', 'slfe', 'slmn',
        'slbs', 'slpa', 'slpb', 'slke', 'slmg', 'slna', 'slsu', 'slec', 'slca',
    ]
    for j, fname in enumerate(chemical_field_names):
        if j == 0 or j >= len(fields):
            continue
        val = _safe_float(fields[j])
        if val is not None:
            match[fname] = val


@transaction.atomic
def _create_profile_from_parsed(profile_data):
    """Create a StoredSoilProfile + layers from parsed dict."""
    layers_data = profile_data.pop('layers', [])

    # Remove None values for optional fields
    soil_id = profile_data['soil_id']

    profile = StoredSoilProfile.objects.create(
        soil_id=soil_id,
        name=profile_data.get('name', ''),
        source='default',
        country=profile_data.get('country', ''),
        site=profile_data.get('site', ''),
        lat=profile_data.get('lat'),
        lon=profile_data.get('lon'),
        soil_classification=profile_data.get('soil_classification', ''),
        scs_family=profile_data.get('scs_family', ''),
        salb=profile_data.get('salb', 0.13),
        slu1=profile_data.get('slu1', 6.0),
        sldr=profile_data.get('sldr', 0.60),
        slro=profile_data.get('slro', 73.0),
        slnf=profile_data.get('slnf', 1.0),
        slpf=profile_data.get('slpf', 1.0),
        scom=profile_data.get('scom', ''),
        smhb=profile_data.get('smhb', ''),
        smpx=profile_data.get('smpx', ''),
        smke=profile_data.get('smke', ''),
    )

    for ldata in layers_data:
        order = ldata.pop('order', 0)
        slmh = ldata.pop('slmh', '')

        # Build kwargs, filtering None values
        kwargs = {'profile': profile, 'order': order}
        if slmh:
            kwargs['slmh'] = slmh

        for field in ['slb', 'slll', 'sdul', 'ssat', 'srgf', 'sbdm', 'sloc',
                       'ssks', 'slcl', 'slsi', 'slcf', 'slni', 'slhw', 'slhb',
                       'scec', 'sadc']:
            val = ldata.get(field)
            if val is not None:
                kwargs[field] = val

        StoredSoilLayer.objects.create(**kwargs)

    return profile
