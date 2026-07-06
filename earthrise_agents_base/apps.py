"""
The earthrise_agents_base app — reusable framework + generic chat
product. This module intentionally contains no product-specific
knowledge. Sub-agents (data / knowledge / domain-specific capability
modules) register themselves via ``earthrise_agents_base.a2a.register_agent``.
Product shell apps handle their own tenant registration, branding, and
skill wiring — see the framework's ``writing_a_product_shell`` guide.
"""

import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class EarthriseAgentsBaseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "earthrise_agents_base"
    label = "earthrise_agents_base"
    display_name = "Platform"

    management_items = [
        {'label': 'Feedback',
         'url_name': 'earthrise_agents_base:feedback_management',
         'section': 'platform',
         'visibility': 'admin'},
    ]

    def ready(self):
        # Importing the signals module attaches its `pre_delete` handler.
        from earthrise_agents_base import signals  # noqa: F401
