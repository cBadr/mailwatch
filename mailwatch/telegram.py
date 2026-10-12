"""إرسال التنبيهات واستقبال الأوامر عبر Telegram Bot API."""
from __future__ import annotations

import html
import logging
import os

import requests

log = logging.getLogger("mailwatch.telegram")
MAX_LEN = 4000


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts


class Telegram:
    def __init__(self, token: str | None = None, chat_ids: str | None = None):
        self.token = (token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip()
        raw = chat_ids if chat_ids is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.chat_ids = [c.strip() for c in str(raw).split(",") if c.strip()]
        self.default_chat = self.chat_ids[0] if self.chat_ids else ""
        self.allowed: set[str] = set(self.chat_ids)
        self.base = f"https://api.telegram.org/bot{self.token}"

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def allow(self, chat_id: str) -> None:
        if chat_id:
            self.allowed.add(str(chat_id))

    def send(self, text: str, chat_id: str | None = None) -> bool:
        chat = str(chat_id or self.default_chat or "")
        if not self.enabled or not chat:
            return False
        ok = True
        for chunk in _split(text):
            payload = {"chat_id": chat, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
            try:
                r = requests.post(f"{self.base}/sendMessage", json=payload, timeout=30)
                if r.status_code == 400:  # غالبًا HTML غير صالح؛ أعد الإرسال كنص عادي
                    payload.pop("parse_mode")
                    r = requests.post(f"{self.base}/sendMessage", json=payload, timeout=30)
                if r.status_code != 200:
                    log.warning("Telegram: فشل الإرسال (%s): %s", r.status_code, r.text[:200])
                    ok = False
            except requests.RequestException as e:
                log.warning("Telegram: خطأ شبكة: %s", e)
                ok = False
        return ok

    def get_updates(self, offset: int | None = None) -> list[dict]:
        if not self.enabled:
            return []
        params = {"timeout": 0, "allowed_updates": '["message","channel_post"]'}
        if offset:
            params["offset"] = offset
        try:
            r = requests.get(f"{self.base}/getUpdates", params=params, timeout=30)
            if r.status_code != 200:
                log.warning("Telegram: getUpdates فشل (%s): %s", r.status_code, r.text[:200])
                return []
            return r.json().get("result", [])
        except (requests.RequestException, ValueError) as e:
            log.warning("Telegram: getUpdates خطأ: %s", e)
            return []


def _fmt_addr(a: dict) -> str:
    name, mail = (a or {}).get("name", ""), (a or {}).get("email", "")
    return f"{name} <{mail}>" if name and mail else (name or mail)


def _fmt_date(iso: str | None) -> str:
    return iso.replace("T", " ")[:16] if iso else "—"


def format_new_mail(acc, msg: dict, dashboard_url: str = "") -> str:
    lines = [f"📩 <b>رسالة جديدة</b> · {esc(acc.name)}"]
    lines.append(f"👤 <b>من:</b> {esc(_fmt_addr(msg.get('from')))}")
    lines.append(f"📌 <b>العنوان:</b> {esc(msg.get('subject') or '(بدون عنوان)')}")
    lines.append(f"🕒 {esc(_fmt_date(msg.get('date') or msg.get('received')))}")
    if msg.get("matched"):
        lines.append(f"🏷 <b>الفلتر:</b> {esc('، '.join(msg['matched']))}")
    atts = msg.get("attachments") or []
    if atts:
        names = "، ".join(a.get("filename", "") for a in atts[:3])
        more = f" (+{len(atts) - 3})" if len(atts) > 3 else ""
        lines.append(f"📎 <b>المرفقات ({len(atts)}):</b> {esc(names)}{esc(more)}")
    snippet = (msg.get("snippet") or "").strip()
    if snippet:
        lines.append("")
        lines.append(esc(snippet[:500]))
    lines.append("")
    ref = f"{acc.id}:{msg['id']}"
    if dashboard_url:
        link = f"{dashboard_url.rstrip('/')}/#/msg/{acc.id}/{msg['id']}"
        lines.append(f'🔗 <a href="{esc(link)}">فتح في لوحة التحكم</a>')
    lines.append(f"🆔 <code>{esc(ref)}</code>")
    return "\n".join(lines)


def format_error(acc, error: str) -> str:
    return (f"⚠️ <b>مشكلة في الحساب</b> {esc(acc.name)} ({esc(acc.email)})\n"
            f"<code>{esc(error[:800])}</code>\n"
            f"سيعاد المحاولة في الفحص القادم.")
