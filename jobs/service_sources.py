SERVICE_BOARD_USERNAME = "jobhub_board"
SERVICE_BOARD_KIND = "jobhub_board"


def is_service_board_user(user):
    username = (getattr(user, "username", "") or "").strip().lower()
    return username == SERVICE_BOARD_USERNAME


def service_board_kind_for_user(user):
    if is_service_board_user(user):
        return SERVICE_BOARD_KIND
    return ""


def service_board_meta_for_user(user):
    kind = service_board_kind_for_user(user)
    return {
        "is_service_board": bool(kind),
        "service_board_kind": kind,
    }
