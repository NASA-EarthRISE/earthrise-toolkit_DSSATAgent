"""
Agent discovery — scans INSTALLED_APPS for sub-agent AppConfigs.

Agents are Django apps whose AppConfig declares an `agent_label` attribute.
This module discovers them at startup and provides metadata to the
orchestrator, skill loader, context processors, and URL router.
"""

import logging
from typing import Dict, List, Optional

from django.apps import apps

logger = logging.getLogger(__name__)

_discovered_agents: Optional[Dict[str, Dict]] = None


def discover_agents() -> Dict[str, Dict]:
    """
    Scan INSTALLED_APPS for agent AppConfigs.

    Returns a dict keyed by agent_label with metadata:
        {
            'data': {
                'app_label': 'data_agent',
                'agent_label': 'data',
                'app_config': <DataAgentConfig>,
                'skills_dir': '/abs/path/to/data_agent/skills',
                'nav_items': [...],
                'plots_module': None,
            },
            ...
        }
    """
    global _discovered_agents
    if _discovered_agents is not None:
        return _discovered_agents

    import os
    agents = {}

    for app_config in apps.get_app_configs():
        agent_label = getattr(app_config, 'agent_label', None)
        if not agent_label:
            continue

        skills_dir_rel = getattr(app_config, 'skills_dir', None)
        if skills_dir_rel:
            skills_dir = os.path.join(app_config.path, skills_dir_rel)
        else:
            skills_dir = None

        agents[agent_label] = {
            'app_label': app_config.label,
            'agent_label': agent_label,
            'app_config': app_config,
            'skills_dir': skills_dir,
            'nav_items': getattr(app_config, 'nav_items', []),
            'plots_module': getattr(app_config, 'plots_module', None),
            'agent_icon': getattr(app_config, 'agent_icon', None),
            'agent_color': getattr(app_config, 'agent_color', None),
        }

        logger.info("Discovered agent: %s (app=%s)", agent_label, app_config.label)

    _discovered_agents = agents
    return agents


def get_agent(agent_label: str) -> Optional[Dict]:
    """Get metadata for a specific agent by label."""
    return discover_agents().get(agent_label)


def resolve_agent_label_to_app_label(agent_label: str) -> Optional[str]:
    """Translate an `agent_label` (e.g. 'data_agent', 'dssat_agent',
    'knowledge_agent') to the Django app_label. Agent labels are now
    standardized to equal the app_label, so this is effectively an
    identity check that also validates the label. Returns None if the
    label is unknown.

    This bridge exists because `Chat.enabled_agents` stores `agent_label`
    values (shorter, user-facing) but the tool registry's `owner_agent`
    and the skill_loader's `_SkillEntry.owner_agent` both use `app_label`.
    The orchestrator converts once per invocation and filters downstream.
    """
    entry = discover_agents().get(agent_label)
    if entry is None:
        return None
    return entry.get('app_label')


def resolve_agent_labels_to_app_labels(agent_labels) -> set:
    """Bulk-convert a list/set of agent_labels into a set of app_labels.
    Unknown labels are silently dropped — callers that need strict
    validation should check membership against `discover_agents()` first.
    """
    out = set()
    for label in agent_labels or ():
        app = resolve_agent_label_to_app_label(label)
        if app:
            out.add(app)
    return out


def get_all_skills_dirs() -> List[str]:
    """Return absolute paths to all discovered agents' skills directories."""
    dirs = []
    for agent in discover_agents().values():
        if agent['skills_dir']:
            dirs.append(agent['skills_dir'])
    return dirs


def get_all_nav_items() -> List[Dict]:
    """Collect nav_items from all discovered agents."""
    items = []
    for agent in discover_agents().values():
        items.extend(agent.get('nav_items', []))
    return items


def get_plots_module(agent_label: str) -> Optional[str]:
    """Get the plots module path for an agent (e.g. 'foo_agent.plots')."""
    agent = get_agent(agent_label)
    if agent:
        return agent.get('plots_module')
    return None


def invalidate_cache():
    """Clear the discovery cache (for testing)."""
    global _discovered_agents
    _discovered_agents = None
