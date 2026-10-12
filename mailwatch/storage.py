"""تخزين الرسائل والفهارس والحالة على القرص (داخل مستودع Git).

البنية داخل DATA_DIR:
  state/<account>.json            حالة السحب لكل حساب (آخر UID لكل مجلد، العدادات)
  index/<account>/<YYYY-MM>.json  فهرس شهري مختصر تستخدمه لوحة التحكم
  messages/<account>/<id>.json    الرسالة كاملة (نص + HTML + بيانات المرفقات)
  raw/<account>/<id>.eml          النسخة الأصلية الخام
  attachments/<account>/<id>/...  المرفقات
  accounts.json                   ملخص الحسابات وحالتها للوحة التحكم
  actions/queue/*.json            أوامر معلّقة (رد/أرشفة/...) من اللوحة
  actions/log.json                سجل تنفيذ الأوامر
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

MAX_ATTACHMENT_BYTES = 90 * 1024 * 1024  # حد GitHub للملف الواحد 100MB
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_filename(name: str, fallback: str = "file") -> str:
    name = unicodedata.normalize("NFC", str(name or ""))
    name = _BAD_CHARS.sub("_", name).strip(" .")
    return (name or fallback)[:120]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_id(aid: str, folder: str, uidvalidity, uid) -> str:
    return hashlib.sha1(f"{aid}|{folder}|{uidvalidity}|{uid}".encode()).hexdigest()[:12]


def write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        for sub in ("state", "index", "messages", "raw", "attachments", "actions/queue"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---------- الحالة ----------
    def state_path(self, aid: str) -> Path:
        return self.root / "state" / f"{aid}.json"

    def load_state(self, aid: str) -> dict:
        st = read_json(self.state_path(aid)) or {}
        st.setdefault("folders", {})
        st.setdefault("counts", {"total": 0, "matched": 0, "unread": 0})
        return st

    def save_state(self, aid: str, state: dict) -> None:
        write_json(self.state_path(aid), state)

    def load_global(self, name: str) -> dict:
        return read_json(self.root / "state" / f"_{name}.json") or {}

    def save_global(self, name: str, obj: dict) -> None:
        write_json(self.root / "state" / f"_{name}.json", obj)

    # ---------- الرسائل ----------
    def message_path(self, aid: str, mid: str) -> Path:
        return self.root / "messages" / aid / f"{mid}.json"

    def message_exists(self, aid: str, mid: str) -> bool:
        return self.message_path(aid, mid).exists()

    def load_message(self, aid: str, mid: str) -> dict | None:
        return read_json(self.message_path(aid, mid))

    def save_message(self, aid: str, msg: dict, raw: bytes | None = None,
                     attachments: list[dict] | None = None) -> None:
        mid = msg["id"]
        if attachments:
            att_dir = self.root / "attachments" / aid / mid
            used: set[str] = set()
            for i, att in enumerate(attachments):
                base = safe_filename(att.get("filename"), f"attachment-{i + 1}")
                name, n = base, 1
                while name.lower() in used:
                    n += 1
                    stem, dot, ext = base.rpartition(".")
                    name = f"{stem}-{n}.{ext}" if dot and stem else f"{base}-{n}"
                used.add(name.lower())
                if i < len(msg["attachments"]):
                    if att["size"] > MAX_ATTACHMENT_BYTES:
                        msg["attachments"][i]["skipped"] = True
                        continue
                    att_dir.mkdir(parents=True, exist_ok=True)
                    (att_dir / name).write_bytes(att["data"])
                    msg["attachments"][i]["path"] = f"attachments/{aid}/{mid}/{name}"
        if raw is not None:
            p = self.root / "raw" / aid / f"{mid}.eml"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            msg["raw_path"] = f"raw/{aid}/{mid}.eml"
        write_json(self.message_path(aid, mid), msg)

    def update_message(self, aid: str, mid: str, changes: dict) -> dict | None:
        msg = self.load_message(aid, mid)
        if msg is None:
            return None
        for k, v in changes.items():
            if k == "flags":
                msg.setdefault("flags", {}).update(v)
            else:
                msg[k] = v
        write_json(self.message_path(aid, mid), msg)
        return msg

    # ---------- الفهرس ----------
    @staticmethod
    def month_key(date_iso: str | None) -> str:
        return (date_iso or now_iso())[:7]

    def index_path(self, aid: str, month: str) -> Path:
        return self.root / "index" / aid / f"{month}.json"

    def load_index(self, aid: str, month: str) -> list[dict]:
        return read_json(self.index_path(aid, month), []) or []

    def list_months(self, aid: str) -> list[str]:
        d = self.root / "index" / aid
        if not d.exists():
            return []
        return sorted((p.stem for p in d.glob("????-??.json")), reverse=True)

    @staticmethod
    def index_entry(msg: dict) -> dict:
        flags = msg.get("flags") or {}
        return {
            "id": msg["id"],
            "folder": msg.get("folder", ""),
            "uid": msg.get("uid"),
            "date": msg.get("date") or msg.get("received") or now_iso(),
            "from": msg.get("from") or {"name": "", "email": ""},
            "to": (msg.get("to") or [{"name": "", "email": ""}])[0],
            "subject": msg.get("subject", ""),
            "snippet": (msg.get("snippet") or "")[:140],
            "matched": msg.get("matched") or [],
            "seen": bool(flags.get("seen")),
            "flagged": bool(flags.get("flagged")),
            "answered": bool(flags.get("answered")),
            "att": len(msg.get("attachments") or []),
            "size": msg.get("size", 0),
            "archived": bool(msg.get("archived")),
            "trashed": bool(msg.get("trashed")),
            "sent": bool(msg.get("sent")),
        }

    def upsert_index(self, aid: str, entry: dict) -> None:
        month = self.month_key(entry.get("date"))
        with self._lock:
            items = self.load_index(aid, month)
            items = [e for e in items if e.get("id") != entry["id"]]
            items.append(entry)
            items.sort(key=lambda e: e.get("date") or "", reverse=True)
            write_json(self.index_path(aid, month), items, compact=True)

    # ---------- ملخص الحسابات ----------
    def write_accounts(self, payload: dict) -> None:
        write_json(self.root / "accounts.json", payload)

    def load_accounts(self) -> dict:
        return read_json(self.root / "accounts.json") or {"updated": None, "accounts": []}

    # ---------- الأوامر ----------
    def queued_actions(self) -> list[Path]:
        return sorted((self.root / "actions" / "queue").glob("*.json"))

    def append_log(self, entry: dict) -> None:
        path = self.root / "actions" / "log.json"
        with self._lock:
            log = read_json(path, []) or []
            log.insert(0, entry)
            write_json(path, log[:500])


class IndexBuffer:
    """تجميع تعديلات الفهرس لحساب واحد وكتابتها مرة واحدة لكل شهر."""

    def __init__(self, store: Store, aid: str):
        self.store = store
        self.aid = aid
        self.months: dict[str, dict[str, dict]] = {}

    def upsert(self, entry: dict) -> None:
        month = Store.month_key(entry.get("date"))
        if month not in self.months:
            self.months[month] = {e["id"]: e for e in self.store.load_index(self.aid, month) if "id" in e}
        self.months[month][entry["id"]] = entry

    def flush(self) -> None:
        for month, items in self.months.items():
            lst = sorted(items.values(), key=lambda e: e.get("date") or "", reverse=True)
            write_json(self.store.index_path(self.aid, month), lst, compact=True)
        self.months.clear()
