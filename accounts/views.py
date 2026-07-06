import json
import logging

from django.contrib.auth import get_user_model, login
from django.contrib.auth.models import Group
from django.contrib.auth.views import PasswordChangeView as BasePasswordChangeView
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie

from .emails import (
    notify_admins_of_registration,
    notify_password_changed,
    notify_user_approved,
    notify_user_rejected,
)
from .forms import RegistrationForm, ProfileForm
from earthrise_agents_base.mixins import AdminRequiredMixin, is_admin
from .models import UserProfile, RegistrationRequest
from .role_discovery import get_available_roles, get_grouped_roles

logger = logging.getLogger(__name__)
User = get_user_model()


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
class RegisterView(View):
    """Account registration — creates inactive user + pending request."""

    def get(self, request):
        form = RegistrationForm()
        return render(request, 'accounts/register.html', {'form': form})

    def post(self, request):
        form = RegistrationForm(request.POST)
        if not form.is_valid():
            return render(request, 'accounts/register.html', {'form': form})

        # Create user as inactive
        user = form.save(commit=False)
        user.is_active = False
        user.save()

        # Create profile
        UserProfile.objects.create(
            user=user,
            organization=form.cleaned_data.get('organization', ''),
        )

        # Create registration request
        reg_request = RegistrationRequest.objects.create(
            user=user,
            organization=form.cleaned_data.get('organization', ''),
            reason=form.cleaned_data.get('reason', ''),
            requested_roles=form.cleaned_data.get('requested_roles', []),
        )

        # Notify superusers so they know a request is pending review.
        notify_admins_of_registration(reg_request, request)

        return render(request, 'accounts/register_pending.html')


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------
class ProfileView(View):
    """User profile management — edit own profile and change password."""

    def get(self, request):
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)
        form = ProfileForm(initial={
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'organization': profile.organization,
            'title': profile.title,
            'phone': profile.phone,
            'country': profile.country,
            'bio': profile.bio,
            'areas_of_interest': profile.areas_of_interest,
            'preferred_language': profile.preferred_language,
        })
        groups = list(user.groups.values_list('name', flat=True))
        # Surface Django superuser/staff flags alongside Group memberships so
        # admins don't see a confusing "No roles assigned" message.
        if user.is_superuser:
            groups.insert(0, 'Superuser')
        elif user.is_staff:
            groups.insert(0, 'Staff')
        return render(request, 'accounts/profile.html', {
            'form': form,
            'user_groups': groups,
        })

    def post(self, request):
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)
        form = ProfileForm(request.POST)

        if form.is_valid():
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data['last_name']
            user.email = form.cleaned_data['email']
            user.save(update_fields=['first_name', 'last_name', 'email'])

            profile.organization = form.cleaned_data.get('organization', '')
            profile.title = form.cleaned_data.get('title', '')
            profile.phone = form.cleaned_data.get('phone', '')
            profile.country = form.cleaned_data.get('country', '')
            profile.bio = form.cleaned_data.get('bio', '')
            profile.areas_of_interest = form.cleaned_data.get('areas_of_interest', '')
            profile.preferred_language = form.cleaned_data.get('preferred_language', 'en')
            profile.save()

            return redirect('accounts:profile')

        groups = list(user.groups.values_list('name', flat=True))
        return render(request, 'accounts/profile.html', {
            'form': form,
            'user_groups': groups,
        })


# --------------------------------------------------------------------------
# User Management (admin only)
# --------------------------------------------------------------------------
class UserManagementView(AdminRequiredMixin, View):
    """Admin page for managing users and registration requests."""

    def get(self, request):
        pending = RegistrationRequest.objects.filter(status='pending')
        # Superusers first, then by join date (newest first). Two-tier sort
        # keeps admins surfaced at the top regardless of when they joined.
        users = (
            User.objects.select_related('profile')
            .order_by('-is_superuser', '-date_joined')
        )
        grouped_roles = get_grouped_roles()

        return render(request, 'accounts/user_management.html', {
            'pending_requests': pending,
            'users': users,
            # JSON-encode for safe JS consumption (dict repr uses single quotes)
            'grouped_roles_json': json.dumps(grouped_roles),
        })


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ApproveRegistrationAPI(AdminRequiredMixin, View):
    """Approve a pending registration request."""

    def post(self, request, request_id):
        reg = get_object_or_404(RegistrationRequest, pk=request_id, status='pending')
        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            data = {}

        approved_roles = data.get('roles', reg.requested_roles)

        # Activate user
        user = reg.user
        user.is_active = True
        user.save(update_fields=['is_active'])

        # Assign groups
        for role_name in approved_roles:
            try:
                group = Group.objects.get(name=role_name)
                user.groups.add(group)
            except Group.DoesNotExist:
                logger.warning("Group %s not found during approval", role_name)

        # Update profile
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.approved_at = timezone.now()
        profile.approved_by = request.user
        profile.save(update_fields=['approved_at', 'approved_by'])

        # Update request
        reg.status = 'approved'
        reg.reviewed_by = request.user
        reg.reviewed_at = timezone.now()
        reg.review_notes = data.get('notes', '')
        reg.save()

        # Let the user know they're in.
        login_url = request.build_absolute_uri(reverse('accounts:login'))
        notify_user_approved(user, approved_roles, login_url)

        return JsonResponse({'success': True, 'user_id': user.pk})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class RejectRegistrationAPI(AdminRequiredMixin, View):
    """Reject a pending registration request."""

    def post(self, request, request_id):
        reg = get_object_or_404(RegistrationRequest, pk=request_id, status='pending')
        try:
            data = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            data = {}

        notes = data.get('notes', '')

        # Keep the applicant's account in place but inactive (it was never
        # activated) so the rejected request retains its audit trail
        # (reviewed_by / reviewed_at / review_notes). An admin can delete the
        # account separately if the email/username should be freed for reuse.
        reg.status = 'rejected'
        reg.reviewed_by = request.user
        reg.reviewed_at = timezone.now()
        reg.review_notes = notes
        reg.save()

        notify_user_rejected(
            reg.user.email,
            reg.user.get_full_name() or reg.user.username,
            notes,
        )

        return JsonResponse({'success': True})


class PasswordChangeView(BasePasswordChangeView):
    """Django's built-in password-change flow plus a confirmation email.

    Wired in ``accounts/urls.py`` so the ``accounts:password_change``
    URL name still resolves — just with an added side-effect after the
    password is successfully updated.
    """
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('accounts:password_change_done')

    def form_valid(self, form):
        response = super().form_valid(form)
        notify_password_changed(self.request.user, self.request)
        return response


@method_decorator(ensure_csrf_cookie, name='dispatch')
class UpdateUserRolesAPI(AdminRequiredMixin, View):
    """Update a user's group assignments."""

    def post(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        roles = data.get('roles', [])

        # Set groups (replace all)
        groups = Group.objects.filter(name__in=roles)
        target_user.groups.set(groups)

        return JsonResponse({'success': True})


@method_decorator(ensure_csrf_cookie, name='dispatch')
class ToggleUserActiveAPI(AdminRequiredMixin, View):
    """Toggle a user's active status."""

    def post(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        # Don't allow deactivating yourself
        if target_user == request.user:
            return JsonResponse({'error': 'Cannot deactivate yourself'}, status=400)

        target_user.is_active = not target_user.is_active
        target_user.save(update_fields=['is_active'])

        return JsonResponse({
            'success': True,
            'is_active': target_user.is_active,
        })
