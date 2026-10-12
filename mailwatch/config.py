"""تحميل إعدادات الحسابات والفلاتر وبيانات الاتصال.

ملف الإعدادات: config/accounts.yaml (أو accounts.json). يمكن تحديد مساره عبر
المتغير CONFIG_PATH. كلمات المرور تُقرأ بالترتيب:
  1. المتغير ACCOUNT_PASSWORDS (JSON: {"account_id": "password"})
  2. متغير بيئة باسم PASS_<ACCOUNT_ID> أو الاسم المحدد في password_env
  3. الحقل password داخل الملف نفسه
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# إعدادات جاهزة لأشهر المزودين
PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "imap_host": "imap.gmail.com", "imap_port": 993,
        "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_security": "ssl",
        "trash": "[Gmail]/Trash", "archive_folder": "", "oauth_provider": "google",
    },
    "yahoo": {
        "imap_host": "imap.mail.yahoo.com", "imap_port": 993,
        "smtp_host": "smtp.mail.yahoo.com", "smtp_port": 465, "smtp_security": "ssl",
        "trash": "Trash", "archive_folder": "Archive",
    },
    "outlook": {
        "imap_host": "outlook.office365.com", "imap_port": 993,
        "smtp_host": "smtp.office365.com", "smtp_port": 587, "smtp_security": "starttls",
        "trash": "Deleted", "archive_folder": "Archive", "oauth_provider": "microsoft",
    },
    "icloud": {
        "imap_host": "imap.mail.me.com", "imap_port": 993,
        "smtp_host": "smtp.mail.me.com", "smtp_port": 587, "smtp_security": "starttls",
        "trash": "Deleted Messages", "archive_folder": "Archive",
    },
    "zoho": {
        "imap_host": "imap.zoho.com", "imap_port": 993,
        "smtp_host": "smtp.zoho.com", "smtp_port": 465, "smtp_security": "ssl",
        "trash": "Trash", "archive_folder": "Archive",
    },
    "custom": {
        "imap_port": 993, "smtp_port": 465, "smtp_security": "ssl",
        "trash": "Trash", "archive_folder": "Archive",
    },
}

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ConfigError(Exception):
    """خطأ في ملف الإعدادات."""


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return [str(x).strip() for x in value if str(x).strip()]


@dataclass
class FilterRule:
    name: str
    from_: list[str] = field(default_factory=list)
    to: list[str] = field(default_factory=list)
    subject: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    anywhere: list[str] = field(default_factory=list)
    match: str = "any"  # any: يكفي تطابق حقل واحد | all: كل الحقول المحددة

    @classmethod
    def from_dict(cls, d: dict, idx: int) -> "FilterRule":
        if not isinstance(d, dict):
            raise ConfigError(f"الفلتر رقم {idx + 1} يجب أن يكون كائنًا بحقول (name, from, subject, ...)")
        match = str(d.get("match") or "any").lower()
        if match not in ("any", "all"):
            raise ConfigError(f"الفلتر '{d.get('name')}': قيمة match يجب أن تكون any أو all")
        return cls(
            name=str(d.get("name") or f"فلتر {idx + 1}"),
            from_=_as_list(d.get("from")),
            to=_as_list(d.get("to")),
            subject=_as_list(d.get("subject")),
            body=_as_list(d.get("body")),
            anywhere=_as_list(d.get("anywhere")),
            match=match,
        )

    @property
    def is_empty(self) -> bool:
        return not (self.from_ or self.to or self.subject or self.body or self.anywhere)


@dataclass
class Account:
    id: str
    name: str
    email: str
    provider: str = "custom"
    auth: str = "password"  # password | oauth2
    username: str = ""
    password: str = ""
    imap_host: str = ""
    imap_port: int = 993
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"  # ssl | starttls | none
    trash: str = ""
    archive_folder: str = ""
    sent_folder: str = ""
    folders: list[str] = field(default_factory=lambda: ["INBOX"])
    archive: str = "all"  # all: حفظ كل الرسائل | matched: المطابقة للفلاتر فقط
    notify: str = "matched"  # matched | all | none
    telegram_chat_id: str = ""
    filters: list[FilterRule] = field(default_factory=list)
    oauth2: dict = field(default_factory=dict)
    store_raw: bool = True
    extract_attachments: bool = True
    enabled: bool = True
    signature: str = ""

    def summary(self) -> dict:
        """بيانات غير حساسة لعرضها في لوحة التحكم."""
        return {
            "id": self.id, "name": self.name, "email": self.email, "provider": self.provider,
            "folders": self.folders, "archive": self.archive, "notify": self.notify,
            "enabled": self.enabled, "filters": [f.name for f in self.filters],
        }


@dataclass
class Config:
    accounts: list[Account]
    defaults: dict
    path: Path | None
    raw: dict

    def account(self, aid: str) -> Account:
        for a in self.accounts:
            if a.id == aid:
                return a
        raise ConfigError(f"الحساب غير موجود في الإعدادات: {aid}")

    @property
    def enabled_accounts(self) -> list[Account]:
        return [a for a in self.accounts if a.enabled]


def resolve_secret(aid: str, raw: dict, key: str = "password") -> str:
    """إيجاد كلمة المرور (أو أي سر) من المتغيرات أو الملف."""
    env_map = os.environ.get("ACCOUNT_PASSWORDS", "").strip()
    if env_map:
        try:
            mapping = json.loads(env_map)
            if isinstance(mapping, dict):
                val = mapping.get(aid) if key == "password" else mapping.get(f"{aid}.{key}")
                if val:
                    return str(val)
        except json.JSONDecodeError:
            raise ConfigError("المتغير ACCOUNT_PASSWORDS ليس JSON صالحًا")
    env_name = raw.get(f"{key}_env") or (
        f"PASS_{aid.upper().replace('-', '_')}" if key == "password"
        else f"{key.upper()}_{aid.upper().replace('-', '_')}"
    )
    if os.environ.get(env_name):
        return os.environ[env_name]
    return str(raw.get(key) or "")


def find_config_path() -> Path | None:
    env = os.environ.get("CONFIG_PATH")
    if env:
        return Path(env)
    bases = [Path(os.environ.get("DATA_DIR", "data"))]
    if os.environ.get("DATA_REPO_DIR"):
        bases.append(Path(os.environ["DATA_REPO_DIR"]))  # وضع المستودعين: config/ في جذر مستودع البيانات
    bases.append(Path("."))
    for base in bases:
        for name in ("accounts.yaml", "accounts.yml", "accounts.json"):
            p = base / "config" / name
            if p.exists():
                return p
    return None


def build_account(d: dict, defaults: dict) -> Account:
    if not isinstance(d, dict):
        raise ConfigError("كل حساب يجب أن يكون كائنًا بحقول (id, email, ...)")
    aid = str(d.get("id") or "").strip()
    if not ID_RE.match(aid):
        raise ConfigError(f"معرّف الحساب غير صالح: '{aid}' (حروف إنجليزية وأرقام و _ - فقط، حتى 40 حرفًا)")
    email_addr = str(d.get("email") or "").strip()
    if "@" not in email_addr:
        raise ConfigError(f"[{aid}] البريد الإلكتروني غير صالح: '{email_addr}'")
    provider = str(d.get("provider") or "custom").lower()
    if provider not in PRESETS:
        raise ConfigError(f"[{aid}] مزود غير معروف: '{provider}'. المتاح: {', '.join(PRESETS)}")
    preset = PRESETS[provider]
    custom = d.get("custom") or {}
    if not isinstance(custom, dict):
        raise ConfigError(f"[{aid}] الحقل custom يجب أن يكون كائنًا")

    def pick(key: str, default: Any = "") -> Any:
        for src in (d, custom, defaults, preset):
            if key in src and src[key] not in (None, ""):
                return src[key]
        return default

    auth = str(d.get("auth") or ("oauth2" if d.get("oauth2") else "password")).lower()
    if auth not in ("password", "oauth2"):
        raise ConfigError(f"[{aid}] قيمة auth يجب أن تكون password أو oauth2")

    password = ""
    oauth2: dict = {}
    if auth == "password":
        password = resolve_secret(aid, d, "password")
        if not password:
            raise ConfigError(
                f"[{aid}] لا توجد كلمة مرور. ضعها في الحقل password أو في المتغير "
                f"PASS_{aid.upper().replace('-', '_')} أو في ACCOUNT_PASSWORDS"
            )
    else:
        oauth2 = dict(d.get("oauth2") or {})
        oauth2.setdefault("provider", preset.get("oauth_provider", ""))
        oauth2["refresh_token"] = resolve_secret(aid, oauth2, "refresh_token")
        oauth2["client_secret"] = resolve_secret(aid, oauth2, "client_secret")
        if not oauth2.get("client_id") or not oauth2.get("refresh_token"):
            raise ConfigError(f"[{aid}] OAuth2 يحتاج client_id و refresh_token (استخدم tools/oauth_setup.py)")
        if oauth2["provider"] not in ("google", "microsoft") and not oauth2.get("token_url"):
            raise ConfigError(f"[{aid}] OAuth2: حدد provider (google/microsoft) أو token_url")

    imap_host = str(pick("imap_host"))
    if not imap_host:
        raise ConfigError(f"[{aid}] المزود المخصص يحتاج imap_host (و smtp_host للإرسال)")

    rules_raw = list(defaults.get("filters") or []) + list(d.get("filters") or [])
    filters = [FilterRule.from_dict(f, i) for i, f in enumerate(rules_raw)]
    filters = [f for f in filters if not f.is_empty]

    archive = str(pick("archive", "all")).lower()
    notify = str(pick("notify", "matched")).lower()
    if archive not in ("all", "matched"):
        raise ConfigError(f"[{aid}] قيمة archive يجب أن تكون all أو matched")
    if notify not in ("matched", "all", "none"):
        raise ConfigError(f"[{aid}] قيمة notify يجب أن تكون matched أو all أو none")
    smtp_security = str(pick("smtp_security", "ssl")).lower()
    if smtp_security not in ("ssl", "starttls", "none"):
        raise ConfigError(f"[{aid}] قيمة smtp_security يجب أن تكون ssl أو starttls أو none")

    folders = _as_list(d.get("folders")) or _as_list(defaults.get("folders")) or ["INBOX"]

    return Account(
        id=aid,
        name=str(d.get("name") or email_addr),
        email=email_addr,
        provider=provider,
        auth=auth,
        username=str(d.get("username") or email_addr),
        password=password,
        imap_host=imap_host,
        imap_port=int(pick("imap_port", 993)),
        smtp_host=str(pick("smtp_host")),
        smtp_port=int(pick("smtp_port", 465)),
        smtp_security=smtp_security,
        trash=str(pick("trash")),
        archive_folder=str(pick("archive_folder")),
        sent_folder=str(pick("sent_folder")),
        folders=folders,
        archive=archive,
        notify=notify,
        telegram_chat_id=str(pick("telegram_chat_id") or ""),
        filters=filters,
        oauth2=oauth2,
        store_raw=bool(pick("store_raw", True)),
        extract_attachments=bool(pick("extract_attachments", True)),
        enabled=bool(d.get("enabled", True)),
        signature=str(pick("signature") or ""),
    )


def load_config(path: str | Path | None = None) -> Config:
    p = Path(path) if path else find_config_path()
    if p is None or not p.exists():
        raise ConfigError(
            "لم يتم العثور على ملف الإعدادات. أنشئ config/accounts.yaml (انظر config/accounts.example.yaml)"
        )
    with open(p, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ConfigError("ملف الإعدادات يجب أن يحتوي على defaults و accounts")
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("الحقل defaults يجب أن يكون كائنًا")
    accounts_raw = raw.get("accounts") or []
    if not isinstance(accounts_raw, list):
        raise ConfigError("الحقل accounts يجب أن يكون قائمة")
    accounts = [build_account(a, defaults) for a in accounts_raw]
    seen: set[str] = set()
    for a in accounts:
        if a.id in seen:
            raise ConfigError(f"معرّف الحساب مكرر: {a.id}")
        seen.add(a.id)
    return Config(accounts=accounts, defaults=defaults, path=p, raw=raw)
