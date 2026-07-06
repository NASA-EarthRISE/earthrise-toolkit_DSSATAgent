"""One-stop, idempotent data initialization for the DSSAT deployment.

``post_migrate`` only registers sources/tenant/roles (lightweight config), so a
fresh ``migrate`` leaves a bootable but empty deployment. This command — owned
by the product shell, which is where domain composition belongs — populates all
the actual data and is safe to re-run (every step is idempotent):

    roles → soils → crop files → cultivars → crop models → system config
          → data-source PostGIS tables → reference rasters → weather ETL
          → knowledge documents

Run it once after ``migrate`` (see the README "Initialize the application"
step). Heavy/external steps can be skipped:

    manage.py initialize_application --skip-weather --skip-documents

The weather ETL is idempotent by coverage (it skips windows already present in
PostGIS and skips entirely when no backfill bbox is configured), and it needs
network access + ``CDS_API_KEY`` to fetch anything. Document ingestion needs the
out-of-band RAG corpus to be present. Both are wrapped so a failure logs and the
run continues rather than aborting the whole initialization.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Populate all data for the DSSAT deployment (idempotent). Run once "
        "after `migrate`. Use --skip-weather / --skip-documents to skip the "
        "external-dependency steps."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--skip-weather', action='store_true',
            help="Skip the weather time-series ETL (needs network + CDS_API_KEY).",
        )
        parser.add_argument(
            '--skip-documents', action='store_true',
            help="Skip RAG document ingestion (needs the out-of-band corpus).",
        )

    def _step(self, label, command, *args, **kwargs):
        """Run one initialization step, reporting success/failure but never
        aborting the whole run on a single step's error."""
        self.stdout.write(self.style.MIGRATE_HEADING(f"→ {label}"))
        try:
            call_command(command, *args, **kwargs)
            self.stdout.write(self.style.SUCCESS(f"  ✓ {label}"))
            return True
        except Exception as exc:  # noqa: BLE001 — intentional: continue on step failure
            self.stderr.write(self.style.ERROR(f"  ✗ {label}: {exc}"))
            return False

    def handle(self, *args, **opts):
        self.stdout.write(self.style.MIGRATE_HEADING(
            "Initializing DSSAT deployment (idempotent)…"
        ))

        # Roles/permissions (also synced on post_migrate; re-run is a no-op).
        self._step("Sync roles & permissions", 'sync_roles')

        # DSSAT reference libraries. seed_soils must precede reference-raster
        # loading (the raster loader relies on the seeded SOIL.SOL).
        self._step("Seed soil profiles", 'seed_soils')
        self._step("Seed crop files", 'seed_crop_files')
        self._step("Seed cultivars & ecotypes", 'seed_cultivars')
        self._step("Seed crop models", 'seed_crop_models')

        # System-config defaults across every agent (runs each seed_*_config).
        self._step("Seed system config defaults", 'seed_config')

        # Raster data: create the PostGIS source tables, then load the in-repo
        # reference rasters (soil-type / planting-date / elevation) into them.
        self._step("Set up data-source PostGIS tables", 'setup_data_sources')
        self._step("Load reference rasters into PostGIS", 'load_reference_rasters')

        # Weather time-series ETL — idempotent by coverage; skips already-loaded
        # windows and no-ops when no backfill bbox is configured.
        if opts['skip_weather']:
            self.stdout.write("→ Skipping weather time-series ETL (--skip-weather)")
        else:
            self._step("Fetch weather time-series (ETL)", 'fetch_all_time_series')

        # RAG corpus ingestion — needs the out-of-band documents to be present.
        if opts['skip_documents']:
            self.stdout.write("→ Skipping knowledge document ingestion (--skip-documents)")
        else:
            self._step("Ingest knowledge documents", 'ingest_documents')

        self.stdout.write(self.style.SUCCESS("\nInitialization complete."))
