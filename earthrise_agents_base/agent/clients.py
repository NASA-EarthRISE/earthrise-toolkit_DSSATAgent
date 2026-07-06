"""
Client abstraction for sub-agent communication.

Provides EmbeddedClient (direct Python import) and RemoteClient (HTTP A2A)
with a factory function that chooses based on AGENT_CLIENTS settings.

Usage:
    client = get_client('data')
    result = client.send('check_availability', {'source': 'power'})
"""

import json
import logging
import uuid
from typing import Any, Dict, Optional

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class EmbeddedClient:
    """
    Direct Python import client — calls service functions without HTTP.

    Each agent_label maps to a services module with callable functions
    that mirror the A2A skill names.
    """

    def __init__(self, agent_label: str):
        self.agent_label = agent_label
        self._module = None

    def _get_module(self):
        """
        Resolve the services module for this agent.

        Discovery order:
        1. Look up agent via discovery.py (scans INSTALLED_APPS for agent_label)
           → imports ``{app_label}.services``
        2. If not discovered, try the AGENT_CLIENTS setting for an explicit
           ``services_module`` key.

        This means any Django app that declares ``agent_label`` on its AppConfig
        and provides a ``services.py`` module is automatically wired up —
        no hardcoded mapping needed.
        """
        if self._module is not None:
            return self._module

        import importlib
        module_path = None

        # 1. Auto-discover from AppConfig
        try:
            from .discovery import get_agent
            agent_meta = get_agent(self.agent_label)
            if agent_meta:
                app_label = agent_meta['app_label']
                module_path = f"{app_label}.services"
        except Exception:
            pass

        # 2. Fallback: explicit setting
        if not module_path:
            from django.conf import settings as _settings
            agent_cfg = getattr(_settings, 'AGENT_CLIENTS', {}).get(self.agent_label, {})
            module_path = agent_cfg.get('services_module')

        if not module_path:
            raise ValueError(
                f"Cannot resolve services module for agent '{self.agent_label}'. "
                f"Ensure the app declares agent_label='{self.agent_label}' on its "
                f"AppConfig and provides a services.py module."
            )

        self._module = importlib.import_module(module_path)
        return self._module

    def send(self, skill: str, params: Dict) -> Dict:
        """
        Call a skill function directly via Python import.

        Resolution order:
        1. ``module.<skill>(**params)``  — keyword expansion
        2. ``module.<skill>(params)``    — single-dict fallback (for skills
           that accept a raw params dict like run_simulation)
        3. ``module.dispatch_skill(skill, params)`` — generic dispatcher
        """
        module = self._get_module()

        # Try direct function match
        fn = getattr(module, skill, None)
        if fn and callable(fn):
            if not params:
                return fn()
            try:
                return fn(**params)
            except TypeError:
                return fn(params)

        # Try generic dispatcher
        dispatch = getattr(module, 'dispatch_skill', None)
        if dispatch and callable(dispatch):
            return dispatch(skill, params)

        raise ValueError(f"Skill '{skill}' not found in {module.__name__}")

    def is_available(self) -> bool:
        """Embedded clients are always available."""
        return True


class RemoteClient:
    """
    HTTP A2A client — sends JSON-RPC requests to a remote agent service.

    Compatible with the existing A2A protocol used by DataAgent,
    KnowledgeAgent, and SimulationAgent standalone deployments.
    """

    def __init__(self, agent_label: str, url: str):
        self.agent_label = agent_label
        self.url = url.rstrip('/')

    def send(self, skill: str, params: Dict) -> Dict:
        """Send a JSON-RPC message/send request."""
        message = {
            'jsonrpc': '2.0',
            'method': 'message/send',
            'id': '1',
            'params': {
                'message': {
                    'messageId': str(uuid.uuid4()),
                    'role': 'user',
                    'parts': [
                        {
                            'type': 'data',
                            'data': {
                                'skill': skill,
                                'params': params,
                            },
                        },
                    ],
                },
            },
        }

        try:
            response = httpx.post(
                f'{self.url}/',
                json=message,
                timeout=300.0,
            )
            response.raise_for_status()
            result = response.json()

            if 'error' in result:
                raise AgentClientError(
                    result['error'].get('message', 'Unknown error')
                )

            return self._extract_data(result)

        except httpx.HTTPError as e:
            logger.error('%s HTTP error: %s', self.agent_label, e)
            raise AgentClientError(
                f'Failed to communicate with {self.agent_label}: {e}'
            )

    def _extract_data(self, rpc_response: Dict) -> Dict:
        """Extract data from an A2A RPC response."""
        result = rpc_response.get('result', {})

        artifacts = result.get('artifacts', [])
        for artifact in artifacts:
            for part in artifact.get('parts', []):
                if part.get('type') == 'data':
                    return part.get('data', {})

        status = result.get('status', {})
        message = status.get('message', {})
        for part in message.get('parts', []):
            if part.get('type') == 'text':
                return {'text': part.get('text', '')}

        return result

    def is_available(self) -> bool:
        """Check if the remote agent is reachable."""
        try:
            response = httpx.get(
                f'{self.url}/.well-known/agent-card.json',
                timeout=5.0,
            )
            return response.status_code == 200
        except Exception:
            return False


class AgentClientError(Exception):
    """Raised when communication with an agent fails."""
    pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_client_cache: Dict[str, Any] = {}


def get_client(agent_label: str) -> Any:
    """
    Get a client for the given agent label.

    Uses AGENT_CLIENTS setting to determine mode (embedded vs remote).
    Defaults to embedded if the agent app is installed.
    """
    if agent_label in _client_cache:
        return _client_cache[agent_label]

    agent_config = getattr(settings, 'AGENT_CLIENTS', {}).get(agent_label, {})
    mode = agent_config.get('mode', 'embedded')

    if mode == 'remote':
        url = agent_config.get('url', '')
        if not url:
            raise ValueError(f"No URL configured for remote agent: {agent_label}")
        client = RemoteClient(agent_label, url)
    else:
        client = EmbeddedClient(agent_label)

    _client_cache[agent_label] = client
    return client


def invalidate_cache():
    """Clear the client cache (for testing)."""
    _client_cache.clear()
