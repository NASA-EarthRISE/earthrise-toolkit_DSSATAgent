import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class KnowledgeAgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'knowledge_agent'
    verbose_name = 'Knowledge Agent'

    # Agent discovery metadata
    agent_label = 'knowledge_agent'
    display_name = 'Knowledge'
    # Visual identity (chat tool-trace accent + icon); see AGENT_VISUALS.
    agent_icon = '📚'
    agent_color = '#1c67e3'
    url_prefix = 'knowledge'
    url_module = 'knowledge_agent.urls'
    a2a_url_module = 'knowledge_agent.a2a'
    a2a_prefix = 'knowledge_agent'
    skills_dir = 'skills'
    nav_items = [
        {'label': 'Strategies', 'url_name': 'knowledge_agent:explorer_index'},
        {'label': 'Documents', 'url_name': 'knowledge_agent:explorer_documents'},
    ]
    home_cards = [
        {'label': 'Retrieval Strategies',
         'url_name': 'knowledge_agent:explorer_index',
         'icon': '🎯',
         'description': 'Compare retrieval strategies used for document search.',
         'color': 'green'},
        {'label': 'Documents',
         'url_name': 'knowledge_agent:explorer_documents',
         'icon': '📄',
         'description': 'Browse and search indexed documents.',
         'color': 'green'},
    ]
    management_items = [
        {'label': 'Knowledge Preferences', 'url_name': 'knowledge_agent:config_preferences',
         'section': 'preferences', 'visibility': 'auth'},
    ]

    def ready(self):
        """Run startup-time validation hooks.

        - Backend validation (Q8): confirm an LLM + embedding backend is
          configured. Hard-fail at startup if not — better to break
          `runserver` than to surface a confusing exception 30 retrieval
          requests later. Soft-warn on backend reachability (Ollama may
          still be warming up). Backend validation does no DB work and
          stays here in ready().
        - Tenant registration from settings dict / YAML file: deferred
          to post_migrate because it writes to KnowledgeTenant +
          KnowledgeSource via the ORM. Django emits a RuntimeWarning
          when AppConfig.ready() touches the DB.
        """
        # Imports kept inside ready() so the AppConfig itself can be
        # loaded by `manage.py makemigrations` without the rest of the
        # code coming along for the ride.
        from .backends import validate_backends

        try:
            validate_backends()
        except Exception:
            logger.exception(
                "knowledge_agent backend validation failed at startup",
            )
            raise

        # Defer tenant + source loading until after migrate has run.
        # post_migrate fires on every `manage.py migrate` invocation,
        # including the startup case where there's nothing to apply.
        from django.db.models.signals import post_migrate
        post_migrate.connect(_load_registered_config_signal, sender=self)


def _load_registered_config_signal(sender, **kwargs):
    """post_migrate handler that loads the settings/YAML tenant config."""
    try:
        from .registry import load_registered_config
        load_registered_config()
    except Exception:
        logger.exception(
            "knowledge_agent tenant registration failed at startup",
        )
        raise
