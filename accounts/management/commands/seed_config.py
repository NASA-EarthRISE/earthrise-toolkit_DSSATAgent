"""
Seed system-level configuration defaults for every agent that provides a
seeder.

Usage: python manage.py seed_config

This command is domain-agnostic: it discovers and runs every management
command named ``seed_*_config`` (e.g. ``seed_dssat_config``,
``seed_knowledge_config``) that any installed app registers. Each agent owns
its own defaults and seeder; accounts holds no domain knowledge and imports
no agent models.
"""

from django.core.management import call_command, get_commands
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Run every agent-provided seed_*_config command.'

    def handle(self, *args, **options):
        seeders = sorted(
            name for name in get_commands()
            if name.startswith('seed_') and name.endswith('_config')
            and name != 'seed_config'
        )
        if not seeders:
            self.stdout.write('No agent config seeders found.')
            return

        for name in seeders:
            self.stdout.write(f'Running {name}...')
            call_command(name)

        self.stdout.write(self.style.SUCCESS('Config seeding complete.'))
