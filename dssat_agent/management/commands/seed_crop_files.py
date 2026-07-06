"""
Management command to pre-generate and store DSSAT .CUL and .ECO files
for all supported crop models (hand-written + auto-discovered).
"""

import logging

from django.core.management.base import BaseCommand

from dssat_agent.models import StoredCropFile
from dssat_agent.services.crop_service import CROP_NAMES
from DSSATTools.crop_registry import CROP_MODEL_CLASSES

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Generate and store DSSAT .CUL/.ECO files for all crop models'

    def handle(self, *args, **options):
        created = 0
        errors = 0

        for smodel, cls in sorted(CROP_MODEL_CLASSES.items()):
            crop_code = cls.code
            try:
                cultivar_list = cls.cultivar_list()
                if not cultivar_list:
                    self.stderr.write(f"No cultivars for {crop_code}/{smodel}, skipping")
                    continue

                # Try each cultivar until one instantiates successfully.
                # Some cultivars reference ecotype codes (e.g. DFAULT, 999991)
                # that don't exist in the ECO file — skip those and try the next.
                crop = None
                for cul_code in cultivar_list:
                    try:
                        crop = cls(cul_code)
                        break
                    except Exception:
                        continue

                if crop is None:
                    raise RuntimeError(
                        f"No cultivar could be instantiated from {len(cultivar_list)} entries"
                    )

                cul_content = crop._write_cul()

                eco_content = None
                if hasattr(crop, 'eco_dtypes') and crop.eco_dtypes:
                    try:
                        eco_content = crop._write_eco()
                    except Exception:
                        pass

                StoredCropFile.objects.update_or_create(
                    crop_code=crop_code,
                    dssat_model=smodel,
                    defaults={
                        'crop_name': CROP_NAMES.get(crop_code, cls.__name__),
                        'cul_file': cul_content,
                        'eco_file': eco_content,
                    }
                )
                created += 1

            except Exception as e:
                logger.warning("Failed to generate files for %s/%s: %s",
                               crop_code, smodel, e)
                errors += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Stored crop files for {created} crop models, {errors} errors"
            )
        )
