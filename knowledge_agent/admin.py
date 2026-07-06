"""
Django admin for the knowledge_agent.

Since the knowledge_agent uses raw SQL tables (not Django ORM models),
this admin module provides read-only views via proxy or custom admin pages.

For now, this is a placeholder — the explorer views provide the browsing UI.
"""

from django.contrib import admin

# Knowledge agent tables are managed via raw SQL in store.py.
# Django admin registration will be added if Django models are introduced
# for schema management.
