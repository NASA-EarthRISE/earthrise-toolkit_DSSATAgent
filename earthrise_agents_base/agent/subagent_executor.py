"""
Subagent task executor — dispatches plan tasks via the client abstraction.

Uses get_client() to route to either EmbeddedClient (direct Python import)
or RemoteClient (HTTP A2A) based on AGENT_CLIENTS settings.
"""

import logging
from typing import Any, Dict

from .clients import get_client, AgentClientError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# $ref parameter resolution
# ---------------------------------------------------------------------------

def resolve_param_references(params: Dict, completed_results: Dict[str, Dict]) -> Dict:
    """
    Walk *params* looking for string values matching ``$task_key.field.subfield``
    and replace them with the actual value from *completed_results*.
    """
    resolved = {}
    for key, value in params.items():
        if isinstance(value, str) and value.startswith("$"):
            resolved[key] = _resolve_ref(value, completed_results)
        elif isinstance(value, dict):
            resolved[key] = resolve_param_references(value, completed_results)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_ref(item, completed_results)
                if isinstance(item, str) and item.startswith("$")
                else item
                for item in value
            ]
        else:
            resolved[key] = value
    return resolved


def _resolve_ref(ref: str, completed_results: Dict[str, Dict]) -> Any:
    """Resolve a single ``$task_key.dotted.path`` reference."""
    path = ref.lstrip("$").split(".")
    task_key = path[0]
    result = completed_results.get(task_key)
    if result is None:
        logger.warning("$ref resolution: task_key '%s' not found in completed results", task_key)
        return ref
    for segment in path[1:]:
        if isinstance(result, dict):
            result = result.get(segment)
        else:
            logger.warning("$ref resolution: cannot traverse '%s' in non-dict value", segment)
            return ref
    return result


# ---------------------------------------------------------------------------
# Generic task execution via client abstraction
# ---------------------------------------------------------------------------

def _resolve_agent_label(agent: str) -> str:
    """Normalize an LLM-provided agent name to the actual agent_label.

    The LLM sometimes returns verbose_name or other variants instead of
    the exact agent_label.  This builds a lookup from all known names.
    """
    from .discovery import discover_agents

    # Exact match is fastest
    agents = discover_agents()
    if agent in agents:
        return agent

    # Build a reverse map: lowercase verbose_name / app_label → agent_label
    for label, meta in agents.items():
        app_config = meta.get('app_config')
        candidates = {label.lower()}
        if app_config:
            candidates.add(getattr(app_config, 'verbose_name', '').lower())
            candidates.add(app_config.label.lower())
        if agent.lower() in candidates:
            return label

    # Fuzzy: check if any agent_label is a substring of what the LLM wrote
    agent_lower = agent.lower()
    for label in agents:
        if label.lower() in agent_lower:
            return label

    return agent  # give up, pass through as-is


def execute_task(agent: str, skill: str, params: Dict) -> Dict:
    """
    Route a task to any discovered agent.

    Args:
        agent: agent_label (e.g., 'dssat_agent', 'data_agent', 'knowledge_agent').
        skill: skill name to invoke.
        params: parameters for the skill.

    Returns:
        dict result from the agent/skill.
    """
    agent = _resolve_agent_label(agent)
    try:
        client = get_client(agent)
        return client.send(skill, params)
    except AgentClientError as e:
        logger.error("%s task '%s' error: %s", agent, skill, e)
        raise


# ---------------------------------------------------------------------------
# Auto-generated capabilities catalog for LLM planning
# ---------------------------------------------------------------------------

# Python builtins/typing artifacts (not callable skills)
_BUILTIN_SKIP = {
    'Dict', 'List', 'Optional', 'Any', 'Tuple', 'Set', 'Union',
    'logger', 'logging',
}


def _discover_all_skills() -> Dict:
    """
    Build a registry of all available skills across all agents.

    Returns:
        dict: {agent_label: {'verbose_name': str, 'skills': [(name, short_doc, full_doc), ...]}}
    """
    import importlib
    import inspect
    from .discovery import discover_agents

    registry = {}

    for agent_label, meta in discover_agents().items():
        app_label = meta['app_label']
        app_config = meta.get('app_config')
        skip = _BUILTIN_SKIP

        try:
            module = importlib.import_module(f"{app_label}.services")
        except Exception as e:
            logger.warning("Could not import %s.services: %s", app_label, e)
            continue

        skills = []
        for name in sorted(dir(module)):
            if name.startswith('_') or name in skip:
                continue
            obj = getattr(module, name)
            if not callable(obj) or isinstance(obj, type):
                continue
            if not inspect.isfunction(obj):
                continue
            full_doc = (obj.__doc__ or '').strip()
            short_doc = full_doc.split('\n')[0] if full_doc else ''
            skills.append((name, short_doc, full_doc))

        if skills:
            registry[agent_label] = {
                'verbose_name': getattr(app_config, 'verbose_name', app_label) if app_config else app_label,
                'skills': skills,
            }

    return registry


def build_skill_index() -> str:
    """
    Build a compact one-line-per-skill index for the LLM to select from.

    Returns a short string listing all skills with brief descriptions.
    """
    registry = _discover_all_skills()
    lines = []
    for agent_label, info in registry.items():
        for name, short_doc, _ in info['skills']:
            lines.append(f"{agent_label}/{name}: {short_doc}")
    return "\n".join(lines)


def build_capabilities_catalog(selected_skills: list = None, agent_labels: list = None) -> str:
    """
    Build a capabilities catalog with full descriptions for selected skills.

    Args:
        selected_skills: List of "agent/skill" or "skill" strings to include.
                         If None, includes all skills (filtered by agent_labels).
        agent_labels: Optional list of agent labels to include.
                      If None, includes all agents.

    Returns a formatted string for the LLM planner prompt.
    """
    registry = _discover_all_skills()

    # Build a set of selected (agent, skill) pairs
    selected_set = None
    if selected_skills:
        selected_set = set()
        for s in selected_skills:
            if '/' in s:
                agent, skill = s.split('/', 1)
                selected_set.add((agent.strip(), skill.strip()))
            else:
                selected_set.add((None, s.strip()))

    lines = ["Available skills:\n"]

    for agent_label, info in registry.items():
        if agent_labels and agent_label not in agent_labels:
            continue

        included = []
        for name, short_doc, full_doc in info['skills']:
            if selected_set:
                if (agent_label, name) not in selected_set and (None, name) not in selected_set:
                    continue
            included.append((name, full_doc))

        if not included:
            continue

        lines.append(f"## {info['verbose_name']} (agent: \"{agent_label}\")")
        for name, doc in included:
            lines.append(f"- {name}: {doc}")
        lines.append("")

    return "\n".join(lines)
