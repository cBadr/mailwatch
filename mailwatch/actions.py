"""تنفيذ الأوامر على الرسائل: رد، إرسال، تعليم كمقروء، تمييز، أرشفة، مهملات."""
from __future__ import annotations

import base64
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from html import escape

from .imap_client import Mailbox, MailboxError
from .oauth import get_access_token
from .parser import parse_message
from .storage import Store, make_id, now_iso, read_json

log = logging.getLogger("mailwatch.actions")

ACTIONS = ("reply", "send", "mark_read", "mark_unread", "flag", "unflag", "archive", "trash")


class ActionError(RuntimeError):
    pass


def _addr_list(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        v = v.replace(";", ",").split(",")
    out = []
    for x in v:
        s = str(x).strip()
        if s and "@" in s:
            out.append(s)
    return out


def _fmt_addr(a: dict | None) -> str:
    a = a or {}
    name, mail = a.get("name", ""), a.get("email", "")
    return f"{name} <{mail}>" if name and mail else (name or mail)


# ---------- SMTP ----------
def smtp_connect(acc) -> smtplib.SMTP:
    if not acc.smtp_host:
        raise ActionError(f"[{acc.id}] لا يوجد smtp_host لهذا الحساب، لا يمكن الإرسال")
    ctx = ssl.create_default_context()
    try:
        if acc.smtp_security == "ssl":
            s = smtplib.SMTP_SSL(acc.smtp_host, acc.smtp_port, timeout=60, context=ctx)
        else:
            s = smtplib.SMTP(acc.smtp_host, acc.smtp_port, timeout=60)
            s.ehlo()
            if acc.smtp_security == "starttls":
                s.starttls(context=ctx)
                s.ehlo()
        if acc.auth == "oauth2":
            token = get_access_token(acc)
            auth = base64.b64encode(f"user={acc.username}\x01auth=Bearer {token}\x01\x01".encode()).decode()
            code, resp = s.docmd("AUTH", "XOAUTH2 " + auth)
            if code != 235:
                raise ActionError(f"[{acc.id}] فشل SMTP XOAUTH2: {code} {resp!r}")
        else:
            s.login(acc.username, acc.password)
        return s
    except (smtplib.SMTPException, OSError) as e:
        raise ActionError(f"[{acc.id}] فشل الاتصال بخادم SMTP: {e}") from e


def _build_email(acc, to: list[str], cc: list[str], subject: str, body: str,
                 original: dict | None = None, quote: bool = True) -> EmailMessage:
    em = EmailMessage()
    em["From"] = formataddr((acc.name, acc.email))
    em["To"] = ", ".join(to)
    if cc:
        em["Cc"] = ", ".join(cc)
    em["Subject"] = subject
    em["Date"] = formatdate(localtime=True)
    em["Message-ID"] = make_msgid(domain=acc.email.split("@")[-1])
    if original and original.get("message_id"):
        em["In-Reply-To"] = original["message_id"]
        refs = list(original.get("references") or []) + [original["message_id"]]
        em["References"] = " ".join(refs[-20:])
    text = body.rstrip()
    if acc.signature:
        text += "\n\n" + acc.signature
    if original and quote and original.get("text"):
        when = (original.get("date") or "")[:16].replace("T", " ")
        text += f"\n\n---\nفي {when} كتب {_fmt_addr(original.get('from'))}:\n"
        text += "\n".join("> " + line for line in original["text"][:3000].splitlines())
    em.set_content(text)
    em.add_alternative(
        '<div dir="auto" style="white-space:pre-wrap;font-family:sans-serif;font-size:15px">'
        + escape(text) + "</div>",
        subtype="html",
    )
    return em


def _deliver(acc, em: EmailMessage, recipients: list[str]) -> None:
    s = smtp_connect(acc)
    try:
        s.send_message(em, from_addr=acc.email, to_addrs=recipients)
    except smtplib.SMTPException as e:
        raise ActionError(f"[{acc.id}] رفض الخادم إرسال الرسالة: {e}") from e
    finally:
        try:
            s.quit()
        except Exception:
            pass


def _save_to_sent(mb: Mailbox, acc, em: EmailMessage) -> None:
    if acc.provider == "gmail":  # Gmail يحفظ المرسل تلقائيًا
        return
    folder = acc.sent_folder or mb.special_folder("\\Sent")
    if folder:
        try:
            mb.append(folder, em.as_bytes())
        except MailboxError as e:
            log.warning("[%s] %s", acc.id, e)


def _archive_sent(store: Store, acc, em: EmailMessage, reply_to_id: str | None = None) -> str:
    raw = em.as_bytes()
    data, _ = parse_message(raw)
    mid = make_id(acc.id, "Sent", "sent", em["Message-ID"])
    data.update({
        "id": mid, "account": acc.id, "folder": "Sent", "uid": None, "uidvalidity": None,
        "received": now_iso(), "flags": {"seen": True, "flagged": False, "answered": False},
        "matched": [], "sent": True, "in_reply_to_id": reply_to_id, "stored_at": now_iso(),
    })
    store.save_message(acc.id, data, raw if acc.store_raw else None, [])
    store.upsert_index(acc.id, Store.index_entry(data))
    st = store.load_state(acc.id)
    st["counts"]["total"] = st["counts"].get("total", 0) + 1
    store.save_state(acc.id, st)
    return mid


def _adjust_unread(store: Store, aid: str, delta: int) -> None:
    st = store.load_state(aid)
    st["counts"]["unread"] = max(0, st["counts"].get("unread", 0) + delta)
    store.save_state(aid, st)


def _locate(mb: Mailbox, msg: dict) -> int:
    folder = msg.get("folder") or "INBOX"
    uv, _ = mb.select(folder, readonly=False)
    uid = msg.get("uid")
    if uid and uv == msg.get("uidvalidity") and mb.search_uids(f"UID {uid}") == [uid]:
        return uid
    found = mb.find_by_message_id(msg.get("message_id", ""))
    if found:
        return found
    raise ActionError("الرسالة لم تعد موجودة في مجلدها الأصلي على الخادم (ربما نُقلت أو حُذفت)")


# ---------- التنفيذ ----------
def _reply(acc, msg: dict, payload: dict, store: Store) -> dict:
    default_to = [a["email"] for a in (msg.get("reply_to") or [msg.get("from") or {}]) if a.get("email")]
    to = _addr_list(payload.get("to")) or default_to
    if not to:
        raise ActionError("لا يوجد عنوان مرسل إليه")
    cc = _addr_list(payload.get("cc"))
    body = str(payload.get("body") or "").strip()
    if not body:
        raise ActionError("نص الرد فارغ")
    subject = str(payload.get("subject") or msg.get("subject") or "").strip()
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    em = _build_email(acc, to, cc, subject, body, original=msg, quote=bool(payload.get("quote", True)))
    _deliver(acc, em, to + cc)
    try:
        mb = Mailbox(acc)
        try:
            uid = _locate(mb, msg)
            mb.store_flags([uid], "(\\Answered)", add=True)
            _save_to_sent(mb, acc, em)
        finally:
            mb.logout()
    except (MailboxError, ActionError) as e:
        log.warning("[%s] بعد الإرسال: %s", acc.id, e)
    updated = store.update_message(acc.id, msg["id"], {"flags": {"answered": True}, "replied_at": now_iso()})
    if updated:
        store.upsert_index(acc.id, Store.index_entry(updated))
    sent_id = _archive_sent(store, acc, em, reply_to_id=msg["id"])
    return {"detail": f"تم إرسال الرد إلى {', '.join(to)}", "sent_id": sent_id}


def _send_new(acc, payload: dict, store: Store) -> dict:
    to = _addr_list(payload.get("to"))
    if not to:
        raise ActionError("حدد عنوان المرسل إليه")
    cc = _addr_list(payload.get("cc"))
    subject = str(payload.get("subject") or "").strip() or "(بدون عنوان)"
    body = str(payload.get("body") or "").strip()
    if not body:
        raise ActionError("نص الرسالة فارغ")
    em = _build_email(acc, to, cc, subject, body)
    _deliver(acc, em, to + cc)
    try:
        mb = Mailbox(acc)
        try:
            _save_to_sent(mb, acc, em)
        finally:
            mb.logout()
    except MailboxError as e:
        log.warning("[%s] بعد الإرسال: %s", acc.id, e)
    sent_id = _archive_sent(store, acc, em)
    return {"detail": f"تم إرسال الرسالة إلى {', '.join(to)}", "sent_id": sent_id}


def _modify(acc, action: str, msg: dict, store: Store) -> dict:
    mb = Mailbox(acc)
    try:
        uid = _locate(mb, msg)
        changes: dict
        detail: str
        if action in ("mark_read", "mark_unread"):
            seen = action == "mark_read"
            mb.store_flags([uid], "(\\Seen)", add=seen)
            was_seen = bool((msg.get("flags") or {}).get("seen"))
            if was_seen != seen:
                _adjust_unread(store, acc.id, -1 if seen else 1)
            changes = {"flags": {"seen": seen}}
            detail = "تم تحديدها كمقروءة" if seen else "تم تحديدها كغير مقروءة"
        elif action in ("flag", "unflag"):
            on = action == "flag"
            mb.store_flags([uid], "(\\Flagged)", add=on)
            changes = {"flags": {"flagged": on}}
            detail = "تمت إضافة نجمة للرسالة" if on else "تمت إزالة النجمة"
        elif action == "archive":
            dest = acc.archive_folder if acc.archive_folder and mb.folder_exists(acc.archive_folder) else None
            dest = dest or mb.special_folder("\\Archive")
            if dest and dest.lower() != (msg.get("folder") or "").lower():
                mb.move([uid], dest)
            elif acc.provider == "gmail":
                mb.delete([uid])  # في Gmail: الحذف من INBOX = أرشفة (تبقى في All Mail)
                dest = "[Gmail]/All Mail"
            else:
                raise ActionError("لا يوجد مجلد أرشيف على هذا الحساب. حدد archive_folder في الإعدادات")
            changes = {"archived": True, "archived_at": now_iso(), "moved_to": dest}
            detail = f"تم نقل الرسالة إلى الأرشيف ({dest})"
        elif action == "trash":
            dest = mb.special_folder("\\Trash") or (acc.trash if acc.trash and mb.folder_exists(acc.trash) else None)
            if not dest:
                raise ActionError("لم يتم العثور على سلة المحذوفات في هذا البريد. حدد اسمها في الحقل trash بالإعدادات")
            mb.move([uid], dest)
            changes = {"trashed": True, "trashed_at": now_iso(), "moved_to": dest}
            detail = "تم حذف الرسالة (نُقلت إلى سلة المحذوفات في بريدك)"
        else:
            raise ActionError(f"أمر غير مدعوم: {action}")
    finally:
        mb.logout()
    updated = store.update_message(acc.id, msg["id"], changes)
    if updated:
        store.upsert_index(acc.id, Store.index_entry(updated))
    return {"detail": detail}


def run_action(cfg, store: Store, payload: dict, source: str = "dashboard") -> dict:
    """ينفذ أمرًا واحدًا ويسجله في السجل. يعيد قاموسًا فيه ok و detail/error."""
    action = str(payload.get("action", "")).lower().strip()
    aid = str(payload.get("account", "") or "").strip()
    mid = str(payload.get("id", "") or "").strip()
    entry = {"time": now_iso(), "source": source, "action": action, "account": aid, "id": mid}
    try:
        if action not in ACTIONS:
            raise ActionError(f"أمر غير معروف: '{action}'")
        acc = cfg.account(aid)
        if action == "send":
            result = _send_new(acc, payload, store)
        else:
            msg = store.load_message(acc.id, mid)
            if not msg:
                raise ActionError(f"الرسالة {mid} غير موجودة في الأرشيف")
            entry["subject"] = msg.get("subject", "")
            result = _reply(acc, msg, payload, store) if action == "reply" else _modify(acc, action, msg, store)
        entry.update(ok=True, **result)
        log.info("[%s] %s: %s", aid, action, result.get("detail"))
    except Exception as e:  # noqa: BLE001 - نريد تسجيل أي خطأ في السجل
        err = f"{type(e).__name__}: {e}" if not isinstance(e, ActionError) else str(e)
        log.error("[%s] %s فشل: %s", aid, action, err)
        entry.update(ok=False, error=err)
    store.append_log(entry)
    return entry


def process_queue(cfg, store: Store) -> int:
    """تنفيذ الأوامر المعلّقة في actions/queue (مسار بديل لـ repository_dispatch)."""
    n = 0
    for path in store.queued_actions():
        payload = read_json(path)
        try:
            if isinstance(payload, dict):
                run_action(cfg, store, payload, source="queue")
                n += 1
            else:
                log.warning("ملف أمر غير صالح: %s", path.name)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
    return n
