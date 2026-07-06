"""
Transactional email helpers.

Each helper renders a subject + body template pair under
``accounts/templates/accounts/emails/`` and dispatches via the
configured EMAIL_BACKEND. Failures are logged but never re-raised — an
email outage must not block the user flow that triggered the send
(account approval, password change, etc.).

``agent_name`` is injected into every template context so all email copy
stays consistent with the rebranded product name (``CUSTOM_AGENT_NAME``).
"""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)
User = get_user_model()


def _send(template_base, to_emails, context, from_email=None):
    """Render + send. Returns True on success, False on any failure."""
    to_emails = [e for e in (to_emails or []) if e]
    if not to_emails:
        return False
    ctx = {
        'agent_name': getattr(settings, 'CUSTOM_AGENT_NAME', 'ChatAgent'),
        **context,
    }
    try:
        subject = render_to_string(
            f'accounts/emails/{template_base}_subject.txt', ctx
        ).strip()
        body = render_to_string(
            f'accounts/emails/{template_base}_body.txt', ctx
        )
    except Exception as e:
        logger.warning("Failed to render %s email templates: %s", template_base, e)
        return False
    try:
        send_mail(
            subject,
            body,
            from_email or settings.DEFAULT_FROM_EMAIL,
            to_emails,
            fail_silently=False,
        )
        return True
    except Exception as e:
        logger.warning("Failed to send %s email to %s: %s",
                       template_base, to_emails, e)
        return False


def notify_admins_of_registration(reg_request, request):
    """Email every active superuser whenever a new registration lands."""
    admin_emails = list(
        User.objects.filter(is_superuser=True, is_active=True)
        .exclude(email='')
        .values_list('email', flat=True)
    )
    if not admin_emails:
        logger.info("No active superuser emails on file — skipping admin notification")
        return
    from django.urls import reverse
    try:
        review_url = request.build_absolute_uri(
            reverse('accounts:user_management')
        )
    except Exception:
        review_url = ''
    _send('registration_received', admin_emails, {
        'reg': reg_request,
        'user': reg_request.user,
        'review_url': review_url,
    })


def notify_user_approved(user, approved_roles, login_url):
    """Email a user confirming their registration was approved + activated."""
    _send('registration_approved', [user.email], {
        'user': user,
        'approved_roles': list(approved_roles or []),
        'login_url': login_url,
    })


def notify_user_rejected(email, full_name, notes=''):
    """Email a rejected applicant.

    We take ``email`` and ``full_name`` directly rather than a user
    instance because the view deletes the inactive user row before this
    runs — capturing the identity fields upfront is the simplest
    ordering.
    """
    _send('registration_rejected', [email], {
        'full_name': full_name or '',
        'notes': notes or '',
    })


def notify_password_changed(user, request=None):
    """Email a user confirming their password was just changed.

    ``request`` is optional — if provided, we include a rough signal of
    where the change happened (user-agent + IP), which helps a real
    account owner tell a stolen-session event from something they did.
    """
    context = {'user': user}
    if request is not None:
        context['ip'] = _client_ip(request)
        context['user_agent'] = request.META.get('HTTP_USER_AGENT', '')
    _send('password_changed', [user.email], context)


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')
