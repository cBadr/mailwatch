"""إخفاء البيانات الحساسة من السجلات عند التشغيل في مستودع عام (PUBLIC_LOGS=1).

سجلات GitHub Actions في المستودعات العامة مرئية للجميع، لذلك تُستبدل عناوين البريد
بـ <email> في كل ما يُطبع.
"""
from __future__ import annotations

import logging
import os
import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def is_public() -> bool:
    return os.environ.get("PUBLIC_LOGS", "").strip() in ("1", "true", "yes")


def mask(text) -> str:
    s = str(text)
    return EMAIL_RE.sub("<email>", s) if is_public() else s


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask(super().format(record))


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level)
    fmt = MaskingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for h in logging.getLogger().handlers:
        h.setFormatter(fmt)
