"""
Signal handlers for the chat app.

Cascade deletion of LangGraph checkpoint rows when their owning Chat is
deleted. The checkpointer's tables (`checkpoints`, `checkpoint_writes`,
`checkpoint_blobs`) live alongside `chat_chat` but aren't FK-linked, so
Django won't cascade automatically — this handler bridges the gap.
"""

from __future__ import annotations

import logging

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from earthrise_agents_base.models import (
    Chat,
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    LangGraphCheckpointBlob,
)

logger = logging.getLogger(__name__)


@receiver(pre_delete, sender=Chat)
def delete_langgraph_checkpoints(sender, instance: Chat, **kwargs) -> None:
    """Remove every LangGraph checkpoint row whose `thread_id` matches the
    Chat being deleted. Run before the Chat row goes away so a transactional
    delete rolls back cleanly on failure.
    """
    thread_id = str(instance.id)
    deleted_total = 0
    for model in (
        LangGraphCheckpointWrite,
        LangGraphCheckpointBlob,
        LangGraphCheckpoint,
    ):
        try:
            count, _ = model.objects.filter(thread_id=thread_id).delete()
            deleted_total += count
        except Exception as e:
            # Tables may not exist yet (e.g. fresh DB before any chat runs)
            # — log but don't block the chat deletion.
            logger.warning(
                "[chat.signals] could not clean %s for thread=%s: %s",
                model.__name__, thread_id, e,
            )
    if deleted_total:
        logger.info(
            "[chat.signals] deleted %d langgraph rows for thread=%s",
            deleted_total, thread_id,
        )
