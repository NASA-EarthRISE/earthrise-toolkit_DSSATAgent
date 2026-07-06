"""
Baseline tests for dssat_chat_agent product-shell wiring.

These lock the following registration invariants:

  1. tenants.yaml parses and contains the DSSAT vocabulary.
  2. The apps.py registration function calls knowledge_agent.registry
     with the correct arguments derived from tenants.yaml.
  3. The DSSAT-branded shell/base.html shadows the framework's default
     because dssat_chat_agent precedes earthrise_agents_base in
     INSTALLED_APPS (template resolution smoke check).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestTenantsYaml:

    def test_yaml_loads_and_has_dssat_tenant(self):
        from dssat_chat_agent.apps import _load_tenants_config

        tenants = _load_tenants_config()
        assert tenants, "tenants.yaml must define at least one tenant"
        tenant_ids = {t.get("tenant_id") for t in tenants}
        assert "dssat" in tenant_ids

    def test_dssat_tenant_has_full_vocabulary(self):
        from dssat_chat_agent.apps import _load_tenants_config

        [dssat] = [t for t in _load_tenants_config() if t["tenant_id"] == "dssat"]
        domain = dssat["domain"]
        assert "hint" in domain
        assert "Crop" in domain["entity_types"]
        assert "CropModel" in domain["entity_types"]
        assert "requiresInput" in domain["relationship_types"]
        assert domain["ontology"]["namespace"].startswith("http://dssat.net")


class TestRegistrationBehavior:

    def test_calls_register_tenant_with_vocabulary(self, monkeypatch):
        """When apps.ready() fires post_migrate, register_tenant is called
        with the domain block from tenants.yaml."""
        from dssat_chat_agent import apps as dssat_apps

        register_tenant_calls = []
        register_source_calls = []

        def fake_register_tenant(**kwargs):
            register_tenant_calls.append(kwargs)

        def fake_register_source(**kwargs):
            register_source_calls.append(kwargs)

        monkeypatch.setattr(
            "knowledge_agent.registry.register_tenant",
            fake_register_tenant,
        )
        monkeypatch.setattr(
            "knowledge_agent.registry.register_source",
            fake_register_source,
        )

        dssat_apps._register_knowledge_tenant()

        # register_tenant fired once for the DSSAT tenant with expected shape
        assert len(register_tenant_calls) == 1
        call = register_tenant_calls[0]
        assert call["tenant_id"] == "dssat"
        assert call["default_strategy"] == "hybrid"
        assert "Crop" in call["domain"]["entity_types"]


class TestTemplateOverride:

    @pytest.mark.django_db
    def test_shell_base_resolves_to_dssat_branded_variant(self):
        """Django template loader must find dssat_chat_agent's
        shell/base.html BEFORE earthrise_agents_base's default,
        because dssat_chat_agent precedes it in INSTALLED_APPS."""
        from django.template.loader import get_template

        template = get_template("shell/base.html")
        # The template's origin should be under dssat_chat_agent, not
        # earthrise_agents_base — that's the whole point of the override.
        origin_path = str(template.origin.name)
        assert "dssat_chat_agent" in origin_path, (
            f"shell/base.html resolved to unexpected path: {origin_path}. "
            "Check INSTALLED_APPS order: dssat_chat_agent must come before "
            "earthrise_agents_base."
        )

    @pytest.mark.django_db
    def test_dssat_shell_renders_branding_footer(self):
        """Sanity check that the DSSAT shell inherits from the framework
        skeleton and injects its branding block."""
        from django.template.loader import get_template

        template = get_template("shell/base.html")
        rendered = template.render({})
        assert "Powered by DSSAT" in rendered
