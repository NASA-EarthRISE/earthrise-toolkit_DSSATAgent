"""
Seed system-level (user=NULL) KnowledgeConfig defaults so admins can change
them via the UI.

Usage: python manage.py seed_knowledge_config

Discovered and run by the generic ``accounts seed_config`` dispatcher (which
runs every ``seed_*_config`` command), so knowledge defaults live here in
knowledge_agent — not in the domain-agnostic accounts app.
"""

from django.core.management.base import BaseCommand


# Knowledge defaults to seed (system-level, user=NULL)
KNOWLEDGE_DEFAULTS = [
    ('default_strategy', 'hybrid', 'Default retrieval strategy'),
]


class Command(BaseCommand):
    help = 'Seed system-level KnowledgeConfig defaults.'

    def handle(self, *args, **options):
        from knowledge_agent.models import KnowledgeConfig

        for key, value, desc in KNOWLEDGE_DEFAULTS:
            _, created = KnowledgeConfig.objects.get_or_create(
                user=None, key=key,
                defaults={'value': value, 'description': desc},
            )
            if created:
                self.stdout.write(f'  Created KnowledgeConfig: {key}={value}')
        self.stdout.write(self.style.SUCCESS('KnowledgeConfig seeding complete.'))
