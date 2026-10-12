"""أوامر تليجرام: تُقرأ عبر getUpdates وتُنفذ في بداية كل دورة فحص."""
from __future__ import annotations

import logging

from .actions import run_action
from .storage import Store
from .telegram import Telegram, esc

log = logging.getLogger("mailwatch.commands")

HELP = """<b>الأوامر المتاحة</b>
/status — حالة الحسابات وعدد الرسائل
/read <code>المعرف</code> — تعليم كمقروءة
/unread <code>المعرف</code> — تعليم كغير مقروءة
/flag <code>المعرف</code> — تمييز الرسالة
/archive <code>المعرف</code> — أرشفة
/trash <code>المعرف</code> — نقل إلى المهملات
/reply <code>المعرف</code> نص الرد — إرسال رد

المعرف يصلك مع كل تنبيه بالشكل <code>حساب:معرف</code>.
تُنفذ الأوامر في دورة الفحص التالية (عادةً خلال 5 دقائق)."""

_CMD_MAP = {
    "/read": "mark_read", "/unread": "mark_unread", "/flag": "flag",
    "/archive": "archive", "/trash": "trash", "/reply": "reply",
}


def _status(store: Store) -> str:
    data = store.load_accounts()
    accounts = data.get("accounts") or []
    if not accounts:
        return "لا توجد بيانات بعد. انتظر أول دورة فحص."
    lines = [f"📊 <b>حالة الحسابات</b> (آخر تحديث {esc((data.get('updated') or '')[:16].replace('T', ' '))})"]
    for a in accounts:
        c = a.get("counts") or {}
        icon = "⏸" if not a.get("enabled", True) else ("⚠️" if a.get("last_error") else "✅")
        line = f"{icon} <b>{esc(a.get('name'))}</b> — {c.get('total', 0)} رسالة، {c.get('unread', 0)} غير مقروءة"
        bf = a.get("backfill") or {}
        if bf.get("remaining"):
            line += f"، متبقي من الأرشيف {bf['remaining']}"
        if a.get("last_error"):
            line += f"\n   خطأ: <code>{esc(str(a['last_error'])[:160])}</code>"
        lines.append(line)
    return "\n".join(lines)


def _handle(cfg, store: Store, text: str) -> str:
    parts = text.split(maxsplit=2)
    cmd = parts[0].lower().split("@")[0]
    if cmd in ("/start", "/help"):
        return HELP
    if cmd == "/status":
        return _status(store)
    if cmd in _CMD_MAP:
        if len(parts) < 2:
            return f"اكتب المعرف بعد الأمر، مثال: <code>{esc(cmd)} myaccount:ab12cd34ef56</code>"
        aid, _, mid = parts[1].partition(":")
        if not mid:
            return "المعرف يجب أن يكون بالشكل <code>حساب:معرف</code> كما يصلك في التنبيه"
        payload = {"action": _CMD_MAP[cmd], "account": aid, "id": mid}
        if cmd == "/reply":
            body = parts[2].strip() if len(parts) > 2 else ""
            if not body:
                return "اكتب نص الرد بعد المعرف: <code>/reply حساب:معرف نص الرد</code>"
            payload["body"] = body
        res = run_action(cfg, store, payload, source="telegram")
        if res.get("ok"):
            return "✅ " + esc(res.get("detail") or "تم التنفيذ")
        return "❌ " + esc(res.get("error") or "فشل التنفيذ")
    return "أمر غير معروف. أرسل /help لعرض الأوامر."


def process_telegram_commands(cfg, store: Store, tg: Telegram) -> int:
    if not tg.enabled:
        return 0
    st = store.load_global("telegram")
    updates = tg.get_updates(st.get("offset"))
    handled = 0
    for u in updates:
        st["offset"] = int(u.get("update_id", 0)) + 1
        m = u.get("message") or u.get("channel_post") or {}
        chat = str((m.get("chat") or {}).get("id", ""))
        text = (m.get("text") or "").strip()
        if not chat or not text.startswith("/"):
            continue
        if chat not in tg.allowed:
            log.warning("تجاهل أمر من محادثة غير مصرح بها: %s", chat)
            continue
        try:
            reply = _handle(cfg, store, text)
        except Exception as e:  # noqa: BLE001
            log.exception("فشل تنفيذ أمر تليجرام")
            reply = f"❌ خطأ غير متوقع: {esc(e)}"
        tg.send(reply, chat)
        handled += 1
    store.save_global("telegram", st)
    return handled
