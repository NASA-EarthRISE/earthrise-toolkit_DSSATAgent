import json
import logging
import os
import time

from django.conf import settings
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View

from .mixins import is_admin
from .models import Chat, Message, MessageFeedback
from .tasks import process_message_task


def _build_feedback_map(user, messages, viewer_is_admin):
    """Return {message_id: feedback_payload} for inclusion in message dicts.

    For the active user: their own rating on each assistant message (so the
    UI can render selected state on page load).
    For admins: additionally include every other rater's feedback on each
    message, so admins inspecting a chat can see all ratings inline.
    """
    assistant_ids = [m.id for m in messages if m.message_type == 'assistant']
    if not assistant_ids:
        return {}

    result = {}

    if user.is_authenticated:
        own_qs = MessageFeedback.objects.filter(
            user=user, message_id__in=assistant_ids,
        )
        for fb in own_qs:
            result.setdefault(str(fb.message_id), {})['own'] = {
                'id': str(fb.id),
                'rating': fb.rating,
                'comment': fb.comment,
            }

    if viewer_is_admin:
        all_qs = (
            MessageFeedback.objects
            .filter(message_id__in=assistant_ids)
            .select_related('user')
            .order_by('-created_at')
        )
        for fb in all_qs:
            entry = result.setdefault(str(fb.message_id), {})
            others = entry.setdefault('others', [])
            others.append({
                'id': str(fb.id),
                'rating': fb.rating,
                'comment': fb.comment,
                'user': {
                    'id': fb.user_id,
                    'username': fb.user.username,
                    'full_name': fb.user.get_full_name(),
                },
                'updated_at': fb.updated_at.isoformat(),
            })

    return result

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def format_time(t):
    minutes = int(t // 60)
    seconds = int(t % 60)
    milliseconds = int((t * 1000) % 1000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


class HomeView(View):
    """Home page with agent selection."""

    def get(self, request):
        if is_admin(request.user):
            recent_chats = Chat.objects.all()[:5]
        else:
            recent_chats = Chat.objects.filter(user=request.user)[:5]
        return render(request, 'earthrise_agents_base/home.html', {'recent_chats': recent_chats})


class ChatView(View):
    """Chat interface."""

    def get(self, request, chat_id):
        chat = get_object_or_404(Chat, id=chat_id)
        # Ownership check: only owner or admin can view
        if chat.user and chat.user != request.user and not is_admin(request.user):
            from django.http import HttpResponseForbidden
            return HttpResponseForbidden("You do not have access to this chat.")
        messages = list(chat.messages.all().order_by('created_at'))
        viewer_is_admin = is_admin(request.user)
        feedback_map = _build_feedback_map(
            request.user, messages, viewer_is_admin,
        )

        messages_data = []
        for msg in messages:
            message_data = {
                'id': str(msg.id),
                'type': msg.message_type,
                'content': msg.content,
                'created_at': msg.created_at.isoformat(),
                'status': msg.status,
                'artifacts': msg.artifacts or [],
                'feedback': feedback_map.get(str(msg.id), {}),
            }
            messages_data.append(message_data)

        subpath = os.environ.get('SUBPATH', '')
        base_path = f'/{subpath}' if subpath else ''

        # Available agent labels drive the toggle bar; enabled_agents is
        # the persisted set. Only embedded agents are toggleable — remote
        # agents don't contribute to the local TOOL_REGISTRY so the filter
        # has no effect on them.
        from earthrise_agents_base.agent.discovery import discover_agents
        agent_clients = getattr(settings, 'AGENT_CLIENTS', {})
        discovered = discover_agents()
        embedded = [
            (label, meta) for label, meta in discovered.items()
            if (meta.get('app_config') is not None
                and agent_clients.get(label, {}).get('mode', 'embedded') == 'embedded')
        ]
        available_labels = [label for label, _ in embedded]
        # Pair each label with its human-readable display name so the JS
        # controller can render both modes (toggle buttons + locked pill)
        # purely from template-data.
        available_agent_list = [
            {
                'label': label,
                'display': (
                    getattr(meta.get('app_config'), 'display_name', None)
                    or label.title()
                ),
            }
            for label, meta in embedded
        ]

        context = {
            'chat': chat,
            'messages': json.dumps(messages_data),
            'chat_id': str(chat.id),
            'is_processing': chat.is_processing,
            'base_path': base_path,
            'enabled_agents_json': json.dumps(list(chat.enabled_agents or [])),
            'available_agent_labels_json': json.dumps(available_labels),
            'available_agent_list_json': json.dumps(available_agent_list),
        }
        return render(request, 'earthrise_agents_base/chat.html', context)


def _get_welcome_message() -> str:
    """Build welcome message from composable agent greeting fragments."""
    try:
        from .agent.chat_agent import _get_skills
        skills = _get_skills()
        raw = skills.get_composed_content("capabilities", section="Greeting")
        if raw:
            # Each agent contributes a capability fragment; assemble into one sentence
            fragments = [f.strip() for f in raw.split('\n\n') if f.strip()]
            if fragments:
                capabilities = ", ".join(fragments)
                return (
                    f"Hello! I can help you {capabilities}. "
                    f"What would you like to do?"
                )
    except Exception:
        pass
    return getattr(settings, 'CHAT_WELCOME_MESSAGE', "Hello! How can I help you today?")


class NewChatView(View):
    """Create a new chat and redirect."""

    def get(self, request):
        return self._create_chat(request)

    def post(self, request):
        return self._create_chat(request)

    def _create_chat(self, request):
        chat = Chat.objects.create(
            title='New Chat',
            user=request.user if request.user.is_authenticated else None,
        )

        Message.objects.create(
            chat=chat,
            message_type='assistant',
            content=_get_welcome_message(),
            status='completed'
        )

        return redirect('earthrise_agents_base:chat', chat_id=chat.id)


@method_decorator(csrf_exempt, name='dispatch')
class NewChatAPIView(View):
    """API endpoint for creating new chats."""

    def post(self, request):
        try:
            data = json.loads(request.body) if request.body else {}
            title = data.get('title', '')

            chat = Chat.objects.create(
                title=title,
                user=request.user if request.user.is_authenticated else None,
            )
            welcome_message = _get_welcome_message()

            Message.objects.create(
                chat=chat,
                message_type='assistant',
                content=welcome_message,
                status='completed'
            )

            return JsonResponse({
                'success': True,
                'chat_id': str(chat.id),
                'redirect_url': f'/chats/{chat.id}/'
            })

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class MessageAPIView(View):
    """API endpoint for sending new messages."""

    def post(self, request, chat_id):
        try:
            data = json.loads(request.body)
            content = data.get('content', '').strip()

            if not content:
                return JsonResponse({'error': 'Message content is required'}, status=400)

            chat = get_object_or_404(Chat, id=chat_id)

            # Authoritative agent scope: the Send payload carries the
            # toggle-bar state so we don't race the debounced PATCH. On
            # locked chats the field is ignored (toggle bar is hidden).
            logger.info(
                "[MessageAPIView] POST body keys=%s has_enabled_agents=%s raw=%r locked=%s",
                list(data.keys()), 'enabled_agents' in data,
                data.get('enabled_agents'), bool(chat.agents_locked),
            )
            if 'enabled_agents' in data and not chat.agents_locked:
                raw = data.get('enabled_agents') or []
                if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
                    from earthrise_agents_base.agent.discovery import discover_agents
                    known = set(discover_agents().keys())
                    seen = set()
                    normalized = []
                    for label in raw:
                        if label in known and label not in seen:
                            seen.add(label)
                            normalized.append(label)
                    chat.enabled_agents = normalized
                    chat.save(update_fields=['enabled_agents', 'updated_at'])
                    logger.info(
                        "[MessageAPIView] updated chat.enabled_agents=%r", normalized,
                    )

            # Build conversation history
            conversation_parts = []
            messages = chat.messages.filter(
                message_type__in=['user', 'assistant']
            ).order_by('created_at')

            for message in messages:
                if message.message_type == 'user':
                    conversation_parts.append(f"User: {message.content}")
                elif message.message_type == 'assistant' and message.content.strip():
                    conversation_parts.append(f"Assistant: {message.content}")

            conversation_parts.append(f"User: {content}")

            if len(conversation_parts) > 1:
                compiled_conversation = "\n\n".join(conversation_parts)
            else:
                compiled_conversation = content

            user_message = Message.objects.create(
                chat=chat,
                message_type='user',
                content=content,
                status='completed'
            )

            assistant_message = Message.objects.create(
                chat=chat,
                message_type='assistant',
                content='',
                status='pending'
            )

            chat.is_processing = True
            chat.save()

            task = process_message_task.delay(
                str(chat.id),
                str(assistant_message.id),
                compiled_conversation,
                content,
                user_id=request.user.pk if request.user.is_authenticated else None,
            )

            assistant_message.task_id = task.id
            assistant_message.save()

            return JsonResponse({
                'success': True,
                'user_message_id': str(user_message.id),
                'assistant_message_id': str(assistant_message.id),
                'task_id': task.id
            })

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class MessageStatusAPIView(View):
    """API endpoint for checking message processing status."""

    def get(self, request, chat_id, message_id):
        try:
            chat = get_object_or_404(Chat, id=chat_id)
            message = get_object_or_404(Message, id=message_id, chat=chat)

            response_data = {
                'status': message.status,
                'is_processing': chat.is_processing,
                'current_node': message.current_node,
            }

            if message.status == 'completed':
                response_data.update({
                    'content': message.content,
                    'charts': message.artifacts or [],
                })

            elif message.status == 'interrupted':
                response_data.update({
                    'content': message.content,
                    'interrupt_data': message.interrupt_data,
                    'charts': message.artifacts or [],
                })

            elif message.status == 'failed':
                response_data['content'] = message.content or 'Message processing failed'
                response_data['error'] = 'Message processing failed'

            return JsonResponse(response_data)

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class ChatsListAPIView(View):
    """API endpoint for listing all chats."""

    def get(self, request):
        try:
            if is_admin(request.user):
                chats = Chat.objects.all().order_by('-updated_at')
            else:
                chats = Chat.objects.filter(user=request.user).order_by('-updated_at')
            chats_data = []
            for chat in chats:
                last_user_message = chat.messages.filter(
                    message_type='user'
                ).order_by('-created_at').first()

                preview = ''
                if last_user_message:
                    preview = last_user_message.content[:60]
                    if len(last_user_message.content) > 60:
                        preview += '...'

                chat_entry = {
                    'id': str(chat.id),
                    'title': chat.title or 'Untitled Chat',
                    'preview': preview,
                    'updated_at': chat.updated_at.isoformat(),
                    'created_at': chat.created_at.isoformat(),
                }
                if is_admin(request.user) and chat.user:
                    chat_entry['owner'] = chat.user.get_full_name() or chat.user.username
                chats_data.append(chat_entry)

            return JsonResponse({'success': True, 'chats': chats_data})

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class ChatDetailAPIView(View):
    """API endpoint for getting a specific chat with messages."""

    def get(self, request, chat_id):
        try:
            chat = get_object_or_404(Chat, id=chat_id)
            # Ownership check
            viewer_is_admin = is_admin(request.user)
            if chat.user and chat.user != request.user and not viewer_is_admin:
                return JsonResponse({'error': 'Access denied'}, status=403)
            messages = list(chat.messages.all().order_by('created_at'))
            feedback_map = _build_feedback_map(
                request.user, messages, viewer_is_admin,
            )

            messages_data = []
            for msg in messages:
                message_data = {
                    'id': str(msg.id),
                    'type': msg.message_type,
                    'content': msg.content,
                    'created_at': msg.created_at.isoformat(),
                    'status': msg.status,
                    'artifacts': msg.artifacts or [],
                    'feedback': feedback_map.get(str(msg.id), {}),
                }
                messages_data.append(message_data)

            return JsonResponse({
                'success': True,
                'chat': {
                    'id': str(chat.id),
                    'title': chat.title or 'Untitled Chat',
                    'created_at': chat.created_at.isoformat(),
                    'updated_at': chat.updated_at.isoformat(),
                    'enabled_agents': list(chat.enabled_agents or []),
                    'agents_locked': bool(chat.agents_locked),
                },
                'messages': messages_data,
                'is_processing': chat.is_processing
            })

        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)


@method_decorator(csrf_exempt, name='dispatch')
class ChatEnabledAgentsAPIView(View):
    """PATCH /api/chats/<uuid:chat_id>/agents/

    Body: {"enabled_agents": ["data", "knowledge"]}  (empty list = all)
    Returns: {"enabled_agents": [...], "agents_locked": bool}

    Rejects with 403 when the chat is `agents_locked=True` (set by any
    caller that wants to hard-pin a chat's agent scope at creation).
    """

    def patch(self, request, chat_id):
        try:
            chat = get_object_or_404(Chat, id=chat_id)
            if chat.user and chat.user != request.user and not is_admin(request.user):
                return JsonResponse({'error': 'Access denied'}, status=403)
            if chat.agents_locked:
                return JsonResponse({
                    'error': (
                        "This chat has a fixed agent scope set at "
                        "creation and cannot be widened. Start a new "
                        "chat to use other agents."
                    ),
                    'enabled_agents': list(chat.enabled_agents or []),
                    'agents_locked': True,
                }, status=403)

            try:
                body = json.loads(request.body or b'{}')
            except (json.JSONDecodeError, ValueError):
                return JsonResponse({'error': 'Invalid JSON body'}, status=400)

            raw = body.get('enabled_agents', [])
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
                return JsonResponse(
                    {'error': '`enabled_agents` must be a list of strings'},
                    status=400,
                )

            # Validate every label exists in the discovered-agent set.
            from earthrise_agents_base.agent.discovery import discover_agents
            known = set(discover_agents().keys())
            unknown = [label for label in raw if label not in known]
            if unknown:
                return JsonResponse({
                    'error': f"Unknown agent label(s): {unknown}",
                    'valid_agents': sorted(known),
                }, status=400)

            # Deduplicate while preserving order.
            seen = set()
            normalized = []
            for label in raw:
                if label in seen:
                    continue
                seen.add(label)
                normalized.append(label)

            chat.enabled_agents = normalized
            chat.save(update_fields=['enabled_agents', 'updated_at'])

            return JsonResponse({
                'enabled_agents': list(chat.enabled_agents),
                'agents_locked': bool(chat.agents_locked),
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
