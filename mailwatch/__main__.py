"""واجهة سطر الأوامر: python -m mailwatch <poll|backfill|action|queue|check|telegram-test>"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .logsafe import is_public, mask, setup_logging
from .storage import Store, read_json
from .telegram import Telegram


def _store() -> Store:
    return Store(os.environ.get("DATA_DIR", "data"))


def _github_output(key: str, value) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def cmd_poll(args) -> int:
    from .poll import run_poll
    cfg = load_config()
    payload = run_poll(
        cfg, _store(), Telegram(),
        backfill_batch=0 if args.no_backfill else args.backfill_batch,
        time_budget_min=args.time_budget, workers=args.workers, only=args.account,
    )
    _github_output("backfill_remaining", payload["totals"]["backfill_remaining"])
    _github_output("errors", payload["totals"]["errors"])
    return 0


def cmd_backfill(args) -> int:
    from .poll import run_poll
    cfg = load_config()
    payload = run_poll(
        cfg, _store(), Telegram(), backfill_batch=10**9, time_budget_min=args.time_budget,
        workers=args.workers, fetch_new=False, only=args.account,
    )
    remaining = payload["totals"]["backfill_remaining"]
    _github_output("backfill_remaining", remaining)
    print(f"متبقي من الأرشيف القديم: {remaining} رسالة")
    return 0


def _event_payload() -> dict | None:
    """قراءة الأمر من ملف حدث GitHub مباشرة حتى لا يظهر محتواه في سجل الخطوة."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return None
    ev = read_json(Path(path)) or {}
    payload = ev.get("client_payload") or {}
    if not payload:
        raw = (ev.get("inputs") or {}).get("payload")
        if raw:
            payload = json.loads(raw)
    return payload if isinstance(payload, dict) and payload.get("action") else None


def cmd_action(args) -> int:
    from .actions import run_action
    if args.event:
        payload = _event_payload()
        if not payload:
            print("لا يوجد أمر مباشر في الحدث (الأوامر تُقرأ من قائمة الانتظار)")
            return 0
    elif args.file:
        payload = read_json(Path(args.file))
    else:
        payload = json.loads(args.payload or "{}")
    if not isinstance(payload, dict) or not payload:
        print("لم يتم تمرير أمر صالح (JSON)", file=sys.stderr)
        return 2
    cfg = load_config()
    res = run_action(cfg, _store(), payload, source=args.source)
    if is_public():
        print(f"{res.get('action')}: {'نجح' if res.get('ok') else 'فشل'}")
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


def cmd_queue(_args) -> int:
    from .actions import process_queue
    n = process_queue(load_config(), _store())
    print(f"تم تنفيذ {n} أمر")
    return 0


def cmd_check(args) -> int:
    cfg = load_config()
    print(f"ملف الإعدادات: {cfg.path}")
    print(f"عدد الحسابات: {len(cfg.accounts)} (مفعّل: {len(cfg.enabled_accounts)})")
    tg = Telegram()
    print("تليجرام:", "مفعّل" if tg.enabled else "غير مفعّل (TELEGRAM_BOT_TOKEN غير موجود)")
    bad = 0
    for acc in cfg.accounts:
        line = (f"- {acc.id}: {acc.email} [{acc.provider}/{acc.auth}] مجلدات={acc.folders} "
                f"فلاتر={len(acc.filters)} أرشفة={acc.archive} تنبيه={acc.notify}"
                + ("" if acc.enabled else " (معطّل)"))
        if args.connect and acc.enabled:
            from .imap_client import Mailbox, MailboxError
            try:
                mb = Mailbox(acc)
                try:
                    folders = mb.folders()
                    for f in acc.folders:
                        mb.select(f)
                    line += f" ✅ اتصال ناجح ({len(folders)} مجلد)"
                finally:
                    mb.logout()
            except Exception as e:  # noqa: BLE001
                bad += 1
                line += f" ❌ {e}"
        print(mask(line))
    return 1 if bad else 0


def cmd_telegram_test(args) -> int:
    tg = Telegram()
    if not tg.enabled:
        print("TELEGRAM_BOT_TOKEN غير موجود", file=sys.stderr)
        return 2
    ok = tg.send(args.text, args.chat)
    print("تم الإرسال" if ok else "فشل الإرسال (تحقق من التوكن و chat_id)")
    return 0 if ok else 1


def main(argv=None) -> int:
    setup_logging(os.environ.get("LOG_LEVEL", "INFO"))
    p = argparse.ArgumentParser(prog="mailwatch", description="مراقب البريد: سحب وفلترة وأرشفة وتنبيه")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("poll", help="فحص الرسائل الجديدة + شريحة من الأرشيف القديم")
    sp.add_argument("--backfill-batch", type=int, default=int(os.environ.get("BACKFILL_BATCH", "100")),
                    help="عدد الرسائل القديمة لكل مجلد في هذه التشغيلة")
    sp.add_argument("--no-backfill", action="store_true")
    sp.add_argument("--time-budget", type=float, default=float(os.environ.get("TIME_BUDGET_MIN", "8")),
                    help="الحد الزمني بالدقائق لسحب الأرشيف القديم")
    sp.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "8")))
    sp.add_argument("--account", action="append", help="قصر التشغيل على حساب معين (يمكن التكرار)")
    sp.set_defaults(func=cmd_poll)

    sb = sub.add_parser("backfill", help="سحب الأرشيف القديم فقط")
    sb.add_argument("--time-budget", type=float, default=float(os.environ.get("TIME_BUDGET_MIN", "20")))
    sb.add_argument("--workers", type=int, default=int(os.environ.get("WORKERS", "4")))
    sb.add_argument("--account", action="append")
    sb.set_defaults(func=cmd_backfill)

    sa = sub.add_parser("action", help="تنفيذ أمر واحد (JSON)")
    sa.add_argument("--payload", help='مثال: {"action":"mark_read","account":"x","id":"abc"}')
    sa.add_argument("--file")
    sa.add_argument("--event", action="store_true", help="قراءة الأمر من ملف حدث GitHub (GITHUB_EVENT_PATH)")
    sa.add_argument("--source", default="dispatch")
    sa.set_defaults(func=cmd_action)

    sq = sub.add_parser("queue", help="تنفيذ الأوامر المعلّقة في actions/queue")
    sq.set_defaults(func=cmd_queue)

    sc = sub.add_parser("check", help="التحقق من الإعدادات (و --connect لاختبار الاتصال)")
    sc.add_argument("--connect", action="store_true")
    sc.set_defaults(func=cmd_check)

    st = sub.add_parser("telegram-test", help="إرسال رسالة اختبار")
    st.add_argument("--text", default="✅ مراقب البريد يعمل. هذه رسالة اختبار.")
    st.add_argument("--chat")
    st.set_defaults(func=cmd_telegram_test)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as e:
        print(mask(f"خطأ في الإعدادات: {e}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
