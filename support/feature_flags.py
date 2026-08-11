from django.conf import settings


def is_support_feature_enabled():
    """Return the server-side availability of the new Support domain.

    This is deliberately independent of any future user subscription or
    employer assignment.  Until the pilot is explicitly enabled, no client can
    discover or enter a partially configured Support workspace.
    """

    return bool(getattr(settings, "SUPPORT_FEATURE_ENABLED", False))


def is_project_first_workspace_enabled():
    """Return whether the isolated project-first employer preview is enabled."""

    return is_support_feature_enabled() and bool(
        getattr(settings, "SUPPORT_PROJECT_FIRST_ENABLED", False)
    )
