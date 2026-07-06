"""Load the in-repo reference rasters into PostGIS.

The soil-type, planting-date, and elevation TIFFs (plus the stakeholder SOL
file) declared under ``reference_rasters`` in ``dssat_agent/data/sources.yaml``
are loaded into PostGIS via ``raster2pgsql``. This is split out of
``post_migrate`` so the heavy raster load only runs on explicit initialization
(see the product shell's ``initialize_application`` command), not on every
``migrate``.

Idempotent: ``register_reference_rasters`` skips any raster whose source-file
SHA-256 is unchanged, so re-running is cheap. Soil profiles should already be
seeded (``seed_soils``) before this runs so the bundled SOIL.SOL is in place.
"""
from pathlib import Path

import yaml
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Load in-repo reference rasters (soil/planting/elevation) into PostGIS."

    def handle(self, *args, **options):
        from dssat_agent.startup.reference_rasters import register_reference_rasters

        sources_path = Path(__file__).resolve().parents[2] / 'data' / 'sources.yaml'
        if not sources_path.exists():
            self.stderr.write(self.style.WARNING(
                f"sources.yaml not found at {sources_path}; nothing to load."
            ))
            return

        with sources_path.open() as f:
            sources = yaml.safe_load(f) or {}

        register_reference_rasters(sources)
        self.stdout.write(self.style.SUCCESS("Reference rasters loaded into PostGIS."))
