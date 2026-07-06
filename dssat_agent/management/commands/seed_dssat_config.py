"""
Seed system-level (user=NULL) DSSATConfig defaults so admins can change
them via the UI.

Usage: python manage.py seed_dssat_config

Discovered and run by the generic ``accounts seed_config`` dispatcher (which
runs every ``seed_*_config`` command), so DSSAT domain defaults live here in
dssat_agent — not in the domain-agnostic accounts app.
"""

from django.core.management.base import BaseCommand


# DSSAT defaults to seed (system-level, user=NULL)
DSSAT_DEFAULTS = [
    ('default_weather_source', 'power', 'Default weather data source'),
    ('default_num_years', 1, 'Default simulation years'),
    ('elevation', 100.0, 'Default elevation (m)'),
    ('sim_water', 'Y', 'Water simulation enabled'),
    ('sim_nitro', 'Y', 'Nitrogen simulation enabled'),
    ('sim_phosphorus', 'N', 'Phosphorus simulation enabled'),
    ('sim_potassium', 'N', 'Potassium simulation enabled'),
    ('output_grout', 'Y', 'Growth output enabled'),
    ('output_caout', 'Y', 'Crop age output enabled'),
    ('output_waout', 'Y', 'Water output enabled'),
    ('output_niout', 'Y', 'Nitrogen output enabled'),
    ('output_miout', 'N', 'Mineral output enabled'),
    ('output_diout', 'N', 'Disease output enabled'),
    ('fert_material', 'FE005', 'Default fertilizer material code'),
    ('fert_application', 'AP002', 'Default fertilizer application method'),
    ('fert_depth', 10, 'Default fertilizer depth (cm)'),
]


class Command(BaseCommand):
    help = 'Seed system-level DSSATConfig defaults.'

    def handle(self, *args, **options):
        from dssat_agent.models import DSSATConfig

        for key, value, desc in DSSAT_DEFAULTS:
            _, created = DSSATConfig.objects.get_or_create(
                user=None, key=key,
                defaults={'value': value, 'description': desc},
            )
            if created:
                self.stdout.write(f'  Created DSSATConfig: {key}={value}')
        self.stdout.write(self.style.SUCCESS('DSSATConfig seeding complete.'))
