import json
from urllib.parse import quote

from django.conf import settings


GOOGLE_PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"


class GooglePlayNotConfiguredError(Exception):
    def __init__(self, code="google_play_not_configured"):
        super().__init__(code)
        self.code = code


class GooglePlayVerificationError(Exception):
    def __init__(self, code, *, detail="", payload=None):
        super().__init__(code)
        self.code = code
        self.detail = (detail or "").strip()
        self.payload = payload or {}


def is_google_play_configured():
    return bool(
        (getattr(settings, "GOOGLE_PLAY_PACKAGE_NAME", "") or "").strip()
        and (getattr(settings, "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "") or "").strip()
    )


def _google_play_service_account_info():
    raw = (getattr(settings, "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "") or "").strip()
    if not raw:
        raise GooglePlayNotConfiguredError()
    try:
        return json.loads(raw)
    except Exception as exc:
        raise GooglePlayVerificationError(
            "google_play_service_account_invalid",
            detail=str(exc),
        ) from exc


def _google_play_package_name():
    package_name = (getattr(settings, "GOOGLE_PLAY_PACKAGE_NAME", "") or "").strip()
    if not package_name:
        raise GooglePlayNotConfiguredError()
    return package_name


def _google_play_access_token():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import service_account
    except ImportError as exc:
        raise GooglePlayVerificationError(
            "google_play_dependencies_missing",
            detail="Install google-auth and requests on the backend.",
        ) from exc

    info = _google_play_service_account_info()
    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=[GOOGLE_PLAY_SCOPE],
    )
    credentials.refresh(Request())
    token = (getattr(credentials, "token", "") or "").strip()
    if not token:
        raise GooglePlayVerificationError("google_play_access_token_failed")
    return token


def _google_play_request(path):
    try:
        import requests
    except ImportError as exc:
        raise GooglePlayVerificationError(
            "google_play_dependencies_missing",
            detail="Install requests on the backend.",
        ) from exc

    token = _google_play_access_token()
    url = f"https://androidpublisher.googleapis.com/androidpublisher/v3/{path.lstrip('/')}"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=15,
    )

    if 200 <= response.status_code < 300:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise GooglePlayVerificationError(
                "google_play_invalid_response",
                detail=str(exc),
            ) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    error_message = (
        ((payload.get("error") or {}).get("message"))
        or payload.get("message")
        or response.text
    )
    raise GooglePlayVerificationError(
        "google_play_api_error",
        detail=str(error_message),
        payload=payload,
    )


def _product_purchase_state(payload):
    context = payload.get("purchaseStateContext") or {}
    return (
        (context.get("purchaseState") or payload.get("purchaseState") or "")
        .strip()
        .upper()
    )


def _subscription_purchase_state(payload):
    return (payload.get("subscriptionState") or "").strip().upper()


def verify_google_play_product_purchase(*, product_id, purchase_token):
    package_name = _google_play_package_name()
    payload = _google_play_request(
        "applications/"
        f"{quote(package_name, safe='')}/purchases/productsv2/tokens/"
        f"{quote((purchase_token or '').strip(), safe='')}"
    )
    line_items = payload.get("productLineItem") or payload.get("productLineItems") or []
    if not any((item.get("productId") or "").strip() == product_id for item in line_items):
        raise GooglePlayVerificationError("google_play_product_mismatch")

    purchase_state = _product_purchase_state(payload)
    if purchase_state != "PURCHASED":
        raise GooglePlayVerificationError(
            "google_play_purchase_not_purchased",
            detail=purchase_state or "unknown",
            payload=payload,
        )
    return payload


def verify_google_play_subscription_purchase(*, subscription_id, purchase_token):
    package_name = _google_play_package_name()
    payload = _google_play_request(
        "applications/"
        f"{quote(package_name, safe='')}/purchases/subscriptionsv2/tokens/"
        f"{quote((purchase_token or '').strip(), safe='')}"
    )
    line_items = payload.get("lineItems") or []
    if not any((item.get("productId") or "").strip() == subscription_id for item in line_items):
        raise GooglePlayVerificationError("google_play_product_mismatch")

    subscription_state = _subscription_purchase_state(payload)
    if subscription_state not in {
        "SUBSCRIPTION_STATE_ACTIVE",
        "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
    }:
        raise GooglePlayVerificationError(
            "google_play_subscription_inactive",
            detail=subscription_state or "unknown",
            payload=payload,
        )
    return payload
