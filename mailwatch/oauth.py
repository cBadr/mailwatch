"""الحصول على access token من refresh token (Google / Microsoft / مخصص)."""
from __future__ import annotations

import threading
import time

import requests

TOKEN_URLS = {
    "google": "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
}
DEFAULT_SCOPES = {
    "google": "https://mail.google.com/",
    "microsoft": "https://outlook.office.com/IMAP.AccessAsUser.All "
                 "https://outlook.office.com/SMTP.Send offline_access",
}

_cache: dict[str, tuple[str, float]] = {}
_lock = threading.Lock()


class OAuthError(RuntimeError):
    pass


def get_access_token(acc) -> str:
    o = acc.oauth2
    with _lock:
        cached = _cache.get(acc.id)
        if cached and cached[1] > time.time() + 60:
            return cached[0]
    provider = o.get("provider", "")
    url = o.get("token_url") or TOKEN_URLS.get(provider)
    if not url:
        raise OAuthError(f"[{acc.id}] لا يوجد token_url لمزود OAuth '{provider}'")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": o["refresh_token"],
        "client_id": o["client_id"],
    }
    if o.get("client_secret"):
        data["client_secret"] = o["client_secret"]
    scope = o.get("scope") or DEFAULT_SCOPES.get(provider)
    if provider == "microsoft" and scope:
        data["scope"] = scope
    try:
        r = requests.post(url, data=data, timeout=30)
    except requests.RequestException as e:
        raise OAuthError(f"[{acc.id}] تعذر الاتصال بخادم OAuth: {e}") from e
    if r.status_code != 200:
        raise OAuthError(f"[{acc.id}] رفض تجديد التوكن ({r.status_code}): {r.text[:300]}")
    j = r.json()
    token = j.get("access_token")
    if not token:
        raise OAuthError(f"[{acc.id}] الرد لا يحتوي على access_token")
    with _lock:
        _cache[acc.id] = (token, time.time() + int(j.get("expires_in", 3600)))
    return token
