from django.contrib import admin
from .models import Chat, Message, MessageFeedback, SiteFeedback


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'is_processing', 'created_at', 'updated_at']
    list_filter = ['is_processing']
    search_fields = ['title']


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['id', 'chat', 'message_type', 'status', 'created_at']
    list_filter = ['message_type', 'status']


@admin.register(MessageFeedback)
class MessageFeedbackAdmin(admin.ModelAdmin):
    list_display = ['id', 'chat', 'message', 'user', 'rating', 'created_at', 'updated_at']
    list_filter = ['rating']
    search_fields = ['comment', 'user__username', 'user__email', 'chat__title']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['chat', 'message', 'user']


@admin.register(SiteFeedback)
class SiteFeedbackAdmin(admin.ModelAdmin):
    list_display = ['id', 'category', 'status', 'user', 'created_at', 'reviewed_by']
    list_filter = ['category', 'status']
    search_fields = ['content', 'admin_notes', 'user__username']
    readonly_fields = ['created_at', 'updated_at']
