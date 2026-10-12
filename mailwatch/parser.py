"""تحويل الرسالة الخام (RFC 822) إلى بنية بيانات قابلة للتخزين والعرض."""
from __future__ import annotations

import email
import html as html_lib
import mimetypes
import re
from datetime import timezone
from email import policy
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

_SCRIPT_RE = re.compile(r"<(script|style|head|title)[^>]*>.*?</\1>", re.S | re.I)
_BLOCK_RE = re.compile(r"<\s*(br|/p|/div|/tr|/li|/h[1-6]|/table)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_HSPACE_RE = re.compile(r"[ \t\r\f\v ]+")


def html_to_text(h: str) -> str:
    t = _SCRIPT_RE.sub(" ", h or "")
    t = _BLOCK_RE.sub("\n", t)
    t = _TAG_RE.sub(" ", t)
    t = html_lib.unescape(t)
    t = _HSPACE_RE.sub(" ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)
    return t.strip()


def _decode_header_value(v) -> str:
    if v is None:
        return ""
    try:
        return str(make_header(decode_header(str(v))))
    except Exception:
        return str(v)


def _header(msg, name: str) -> str:
    try:
        v = msg.get(name)
        return str(v) if v is not None else ""
    except Exception:
        try:
            return _decode_header_value(msg.get(name, ""))
        except Exception:
            return ""


def _addresses(msg, name: str) -> list[dict]:
    try:
        values = msg.get_all(name, []) or []
    except Exception:
        values = []
    strs = []
    for v in values:
        try:
            strs.append(str(v))
        except Exception:
            strs.append(_decode_header_value(v))
    out = []
    for n, a in getaddresses(strs):
        n, a = n.strip(), a.strip()
        if n or a:
            out.append({"name": n, "email": a.lower()})
    return out


def _decode_text(payload: bytes, charset: str | None) -> str:
    for cs in (charset, "utf-8", "windows-1256", "latin-1"):
        if not cs:
            continue
        try:
            return payload.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("utf-8", errors="replace")


def parse_message(raw: bytes) -> tuple[dict, list[dict]]:
    """يعيد (بيانات الرسالة، قائمة المرفقات مع محتواها الخام في المفتاح data)."""
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception:
        msg = email.message_from_bytes(raw, policy=policy.compat32)

    texts: list[str] = []
    htmls: list[str] = []
    attachments: list[dict] = []
    for part in msg.walk():
        try:
            if part.is_multipart():
                continue
            ctype = (part.get_content_type() or "application/octet-stream").lower()
        except Exception:
            continue
        try:
            disp = (part.get_content_disposition() or "").lower()
        except Exception:
            disp = ""
        try:
            fname = part.get_filename()
        except Exception:
            fname = None
        fname = _decode_header_value(fname) if fname else ""
        try:
            payload = part.get_payload(decode=True) or b""
        except Exception:
            payload = b""

        if ctype in ("text/plain", "text/html") and disp != "attachment" and not fname:
            try:
                charset = part.get_content_charset()
            except Exception:
                charset = None
            text = _decode_text(payload, charset)
            (texts if ctype == "text/plain" else htmls).append(text)
            continue
        if not payload and not fname:
            continue
        cid = (_header(part, "Content-ID") or "").strip("<> ")
        ext = mimetypes.guess_extension(ctype) or ""
        attachments.append({
            "filename": fname or f"attachment-{len(attachments) + 1}{ext}",
            "content_type": ctype,
            "size": len(payload),
            "content_id": cid,
            "inline": disp == "inline",
            "data": payload,
        })

    text = "\n\n".join(t for t in texts if t.strip()).strip()
    html_body = "\n".join(h for h in htmls if h.strip()).strip()
    if not text and html_body:
        text = html_to_text(html_body)
    snippet = _HSPACE_RE.sub(" ", text.replace("\n", " ")).strip()[:300]

    date_iso = None
    try:
        d = parsedate_to_datetime(_header(msg, "Date"))
        if d is not None:
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            date_iso = d.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass

    senders = _addresses(msg, "From")
    data = {
        "message_id": _header(msg, "Message-ID").strip(),
        "in_reply_to": _header(msg, "In-Reply-To").strip(),
        "references": _header(msg, "References").split(),
        "date": date_iso,
        "from": senders[0] if senders else {"name": "", "email": ""},
        "reply_to": _addresses(msg, "Reply-To"),
        "to": _addresses(msg, "To"),
        "cc": _addresses(msg, "Cc"),
        "subject": _header(msg, "Subject").strip(),
        "text": text,
        "html": html_body,
        "snippet": snippet,
        "attachments": [{k: v for k, v in a.items() if k != "data"} for a in attachments],
        "size": len(raw),
    }
    return data, attachments
