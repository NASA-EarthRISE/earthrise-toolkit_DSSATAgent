"""
Management command to ingest all DSSAT cultivars and ecotypes from bundled
.CUL/.ECO files into DSSATCultivar and DSSATEcotype Django models.
"""

import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from dssat_agent.models import DSSATCultivar, DSSATEcotype
from dssat_agent.services.crop_service import CROP_NAMES
from dssat_agent.services.cultivar_service import (
    extract_cultivar_params,
    extract_ecotype_params,
    _get_cultivar_name,
    _get_ecotype_name,
)
from DSSATTools.crop_registry import CROP_MODEL_CLASSES

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Ingest all DSSAT cultivars and ecotypes into the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear', action='store_true',
            help='Delete all existing DSSAT-sourced records before seeding',
        )

    def handle(self, *args, **options):
        if options['clear']:
            n_cul, _ = DSSATCultivar.objects.filter(source='dssat').delete()
            n_eco, _ = DSSATEcotype.objects.filter(source='dssat').delete()
            self.stdout.write(f"Cleared {n_cul} cultivars and {n_eco} ecotypes")

        cul_created = 0
        eco_created = 0
        cul_errors = 0
        eco_errors = 0

        for smodel in sorted(CROP_MODEL_CLASSES.keys()):
            cls = CROP_MODEL_CLASSES[smodel]
            crop_code = cls.code
            has_eco = bool(cls.eco_dtypes)

            # ── Phase 1: Seed ecotypes ───────────────────────────────
            # We collect ecotype objects keyed by code so cultivars can
            # reference them via FK.
            eco_map = {}  # eco_code -> DSSATEcotype instance

            if has_eco:
                # We need to discover all ecotype codes.  The best way is
                # to iterate cultivars and collect unique eco codes, since
                # ecotype files don't have a simple list method.
                eco_codes_seen = set()
                eco_instances = {}  # eco_code -> (eco_obj, name, params)

                for cul_code in cls.cultivar_list():
                    try:
                        crop = cls(cul_code)
                        eco = crop['eco#']
                        if not hasattr(eco, 'str'):
                            continue
                        eco_code = eco.str.strip()
                        if eco_code and eco_code not in eco_codes_seen:
                            eco_codes_seen.add(eco_code)
                            eco_instances[eco_code] = (
                                eco,
                                _get_ecotype_name(eco, cls.eco_dtypes),
                                extract_ecotype_params(eco, cls.eco_dtypes),
                            )
                    except Exception:
                        continue

                for eco_code, (eco_obj, eco_name, eco_params) in eco_instances.items():
                    try:
                        obj, created = DSSATEcotype.objects.update_or_create(
                            ecotype_code=eco_code,
                            dssat_model=smodel,
                            source='dssat',
                            defaults={
                                'ecotype_name': eco_name[:64],
                                'crop_code': crop_code,
                                'params': eco_params,
                            },
                        )
                        eco_map[eco_code] = obj
                        eco_created += 1
                    except Exception as e:
                        logger.warning("Eco error %s/%s/%s: %s",
                                       crop_code, smodel, eco_code, e)
                        eco_errors += 1

            # Also load existing ecotypes from DB for FK resolution
            for obj in DSSATEcotype.objects.filter(dssat_model=smodel, source='dssat'):
                eco_map.setdefault(obj.ecotype_code, obj)

            # ── Phase 2: Seed cultivars ──────────────────────────────
            # Track codes already seen for this model so that when a CUL
            # file contains duplicate codes (e.g. IB0067 in MZCER048),
            # the first occurrence wins.  Print both entries so the user
            # can review which one was kept.
            seen_cul = {}  # code -> (name, params dict)
            for cul_code in cls.cultivar_list():
                try:
                    crop = cls(cul_code)
                except Exception:
                    cul_errors += 1
                    continue

                try:
                    cul_name = _get_cultivar_name(crop, cls.cul_dtypes)
                    cul_params = extract_cultivar_params(crop, cls.cul_dtypes)

                    # Check for duplicate code within this model
                    if cul_code in seen_cul:
                        kept_name, kept_params = seen_cul[cul_code]
                        self.stderr.write(self.style.WARNING(
                            f"  Duplicate {crop_code}/{smodel}/{cul_code}:\n"
                            f"    KEPT:    {cul_code} {kept_name} {kept_params}\n"
                            f"    SKIPPED: {cul_code} {cul_name} {cul_params}"
                        ))
                        continue

                    # Resolve ecotype FK
                    eco_fk = None
                    if has_eco:
                        try:
                            eco = crop['eco#']
                            eco_code = eco.str.strip() if hasattr(eco, 'str') else ''
                            eco_fk = eco_map.get(eco_code)
                        except (KeyError, TypeError):
                            pass

                    DSSATCultivar.objects.update_or_create(
                        cultivar_code=cul_code,
                        dssat_model=smodel,
                        source='dssat',
                        defaults={
                            'cultivar_name': cul_name[:64],
                            'crop_code': crop_code,
                            'ecotype': eco_fk,
                            'params': cul_params,
                        },
                    )
                    seen_cul[cul_code] = (cul_name, cul_params)
                    cul_created += 1
                except Exception as e:
                    logger.warning("Cultivar error %s/%s/%s: %s",
                                   crop_code, smodel, cul_code, e)
                    cul_errors += 1

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {cul_created} cultivars ({cul_errors} errors), "
            f"{eco_created} ecotypes ({eco_errors} errors)"
        ))
