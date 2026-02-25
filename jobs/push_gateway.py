import json
import uuid
import threading
from urllib import request as urllib_request
from urllib.error import HTTPError

from django.conf import settings

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover
    firebase_admin = None
    credentials = None
    messaging = None


_firebase_app = None
_firebase_lock = threading.Lock()

def _normalize_data_payload(data):
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def _firebase_service_account_info():
    raw_json = (getattr(settings, "FIREBASE_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if raw_json:
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            raise ValueError("firebase_service_account_json_invalid")
        return parsed

    project_id = (getattr(settings, "FIREBASE_PROJECT_ID", "") or "").strip()
    client_email = (getattr(settings, "FIREBASE_CLIENT_EMAIL", "") or "").strip()
    private_key = (getattr(settings, "FIREBASE_PRIVATE_KEY", "") or "")
    private_key = private_key.replace("\\n", "\n").strip()

    if not (project_id and client_email and private_key):
        raise ValueError("firebase_credentials_missing")

    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key": private_key,
        "client_email": client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _firebase_app_instance():
    if firebase_admin is None or credentials is None or messaging is None:
        raise RuntimeError("firebase_admin_not_installed")

    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    with _firebase_lock:
        if _firebase_app is not None:
            return _firebase_app
        info = _firebase_service_account_info()
        cred = credentials.Certificate(info)
        try:
            _firebase_app = firebase_admin.initialize_app(cred, name="jobhub-push")
        except ValueError:
            _firebase_app = firebase_admin.get_app("jobhub-push")
        return _firebase_app


def send_push_message(*, token, title, body, data=None):
    provider = (getattr(settings, "PUSH_PROVIDER", "") or "").strip().lower()
    token = (token or "").strip()
    if not token:
        return "failed", "", "device_token_missing"

    payload_data = _normalize_data_payload(data)

    if provider == "log":
        message_id = f"log-{uuid.uuid4().hex[:16]}"
        print(
            "[PUSH-LOG] "
            f"title={title!r} body={body!r} token_tail={token[-8:]} data={payload_data}"
        )
        return "sent", message_id, ""

    if provider == "fcm_v1":
        try:
            app = _firebase_app_instance()
        except Exception as exc:
            return "skipped_not_configured", "", f"fcm_v1_setup_error:{exc}"

        message = messaging.Message(
            token=token,
            notification=messaging.Notification(title=title, body=body),
            data=payload_data,
            android=messaging.AndroidConfig(priority="high"),
        )
        try:
            message_id = messaging.send(message, app=app)
            return "sent", (message_id or "").strip(), ""
        except Exception as exc:
            return "failed", "", f"fcm_v1_send_error:{exc}"

    if provider != "fcm_legacy":
        return "skipped_not_configured", "", "push_provider_not_configured"

    server_key = (getattr(settings, "FCM_SERVER_KEY", "") or "").strip()
    if not server_key:
        return "skipped_not_configured", "", "fcm_server_key_missing"

    body_payload = {
        "to": token,
        "priority": "high",
        "notification": {
            "title": title,
            "body": body,
        },
        "data": payload_data,
    }
    req = urllib_request.Request(
        "https://fcm.googleapis.com/fcm/send",
        data=json.dumps(body_payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"key={server_key}",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(req, timeout=12) as res:
            raw = res.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return "failed", "", f"http_{exc.code}:{raw[:400]}"
    except Exception as exc:
        return "failed", "", str(exc)

    try:
        parsed = json.loads(raw)
    except Exception:
        return "failed", "", f"invalid_json_response:{raw[:200]}"

    if not isinstance(parsed, dict):
        return "failed", "", "invalid_fcm_response"

    results = parsed.get("results")
    if isinstance(results, list) and results:
        first = results[0] or {}
        if isinstance(first, dict):
            error = (first.get("error") or "").strip()
            if error:
                return "failed", "", f"fcm_error:{error}"
            message_id = (first.get("message_id") or "").strip()
            if message_id:
                return "sent", message_id, ""

    failure = int(parsed.get("failure", 0) or 0)
    if failure > 0:
        return "failed", "", f"fcm_failure:{raw[:300]}"

    return "sent", "", ""
