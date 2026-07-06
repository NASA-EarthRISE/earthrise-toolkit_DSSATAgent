from django.contrib import admin
from .models import UserProfile, RegistrationRequest


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'country', 'approved_at']
    search_fields = ['user__username', 'user__email', 'organization']
    raw_id_fields = ['user', 'approved_by']


@admin.register(RegistrationRequest)
class RegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ['user', 'organization', 'status', 'created_at', 'reviewed_at']
    list_filter = ['status']
    search_fields = ['user__email', 'user__username', 'organization']
    raw_id_fields = ['user', 'reviewed_by']
