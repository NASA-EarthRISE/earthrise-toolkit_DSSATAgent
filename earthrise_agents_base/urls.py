from django.urls import path

from . import views, views_stream, views_feedback, views_management

app_name = 'earthrise_agents_base'

urlpatterns = [
    # Chat UI
    path('', views.HomeView.as_view(), name='home'),
    path('chats/<uuid:chat_id>/', views.ChatView.as_view(), name='chat'),
    path('new/', views.NewChatView.as_view(), name='new_chat'),

    # Chat API (AJAX from SPA)
    path('api/chats/', views.ChatsListAPIView.as_view(), name='api_chats'),
    path('api/chats/new/', views.NewChatAPIView.as_view(), name='api_new_chat'),
    path('api/chats/<uuid:chat_id>/', views.ChatDetailAPIView.as_view(), name='api_chat_detail'),
    path('api/chats/<uuid:chat_id>/agents/',
         views.ChatEnabledAgentsAPIView.as_view(), name='api_chat_enabled_agents'),
    path('api/chats/<uuid:chat_id>/messages/', views.MessageAPIView.as_view(), name='api_messages'),
    path('api/chats/<uuid:chat_id>/messages/<uuid:message_id>/status/',
         views.MessageStatusAPIView.as_view(), name='api_message_status'),
    path('api/chats/<uuid:chat_id>/messages/<uuid:message_id>/stream/',
         views_stream.message_stream_view, name='api_message_stream'),
    path('api/chats/<uuid:chat_id>/messages/<uuid:message_id>/feedback/',
         views_feedback.MessageFeedbackAPIView.as_view(), name='api_message_feedback'),

    # Sitewide feedback
    path('api/feedback/',
         views_feedback.SiteFeedbackAPIView.as_view(), name='api_site_feedback'),

    # Management (admin only)
    path('management/feedback/',
         views_management.FeedbackManagementView.as_view(),
         name='feedback_management'),
    path('management/feedback/messages/',
         views_management.MessageFeedbackListAPIView.as_view(),
         name='api_management_message_feedback'),
    path('management/feedback/<uuid:feedback_id>/update/',
         views_management.SiteFeedbackUpdateAPIView.as_view(),
         name='api_management_site_feedback_update'),
]
