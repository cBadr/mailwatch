"""عميل IMAP مبسّط فوق imaplib مع دعم كلمة المرور و XOAUTH2."""
from __future__ import annotations

import imaplib
import re
import ssl
import time
from datetime import datetime, timezone

from .oauth import get_access_token

imaplib._MAXLINE = 50_000_000  # السماح برسائل كبيرة

_UID_RE = re.compile(rb"UID (\d+)")
_FLAGS_RE = re.compile(rb"FLAGS \(([^)]*)\)")
_DATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')
_LIST_RE = re.compile(rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>"[^"]*"|NIL)\s+(?P<name>.+)$')

# أسماء شائعة للمجلدات الخاصة عند غياب علامات SPECIAL-USE
_FALLBACK_NAMES = {
    "\\trash": ["Trash", "[Gmail]/Trash", "Deleted", "Deleted Items", "Deleted Messages", "INBOX.Trash", "Junk"],
    "\\sent": ["Sent", "[Gmail]/Sent Mail", "Sent Items", "Sent Messages", "INBOX.Sent"],
    "\\archive": ["Archive", "Archives", "INBOX.Archive", "[Gmail]/All Mail"],
}


class MailboxError(RuntimeError):
    pass


def quote_folder(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_internaldate(s: str) -> str | None:
    try:
        d = datetime.strptime(s.strip(), "%d-%b-%Y %H:%M:%S %z")
        return d.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return None


def _txt(data) -> str:
    if isinstance(data, (list, tuple)):
        data = data[0] if data else b""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


class Mailbox:
    def __init__(self, acc, timeout: int = 60):
        self.acc = acc
        self.current: str | None = None
        self._folders: list[tuple[list[str], str]] | None = None
        ctx = ssl.create_default_context()
        try:
            self.imap = imaplib.IMAP4_SSL(acc.imap_host, acc.imap_port, ssl_context=ctx, timeout=timeout)
        except OSError as e:
            raise MailboxError(f"تعذر الاتصال بخادم IMAP {acc.imap_host}:{acc.imap_port} — {e}") from e
        try:
            if acc.auth == "oauth2":
                token = get_access_token(acc)
                auth = f"user={acc.username}\x01auth=Bearer {token}\x01\x01".encode()
                self.imap.authenticate("XOAUTH2", lambda _: auth)
            else:
                self.imap.login(acc.username, acc.password)
        except imaplib.IMAP4.error as e:
            raise MailboxError(f"فشل تسجيل الدخول إلى {acc.email}: {_txt(e.args)}") from e

    # ---------- مساعدات ----------
    def _check(self, typ: str, data, what: str) -> None:
        if typ != "OK":
            raise MailboxError(f"{what}: {_txt(data)}")

    def _resp_int(self, key: str) -> int | None:
        _, vals = self.imap.response(key)
        try:
            return int(vals[0]) if vals and vals[0] else None
        except (TypeError, ValueError):
            return None

    # ---------- المجلدات ----------
    def select(self, folder: str, readonly: bool = True) -> tuple[int | None, int | None]:
        typ, data = self.imap.select(quote_folder(folder), readonly=readonly)
        self._check(typ, data, f"تعذر فتح المجلد '{folder}'")
        self.current = folder
        return self._resp_int("UIDVALIDITY"), self._resp_int("UIDNEXT")

    def folders(self) -> list[tuple[list[str], str]]:
        if self._folders is not None:
            return self._folders
        typ, data = self.imap.list()
        self._check(typ, data, "تعذر جلب قائمة المجلدات")
        out: list[tuple[list[str], str]] = []
        for item in data or []:
            if isinstance(item, tuple):  # اسم المجلد كـ literal
                head, literal = item[0], item[1]
                m = _LIST_RE.match(head.strip())
                if not m:
                    continue
                flags = m.group("flags").decode(errors="replace").split()
                out.append((flags, literal.decode("utf-8", errors="replace")))
                continue
            if not isinstance(item, bytes):
                continue
            m = _LIST_RE.match(item.strip())
            if not m:
                continue
            flags = m.group("flags").decode(errors="replace").split()
            name = m.group("name").decode("utf-8", errors="replace").strip()
            if name.startswith('"') and name.endswith('"'):
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            out.append((flags, name))
        self._folders = out
        return out

    def special_folder(self, use_flag: str) -> str | None:
        """إيجاد مجلد خاص مثل \\Trash أو \\Sent أو \\Archive."""
        want = use_flag.lower()
        all_folders = self.folders()
        for flags, name in all_folders:
            if want in [f.lower() for f in flags]:
                return name
        names = {n.lower(): n for _, n in all_folders}
        for candidate in _FALLBACK_NAMES.get(want, []):
            if candidate.lower() in names:
                return names[candidate.lower()]
        return None

    def folder_exists(self, name: str) -> bool:
        return name.lower() in {n.lower() for _, n in self.folders()}

    # ---------- البحث والجلب ----------
    def search_uids(self, criteria: str = "ALL") -> list[int]:
        typ, data = self.imap.uid("SEARCH", None, criteria)
        self._check(typ, data, f"فشل البحث ({criteria})")
        if not data or not data[0]:
            return []
        return sorted({int(x) for x in data[0].split()})

    def fetch(self, uids: list[int]) -> list[dict]:
        """جلب الرسائل كاملة دون تعليمها كمقروءة."""
        if not uids:
            return []
        uidset = ",".join(str(u) for u in uids)
        typ, data = self.imap.uid("FETCH", uidset, "(UID FLAGS INTERNALDATE BODY.PEEK[])")
        self._check(typ, data, "فشل جلب الرسائل")
        items: list[dict] = []
        cur: dict | None = None
        for part in data or []:
            if isinstance(part, tuple):
                if cur:
                    items.append(cur)
                cur = {"head": part[0] or b"", "raw": part[1] or b""}
            elif isinstance(part, bytes) and cur is not None:
                cur["head"] += b" " + part
        if cur:
            items.append(cur)
        out = []
        for it in items:
            m = _UID_RE.search(it["head"])
            if not m:
                continue
            fl = _FLAGS_RE.search(it["head"])
            dt = _DATE_RE.search(it["head"])
            out.append({
                "uid": int(m.group(1)),
                "flags": fl.group(1).decode(errors="replace").split() if fl else [],
                "internaldate": parse_internaldate(dt.group(1).decode()) if dt else None,
                "raw": it["raw"],
            })
        return out

    def find_by_message_id(self, message_id: str) -> int | None:
        if not message_id:
            return None
        mid = message_id.replace('"', "")
        uids = self.search_uids(f'HEADER Message-ID "{mid}"')
        return uids[-1] if uids else None

    # ---------- التعديل ----------
    def store_flags(self, uids: list[int], flags: str, add: bool = True) -> None:
        uidset = ",".join(str(u) for u in uids)
        typ, data = self.imap.uid("STORE", uidset, "+FLAGS.SILENT" if add else "-FLAGS.SILENT", flags)
        self._check(typ, data, "فشل تعديل العلامات")

    def expunge(self) -> None:
        self.imap.expunge()

    def move(self, uids: list[int], dest: str) -> None:
        uidset = ",".join(str(u) for u in uids)
        if "MOVE" in self.imap.capabilities:
            typ, data = self.imap.uid("MOVE", uidset, quote_folder(dest))
            self._check(typ, data, f"فشل نقل الرسالة إلى '{dest}'")
            return
        typ, data = self.imap.uid("COPY", uidset, quote_folder(dest))
        self._check(typ, data, f"فشل نسخ الرسالة إلى '{dest}'")
        self.store_flags(uids, "(\\Deleted)", add=True)
        self.expunge()

    def delete(self, uids: list[int]) -> None:
        self.store_flags(uids, "(\\Deleted)", add=True)
        self.expunge()

    def append(self, folder: str, raw: bytes, flags: str = "(\\Seen)") -> None:
        typ, data = self.imap.append(quote_folder(folder), flags, imaplib.Time2Internaldate(time.time()), raw)
        self._check(typ, data, f"فشل حفظ نسخة في '{folder}'")

    def logout(self) -> None:
        try:
            self.imap.logout()
        except Exception:
            pass
