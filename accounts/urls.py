from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

# Django's PasswordResetView renders its email + subject templates outside
# the normal request lifecycle, so template context processors do NOT
# run for those renders. Injecting `agent_name` via extra_email_context
# keeps the password-reset email branded the same as the web pages.
_PW_RESET_EMAIL_CONTEXT = {'agent_name': settings.CUSTOM_AGENT_NAME}

app_name = 'accounts'

urlpatterns = [
    # Authentication
    path('login/', auth_views.LoginView.as_view(
        template_name='accounts/login.html',
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),

    # Registration
    path('register/', views.RegisterView.as_view(), name='register'),

    # Password reset
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='accounts/password_reset.html',
        email_template_name='accounts/password_reset_email.html',
        subject_template_name='accounts/password_reset_subject.txt',
        success_url=reverse_lazy('accounts:password_reset_done'),
        extra_email_context=_PW_RESET_EMAIL_CONTEXT,
    ), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='accounts/password_reset_done.html',
    ), name='password_reset_done'),
    path('password-reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='accounts/password_reset_confirm.html',
        success_url=reverse_lazy('accounts:password_reset_complete'),
    ), name='password_reset_confirm'),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='accounts/password_reset_complete.html',
    ), name='password_reset_complete'),

    # Profile
    path('profile/', views.ProfileView.as_view(), name='profile'),

    # Change password (for authenticated users). Wraps Django's built-in
    # PasswordChangeView with a confirmation-email side-effect — see
    # ``accounts.views.PasswordChangeView``.
    path('password-change/',
         views.PasswordChangeView.as_view(),
         name='password_change'),
    path('password-change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='accounts/password_change_done.html',
    ), name='password_change_done'),

    # User management (admin only)
    path('users/', views.UserManagementView.as_view(), name='user_management'),
    path('api/approve/<uuid:request_id>/',
         views.ApproveRegistrationAPI.as_view(), name='approve_registration'),
    path('api/reject/<uuid:request_id>/',
         views.RejectRegistrationAPI.as_view(), name='reject_registration'),
    path('api/users/<int:user_id>/roles/',
         views.UpdateUserRolesAPI.as_view(), name='update_user_roles'),
    path('api/users/<int:user_id>/toggle-active/',
         views.ToggleUserActiveAPI.as_view(), name='toggle_user_active'),
]
