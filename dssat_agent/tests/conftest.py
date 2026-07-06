"""Test-database seeding for dssat_agent integration tests.

Production data population moved out of ``post_migrate`` into the product
shell's ``initialize_application`` command (``post_migrate`` is now config-only:
schema/source registration + tenant + roles). The test database is therefore no
longer auto-seeded on ``migrate``.

The RunPipeline / ChatPipeline integration tests in ``test_wizard_submit.py``
bind Fields to *real* ``StoredSoilProfile`` rows (e.g. the 9-char SSURGO soil
``AL1147544``) and run DSSAT to completion, so they need the reference soils +
crop files present. Seed them once per test session here — the seam that used to
be filled implicitly by ``post_migrate``.
"""
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _seed_dssat_reference_data(django_db_setup, django_db_blocker):
    """Seed the reference soils + crop files into the session's test DB once.

    Depends on ``django_db_setup`` so it runs after the test DB is created, and
    commits (outside any per-test transaction) so every TestCase sees the rows.
    """
    with django_db_blocker.unblock():
        from django.core.management import call_command
        from dssat_agent.startup.reference_rasters import _seed_soil_profiles
        import dssat_agent

        # Crop-genotype files (.CUL/.ECO) the DSSAT run pipeline reads.
        call_command("seed_crop_files")

        # SSURGO reference soil profiles (incl. AL1147544) the integration
        # tests bind Fields to. Only the StoredSoilProfile rows are needed — no
        # raster load — so call the profile seeder directly (fast, no
        # raster2pgsql). No-op if the .SOL is absent (e.g. a slimmed checkout).
        sol = (Path(dssat_agent.__file__).parent / "data"
               / "reference_rasters" / "se_us_ssurgo.sol")
        if sol.exists():
            _seed_soil_profiles(str(sol))
