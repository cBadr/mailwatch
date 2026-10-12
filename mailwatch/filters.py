"""محرك الفلترة: مطابقة الرسائل مع القواعد المحددة في الإعدادات.

المطابقة نصية جزئية (substring) غير حساسة لحالة الأحرف، مع تطبيع للعربية
(إزالة التشكيل والتطويل وتوحيد الهمزات والياء والتاء المربوطة) حتى تطابق
"محمّد" مع "محمد" و"إيميل" مع "ايميل".
"""
from __future__ import annotations

import re

from .config import FilterRule

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_TRANS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_WS = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    t = _DIACRITICS.sub("", str(text)).translate(_TRANS).casefold()
    return _WS.sub(" ", t).strip()


def _addr_text(items: list[dict]) -> str:
    return " ".join(f"{a.get('name', '')} {a.get('email', '')}" for a in items or [])


class Matcher:
    def __init__(self, rules: list[FilterRule]):
        self.rules = []
        for r in rules:
            self.rules.append({
                "name": r.name,
                "match": r.match,
                "from": [normalize(t) for t in r.from_],
                "to": [normalize(t) for t in r.to],
                "subject": [normalize(t) for t in r.subject],
                "body": [normalize(t) for t in r.body],
                "anywhere": [normalize(t) for t in r.anywhere],
            })

    def match(self, msg: dict) -> list[str]:
        """يعيد أسماء الفلاتر المطابقة للرسالة."""
        if not self.rules:
            return []
        fields = {
            "from": normalize(_addr_text([msg.get("from") or {}] + list(msg.get("reply_to") or []))),
            "to": normalize(_addr_text(list(msg.get("to") or []) + list(msg.get("cc") or []))),
            "subject": normalize(msg.get("subject")),
            "body": normalize(msg.get("text") or msg.get("snippet")),
        }
        everything = " ".join(fields.values())
        hits: list[str] = []
        for rule in self.rules:
            checks: list[bool] = []
            for key in ("from", "to", "subject", "body"):
                terms = rule[key]
                if terms:
                    checks.append(any(t in fields[key] for t in terms))
            if rule["anywhere"]:
                checks.append(any(t in everything for t in rule["anywhere"]))
            if not checks:
                continue
            ok = any(checks) if rule["match"] == "any" else all(checks)
            if ok:
                hits.append(rule["name"])
        return hits
