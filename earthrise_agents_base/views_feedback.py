"""
Feedback API endpoints.

Separated from chat/views.py to keep the message-handling hot path free of
feedback imports. Follows the same pattern as chat/views_stream.py.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .models import Chat, Message, MessageFeedback, SiteFeedback

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class MessageFeedbackAPIView(View):
    """Upsert / delete the current user's rating on a single message.

    PUT /api/chats/<chat_id>/messages/<message_id>/feedback/
        Body: {rating: 'up'|'down', comment?: str}
        Creates or updates the (user, message) feedback row.

    DELETE /api/chats/<chat_id>/messages/<message_id>/feedback/
        Removes the current user's rating on this message.
    """

    def put(self, request, chat_id, message_id):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)

        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        rating = data.get('rating')
        if rating not in ('up', 'down'):
            return JsonResponse(
                {'error': "rating must be 'up' or 'down'"}, status=400,
            )

        comment = (data.get('comment') or '').strip()

        chat = get_object_or_404(Chat, id=chat_id)
        message = get_object_or_404(Message, id=message_id, chat=chat)
        if message.message_type != 'assistant':
            return JsonResponse(
                {'error': 'Feedback is only allowed on assistant messages'},
                status=400,
            )

        feedback, created = MessageFeedback.objects.update_or_create(
            user=request.user,
            message=message,
            defaults={
                'rating': rating,
                'comment': comment,
                'chat': chat,
            },
        )
        return JsonResponse({
            'success': True,
            'created': created,
            'feedback': {
                'id': str(feedback.id),
                'rating': feedback.rating,
                'comment': feedback.comment,
                'updated_at': feedback.updated_at.isoformat(),
            },
        })

    def delete(self, request, chat_id, message_id):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)

        chat = get_object_or_404(Chat, id=chat_id)
        message = get_object_or_404(Message, id=message_id, chat=chat)
        deleted, _ = MessageFeedback.objects.filter(
            user=request.user, message=message,
        ).delete()
        return JsonResponse({'success': True, 'removed': deleted})


@method_decorator(csrf_exempt, name='dispatch')
class SiteFeedbackAPIView(View):
    """Accept a platform-wide feedback submission.

    POST /api/feedback/
        Body: {category, content, page_url?, anonymous_email?}

    Unauthenticated submissions are allowed; `user` is set when the request
    has an authenticated session.
    """

    _ALLOWED_CATEGORIES = {key for key, _ in SiteFeedback.CATEGORIES}

    def post(self, request):
        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        category = data.get('category')
        content = (data.get('content') or '').strip()
        page_url = (data.get('page_url') or '').strip()
        anonymous_email = (data.get('anonymous_email') or '').strip()

        if category not in self._ALLOWED_CATEGORIES:
            return JsonResponse(
                {'error': f"Unknown category '{category}'",
                 'allowed': sorted(self._ALLOWED_CATEGORIES)},
                status=400,
            )
        if not content:
            return JsonResponse({'error': 'content is required'}, status=400)

        user = request.user if request.user.is_authenticated else None

        feedback = SiteFeedback.objects.create(
            user=user,
            anonymous_email=anonymous_email if not user else '',
            category=category,
            content=content,
            page_url=page_url[:500],
        )
        logger.info(
            "[SiteFeedback] new %s submission id=%s user=%s",
            category, feedback.id, user.pk if user else 'anonymous',
        )
        return JsonResponse({
            'success': True,
            'id': str(feedback.id),
        })
