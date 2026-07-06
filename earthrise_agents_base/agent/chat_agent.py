"""
ChatAgent LangGraph Orchestrator

Generic agent orchestrator that routes user queries to discovered subagents.
No domain-specific logic — all domain knowledge lives in subagent apps.

Graph: intake → react_loop ⇄ tool_executor → respond
"""

import json
import os
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum

from langchain_ollama import ChatOllama, OllamaLLM
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command, interrupt

from .skill_loader import SkillLoader
from .subagent_executor import execute_task, resolve_param_references

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _summarize_schema(schema: Dict) -> Dict[str, Any]:
    """Compact summary of a JSON schema for embedding in a ToolMessage.

    The full schema is large; when a tool returns `needs_input`, we only
    need to hint the LLM about which fields the user's reply should cover
    so it can re-invoke the tool with structured args. Returns a dict of
    {field_name: short_description or type}.
    """
    if not isinstance(schema, dict):
        return {}
    props = schema.get("properties") or {}
    summary: Dict[str, Any] = {}
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        desc = spec.get("description") or spec.get("type") or ""
        if isinstance(desc, str):
            desc = desc.split("\n", 1)[0][:140]
        summary[name] = desc
    required = schema.get("required")
    if isinstance(required, list):
        summary["__required__"] = required
    return summary


# =============================================================================
# Configuration
# =============================================================================

def load_config():
    return {
        'ollama_url': os.environ.get('OLLAMA_URL', 'http://localhost:11434'),
        'model_name': os.environ.get('MODEL_NAME', 'llama3.1'),
        # Upper bound on ReAct iterations. Beyond this, respond synthesizes
        # whatever state we have (prevents runaway loops on flaky models).
        'react_max_iterations': int(os.environ.get('REACT_MAX_ITERATIONS', '15')),
    }


DATA_FETCH_TIMEOUT = int(os.environ.get('DATA_FETCH_TIMEOUT', '300'))
DATA_FETCH_POLL_INTERVAL = 10


# =============================================================================
# Skill Loader
# =============================================================================

_skill_loader = None

# Singleton checkpointer shared across ChatAgent instances in the same
# process. Postgres-backed so LangGraph `interrupt()` state survives across
# Celery task invocations — the wizard can pause on one message and resume
# on the next.
_checkpointer = None
_checkpointer_init_failed = False


def _get_checkpointer():
    """Return a shared LangGraph checkpointer.

    Prefers `PostgresSaver` (state survives worker restarts + multiple
    workers see each other's paused threads). Falls back to the in-process
    `MemorySaver` on setup failure so a bad DSN never blocks the chat
    agent from booting — with a visible warning.
    """
    global _checkpointer, _checkpointer_init_failed
    if _checkpointer is not None:
        return _checkpointer
    if _checkpointer_init_failed:
        return MemorySaver()

    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        from earthrise_agents_base.db import get_raw_connection

        conn = get_raw_connection(driver="psycopg", autocommit=True)
        saver = PostgresSaver(conn)
        saver.setup()  # idempotent — CREATE TABLE IF NOT EXISTS
        _checkpointer = saver
        logger.info("[ChatAgent] PostgresSaver checkpointer ready.")
        return _checkpointer
    except Exception as e:
        _checkpointer_init_failed = True
        logger.warning(
            "[ChatAgent] PostgresSaver unavailable (%s); falling back to "
            "MemorySaver (wizard state will NOT survive worker restarts).",
            e,
        )
        return MemorySaver()


def _get_skills() -> SkillLoader:
    """
    Get the global SkillLoader, scanning chat/skills/ plus all discovered
    agent apps' skills directories.
    """
    global _skill_loader
    if _skill_loader is None:
        # Primary: chat/skills/
        skills_dir = os.environ.get(
            'SKILLS_DIR',
            os.path.join(os.path.dirname(__file__), '..', 'skills')
        )
        _skill_loader = SkillLoader(skills_dir)

        # Also scan skills from discovered agent apps
        try:
            from .discovery import get_all_skills_dirs
            for extra_dir in get_all_skills_dirs():
                if os.path.isdir(extra_dir) and extra_dir != skills_dir:
                    extra_loader = SkillLoader(extra_dir)
                    # Merge discovered skills into the primary loader
                    _skill_loader._index.update(extra_loader._index)
                    # Merge composable skills too
                    for target, entries in extra_loader._composable.items():
                        _skill_loader._composable.setdefault(target, []).extend(entries)
        except Exception as e:
            logger.warning("Could not scan agent skills: %s", e)

    return _skill_loader


# =============================================================================
# Enums
# =============================================================================

class ChatIntent(str, Enum):
    TASK = "task"
    CAPABILITIES = "capabilities"
    SELF_HELP = "self_help"
    KNOWLEDGE = "knowledge"
    BROWSE = "browse"
    CLARIFICATION_RESPONSE = "clarification_response"
    GREETING = "greeting"
    MULTI_TASK = "multi_task"
    OTHER = "other"


# =============================================================================
# State
# =============================================================================

@dataclass
class ChatAgentState:
    user_query: str = ""
    messages: List[Dict] = field(default_factory=list)
    params: Dict = field(default_factory=dict)  # generic extracted parameters
    intent: str = "other"
    confidence: float = 0.0
    current_extraction: Dict = field(default_factory=dict)
    pending_clarification: List[str] = field(default_factory=list)
    turn_count: int = 0
    response: str = ""
    wizard_params: Optional[Dict] = None
    chat_id: Optional[str] = None  # orchestrator chat session UUID
    user_id: Optional[int] = None  # Django auth user PK (passed to subagents for ownership)
    # Legacy plan-execute path fields
    message_id: Optional[str] = None
    plan_summary: str = ""
    plan_tasks: List[Dict] = field(default_factory=list)
    plan_results: Dict[str, Dict] = field(default_factory=dict)
    # ReAct path fields (mode='react'); ignored in 'plan_execute' mode.
    messages_lc: List[BaseMessage] = field(default_factory=list)
    iteration: int = 0
    pending_tool_context: Dict[str, Dict] = field(default_factory=dict)
    active_skills: List[str] = field(default_factory=list)
    loaded_skill_bodies: Dict[str, str] = field(default_factory=dict)
    # Per-chat agent scoping. Empty list = every discovered agent is
    # available. Populated list restricts the orchestrator: tools and
    # skills owned by agents not in the list are filtered out of the
    # ReAct registry and `list_capabilities`/`load_skill` responses.
    # Values are agent_label strings (e.g. 'data_agent', 'knowledge_agent',
    # 'dssat_agent') — the chat agent converts to app_labels at filter time.
    enabled_agents: List[str] = field(default_factory=list)
    # Aggregated tool results by tool_call_id, for respond-node synthesis.
    tool_results: List[Dict] = field(default_factory=list)


# =============================================================================
# Conversation Memory
# =============================================================================

class ConversationMemory:
    """Persists conversation state across turns. Fully generic — no domain-specific fields."""

    def __init__(self):
        self.messages: List[Dict] = []
        self.confirmed_params: Dict = {}
        self.turn_count: int = 0
        self.pending_clarification: List[str] = []
        self.wizard_params: Optional[Dict] = None
        # WizardDraft id when this chat is mid-flow on a server-persisted
        # nested-step wizard build. Lets the chat-side wizard skill share
        # a single draft row with the SPA so a build started in one UI
        # can be resumed in the other.
        self.draft_id: Optional[str] = None
        self._required_fields: List[str] = []  # set from agent schema
        self.context_store: Dict = {}  # persisted context from subagents
        # ReAct-mode skill bundle state. Survives across turns so the LLM
        # doesn't have to re-discover skills on every user message; also
        # means subagent skill bodies stay injected into the system prompt
        # for follow-up questions.
        self.active_skills: List[str] = []
        self.loaded_skill_bodies: Dict[str, str] = {}

    def add_user_message(self, content: str):
        self.messages.append({"role": "user", "content": content})
        self.turn_count += 1

    def add_assistant_message(self, content: str):
        self.messages.append({"role": "assistant", "content": content})

    def get_context_string(self, max_turns: int = 10) -> str:
        recent = self.messages[-(max_turns * 2):]
        parts = []
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            parts.append(f"{role}: {msg['content']}")
        return "\n\n".join(parts) if parts else "No previous conversation."

    def set_required_fields(self, schema: Dict):
        """Set required fields from a parameter schema."""
        self._required_fields = [
            f['name'] for f in schema.get('fields', []) if f.get('required')
        ]

    def get_missing_required(self) -> List[str]:
        """Return list of required fields not yet in confirmed_params."""
        return [f for f in self._required_fields if not self.confirmed_params.get(f)]

    def merge_extraction(self, extraction: Dict):
        """Merge any non-null extracted values into confirmed_params."""
        skip_keys = {'intent', 'confidence'}
        for key, value in extraction.items():
            if key in skip_keys:
                continue
            if value is not None:
                self.confirmed_params[key] = value

    def merge_context(self, context: Dict):
        """Merge subagent-provided context into the persistent store."""
        if context:
            self.context_store.update(context)

    def to_dict(self) -> Dict:
        result = {
            "messages": self.messages,
            "confirmed_params": self.confirmed_params,
            "turn_count": self.turn_count,
            "pending_clarification": self.pending_clarification,
            "context_store": self.context_store,
            "active_skills": list(self.active_skills),
            "loaded_skill_bodies": dict(self.loaded_skill_bodies),
        }
        if self.wizard_params:
            result["wizard_params"] = self.wizard_params
        if self.draft_id:
            result["draft_id"] = self.draft_id
        return result

    @classmethod
    def from_dict(cls, d: Dict) -> 'ConversationMemory':
        mem = cls()
        mem.messages = d.get("messages", [])
        mem.confirmed_params = d.get("confirmed_params", {})
        mem.turn_count = d.get("turn_count", 0)
        mem.pending_clarification = d.get("pending_clarification", [])
        mem.wizard_params = d.get("wizard_params")
        mem.draft_id = d.get("draft_id")
        mem.context_store = d.get("context_store", {})
        mem.active_skills = list(d.get("active_skills", []) or [])
        mem.loaded_skill_bodies = dict(d.get("loaded_skill_bodies", {}) or {})
        return mem


# =============================================================================
# ChatAgent
# =============================================================================

class ChatAgent:
    """
    Generic orchestrator using LangGraph.

    Routes user queries to discovered subagents via dynamic skill selection.
    No domain-specific logic — all knowledge lives in subagent apps.
    """

    def __init__(self, memory: Optional[ConversationMemory] = None):
        config = load_config()
        self.llm = OllamaLLM(
            base_url=config['ollama_url'],
            model=config['model_name'],
            num_predict=4096,
            num_ctx=32768,
        )
        # Chat/tool-calling variant used by the ReAct path. Kept alongside
        # the completion OllamaLLM during the transition; once every call
        # site is converted `self.llm` can be removed.
        self.chat_llm = ChatOllama(
            base_url=config['ollama_url'],
            model=config['model_name'],
            num_predict=4096,
            num_ctx=32768,
            temperature=0,
        )
        self.memory = memory or ConversationMemory()
        self.skills = _get_skills()

        self.react_max_iterations = config['react_max_iterations']

        logger.info(
            "[ChatAgent] react_max_iterations=%d",
            self.react_max_iterations,
        )

        # Tool registry is rebuilt per invocation — per-chat agent scoping
        # (enabled_agents) means the filter varies per request, and dynamic
        # enum injection wants fresh DB reads anyway. No instance-level
        # cache; _collect_subagent_tools() is cheap.

        # Build workflow
        self.workflow = self._build_graph()

    def _build_graph(self) -> StateGraph:
        logger.info(
            "[ChatAgent] building graph: react_max_iterations=%s",
            self.react_max_iterations,
        )
        workflow = StateGraph(ChatAgentState)

        # intake → react_loop ⇄ tool_executor → respond
        workflow.add_node("intake", self._intake_node)
        workflow.add_node("react_loop", self._react_node)
        workflow.add_node("tool_executor", self._tool_executor_node)
        workflow.add_node("respond", self._respond_node)
        workflow.set_entry_point("intake")
        workflow.add_edge("respond", END)

        workflow.add_conditional_edges(
            "intake",
            self._route_from_intake,
            {"react_loop": "react_loop", "respond": "respond"},
        )
        workflow.add_conditional_edges(
            "react_loop",
            self._route_after_react,
            {"tool_executor": "tool_executor", "respond": "respond"},
        )
        workflow.add_edge("tool_executor", "react_loop")
        logger.info(
            "[ChatAgent] graph nodes: intake → react_loop "
            "⇄ tool_executor → respond (interrupts via ``interrupt(...)`` "
            "inside tool_executor for needs_input round-trips)",
        )

        compiled = workflow.compile(checkpointer=_get_checkpointer())
        logger.info(
            "[ChatAgent] graph compiled with checkpointer=%s",
            type(_get_checkpointer()).__name__,
        )
        return compiled

    # ---- Routing ----

    def _route_from_intake(self, state: ChatAgentState) -> str:
        """Route out of the intake node.

        Every query enters the ReAct loop — the LLM decides via tool
        selection whether to call a task tool or respond directly. The
        only bypass is `pending_clarification`, which shortcuts to
        `respond` so the user's missing-info prompt is surfaced without
        another LLM turn.
        """
        if state.pending_clarification:
            return "respond"
        return "react_loop"

    def _route_after_react(self, state: ChatAgentState) -> str:
        """After the LLM runs: if it asked for a tool call → executor; else → respond."""
        if not state.messages_lc:
            return "respond"
        last = state.messages_lc[-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        if tool_calls and state.iteration < self.react_max_iterations:
            return "tool_executor"
        if tool_calls:
            logger.warning(
                "[ReAct] Iteration cap %d hit with %d pending tool calls — "
                "dropping to respond.",
                self.react_max_iterations, len(tool_calls),
            )
        return "respond"

    # ---- ReAct tool registry + nodes ----

    # Status values that signal the envelope contract to _tool_executor_node.
    _NEEDS_INPUT_STATUS = "needs_input"
    _CANCELLED_STATUS = "cancelled"
    _SKILL_LOADED_STATUS = "skill_loaded"

    # Names of the always-bound meta tools.
    _META_TOOL_NAMES = ("list_capabilities", "load_skill")

    def _build_tool_registry(
        self,
        active_skills: Optional[List[str]] = None,
        *,
        enabled_agents: Optional[List[str]] = None,
    ) -> Dict[str, Dict]:
        """
        Return {tool_name: {"schema": <openai-style tool dict>,
                            "func": callable, "owner_agent": "..."}}.

        Meta tools (`list_capabilities`, `load_skill`) are always present.
        Subagent tools are included only when their owning skill is listed
        in `active_skills` — the LLM must call `load_skill(name)` first.

        `enabled_agents` (agent_label strings, e.g. 'data', 'knowledge')
        restricts which subagents contribute. An empty / None list means
        unrestricted (every discovered agent contributes). When
        restricted, tools whose `owner_agent` is not in the resolved
        app_label set are dropped before being hydrated.

        Subagent tool registries are discovered generically: each subagent
        app that wants to expose tools defines a `tools.py` module with a
        module-level `TOOL_REGISTRY` dict. No hardcoded per-agent imports
        live here.
        """
        active_skills = list(active_skills or [])
        allowed_app_labels = self._resolve_enabled_app_labels(enabled_agents)

        registry: Dict[str, Dict] = {}
        # Meta tools first. Always bound; their handlers close over
        # `allowed_app_labels` so they can filter their own outputs
        # (e.g. list_capabilities / load_skill).
        registry.update(
            self._meta_tool_registry(allowed_app_labels=allowed_app_labels)
        )

        # Gate: nothing else is bound until a skill is loaded.
        if not active_skills:
            return registry

        # Gather {tool_name: subagent_entry}, filtered to enabled agents.
        subagent_tools = self._collect_subagent_tools(
            allowed_app_labels=allowed_app_labels,
        )

        # Map each active skill to its declared tool names via skill_loader.
        for skill_name in active_skills:
            entry = self.skills.get_entry(skill_name)
            if entry is None:
                logger.warning(
                    "[ReAct] active_skills references unknown skill: %s",
                    skill_name,
                )
                continue
            # Skip skills owned by a disabled agent (belt-and-suspenders:
            # the owning tools are already filtered out of subagent_tools
            # but the skill body itself shouldn't be inserted either).
            if allowed_app_labels is not None and entry.owner_agent and entry.owner_agent not in allowed_app_labels:
                continue
            for tool_name in entry.tools:
                sub_entry = subagent_tools.get(tool_name)
                if sub_entry is None:
                    logger.warning(
                        "[ReAct] skill '%s' declared tool '%s' but no "
                        "subagent exposes it (or it was filtered out by "
                        "enabled_agents).", skill_name, tool_name,
                    )
                    continue
                registry[tool_name] = self._hydrate_subagent_tool(
                    tool_name, sub_entry,
                )

        return registry

    @staticmethod
    def _resolve_enabled_app_labels(
        enabled_agents: Optional[List[str]],
    ) -> Optional[set]:
        """Translate a list of user-facing `agent_label` strings into the
        set of matching `app_label` strings. Returns None when the list
        is empty / None (meaning no restriction — all agents allowed)."""
        if not enabled_agents:
            return None
        from .discovery import resolve_agent_labels_to_app_labels

        return resolve_agent_labels_to_app_labels(enabled_agents)

    def _collect_subagent_tools(
        self, *, allowed_app_labels: Optional[set] = None,
    ) -> Dict[str, Dict]:
        """Scan discovered agents for a `tools.py::TOOL_REGISTRY` module.

        Returns a merged dict keyed by tool name. `allowed_app_labels`
        (when provided) restricts discovery to only those Django apps —
        tools owned by disabled agents never enter the combined map.

        The previous process-wide `_tool_registry_cache` has been dropped
        in favor of per-invocation rebuilding: the cache key would have
        needed to include `allowed_app_labels`, and the registry is cheap
        to rebuild (one importlib lookup per registered subagent). Keeping
        it stateless is simpler than a keyed cache and correct under
        per-chat agent scoping.
        """
        import importlib

        from .discovery import discover_agents

        combined: Dict[str, Dict] = {}
        for agent_label, meta in discover_agents().items():
            app_config = meta.get("app_config")
            if app_config is None:
                continue
            app_label = app_config.label
            if allowed_app_labels is not None and app_label not in allowed_app_labels:
                continue
            module_path = f"{app_config.name}.tools"
            try:
                module = importlib.import_module(module_path)
            except ModuleNotFoundError:
                continue
            except Exception as e:
                logger.warning(
                    "[ReAct] failed to import %s: %s", module_path, e,
                )
                continue
            sub_registry = getattr(module, "TOOL_REGISTRY", None)
            if not isinstance(sub_registry, dict):
                continue
            for tool_name, spec in sub_registry.items():
                if tool_name in combined:
                    logger.warning(
                        "[ReAct] tool name collision on '%s' (already "
                        "registered by %s; now also %s)",
                        tool_name,
                        combined[tool_name].get("owner_agent", "?"),
                        spec.get("owner_agent", agent_label),
                    )
                combined[tool_name] = spec

        return combined

    def _hydrate_subagent_tool(self, tool_name: str, spec: Dict) -> Dict:
        """Turn a subagent TOOL_REGISTRY entry into a bind-ready schema+func."""
        desc_fn = spec.get("description")
        description = desc_fn() if callable(desc_fn) else (desc_fn or "")
        schema_fn = spec.get("schema_builder")
        parameters = schema_fn() if callable(schema_fn) else {}
        return {
            "owner_agent": spec.get("owner_agent", ""),
            "schema": {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": description,
                    "parameters": parameters,
                },
            },
            "func": spec.get("func"),
        }

    def _meta_tool_registry(
        self, *, allowed_app_labels: Optional[set] = None,
    ) -> Dict[str, Dict]:
        """Always-bound meta tools: `list_capabilities` and `load_skill`.

        These tools are chat-agent-level — they manipulate the orchestrator
        state directly and carry no domain content. When
        `allowed_app_labels` is not None, the handlers close over it and
        restrict their outputs: `list_capabilities` filters its skill list,
        and `load_skill` rejects skills owned by disabled agents.
        """
        list_caps_schema = {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "description": (
                        "Optional filter: restrict the listing to a single "
                        "owner agent label (e.g. 'foo_agent', "
                        "'data_agent', 'knowledge_agent'). Omit to list "
                        "everything available."
                    ),
                },
            },
            "required": [],
        }
        load_skill_schema = {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Name of the skill to load (as returned by "
                        "`list_capabilities`). Loading a skill adds its "
                        "bundled tools to subsequent tool calls in this "
                        "conversation."
                    ),
                },
            },
            "required": ["name"],
        }
        return {
            "list_capabilities": {
                "owner_agent": "chat",
                "schema": {
                    "type": "function",
                    "function": {
                        "name": "list_capabilities",
                        "description": (
                            "List every loadable skill — the catalog of "
                            "task-specific playbooks available across "
                            "subagents. Each entry includes a description "
                            "and the tool names that skill will expose once "
                            "loaded. Call this first when you don't know "
                            "which skill to use; call it when the user asks "
                            "what the system can do."
                        ),
                        "parameters": list_caps_schema,
                    },
                },
                "func": lambda args, user=None: self._tool_list_capabilities(
                    args, user=user, allowed_app_labels=allowed_app_labels,
                ),
            },
            "load_skill": {
                "owner_agent": "chat",
                "schema": {
                    "type": "function",
                    "function": {
                        "name": "load_skill",
                        "description": (
                            "Load a skill by name. On success the skill's "
                            "playbook is injected into subsequent system "
                            "prompts and its declared tools become "
                            "callable. Call this once you've picked the "
                            "skill that matches the user's need — typically "
                            "after `list_capabilities`."
                        ),
                        "parameters": load_skill_schema,
                    },
                },
                "func": lambda args, user=None: self._tool_load_skill(
                    args, user=user, allowed_app_labels=allowed_app_labels,
                ),
            },
        }

    def _tool_list_capabilities(
        self, args: Dict, user: Any = None,
        *, allowed_app_labels: Optional[set] = None,
    ) -> Dict:
        """list_capabilities implementation.

        When `allowed_app_labels` is a populated set, only skills owned
        by those agents are surfaced — matches the per-chat
        `enabled_agents` policy.
        """
        scope = (args or {}).get("scope") or None
        entries = self.skills.list_loadable_skills()
        if allowed_app_labels is not None:
            entries = [e for e in entries if e["owner_agent"] in allowed_app_labels]
        if scope:
            entries = [e for e in entries if e["owner_agent"] == scope]
        return {
            "status": "ok",
            "skills": entries,
            "enabled_agents_active": allowed_app_labels is not None,
            "hint": (
                "Call `load_skill(name=\"<skill-name>\")` to activate a "
                "skill. Until then, only list_capabilities and load_skill "
                "are bound."
            ),
        }

    def _tool_load_skill(
        self, args: Dict, user: Any = None,
        *, allowed_app_labels: Optional[set] = None,
    ) -> Dict:
        """load_skill implementation. Returns a skill_loaded envelope that
        _tool_executor_node interprets to mutate graph state."""
        args = args or {}
        name = args.get("name")
        # _pending_intent is set when the content-fallback parser rewrote
        # a direct skill-name tool call into a load_skill call — preserve
        # the caller's original arguments so the next turn's LLM can
        # invoke the intended skill tool with them.
        pending_intent = args.get("_pending_intent") or {}
        if not name:
            return {
                "status": "validation_error",
                "error": "load_skill requires a `name` argument.",
                "issues": [{
                    "field": "name",
                    "message": "name is required",
                    "received": None,
                }],
            }
        entry = self.skills.get_entry(name)
        if entry is None or not entry.tools:
            available = [e["name"] for e in self.skills.list_loadable_skills()]
            return {
                "status": "validation_error",
                "error": f"No loadable skill named '{name}'.",
                "issues": [{
                    "field": "name",
                    "message": "skill not found",
                    "received": name,
                    "valid_values": available,
                }],
                "hint": "Call `list_capabilities` to see available skills.",
            }
        # Per-chat agent-scoping gate: refuse to load a skill whose
        # owner_agent has been disabled via `enabled_agents`.
        logger.info(
            "[ReAct] load_skill gate: name=%r owner=%r allowed=%s",
            name, entry.owner_agent, allowed_app_labels,
        )
        if (
            allowed_app_labels is not None
            and entry.owner_agent
            and entry.owner_agent not in allowed_app_labels
        ):
            available = [
                e["name"] for e in self.skills.list_loadable_skills()
                if e["owner_agent"] in allowed_app_labels
            ]
            return {
                "status": "validation_error",
                "error": (
                    f"Skill '{name}' belongs to agent '{entry.owner_agent}', "
                    "which is disabled for this chat."
                ),
                "issues": [{
                    "field": "name",
                    "message": "skill owner is not in enabled_agents for this chat",
                    "received": name,
                    "valid_values": available,
                }],
                "hint": (
                    "Call `list_capabilities` to see skills the user's "
                    "current agent scope allows."
                ),
            }
        try:
            body = self.skills.get_skill_body(name)
        except Exception as e:
            body = ""
            logger.warning("[ReAct] Could not load body for skill %s: %s", name, e)
        result = {
            "status": self._SKILL_LOADED_STATUS,
            "skill_name": name,
            "owner_agent": entry.owner_agent,
            "tools_now_available": list(entry.tools),
            "body_preview": body[:500],
            "body": body,
        }
        if pending_intent:
            result["pending_intent"] = {
                "hint": (
                    "A previous turn's tool call routed through load_skill "
                    "with these args — call the appropriate bundled tool "
                    "(typically the first in tools_now_available) with "
                    "them now."
                ),
                "args": pending_intent,
            }
        return result

    def _build_tool_registry_for_state(self, state: ChatAgentState) -> Dict[str, Dict]:
        """Convenience wrapper — pulls active_skills and enabled_agents off state."""
        return self._build_tool_registry(
            active_skills=state.active_skills,
            enabled_agents=state.enabled_agents,
        )

    def _evict_disabled_active_skills(self, state: ChatAgentState) -> None:
        """Drop any entries from `state.active_skills` /
        `state.loaded_skill_bodies` whose owning agent is no longer
        enabled. Prevents skills loaded in an earlier turn (when the
        owning agent was enabled) from keeping their tools bound after
        the user disables that agent.
        """
        if not state.enabled_agents:
            return
        allowed = self._resolve_enabled_app_labels(state.enabled_agents)
        if allowed is None:
            return
        kept_skills: List[str] = []
        for skill_name in state.active_skills:
            entry = self.skills.get_entry(skill_name)
            if entry is None:
                # Unknown skill — drop (shouldn't happen; skills are stable).
                continue
            if entry.owner_agent and entry.owner_agent not in allowed:
                logger.info(
                    "[ReAct] evicting active_skill %r — owner agent %r is "
                    "no longer enabled.", skill_name, entry.owner_agent,
                )
                state.loaded_skill_bodies.pop(skill_name, None)
                continue
            kept_skills.append(skill_name)
        state.active_skills[:] = kept_skills

    @staticmethod
    def _tc_fields(tc: Any) -> tuple:
        """Extract (name, id) from a tool_call (dict-shaped or attribute-shaped)."""
        if isinstance(tc, dict):
            return tc.get("name") or "", tc.get("id") or ""
        return getattr(tc, "name", "") or "", getattr(tc, "id", "") or ""

    def _publish_tool_call(
        self, message_id: Optional[str], tc: Any, iteration: int,
    ) -> None:
        """Emit a `tool_call` SSE event — a new row in the trace view."""
        if not message_id:
            return
        try:
            from earthrise_agents_base.tasks import _publish_chat_event

            name, tc_id = self._tc_fields(tc)
            # Best-effort owner lookup from a freshly-built unfiltered
            # tool map. Used for the trace row badge color; not
            # load-bearing if missing.
            try:
                subagent_tools = self._collect_subagent_tools()
            except Exception:
                subagent_tools = {}
            owner = (subagent_tools.get(name) or {}).get("owner_agent", "")
            _publish_chat_event(message_id, {
                "type": "tool_call",
                "tool_call_id": tc_id,
                "name": name,
                "owner_agent": owner,
                "iteration": iteration,
                "status": "running",
            })
        except Exception as e:
            logger.debug("[ReAct] _publish_tool_call skipped: %s", e)

    def _publish_tool_event(self, message_id: Optional[str], tc: Any, result: Dict) -> None:
        """Emit a `tool_result` SSE event — finalizes the row in the trace view."""
        if not message_id:
            return
        try:
            from earthrise_agents_base.tasks import _publish_chat_event

            name, tc_id = self._tc_fields(tc)
            status = (result or {}).get("status")
            _publish_chat_event(message_id, {
                "type": "tool_result",
                "tool_call_id": tc_id,
                "name": name,
                "status": status,
            })
        except Exception as e:
            logger.debug("[ReAct] _publish_tool_event skipped: %s", e)

    def _publish_thinking(self, message_id: Optional[str], stage: str, title: str = "") -> None:
        """Emit a `thinking` SSE event for coarse agent-level progress."""
        if not message_id:
            return
        try:
            from earthrise_agents_base.tasks import _publish_chat_event

            _publish_chat_event(message_id, {
                "type": "thinking",
                "stage": stage,
                "title": title or stage.replace("_", " ").title(),
                "description": "",
            })
        except Exception as e:
            logger.debug("[ReAct] _publish_thinking skipped: %s", e)

    def _build_react_system_prompt(self, state: ChatAgentState) -> str:
        """Generic system prompt for the ReAct loop.

        Chat-agent-level guidance only — no domain knowledge. Domain hints
        (crop codes, location shapes, result-summary formats, etc.) live in
        skill bodies and tool descriptions. Skill bodies are injected below
        via `state.loaded_skill_bodies` once progressive disclosure wires up
        in task #5.
        """
        parts = [
            "You are an orchestrator connected to tools provided by subagents.",
            "",
            "CRITICAL: when you need to use a tool, EMIT a structured tool "
            "call — do NOT describe the call in your content, do NOT write "
            "prose like \"I will call X\", do NOT invent tool responses, and "
            "do NOT invent tool names. Use ONLY the tool names listed in the "
            "current tool set. Text output is reserved for the final user-"
            "facing reply AFTER all required tools have actually run.",
            "",
            "Progressive disclosure:",
            "- By default, only two tools are bound: `list_capabilities` and "
            "`load_skill`. Task-specific tools become available only after "
            "you `load_skill(name=...)` the skill that owns them.",
            "- When a user request clearly needs a task tool that is not yet "
            "bound, your FIRST action is to emit a `list_capabilities` tool "
            "call (empty args) — do not guess. On the next turn, you will "
            "see the returned skill catalog and can emit `load_skill`. The "
            "turn after that, the bundled tools will be bound and you can "
            "call them.",
            "- Once a skill is loaded, its playbook is injected below as "
            "guidance. Follow it.",
            "",
            "Rules:",
            "- Respect the JSON schema exactly. Where a field's `type` is "
            '`"object"`, emit a real JSON object `{...}` with the nested '
            "fields inside it. Do NOT flatten nested fields into dotted keys "
            '(e.g. `"a.b": ...`) and do NOT paste a free-text label or a '
            "type name as the value — use the concrete structure the schema "
            "shows.",
            "- For fields typed as a tagged/discriminated union, set the "
            "discriminator field (usually `kind` or `type`) to one of the "
            "listed literals and include that variant's required fields.",
            "- Fill in only what the user explicitly stated. Leave everything "
            "else out — downstream defaults fill gaps.",
            "- If a tool returns a validation_error envelope, adjust the "
            "offending arguments and try again, using any `suggestions` "
            "it provided.",
            "- When a tool returns a terminal status (completed / ok / "
            "cancelled / error), do NOT call another tool — write the reply.",
            "- For greetings, small talk, or system questions not tied to any "
            "tool, reply directly without calling anything.",
        ]
        # Inject the orchestrator's conversation context (subagent-provided
        # state like experiment_id, crop_code, location_name — whatever
        # tools have stashed across turns). This is what lets the LLM
        # answer follow-ups about prior results without re-running tools.
        ctx = getattr(self.memory, "context_store", None) or {}
        if ctx:
            parts.append("")
            parts.append(
                "Conversation context (facts about prior turns in this "
                "conversation — reference these when the user asks follow-up "
                "questions about earlier results):"
            )
            for key, value in ctx.items():
                if value is None or isinstance(value, dict):
                    continue
                if isinstance(value, (list, tuple)) and not value:
                    continue
                parts.append(f"- {key}: {value}")

        if state.loaded_skill_bodies:
            parts.append("")
            parts.append("Active skills (subagent-provided guidance):")
            for name, body in state.loaded_skill_bodies.items():
                # Skill bodies are truncated at 25 KB. Serious skills
                # (e.g. foo-experiment-run ≈ 19 KB) are multi-section
                # playbooks whose key guidance (continuation patterns,
                # anti-hallucination rules, type tables) sits well past
                # 2 KB, so the cap must be generous or the LLM loses the
                # rules it needs for ``needs_input`` follow-ups,
                # multi-step wizards, etc. With ``num_ctx=32768`` and at
                # most a handful of skills loaded per turn, a 25 KB
                # budget is safe.
                truncated = body[:25000]
                if len(body) > len(truncated):
                    logger.warning(
                        "[ReAct] skill %r body truncated: %d → %d chars "
                        "(losing %d chars of guidance — bump the cap if "
                        "this skill needs longer instructions)",
                        name, len(body), len(truncated),
                        len(body) - len(truncated),
                    )
                parts.append(f"## {name}\n{truncated}")

        # Per-chat agent scoping disclosure — generic, domain-free.
        if state.enabled_agents:
            try:
                from .discovery import discover_agents
                all_agents = set(discover_agents().keys())
            except Exception:
                all_agents = set()
            enabled = set(state.enabled_agents)
            if all_agents and enabled and enabled != all_agents:
                parts.append("")
                parts.append(
                    "This chat is scoped to the following subagents: "
                    f"{sorted(enabled)}. Tools from other subagents are "
                    "unavailable for this conversation."
                )
        return "\n".join(parts)

    def _react_node(self, state: ChatAgentState) -> Dict:
        """Invoke ChatOllama with the currently bound tools; append the AIMessage."""
        # Evict stale active_skills whose owning agent has been disabled
        # via `enabled_agents`. Runs at the top of every iteration so a
        # toggle change between turns takes effect before new tools bind.
        self._evict_disabled_active_skills(state)

        registry = self._build_tool_registry_for_state(state)
        tool_dicts = [entry["schema"] for entry in registry.values()]
        tool_names = {entry["schema"]["function"]["name"] for entry in registry.values()}
        llm_with_tools = self.chat_llm.bind_tools(tool_dicts)

        # Seed the message list on the first iteration.
        messages = list(state.messages_lc)
        if not messages:
            messages = [
                SystemMessage(content=self._build_react_system_prompt(state)),
                HumanMessage(content=state.user_query),
            ]
        else:
            # Replace the leading SystemMessage every iteration so that
            # newly-loaded skill bodies show up in the prompt without
            # forcing a full message-list rewrite.
            fresh_system = SystemMessage(
                content=self._build_react_system_prompt(state),
            )
            if messages and isinstance(messages[0], SystemMessage):
                messages = [fresh_system] + messages[1:]
            else:
                messages = [fresh_system] + messages

        logger.info(
            "[ReAct] iteration=%d tools=%d messages=%d active_skills=%s enabled_agents=%s",
            state.iteration, len(tool_dicts), len(messages),
            state.active_skills, state.enabled_agents,
        )
        # Tail-of-conversation diagnostics. Helps diagnose post-
        # ``needs_input`` resume bugs where the LLM should be reading the
        # latest HumanMessage (the user's reply) but somehow isn't.
        # Logs the last 3 messages' types + content prefix so we can
        # confirm the reply is actually present in the prompt.
        try:
            tail = messages[-3:]
            for i, m in enumerate(tail):
                preview = ""
                if isinstance(m, ToolMessage):
                    preview = (m.content or "")[:200]
                elif isinstance(m, HumanMessage):
                    preview = (m.content or "")[:200]
                elif isinstance(m, AIMessage):
                    tcs = getattr(m, "tool_calls", None) or []
                    preview = (
                        f"tool_calls={[tc.get('name') if isinstance(tc, dict) else getattr(tc, 'name', '?') for tc in tcs]}"
                        if tcs else (m.content or "")[:200]
                    )
                logger.info(
                    "[ReAct]   msg[-%d] %s: %s",
                    len(tail) - i, type(m).__name__, preview,
                )
        except Exception:
            pass
        # Skill-presence diagnostics: confirm the SystemMessage actually
        # contains the loaded-skill bodies (and how big they are after
        # truncation). The "wizard stuck on resume" bug was rooted in
        # the skill body being silently truncated below the section that
        # explained how to handle ``needs_input`` follow-ups.
        try:
            sys_msg = messages[0] if messages and isinstance(messages[0], SystemMessage) else None
            if sys_msg:
                logger.info(
                    "[ReAct] system_prompt_len=%d active_skill_section_present=%s",
                    len(sys_msg.content or ""),
                    "Active skills" in (sys_msg.content or ""),
                )
        except Exception:
            pass
        response = llm_with_tools.invoke(messages)

        tool_calls = list(getattr(response, "tool_calls", None) or [])
        # Fallback: llama3.1 + Ollama occasionally emits a tool call as raw
        # JSON in `content` instead of populating structured tool_calls. Salvage.
        if not tool_calls and response.content:
            parsed = self._try_parse_tool_call_from_content(
                response.content, tool_names, state.iteration,
            )
            if parsed:
                logger.info(
                    "[ReAct] recovered tool_call from content: name=%s",
                    parsed.get("name"),
                )
                tool_calls = [parsed]
                response.tool_calls = tool_calls  # type: ignore[attr-defined]
                response.content = ""
        messages.append(response)

        logger.info(
            "[ReAct] response: content_len=%d tool_calls=%d",
            len((response.content or "")), len(tool_calls),
        )
        return {
            "messages_lc": messages,
            "iteration": state.iteration + 1,
        }

    def _try_parse_tool_call_from_content(
        self, content: str, known_names: set, iteration: int,
    ) -> Optional[Dict]:
        """Extract a tool call dict from a content string when the model
        emitted it as prose instead of a structured tool_calls payload.

        Accepted shapes (either Ollama-native or OpenAI-function-style):
            {"name": "<tool>", "parameters": {...}}
            {"name": "<tool>", "arguments": {...}}
            {"name": "<tool>", "args": {...}}
            {"function": {"name": "...", "arguments": "..."}}

        Auto-translation: if the `name` matches a loadable skill instead
        of a bound tool, convert the call to `load_skill(name=<skill>)`.
        The LLM frequently confuses the two — mapping back to the meta
        tool is the right behavior.
        """
        raw = (content or "").strip()
        if not raw:
            return None
        # Strip code fences if present.
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        # Isolate the first JSON object.
        first_brace = raw.find("{")
        if first_brace < 0:
            return None
        try:
            obj = json.loads(raw[first_brace:])
        except json.JSONDecodeError:
            try:
                # Sometimes the closing brace is missing; try a recovering parse.
                obj = json.loads(raw[first_brace:] + "}")
            except json.JSONDecodeError:
                return None
        if not isinstance(obj, dict):
            return None

        # Unwrap the OpenAI-function envelope if present.
        if "function" in obj and isinstance(obj["function"], dict):
            obj = obj["function"]

        name = obj.get("name")
        if not isinstance(name, str) or not name:
            return None

        args = obj.get("parameters", obj.get("arguments", obj.get("args", {})))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}

        # If the LLM passed a loadable skill name instead of a tool name,
        # translate into a `load_skill(name=...)` call. This is what the
        # orchestrator would have wanted anyway. Preserve the original
        # args under `_pending_intent` so the load_skill handler can
        # echo them back — the LLM sees the hint on the next turn and
        # can call the actual skill tool with those args.
        if name not in known_names:
            try:
                loadable = {
                    e.get("name") for e in self.skills.list_loadable_skills()
                } if hasattr(self.skills, "list_loadable_skills") else set()
            except Exception:
                loadable = set()
            if name in loadable and "load_skill" in known_names:
                logger.info(
                    "[ReAct] content tool-call `%s` matches a loadable skill; "
                    "rewriting to load_skill(name='%s', _pending_intent=%s).",
                    name, name, args,
                )
                return {
                    "id": f"recovered-{iteration}-{abs(hash(raw)) % 100000}",
                    "name": "load_skill",
                    "args": {"name": name, "_pending_intent": args},
                    "type": "tool_call",
                }
            return None

        return {
            "id": f"recovered-{iteration}-{abs(hash(raw)) % 100000}",
            "name": name,
            "args": args,
            "type": "tool_call",
        }

    def _tool_executor_node(self, state: ChatAgentState) -> Dict:
        """Dispatch the tool calls emitted by the most recent AIMessage.

        Envelope handling:
          - status='needs_input'  → stored in pending_tool_context; returned
                                    as a ToolMessage so the LLM sees the
                                    request. This becomes a normal
                                    observation the LLM can respond to.
          - status='validation_error' → ToolMessage; ReAct retries.
          - status='cancelled' → ToolMessage; ReAct will either finalize or
                                 retry.
          - anything else → ToolMessage; ReAct typically responds without
                            another tool call.
        """
        registry = self._build_tool_registry_for_state(state)
        messages = list(state.messages_lc)
        ai_msg = messages[-1] if messages else None
        tool_calls = getattr(ai_msg, "tool_calls", None) or []
        if not tool_calls:
            return {"messages_lc": messages}

        tool_results: List[Dict] = list(state.tool_results)
        pending_ctx = dict(state.pending_tool_context)
        active_skills: List[str] = list(state.active_skills)
        loaded_bodies: Dict[str, str] = dict(state.loaded_skill_bodies)

        from django.contrib.auth import get_user_model

        user_obj = None
        if state.user_id:
            try:
                user_obj = get_user_model().objects.get(pk=state.user_id)
            except Exception:
                user_obj = None

        for tc in tool_calls:
            # LangChain normalizes tool_calls to a dict shape.
            tc_id = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", "")
            tc_name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
            tc_args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {}) or {}

            # Audit log: pair every LLM-emitted tool call with the
            # user_query that triggered it so we can later diagnose
            # cases where the LLM hallucinated values not present in
            # the user message (e.g. routed a vague "I want to run a
            # crop experiment" to ``run_experiment`` with invented
            # crop/location/date instead of to the wizard). The args
            # are truncated to keep log lines readable.
            try:
                _args_preview = json.dumps(tc_args, default=str)[:1000]
            except Exception:
                _args_preview = repr(tc_args)[:1000]
            logger.info(
                "[ReAct] tool_call iter=%s tool=%s tc_id=%s "
                "user_query=%r args=%s",
                state.iteration, tc_name, tc_id,
                (state.user_query or "")[:300], _args_preview,
            )

            # Announce the tool call to the UI trace. The corresponding
            # `tool_result` event fires later in this loop once the tool
            # returns (or is rewritten, e.g. needs_input).
            self._publish_tool_call(state.message_id, tc, state.iteration)
            if not isinstance(tc_args, dict):
                try:
                    tc_args = json.loads(tc_args)
                except Exception:
                    tc_args = {}

            entry = registry.get(tc_name)
            if entry is None:
                result = {
                    "status": "error",
                    "error": f"Unknown tool '{tc_name}'. Available: "
                             f"{list(registry.keys())}",
                }
            else:
                # Merge any partial state carried forward from a prior
                # needs_input round-trip on the same tool_call_id.
                carry = pending_ctx.pop(tc_id, {})
                merged_args = {**carry, **tc_args}
                if carry:
                    logger.info(
                        "[ReAct] tool_executor carry-merge tc_id=%s "
                        "carry_keys=%s tc_args_keys=%s merged_keys=%s",
                        tc_id, sorted(carry.keys()),
                        sorted(tc_args.keys() if isinstance(tc_args, dict) else []),
                        sorted(merged_args.keys()),
                    )
                # Stash publish target so tools can call
                # `chat.agent.progress.publish_progress(...)` from
                # anywhere in their call stack — no signature changes.
                from earthrise_agents_base.agent.progress import (
                    set_progress_context, clear_progress_context,
                )
                set_progress_context(
                    message_id=state.message_id,
                    tool_name=tc_name,
                    iteration=state.iteration,
                )
                try:
                    result = entry["func"](merged_args, user=user_obj)
                    if not isinstance(result, dict):
                        result = {"status": "ok", "payload": result}
                except Exception as e:
                    logger.exception("[ReAct] Tool '%s' raised: %s", tc_name, e)
                    result = {"status": "error", "error": str(e)}
                finally:
                    clear_progress_context()

            status = result.get("status")
            # LangGraph interrupt pattern: when a tool returns `needs_input`,
            # post the envelope as a ToolMessage (so the LLM has the context
            # on resume), call `interrupt()` to pause the graph, then on
            # resume inject the user's reply as a HumanMessage and return.
            # The next ReAct iteration re-invokes the tool with args the
            # LLM extracts from the full conversation.
            if status == self._NEEDS_INPUT_STATUS:
                pending_ctx[tc_id] = {
                    **pending_ctx.get(tc_id, {}),
                    **(result.get("known") or {}),
                }
                # Persist the envelope before pausing.
                envelope_for_llm = {
                    "status": self._NEEDS_INPUT_STATUS,
                    "prompt": result.get("prompt", ""),
                    "known": result.get("known", {}),
                }
                if result.get("schema"):
                    envelope_for_llm["schema_summary"] = _summarize_schema(
                        result["schema"]
                    )
                if result.get("next_tool"):
                    envelope_for_llm["next_tool"] = result["next_tool"]
                env_content = json.dumps(envelope_for_llm, default=str)[:4000]
                messages.append(ToolMessage(
                    content=env_content, tool_call_id=tc_id, name=tc_name,
                ))
                tool_results.append({
                    "tool_call_id": tc_id,
                    "name": tc_name,
                    "owner_agent": (entry or {}).get("owner_agent", ""),
                    "status": status,
                    "result": result,
                })
                self._publish_tool_event(
                    state.message_id, tc, {"status": status},
                )

                logger.info(
                    "[ReAct] tool_executor PAUSE on needs_input "
                    "tc_id=%s tool=%s prompt_len=%d known_keys=%s "
                    "next_tool=%s — graph will checkpoint and wait "
                    "for the user's reply.",
                    tc_id, tc_name, len(result.get("prompt", "") or ""),
                    sorted((pending_ctx.get(tc_id) or {}).keys()),
                    result.get("next_tool") or tc_name,
                )
                user_reply = interrupt({
                    "prompt": result.get("prompt", ""),
                    "schema": result.get("schema", {}),
                    "known": pending_ctx[tc_id],
                    "tool_call_id": tc_id,
                    "tool_name": tc_name,
                    "next_tool": result.get("next_tool") or tc_name,
                })
                logger.info(
                    "[ReAct] tool_executor RESUME tc_id=%s reply=%r",
                    tc_id, (user_reply if isinstance(user_reply, str)
                            else json.dumps(user_reply, default=str))[:300],
                )

                # Reserved cancel sentinel — clean up and bail.
                if user_reply == "__cancel__" or (
                    isinstance(user_reply, dict)
                    and user_reply.get("__reply__") == "__cancel__"
                ):
                    pending_ctx.pop(tc_id, None)
                    messages.append(AIMessage(content="Cancelled."))
                    return {
                        "messages_lc": messages,
                        "pending_tool_context": pending_ctx,
                        "tool_results": tool_results,
                        "active_skills": active_skills,
                        "loaded_skill_bodies": loaded_bodies,
                    }

                reply_text = (
                    user_reply if isinstance(user_reply, str)
                    else json.dumps(user_reply, default=str)
                )
                messages.append(HumanMessage(content=reply_text))
                # Don't clear pending_ctx — the LLM may re-invoke the tool
                # with partial args and carry is needed.
                return {
                    "messages_lc": messages,
                    "pending_tool_context": pending_ctx,
                    "tool_results": tool_results,
                    "active_skills": active_skills,
                    "loaded_skill_bodies": loaded_bodies,
                }

            if status == self._SKILL_LOADED_STATUS:
                # Side-effect tool: mutate graph state so the next react
                # iteration binds the newly-available tool bundle.
                skill_name = result.get("skill_name") or ""
                if skill_name and skill_name not in active_skills:
                    active_skills.append(skill_name)
                body = result.get("body", "")
                if skill_name and body:
                    loaded_bodies[skill_name] = body
                # Trim the body on the envelope we hand back to the LLM —
                # the full body lives in the system prompt via
                # loaded_skill_bodies; no need to duplicate it inside the
                # ToolMessage.
                trimmed = dict(result)
                trimmed.pop("body", None)
                result = trimmed

            # Tool completed (terminal status) — clear any carried state.
            pending_ctx.pop(tc_id, None)

            content = json.dumps(result, default=str)
            if len(content) > 8000:
                content = content[:8000]
            messages.append(ToolMessage(
                content=content,
                tool_call_id=tc_id,
                name=tc_name,
            ))
            tool_results.append({
                "tool_call_id": tc_id,
                "name": tc_name,
                "owner_agent": (entry or {}).get("owner_agent", ""),
                "status": status,
                "result": result,
            })

        return {
            "messages_lc": messages,
            "pending_tool_context": pending_ctx,
            "tool_results": tool_results,
            "active_skills": active_skills,
            "loaded_skill_bodies": loaded_bodies,
        }

    # ---- Nodes ----

    def _intake_node(self, state: ChatAgentState) -> Dict:
        """Entry node: sync the user message into memory and hand off to
        the ReAct loop. Intent defaults to TASK — the LLM decides via
        tool selection whether a task tool is needed or it should just
        respond directly.
        """
        user_query = state.user_query

        # Add user message to memory
        self.memory.add_user_message(user_query)

        return {
            "intent": ChatIntent.TASK,
            "confidence": 1.0,
            "turn_count": self.memory.turn_count,
            "messages": self.memory.messages.copy(),
        }

    # ---- Respond Node ----

    def _respond_node(self, state: ChatAgentState) -> Dict:
        """Synthesize the ReAct loop's final response."""
        self._publish_thinking(state.message_id, "generating_response", "Generating response")
        return self._respond_node_body(state)

    def _respond_node_body(self, state: ChatAgentState) -> Dict:
        """Actual respond-node body. Factored so the `generating_response`
        thinking event always fires first.

        Priority order:
          1. Most recent tool result with `status` in `{completed, ok}` →
             let the subagent's response hook (response_text / artifacts /
             response_footer) win. If absent, fall back to LLM synthesis
             from the final AIMessage content.
          2. `needs_input` result → surface the tool's prompt verbatim (real
             interrupt wiring arrives with task #8).
          3. validation_error / error → friendly message from the issue
             list.
          4. No tool calls were made → return the final AIMessage content as
             a direct reply (greeting / small talk).
        """
        messages = state.messages_lc or []
        final_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        final_ai_content = (final_ai.content if final_ai else "") or ""

        # No tool activity: the LLM answered directly.
        if not state.tool_results:
            cleaned = final_ai_content.strip()
            # Guard: if the LLM emitted a tool-call-shaped JSON blob as its
            # final answer (common failure mode when it tries to call a
            # skill name as a tool), don't leak that to the user.
            if cleaned.startswith("{") and '"name"' in cleaned[:120]:
                cleaned = (
                    "I wasn't sure how to handle that request. Could you "
                    "rephrase — or ask to run a new experiment or follow up "
                    "on a recent one?"
                )
            response = cleaned or (
                "I'm not sure how to help with that. Could you rephrase?"
            )
            self.memory.add_assistant_message(response)
            return {"response": response}

        # Find the most recent terminal tool result from a subagent (prefer
        # completed/ok). Meta tools (owner_agent='chat' — list_capabilities,
        # load_skill) are bookkeeping, not task work; don't treat them as
        # the terminal answer. If no subagent task produced a result, fall
        # back to the last result overall.
        terminal = None
        for entry in reversed(state.tool_results):
            status = (entry.get("status") or "").lower()
            if entry.get("owner_agent") == "chat":
                continue
            if status in ("completed", "ok", "done"):
                terminal = entry
                break
        if terminal is None:
            for entry in reversed(state.tool_results):
                if entry.get("owner_agent") != "chat":
                    terminal = entry
                    break
        if terminal is None:
            terminal = state.tool_results[-1]

        result = terminal.get("result") or {}
        status = (terminal.get("status") or "").lower()

        # needs_input: the tool asked for more information. Surface the prompt.
        if status == self._NEEDS_INPUT_STATUS:
            prompt_text = result.get("prompt", "I need a bit more information.")
            response = prompt_text
            self.memory.add_assistant_message(response)
            return {"response": response}

        # validation_error: build a friendly clarification from the issues.
        if status == "validation_error":
            issues = result.get("issues") or []
            if issues:
                lines = [f"- {i.get('field', '?')}: {i.get('message', '')}".rstrip()
                         for i in issues]
                response = (
                    "I couldn't run that yet — a few inputs need attention:\n\n"
                    + "\n".join(lines)
                )
            else:
                response = result.get(
                    "error", "Some inputs need attention.",
                )
            if result.get("hint"):
                response += f"\n\n{result['hint']}"
            self.memory.add_assistant_message(response)
            return {"response": response}

        # error / failed: best-effort message.
        if status in ("error", "failed", "missing_data"):
            response = result.get("error") or result.get("message") or (
                "Something went wrong. Could you try again in a moment?"
            )
            self.memory.add_assistant_message(response)
            return {"response": response}

        # Successful terminal result — prefer subagent-provided response_text.
        if isinstance(result, dict) and result.get("response_text"):
            response = result["response_text"]
        else:
            # Plug the terminal tool result into the LLM for narrative synthesis.
            response = self._synthesize_react_result(state, terminal)

        # Attach any footer the tool included.
        footer = (result.get("response_footer") or "").strip() if isinstance(result, dict) else ""
        if footer and footer not in response:
            response = f"{response}{footer}"

        # Merge subagent-provided context_store (experiment_id, etc.) for next turn.
        conv_ctx = result.get("conversation_context") if isinstance(result, dict) else None
        if conv_ctx:
            self.memory.merge_context(conv_ctx)

        self.memory.add_assistant_message(response)
        return {"response": response, "plan_results": {
            # Legacy shape retained so downstream code (chart rendering,
            # artifact surfacing) can read tool results by agent name.
            terminal.get("name", "result"): result,
        }}

    def _synthesize_react_result(self, state: ChatAgentState, terminal: Dict) -> str:
        """Dispatch to the existing `_synthesize_results` after shimming the
        terminal ReAct tool result into a plan_results-shaped dict.

        Reuses the synthesis prompt / `chat-results` skill body that already
        works for the legacy path — keeps tone/format consistent.
        """
        name = terminal.get("name") or "result"
        shim_state = ChatAgentState(
            user_query=state.user_query,
            intent=state.intent,
            params=state.params,
            plan_summary="",
            plan_tasks=[{
                "task_key": name,
                "display_name": name,
                "agent": terminal.get("owner_agent", ""),
                "skill": name,
            }],
            plan_results={name: terminal.get("result") or {}},
            chat_id=state.chat_id,
            user_id=state.user_id,
        )
        try:
            return self._synthesize_results(shim_state)
        except Exception as e:
            logger.warning("[ReAct/respond] synthesis failed: %s", e)
            # Last-ditch fallback: echo something terse so the user sees progress.
            return "The simulation completed — see the attached details."

    def _synthesize_results(self, state: ChatAgentState) -> str:
        """Synthesize task results into a human-readable narrative using LLM."""
        plan_results = state.plan_results

        # Build focused context for the LLM using key_results (not raw data)
        results_parts = []
        for task_key, result in plan_results.items():
            if not isinstance(result, dict):
                continue
            task_def = next(
                (t for t in state.plan_tasks if t["task_key"] == task_key),
                {"display_name": task_key},
            )
            display_name = task_def.get("display_name", task_key)

            # Build narrative-ready context generically from subagent results
            context = {}
            for key in ('status', 'error'):
                if key in result:
                    context[key] = result[key]

            key_results = result.get('key_results')
            if key_results:
                # Present key results as readable lines for the LLM
                lines = []
                for label, value in key_results.items():
                    if isinstance(value, dict):
                        # Nested dict (e.g. stress factors) — flatten
                        for sub_key, sub_val in value.items():
                            if isinstance(sub_val, dict) and 'avg' in sub_val:
                                avg = sub_val['avg']
                                severity = 'severe' if avg > 0.50 else 'moderate' if avg > 0.20 else 'mild'
                                lines.append(f"- {sub_key.replace('_', ' ').title()}: {severity} (avg {avg:.2f})")
                            else:
                                lines.append(f"- {sub_key}: {sub_val}")
                    else:
                        lines.append(f"- {label}: {value}")
                context['results'] = "\n".join(lines)
            elif isinstance(result.get('results'), list) and result['results']:
                # Generic list-of-hits shape (e.g. RAG retrieval chunks). Pass
                # through raw so the LLM can cite/summarize. No domain-specific
                # formatting here — owning subagents shape their result dicts
                # to whatever is useful; synthesis reads them verbatim.
                context['results'] = result['results']
                if 'query' in result:
                    context['query'] = result['query']
                if 'strategy' in result:
                    context['strategy'] = result['strategy']
                if 'count' in result:
                    context['count'] = result['count']
                # Pre-built citation markdown (when the owning subagent
                # supplied it) lets the LLM cite sources without composing
                # URLs itself. Passed through verbatim.
                if isinstance(result.get('sources_markdown'), list):
                    context['sources_markdown'] = result['sources_markdown']
            else:
                # Fallback: extract from artifact tables
                artifacts = result.get('artifacts', [])
                if artifacts:
                    readable_parts = []
                    for a in artifacts:
                        if a.get('type') == 'table' and a.get('data'):
                            rows = a['data'].get('rows', [])
                            table_lines = [f"{a.get('title', 'Data')}:"]
                            for row in rows:
                                table_lines.append("  " + " | ".join(str(c) for c in row))
                            readable_parts.append("\n".join(table_lines))
                    if readable_parts:
                        context['results'] = "\n\n".join(readable_parts)

            # Merge conversation context from subagent into memory
            conv_context = result.get('conversation_context')
            if conv_context:
                self.memory.merge_context(conv_context)
                logger.info("[Synthesis] Merged conversation_context: %s", list(conv_context.keys()))

            context_str = json.dumps(context, indent=2)
            if len(context_str) > 3000:
                context_str = context_str[:3000] + "\n... (truncated)"
            results_parts.append(f"### {display_name}\n```json\n{context_str}\n```")

        all_results = "\n\n".join(results_parts)
        params_str = json.dumps(state.params, indent=2)[:1000] if state.params else "None"

        # Dynamic results composition: only pull composable `compose_into:
        # results` fragments from subagents that actually produced a result
        # this turn. Derived from `plan_tasks[].agent` so no hardcoded
        # mapping in the chat agent.
        relevant_agents = {
            (t.get("agent") or "") for t in state.plan_tasks if t.get("agent")
        }
        # Skip "chat" — meta-tool results shouldn't pull any subagent
        # results guidance.
        relevant_agents.discard("chat")
        if relevant_agents and hasattr(self.skills, "get_composed_content_for_agents"):
            agent_results_guide = self.skills.get_composed_content_for_agents(
                "results", agents=list(relevant_agents), section="Results",
            )
            # Fallback to the unfiltered version if nothing matched — an
            # unknown agent label shouldn't silently drop all results
            # guidance.
            if not agent_results_guide:
                agent_results_guide = self.skills.get_composed_content(
                    "results", section="Results",
                )
        else:
            agent_results_guide = self.skills.get_composed_content(
                "results", section="Results",
            )
        logger.info("[Synthesis] Composed results guide present: %s (%d chars)  agents=%s",
                    bool(agent_results_guide), len(agent_results_guide or ''),
                    sorted(relevant_agents))
        logger.info("[Synthesis] Results context:\n%s", all_results[:1500])

        try:
            prompt = self.skills.get_prompt("chat-results", section="Results")
            prompt = prompt.format(
                user_query=state.user_query,
                params=params_str,
                results=all_results,
                agent_results=agent_results_guide or "Present results clearly with markdown formatting.",
            )
            logger.info("[Synthesis] Full prompt length: %d chars (~%d tokens)",
                        len(prompt), len(prompt) // 4)
            response = self.llm.invoke(prompt).strip().strip('"\'')
            logger.info("[Synthesis] Response length: %d chars", len(response))
        except Exception as e:
            logger.warning("[ChatAgent] Synthesis failed: %s, using fallback", e)
            response = "Results received but could not be formatted."

        # Append any response footers provided by subagents
        for result in plan_results.values():
            if isinstance(result, dict) and result.get('response_footer'):
                footer = result['response_footer'].strip()
                if footer and footer not in response:
                    response += f"\n\n{footer}"

        return response

    # ---- Public API ----

    def _invoke_graph(
        self, message: str, *, thread_id: str = "default",
        chat_id: Optional[str] = None, user_id: Optional[int] = None,
        message_id: Optional[str] = None,
        enabled_agents: Optional[List[str]] = None,
    ) -> Dict:
        """Invoke the compiled graph, choosing between a fresh invocation
        and a `Command(resume=...)` if the thread is paused at an interrupt.

        Used by both `evaluate()` (simple API) and `tasks._process_message`
        (streaming, Celery path).
        """
        config_dict = {"configurable": {"thread_id": thread_id}}
        snapshot = None
        try:
            snapshot = self.workflow.get_state(config_dict)
        except Exception as e:
            logger.debug("[ChatAgent] get_state failed (%s) — treating as fresh.", e)

        # Snapshot diagnostics — surface what the checkpointer has so we
        # can tell whether resume picks up the right pending tool call,
        # the messages list is intact, and pending_tool_context still
        # holds what was carried forward from the last needs_input.
        if snapshot is not None:
            try:
                snap_values = snapshot.values or {}
                snap_messages = snap_values.get("messages_lc") or []
                pending_ctx = snap_values.get("pending_tool_context") or {}
                last_tool_calls = []
                for m in reversed(snap_messages):
                    if hasattr(m, "tool_calls") and getattr(m, "tool_calls", None):
                        last_tool_calls = m.tool_calls
                        break
                logger.info(
                    "[ChatAgent] snapshot thread=%s next=%s "
                    "messages=%d pending_ctx_keys=%s "
                    "last_tool_calls=%s active_skills=%s",
                    thread_id, snapshot.next, len(snap_messages),
                    list(pending_ctx.keys()),
                    [
                        (tc.get("name") if isinstance(tc, dict)
                         else getattr(tc, "name", "?"))
                        for tc in last_tool_calls
                    ],
                    snap_values.get("active_skills") or [],
                )
            except Exception as e:
                logger.warning("[ChatAgent] snapshot inspect failed: %s", e)

        paused_at_interrupt = bool(snapshot and snapshot.next)
        if paused_at_interrupt:
            logger.info(
                "[ChatAgent] thread %s is paused at %s — resuming with "
                "user reply: %r",
                thread_id, snapshot.next, message[:200],
            )
            # Cancel-keyword shortcut: when the user's reply to an
            # interrupt is a single cancel-style word, substitute the
            # `__cancel__` sentinel that `_tool_executor_node` already
            # handles. Single-word match keeps "cancel my fertilizer
            # plan" from being interpreted as an exit.
            if self._is_cancel_reply(message):
                logger.info("[ChatAgent] interrupt resume routed to __cancel__.")
                resume_value: Any = "__cancel__"
            else:
                resume_value = message
            result = self.workflow.invoke(
                Command(resume=resume_value), config_dict,
            )
            # Fall through to the post-invoke interrupt-extraction block
            # below so a follow-up wizard prompt (the wizard advanced one
            # step and called interrupt() again) becomes the user-facing
            # response. Without this, multi-step wizard turns return an
            # empty string after the first reply.

        else:
            # Carry the prior turn's LangChain message history into this turn so
            # follow-up questions ("add the LAI chart", "what was the yield?")
            # see the previous experiment's tool calls + results, not just the
            # bare new HumanMessage. Without this seed, the ReAct loop opens
            # with `[SystemMessage, HumanMessage]` only — the LLM has zero
            # memory of what was just run and gives a generic "please clarify"
            # reply with no tool calls. We append the new user message here;
            # the ReAct loop's leading SystemMessage gets refreshed each
            # iteration so newly-loaded skills land in the prompt.
            prior_messages_lc: List[BaseMessage] = []
            if snapshot is not None:
                try:
                    prior_messages_lc = list(
                        (snapshot.values or {}).get("messages_lc") or []
                    )
                except Exception:
                    prior_messages_lc = []
            seeded_messages_lc: List[BaseMessage] = list(prior_messages_lc)
            if seeded_messages_lc:
                seeded_messages_lc.append(HumanMessage(content=message))

            initial_state = ChatAgentState(
                user_query=message,
                params=self.memory.confirmed_params,
                messages=self.memory.messages.copy(),
                messages_lc=seeded_messages_lc,
                turn_count=self.memory.turn_count,
                chat_id=chat_id,
                user_id=user_id,
                message_id=message_id,
                # Seed ReAct state from persisted memory so skills loaded in
                # prior turns stay available and the LLM has access to the
                # subagent-provided context (experiment_id, etc.) without
                # re-discovering everything.
                active_skills=list(self.memory.active_skills),
                loaded_skill_bodies=dict(self.memory.loaded_skill_bodies),
                enabled_agents=list(enabled_agents or []),
            )
            result = self.workflow.invoke(initial_state, config_dict)

        # Persist active_skills / loaded_skill_bodies back to memory so the
        # next turn sees them. (The graph checkpointer survives a Celery
        # worker but the ConversationMemory is what tasks.py writes to
        # Chat.agent_memory.)
        self.memory.active_skills = list(
            result.get("active_skills", []) or self.memory.active_skills
        )
        self.memory.loaded_skill_bodies = dict(
            result.get("loaded_skill_bodies", {})
            or self.memory.loaded_skill_bodies
        )

        # If the graph paused at an interrupt, the `respond` node never ran
        # — `result["response"]` is empty and the UI would show nothing.
        # Surface the most recent interrupt's prompt so the user sees the
        # wizard's question and can reply.
        if not result.get("response"):
            interrupts = result.get("__interrupt__") or ()
            if interrupts:
                latest = interrupts[-1]
                payload = getattr(latest, "value", None)
                if payload is None and isinstance(latest, dict):
                    payload = latest.get("value", latest)
                prompt_text = ""
                if isinstance(payload, dict):
                    prompt_text = str(payload.get("prompt") or "").strip()
                    if not prompt_text and payload.get("known"):
                        prompt_text = "I need more information to continue."
                if prompt_text:
                    response = (
                        f"{prompt_text}\n\n_Reply 'cancel' to exit this flow._"
                    )
                    result["response"] = response
                    self.memory.add_assistant_message(response)
        return result

    @staticmethod
    def _is_cancel_reply(message: str) -> bool:
        """Single-word cancel-style replies route to the `__cancel__`
        sentinel that `_tool_executor_node` already handles. Restrictive
        on purpose — "cancel my fertilizer plan" should not match.
        """
        if not isinstance(message, str):
            return False
        cleaned = message.strip().lower().rstrip(".!?")
        return cleaned in {
            "cancel", "stop", "abandon", "exit", "quit",
            "nevermind", "never mind",
        }

    def evaluate(
        self, message: str, *, thread_id: str = "default",
        enabled_agents: Optional[List[str]] = None,
    ) -> str:
        """Process a single user message and return the response."""
        result = self._invoke_graph(
            message, thread_id=thread_id, enabled_agents=enabled_agents,
        )
        return result.get("response", "I encountered an error processing your request.")

    def verbose_evaluate(
        self, message: str, *, thread_id: str = "default",
        enabled_agents: Optional[List[str]] = None,
    ) -> tuple:
        """Process message and return (response, full_state_dict)."""
        result = self._invoke_graph(
            message, thread_id=thread_id, enabled_agents=enabled_agents,
        )
        response = result.get("response", "")
        return response, result

    def get_memory_state(self) -> Dict:
        return self.memory.to_dict()

    def restore_memory(self, state: Dict):
        self.memory = ConversationMemory.from_dict(state)

    def reset(self):
        self.memory = ConversationMemory()


def create_chat_agent(memory: Optional[ConversationMemory] = None) -> ChatAgent:
    """Factory function to create a ChatAgent."""
    return ChatAgent(memory=memory)
