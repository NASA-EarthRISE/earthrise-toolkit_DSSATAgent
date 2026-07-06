"""
Admin-only management views for the "Platform" management tab.

Exposes the feedback review UI: a "Message Feedback" browser for developers
to diagnose intent-vs-response mismatches on flagged conversations, and a
"Site Feedback" triage view for platform-level submissions.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from .mixins import AdminRequiredMixin
from .models import MessageFeedback, SiteFeedback

logger = logging.getLogger(__name__)


def _message_feedback_to_dict(fb: MessageFeedback) -> dict:
    """Serialise a MessageFeedback row with enough context for dev review."""
    message = fb.message
    chat = fb.chat  # Denormalised FK — no join through message needed.
    # Find the last user message before this assistant message, so reviewers
    # can see the query alongside the rated response.
    prior_user = (
        chat.messages
        .filter(message_type='user', created_at__lt=message.created_at)
        .order_by('-created_at')
        .first()
    )
    return {
        'id': str(fb.id),
        'rating': fb.rating,
        'comment': fb.comment,
        'created_at': fb.created_at.isoformat(),
        'updated_at': fb.updated_at.isoformat(),
        'user': {
            'id': fb.user_id,
            'username': fb.user.username if fb.user_id else '',
            'full_name': fb.user.get_full_name() if fb.user_id else '',
        },
        'chat': {
            'id': str(chat.id),
            'title': chat.title or 'Untitled Chat',
        },
        'message': {
            'id': str(message.id),
            'snippet': (message.content or '')[:200],
        },
        'prior_user_message': (
            {'snippet': (prior_user.content or '')[:200]}
            if prior_user else None
        ),
    }


class FeedbackManagementView(AdminRequiredMixin, View):
    """Render the two-tab feedback management page (admin only)."""

    def get(self, request):
        # Bootstrap counts so the UI can show tab badges without a second fetch.
        message_feedback_count = MessageFeedback.objects.count()
        site_feedback_new_count = SiteFeedback.objects.filter(status='new').count()

        context = {
            'message_feedback_count': message_feedback_count,
            'site_feedback_new_count': site_feedback_new_count,
            'categories': SiteFeedback.CATEGORIES,
            'statuses': SiteFeedback.STATUS,
            # Server-side render of site feedback — admin table is small
            # enough not to need pagination in v1.
            'site_feedback': (
                SiteFeedback.objects
                .select_related('user', 'reviewed_by')
                .all()
            ),
        }
        return render(request, 'earthrise_agents_base/feedback_management.html', context)


class MessageFeedbackListAPIView(AdminRequiredMixin, View):
    """JSON feed for the "Message Feedback" tab.

    GET /management/feedback/messages/?rating=<up|down|all>&page=<n>

    Paginates at 50 rows per page. Returns context (chat title, user, prior
    question snippet) so the tab can render rows without follow-up fetches.
    """

    PAGE_SIZE = 50

    def get(self, request):
        rating = request.GET.get('rating', 'all')
        chat_id = request.GET.get('chat_id')
        try:
            page = max(1, int(request.GET.get('page', '1')))
        except ValueError:
            page = 1

        qs = (
            MessageFeedback.objects
            .select_related('chat', 'message', 'user')
            .order_by('-created_at')
        )
        if rating in ('up', 'down'):
            qs = qs.filter(rating=rating)
        if chat_id:
            qs = qs.filter(chat_id=chat_id)

        total = qs.count()
        start = (page - 1) * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        rows = [_message_feedback_to_dict(fb) for fb in qs[start:end]]

        return JsonResponse({
            'success': True,
            'total': total,
            'page': page,
            'page_size': self.PAGE_SIZE,
            'has_next': end < total,
            'feedback': rows,
        })


@method_decorator(csrf_exempt, name='dispatch')
class SiteFeedbackUpdateAPIView(AdminRequiredMixin, View):
    """Admin action: update status / notes on a SiteFeedback row.

    POST /management/feedback/<feedback_id>/update/
        Body: {status?, admin_notes?}
    """

    _ALLOWED_STATUSES = {key for key, _ in SiteFeedback.STATUS}

    def post(self, request, feedback_id):
        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        feedback = get_object_or_404(SiteFeedback, id=feedback_id)

        update_fields = []
        if 'status' in data:
            new_status = data['status']
            if new_status not in self._ALLOWED_STATUSES:
                return JsonResponse(
                    {'error': f"Unknown status '{new_status}'",
                     'allowed': sorted(self._ALLOWED_STATUSES)},
                    status=400,
                )
            if feedback.status != new_status:
                feedback.status = new_status
                update_fields.append('status')
                feedback.reviewed_at = timezone.now()
                feedback.reviewed_by = request.user
                update_fields.extend(['reviewed_at', 'reviewed_by'])

        if 'admin_notes' in data:
            feedback.admin_notes = (data.get('admin_notes') or '').strip()
            update_fields.append('admin_notes')

        if update_fields:
            feedback.save(update_fields=update_fields + ['updated_at'])

        return JsonResponse({
            'success': True,
            'feedback': {
                'id': str(feedback.id),
                'status': feedback.status,
                'admin_notes': feedback.admin_notes,
                'reviewed_at': (
                    feedback.reviewed_at.isoformat() if feedback.reviewed_at else None
                ),
                'reviewed_by': (
                    feedback.reviewed_by.get_full_name() or feedback.reviewed_by.username
                    if feedback.reviewed_by_id else None
                ),
            },
        })
