import json
import asyncio
import logging
from asgiref.sync import sync_to_async
from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .models import Chat, Message
from .agent.chat_agent import ChatAgent, ChatAgentState, ConversationMemory


logger = logging.getLogger(__name__)


# Node name → human-readable title/description for SSE events
NODE_TITLES = {
    'orchestrator': 'Understanding Request',
    'plan': 'Planning',
    'execute': 'Executing Tasks',
    'respond': 'Generating Response',
}

NODE_DESCRIPTIONS = {
    'orchestrator': 'Analyzing your message...',
    'plan': 'Selecting the right approach...',
    'execute': 'Running tasks...',
    'respond': 'Preparing your response...',
}


def _publish_chat_event(message_id, data):
    """Publish an event to the Redis pub/sub channel for a chat message."""
    import redis as _redis
    try:
        r = _redis.Redis.from_url(settings.CELERY_BROKER_URL)
        r.publish(f"earthrise_agents_base:{message_id}", json.dumps(data))
    except Exception as e:
        logger.warning("Failed to publish chat event for %s: %s", message_id, e)


@shared_task(bind=True)
def process_message_task(self, chat_id, message_id, message_content, raw_content=None, user_id=None):
    """Celery task to process a chat message."""
    final_state = asyncio.run(
        _process_message(chat_id, message_id, message_content, raw_content, user_id=user_id)
    )
    return final_state


async def _process_message(chat_id, message_id, message_content, raw_content=None, user_id=None):
    """Process message using the ChatAgent orchestrator with memory persistence."""
    chat = await sync_to_async(Chat.objects.get)(id=chat_id)
    message = await sync_to_async(Message.objects.get)(id=message_id)

    message.status = 'pending'
    await sync_to_async(message.save)(update_fields=['status'])

    message_content = raw_content or message_content

    # Restore memory from chat if available
    memory = None
    if chat.agent_memory:
        memory = ConversationMemory.from_dict(chat.agent_memory)

    agent = ChatAgent(memory=memory)

    # Check for wizard params in agent_state or memory
    wizard_params = None
    if chat.agent_state and 'wizard_params' in chat.agent_state:
        wizard_params = chat.agent_state['wizard_params']
    elif agent.memory.wizard_params:
        wizard_params = agent.memory.wizard_params

    # Create initial state
    initial_state = ChatAgentState(
        user_query=message_content,
        params=agent.memory.confirmed_params,
        messages=agent.memory.messages.copy(),
        turn_count=agent.memory.turn_count,
        wizard_params=wizard_params,
        message_id=str(message.id),
        chat_id=str(chat.id),
        user_id=user_id,
    )

    message_id = str(message.id)
    final_state_dict = {}

    try:
        # ReAct path uses the sync PostgresSaver checkpointer, which doesn't
        # implement `aget_tuple` required by `astream_events`. Invoke the
        # compiled graph synchronously through `sync_to_async`; per-node
        # thinking events are dropped here — task #10 (UI trace rework)
        # restores richer events by publishing per-tool-call and per-stage
        # events directly from the ReAct nodes.
        _publish_chat_event(message_id, {
            'type': 'thinking',
            'title': 'Understanding Request',
            'description': 'Analyzing your message...',
        })
        logger.info(
            "[ChatTask] invoking graph chat_id=%s enabled_agents=%s agents_locked=%s",
            chat.id, list(chat.enabled_agents or []), bool(chat.agents_locked),
        )
        final_state_dict = await sync_to_async(
            agent._invoke_graph, thread_sensitive=False,
        )(
            message_content,
            thread_id=str(chat.id),
            chat_id=str(chat.id),
            user_id=user_id,
            message_id=message_id,
            # Per-chat agent-scoping policy. Empty list means no
            # restriction (all discovered agents enabled).
            enabled_agents=list(chat.enabled_agents or []),
        )

        # Get response
        response_content = final_state_dict.get("response", "")

        # Save agent memory for multi-turn conversations
        chat.agent_memory = agent.get_memory_state()

        # Serialize a bounded subset of the final graph state for UI/debug
        # use. Runtime-only fields (LangChain messages, active skill bodies,
        # pending tool contexts, etc.) are owned by the LangGraph
        # checkpointer — keeping them out of `Chat.agent_state` both avoids
        # BaseMessage JSON-serialization errors and prevents duplication.
        _EXCLUDED_STATE_KEYS = {
            "messages_lc",
            "pending_tool_context",
            "active_skills",
            "loaded_skill_bodies",
            "tool_results",
        }

        def _json_safe(value):
            if hasattr(value, "to_dict"):
                try:
                    return value.to_dict()
                except Exception:
                    return None
            if isinstance(value, (str, int, float, bool, type(None))):
                return value
            if isinstance(value, list):
                return [_json_safe(v) for v in value]
            if isinstance(value, dict):
                return {k: _json_safe(v) for k, v in value.items()}
            return None  # drop anything else (AIMessage, enums without to_dict, etc.)

        serializable_state = {}
        for key, value in final_state_dict.items():
            if key in _EXCLUDED_STATE_KEYS:
                continue
            safe = _json_safe(value)
            if safe is not None or value is None:
                serializable_state[key] = safe

        chat.agent_state = serializable_state
        chat.is_processing = False
        chat.updated_at = timezone.now()
        await sync_to_async(chat.save)()

        # Experiment records are now stored directly on ExperimentSession
        # via chat_id (passed through run_full_simulation). No ChatExperiment needed.

        # Collect artifacts from plan results (charts, maps, etc. generated by subagents)
        artifacts = []
        plan_results = final_state_dict.get('plan_results', {})
        for tk, result in plan_results.items():
            if isinstance(result, dict):
                # Charts
                if result.get('charts'):
                    artifacts.extend(result['charts'])
                # Any other artifacts subagents may return
                if result.get('artifacts'):
                    artifacts.extend(result['artifacts'])

        # Mark message as completed with artifacts
        message.content = response_content
        message.status = 'completed'
        message.current_node = None
        message.artifacts = artifacts
        await sync_to_async(message.save)(update_fields=['content', 'status', 'current_node', 'artifacts'])

        # Publish done event for SSE consumers
        _publish_chat_event(message_id, {
            'type': 'done',
            'content': response_content,
            'artifacts': artifacts,
        })

        return serializable_state

    except Exception as e:
        logger.error(f"[ChatAgent] Error in workflow: {str(e)}")
        error_msg = f"I encountered an error: {str(e)}. Please try again."
        message.content = error_msg
        message.status = 'failed'
        message.current_node = None
        await sync_to_async(message.save)(update_fields=['content', 'status', 'current_node'])

        # Publish error event for SSE consumers
        _publish_chat_event(message_id, {
            'type': 'error',
            'message': error_msg,
        })

        chat.is_processing = False
        await sync_to_async(chat.save)(update_fields=['is_processing'])
        raise e
