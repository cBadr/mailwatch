"""الحصول على refresh token لحسابات OAuth2 (Google أو Microsoft).

يُشغّل مرة واحدة على جهازك ثم تضع الناتج في accounts.yaml:

  python tools/oauth_setup.py google --client-id ID --client-secret SECRET
      - أنشئ مشروعًا في https://console.cloud.google.com ← فعّل Gmail API
      - OAuth consent screen ← External ← أضف بريدك كـ Test user
        (أو انشر التطبيق "In production" حتى لا ينتهي التوكن بعد 7 أيام)
      - Credentials ← Create OAuth client ID ← Desktop app

  python tools/oauth_setup.py microsoft --client-id ID
      - https://entra.microsoft.com ← App registrations ← New registration
      - Supported account types: "Personal Microsoft accounts" (أو Any)
      - Authentication ← Allow public client flows: Yes
      - API permissions: IMAP.AccessAsUser.All, SMTP.Send, offline_access
"""
from __future__ import annotations

import argparse
import http.server
import sys
import threading
import time
import urllib.parse
import webbrowser

import requests

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_SCOPE = "https://mail.google.com/"
MS_DEVICE = "https://login.microsoftonline.com/common/oauth2/v2.0/devicecode"
MS_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
MS_SCOPE = ("https://outlook.office.com/IMAP.AccessAsUser.All "
            "https://outlook.office.com/SMTP.Send offline_access")


def google_flow(client_id: str, client_secret: str, port: int) -> dict:
    redirect = f"http://127.0.0.1:{port}/"
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result["code"] = (q.get("code") or [""])[0]
            result["error"] = (q.get("error") or [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2 dir='rtl'>تم. يمكنك إغلاق هذه النافذة والعودة إلى الطرفية.</h2>".encode())

        def log_message(self, *_):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    params = {
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code",
        "scope": GOOGLE_SCOPE, "access_type": "offline", "prompt": "consent",
    }
    url = GOOGLE_AUTH + "?" + urllib.parse.urlencode(params)
    print("افتح الرابط التالي في المتصفح ووافق على الصلاحيات:\n", url, "\n")
    webbrowser.open(url)
    while "code" not in result:
        time.sleep(0.5)
    srv.shutdown()
    if result.get("error") or not result.get("code"):
        sys.exit(f"فشل التفويض: {result.get('error')}")
    r = requests.post(GOOGLE_TOKEN, data={
        "code": result["code"], "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }, timeout=30)
    if r.status_code != 200:
        sys.exit(f"فشل الحصول على التوكن: {r.text}")
    return r.json()


def microsoft_flow(client_id: str) -> dict:
    r = requests.post(MS_DEVICE, data={"client_id": client_id, "scope": MS_SCOPE}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"فشل بدء التفويض: {r.text}")
    d = r.json()
    print(f"\nافتح {d['verification_uri']} وأدخل الرمز: {d['user_code']}\n")
    webbrowser.open(d["verification_uri"])
    interval = int(d.get("interval", 5))
    while True:
        time.sleep(interval)
        t = requests.post(MS_TOKEN, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id, "device_code": d["device_code"],
        }, timeout=30)
        j = t.json()
        if t.status_code == 200:
            return j
        if j.get("error") in ("authorization_pending", "slow_down"):
            continue
        sys.exit(f"فشل التفويض: {j}")


def main() -> None:
    p = argparse.ArgumentParser(description="الحصول على refresh token")
    sub = p.add_subparsers(dest="provider", required=True)
    g = sub.add_parser("google")
    g.add_argument("--client-id", required=True)
    g.add_argument("--client-secret", required=True)
    g.add_argument("--port", type=int, default=8765)
    m = sub.add_parser("microsoft")
    m.add_argument("--client-id", required=True)
    args = p.parse_args()

    if args.provider == "google":
        tok = google_flow(args.client_id, args.client_secret, args.port)
        print("\nأضف هذا إلى الحساب في accounts.yaml:\n")
        print("    auth: oauth2")
        print("    oauth2:")
        print("      provider: google")
        print(f"      client_id: \"{args.client_id}\"")
        print(f"      client_secret: \"{args.client_secret}\"")
        print(f"      refresh_token: \"{tok.get('refresh_token', '')}\"")
    else:
        tok = microsoft_flow(args.client_id)
        print("\nأضف هذا إلى الحساب في accounts.yaml:\n")
        print("    auth: oauth2")
        print("    oauth2:")
        print("      provider: microsoft")
        print(f"      client_id: \"{args.client_id}\"")
        print(f"      refresh_token: \"{tok.get('refresh_token', '')}\"")
    if not tok.get("refresh_token"):
        print("\nتحذير: لم يُرجع الخادم refresh_token. تأكد من prompt=consent / offline_access.", file=sys.stderr)


if __name__ == "__main__":
    main()
