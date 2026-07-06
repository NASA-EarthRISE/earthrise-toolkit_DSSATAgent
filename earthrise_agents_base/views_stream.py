"""
Server-Sent Events endpoint for real-time message progress streaming.

Subscribes to a Redis pub/sub channel ``chat:<message_id>`` and forwards
events to the browser as SSE.
"""

import json
import asyncio
import logging

import redis.asyncio as aioredis
from django.conf import settings
from django.http import StreamingHttpResponse, JsonResponse
from django.views import View

logger = logging.getLogger(__name__)

# How long to wait for events before giving up (seconds)
STREAM_TIMEOUT = 600


class MessageStreamView(View):
    """GET /api/chats/<chat_id>/messages/<message_id>/stream/"""

    async def get(self, request, chat_id, message_id):
        return StreamingHttpResponse(
            self._event_generator(str(message_id)),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            },
        )

    async def _event_generator(self, message_id):
        channel_name = f"earthrise_agents_base:{message_id}"

        r = aioredis.from_url(settings.CELERY_BROKER_URL)
        pubsub = r.pubsub()
        await pubsub.subscribe(channel_name)

        try:
            deadline = asyncio.get_event_loop().time() + STREAM_TIMEOUT
            while asyncio.get_event_loop().time() < deadline:
                msg = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=5.0,
                )
                if msg is None:
                    # Send heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
                    continue
                if msg['type'] != 'message':
                    continue

                try:
                    data = json.loads(msg['data'])
                except (json.JSONDecodeError, TypeError):
                    continue

                yield f"data: {json.dumps(data)}\n\n"

                if data.get('type') in ('done', 'error'):
                    break

        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Stream timed out.'})}\n\n"
        finally:
            await pubsub.unsubscribe(channel_name)
            await r.aclose()


message_stream_view = MessageStreamView.as_view()
