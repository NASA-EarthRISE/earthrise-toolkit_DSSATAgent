"""
Management command to sync roles from roles.yaml files.

Usage: python manage.py sync_roles

This is also called automatically via post_migrate signal.
"""

from django.core.management.base import BaseCommand
from accounts.role_discovery import sync_all_roles


class Command(BaseCommand):
    help = 'Sync roles and permissions from roles.yaml files in agent apps'

    def handle(self, *args, **options):
        self.stdout.write('Syncing roles from agent roles.yaml files...')
        sync_all_roles()
        self.stdout.write(self.style.SUCCESS('Role sync complete.'))
