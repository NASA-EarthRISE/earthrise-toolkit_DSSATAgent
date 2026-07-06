import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DataAgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'data_agent'
    verbose_name = 'Data Agent'

    # Agent discovery metadata
    agent_label = 'data_agent'
    display_name = 'Data'
    # Visual identity (chat tool-trace accent + icon), discovered like the
    # rest of the AppConfig metadata and exposed to the UI via
    # window.AGENT_VISUALS. Keyed by agent_label, which the tool-trace stream
    # emits as owner_agent.
    agent_icon = '🌦️'
    agent_color = '#16a34a'
    url_prefix = 'data'
    url_module = 'data_agent.urls'
    a2a_url_module = 'data_agent.a2a'
    a2a_prefix = 'data_agent'
    skills_dir = 'skills'
    nav_items = [
        {'label': 'Data Availability', 'url_name': 'data_agent:data_availability'},
        {'label': 'Map', 'url_name': 'data_agent:explorer_map'},
    ]
    home_cards = [
        {'label': 'Data Availability',
         'url_name': 'data_agent:data_availability',
         'icon': '🌦️',
         'description': 'Check weather and reference data coverage for a location and date range.',
         'color': 'teal'},
        {'label': 'Map Explorer',
         'url_name': 'data_agent:explorer_map',
         'icon': '🗺️',
         'description': 'Interactive map for browsing rasters, vectors, and spatial datasets.',
         'color': 'teal'},
    ]
    management_items = []

    def ready(self):
        from django.db.models.signals import post_migrate
        post_migrate.connect(_verify_schema_on_migrate, sender=self)


def _verify_schema_on_migrate(sender, **kwargs):
    """Ensure the dataagent schema exists after migrations."""
    try:
        from data_agent.services import verify_schema
        verify_schema()
    except Exception as e:
        logger.warning("Failed to ensure dataagent schema: %s", e)
