from django.urls import path

from . import web_views


app_name = "support"

urlpatterns = [
    path("", web_views.workspace_home, name="workspace"),
    path("team/", web_views.team_management, name="team"),
    path("time/", web_views.timekeeping_workspace, name="time"),
    path("conversations/", web_views.conversations_workspace, name="conversations"),
    path(
        "conversations/<uuid:conversation_public_id>/",
        web_views.conversation_detail,
        name="conversation-detail",
    ),
    path("requests/", web_views.worker_requests_workspace, name="worker-requests"),
    path("registries/", web_views.registries, name="registries"),
    path("transport/", web_views.transport_workspace, name="transport"),
    path("workers/<uuid:connection_public_id>/", web_views.worker_card, name="worker-card"),
]
