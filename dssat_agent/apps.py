import logging
import os

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class DssatAgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dssat_agent'
    verbose_name = 'DSSAT Agent'

    # Agent discovery metadata
    agent_label = 'dssat_agent'
    display_name = 'DSSAT'
    # Visual identity (chat tool-trace accent + icon); see AGENT_VISUALS.
    agent_icon = '🌾'
    agent_color = '#0170B9'
    url_prefix = 'dssat'
    url_module = 'dssat_agent.urls'
    a2a_url_module = 'dssat_agent.a2a'
    a2a_prefix = 'dssat_agent'
    skills_dir = 'skills'
    plots_module = 'dssat_agent.plots'
    nav_items = [
        {'label': 'Experiment Wizard', 'url_name': 'dssat_agent:experiment_wizard'},
        {'label': 'Soils', 'url_name': 'dssat_agent:explorer_soils'},
        {'label': 'Crops', 'url_name': 'dssat_agent:explorer_crops'},
        {'label': 'Fields', 'url_name': 'dssat_agent:explorer_fields'},
        {'label': 'Treatments', 'url_name': 'dssat_agent:explorer_treatments'},
        {'label': 'Experiments', 'url_name': 'dssat_agent:experiment_list'},
        {'label': 'Codes', 'url_name': 'dssat_agent:explorer_codes'},
    ]
    home_cards = [
        {'label': 'Experiment Wizard',
         'url_name': 'dssat_agent:experiment_wizard',
         'icon': '🧪',
         'description': 'Step-by-step setup for a DSSAT crop simulation.',
         'color': 'purple'},
        {'label': 'Crop Browser',
         'url_name': 'dssat_agent:explorer_crops',
         'icon': '🌾',
         'description': 'Browse supported crops and cultivars.',
         'color': 'primary'},
        {'label': 'Soil Profiles',
         'url_name': 'dssat_agent:explorer_soils',
         'icon': '🟫',
         'description': 'Inspect and manage the soil profile library used by simulations.',
         'color': 'primary'},
        {'label': 'Experiment History',
         'url_name': 'dssat_agent:experiment_list',
         'icon': '📊',
         'description': 'Review past simulations, batch runs, and captured input/output files.',
         'color': 'purple'},
        {'label': 'DSSAT Codes',
         'url_name': 'dssat_agent:explorer_codes',
         'icon': '🔡',
         'description': 'Look up DSSAT FileX codes — planting methods, fertilizers, tillage, and more.',
         'color': 'primary'},
    ]
    management_items = [
        {'label': 'DSSAT Preferences', 'url_name': 'dssat_agent:config_preferences',
         'section': 'preferences', 'visibility': 'auth'},
        {'label': 'DSSAT System Config', 'url_name': 'dssat_agent:config_system',
         'section': 'system', 'visibility': 'permission',
         'permission': 'dssat_agent.manage_config'},
    ]

    def ready(self):
        """Register data requirements with the data_agent.

        Only the chat app integrates with knowledge_agent (single
        integration point, per the single-tenant design); dssat_agent
        does not import or call into it.
        """
        # Data source registration needs DB — defer to post_migrate
        from django.db.models.signals import post_migrate
        post_migrate.connect(_register_data_on_migrate, sender=self)


def _register_data_on_migrate(sender, **kwargs):
    """Register required data sources with data_agent after migrations complete."""
    from django.db import connection

    # Use a PostgreSQL advisory lock so only one process runs this
    lock_id = 8675309  # arbitrary unique integer
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        acquired = cur.fetchone()[0]

    if not acquired:
        logger.info("Another process is handling data registration, skipping")
        return

    try:
        _do_register()
    except Exception as e:
        logger.warning("Failed to register data requirements: %s", e)
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [lock_id])


def _do_register():
    import yaml
    from django.conf import settings

    sources_path = os.path.join(
        os.path.dirname(__file__), 'data', 'sources.yaml'
    )
    if not os.path.exists(sources_path):
        logger.warning("sources.yaml not found at %s", sources_path)
        return

    with open(sources_path, 'r') as f:
        sources = yaml.safe_load(f)

    agent_config = getattr(settings, 'AGENT_CLIENTS', {}).get('data_agent', {})
    mode = agent_config.get('mode', 'embedded')

    if mode == 'embedded':
        _register_embedded(sources)
    else:
        _register_remote(sources, agent_config.get('url', ''))

    # post_migrate performs lightweight source *registration* only, so a fresh
    # `migrate` (and every deploy) leaves a valid, bootable config without heavy
    # data work. Actual data population — soil profiles, crop files, cultivars,
    # crop models, DSSATConfig defaults, loading reference rasters into PostGIS,
    # and the weather time-series ETL — runs once via the explicit, idempotent
    # `initialize_application` management command in the product shell
    # (dssat_chat_agent). See the README "Initialize the application" step.


# knowledge_agent integration is owned by chat/apps.py (the single
# integration point); dssat_agent does not import or call into
# knowledge_agent.


def _register_embedded(sources):
    from data_agent import services as data_svc

    # Register single raster sources. Always pass all configs through so
    # yaml edits (e.g. adjusting query_limits, url templates) propagate
    # to existing rows; register_raster_source is idempotent and updates
    # dataset_information on change.
    single_configs = sources.get('raster_sources', {}).get('single', [])
    if single_configs:
        results = data_svc.register_raster_sources(single_configs)
        created = sum(1 for r in results if r['created'])
        logger.info("Synced %d raster sources (%d newly created)",
                    len(single_configs), created)

    # Combined sources are now registered as RasterDataset entries with
    # source_groups via setup_data_sources management command.

    # Register vector sources (always update to sync metadata)
    vector_configs = sources.get('vector_sources', [])
    if vector_configs:
        results = data_svc.register_vector_sources(vector_configs)
        created = sum(1 for r in results if r['created'])
        if created:
            logger.info("Registered %d new vector sources", created)

    # Load unloaded vector datasets via data_agent
    loaded = data_svc.load_unloaded_vectors()
    if loaded:
        logger.info("Loaded vector datasets: %s", ', '.join(loaded))


def _register_remote(sources, url):
    """Register data requirements via A2A when data_agent is remote."""
    from earthrise_agents_base.agent.clients import get_client, AgentClientError

    try:
        client = get_client('data')

        existing = client.send('list_raster_sources', {})
        existing_names = {s.get('prefix', '') for s in existing.get('single_sources', [])}

        single_configs = sources.get('raster_sources', {}).get('single', [])
        to_register = [
            c for c in single_configs
            if c.get('dataset_subtype', c.get('name', '')) not in existing_names
        ]
        if to_register:
            client.send('register_raster_sources', {'configs': to_register})

        # Combined sources are now registered as RasterDataset entries with
        # source_groups via setup_data_sources management command.

        existing_vectors = {v['name'] for v in client.send('list_vector_sources', {})}
        vector_configs = sources.get('vector_sources', [])
        to_register_vectors = [c for c in vector_configs if c['name'] not in existing_vectors]
        if to_register_vectors:
            client.send('register_vector_sources', {'configs': to_register_vectors})

        # Ask remote data_agent to load unloaded vectors
        client.send('load_unloaded_vectors', {})

    except AgentClientError as e:
        logger.warning("Failed to register data requirements with remote data_agent: %s", e)
