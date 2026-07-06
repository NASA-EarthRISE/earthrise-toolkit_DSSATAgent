"""Seed CropModel rows from GRSTAGE.CDE + crop registry.

Idempotent. Re-running refreshes the purely file-derived fields
(``harvest_stages``, ``display_name``, ``default_cultivar_code``) but
preserves admin-edited fields (spacing ranges, ``supported_harvs_modes``,
``supported_management``, curations, notes) unless ``--force`` is passed.
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from dssat_agent.models import CropModel, DSSATCultivar
from dssat_agent.services.codes_service import get_all_harvest_stages
from dssat_agent.services.crop_service import CROP_NAMES

logger = logging.getLogger(__name__)


# Crop group categorization (hand-curated — small + stable). Covers every
# crop code registered in DSSATTools' CROP_MODEL_CLASSES so the seeder can
# label every row. Unknown/niche crops default to '' (admin fills in).
CROP_GROUPS = {
    # Cereals
    'MZ': 'cereal',  'SW': 'cereal',  'WH': 'cereal',  'BA': 'cereal',
    'RI': 'cereal',  'SG': 'cereal',  'ML': 'cereal',  'TF': 'cereal',
    'QU': 'cereal',
    # Legumes
    'SB': 'legume',  'BN': 'legume',  'CH': 'legume',  'CP': 'legume',
    'FB': 'legume',  'GB': 'legume',  'PN': 'legume',  'VB': 'legume',
    'PP': 'legume',  'GY': 'legume',
    # Vegetables / fruits
    'CB': 'vegetable', 'TM': 'vegetable', 'PR': 'vegetable',
    'SR': 'fruit',     'PI': 'fruit',
    # Tubers / roots
    'PT': 'tuber',   'CS': 'tuber',   'TN': 'tuber',   'TR': 'tuber',
    # Sugar
    'SC': 'sugar',   'BS': 'sugar',
    # Fiber
    'CO': 'fiber',
    # Oilseeds
    'SU': 'oilseed', 'CN': 'oilseed', 'SF': 'oilseed', 'BC': 'oilseed',
    'CI': 'oilseed',
    # Forage grasses & legumes
    'AL': 'forage',  'BM': 'forage',  'BH': 'forage',  'BR': 'forage',
    'G0': 'forage',  'GG': 'forage',
}


# Default set of harvest modes every (crop, model) pair starts with. Admins
# trim any modes that don't behave well for a given crop.
DEFAULT_HARVS_MODES = ['auto', 'maturity', 'on_date', 'growth_stage', 'dap']

# Default set of management practices every crop starts with.
DEFAULT_MANAGEMENT = [
    'fertilizer', 'irrigation', 'harvest', 'tillage', 'chemical', 'residue',
]


class Command(BaseCommand):
    help = 'Seed CropModel rows from GRSTAGE.CDE and the crop registry.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force', action='store_true',
            help='Overwrite admin-editable fields with seeder defaults '
                 '(normally preserved once set).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Report what would change without writing.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        force = options['force']
        dry_run = options['dry_run']

        # Every (crop, model) pair DSSATTools supports — authoritative list
        # from the crop registry. GRSTAGE-only entries (e.g. Fallow, used by
        # sequence/rotation simulations rather than as a runnable crop) are
        # intentionally skipped: without a .SPE/.CUL the user can't select
        # them in the wizard anyway.
        stages = get_all_harvest_stages()  # {(crop, model): [stage, ...]}
        try:
            from DSSATTools.crop_registry import CROP_MODEL_CLASSES
            registry_pairs = {(cls.code, smodel) for smodel, cls in CROP_MODEL_CLASSES.items()}
        except Exception as e:
            logger.warning("Could not read CROP_MODEL_CLASSES: %s", e)
            registry_pairs = set()

        all_pairs = sorted(registry_pairs)
        all_crop_codes = sorted({c for (c, _) in all_pairs})

        model_rows_written = 0
        model_rows_refreshed = 0
        crop_rows_written = 0
        crop_rows_refreshed = 0

        # ── Model-specific rows ──────────────────────────────────────
        for (crop, model) in all_pairs:
            stage_list = stages.get((crop, model), [])
            default_cultivar = ''
            first_cul = DSSATCultivar.objects.filter(
                crop_code=crop, dssat_model=model
            ).order_by('cultivar_code').first()
            if first_cul:
                default_cultivar = first_cul.cultivar_code

            display_name = CROP_NAMES.get(crop, crop)

            # Fields we always refresh (purely file-derived)
            refresh_defaults = {
                'harvest_stages': stage_list,
                'display_name': display_name,
                'crop_group': CROP_GROUPS.get(crop, ''),
                'default_cultivar_code': default_cultivar,
            }

            # Fields that initialize only once (admin-editable after).
            # Drop `growth_stage` from the default mode set when there are
            # no stages in the vocabulary — otherwise the wizard offers a
            # mode whose dropdown is always empty.
            default_modes = list(DEFAULT_HARVS_MODES)
            if not stage_list and 'growth_stage' in default_modes:
                default_modes.remove('growth_stage')
            initial_defaults = {
                'supported_harvs_modes': default_modes,
                'supported_management': list(DEFAULT_MANAGEMENT),
            }

            row = CropModel.objects.filter(
                crop_code=crop, dssat_model=model
            ).first()

            if row is None:
                if dry_run:
                    self.stdout.write(f"[dry-run] CREATE ({crop}, {model})")
                else:
                    CropModel.objects.create(
                        crop_code=crop, dssat_model=model,
                        **refresh_defaults, **initial_defaults,
                    )
                model_rows_written += 1
            else:
                changed = False
                for k, v in refresh_defaults.items():
                    if getattr(row, k) != v:
                        setattr(row, k, v)
                        changed = True
                if force:
                    for k, v in initial_defaults.items():
                        if not getattr(row, k):
                            setattr(row, k, v)
                            changed = True
                else:
                    # Only populate the admin-editable defaults if currently empty.
                    for k, v in initial_defaults.items():
                        if not getattr(row, k):
                            setattr(row, k, v)
                            changed = True
                if changed:
                    if not dry_run:
                        row.save()
                    model_rows_refreshed += 1

        # ── Crop-wide rows (one per crop) ─────────────────────────────
        for crop in all_crop_codes:
            refresh_defaults = {
                'display_name': CROP_NAMES.get(crop, crop),
                'crop_group': CROP_GROUPS.get(crop, ''),
            }
            row = CropModel.objects.filter(
                crop_code=crop, dssat_model=''
            ).first()

            if row is None:
                if dry_run:
                    self.stdout.write(f"[dry-run] CREATE ({crop}, crop-wide)")
                else:
                    CropModel.objects.create(
                        crop_code=crop, dssat_model='', **refresh_defaults,
                    )
                crop_rows_written += 1
            else:
                changed = False
                for k, v in refresh_defaults.items():
                    if not getattr(row, k):  # only fill if blank
                        setattr(row, k, v)
                        changed = True
                if changed:
                    if not dry_run:
                        row.save()
                    crop_rows_refreshed += 1

        prefix = '[dry-run] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}CropModel seeded: "
            f"{model_rows_written} new model-specific rows, "
            f"{model_rows_refreshed} refreshed, "
            f"{crop_rows_written} new crop-wide rows, "
            f"{crop_rows_refreshed} refreshed."
        ))
