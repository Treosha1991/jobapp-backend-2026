from django.urls import path

from . import web_views
from .project_first_web import project_first_reset_plan, project_first_workspace


app_name = "support"

urlpatterns = [
    path("project-first/", project_first_workspace, name="project-first"),
    path(
        "project-first/reset-plan/",
        project_first_reset_plan,
        name="project-first-reset-plan",
    ),
    path(
        "project-first/projects/<uuid:project_public_id>/",
        project_first_workspace,
        name="project-first-detail",
    ),
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
    path("projects/", web_views.projects_workspace, name="projects"),
    path(
        "projects/<uuid:project_public_id>/",
        web_views.projects_workspace,
        name="project-detail",
    ),
    path("transport/", web_views.transport_workspace, name="transport"),
    path("fleet/", web_views.fleet_workspace, name="fleet"),
    path("workers/<uuid:connection_public_id>/", web_views.worker_card, name="worker-card"),
]
