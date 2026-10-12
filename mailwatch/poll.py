"""دورة الفحص: سحب الرسائل الجديدة، الفلترة، التخزين، التنبيه، وسحب الأرشيف القديم تدريجيًا."""
from __future__ import annotations

import logging
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from .actions import process_queue
from .commands import process_telegram_commands
from .config import Account, Config
from .filters import Matcher
from .imap_client import Mailbox
from .parser import parse_message
from .storage import IndexBuffer, Store, make_id, now_iso
from .telegram import Telegram, format_error, format_new_mail

log = logging.getLogger("mailwatch.poll")
FETCH_CHUNK = 15          # عدد الرسائل في طلب FETCH الواحد
CHECKPOINT_EVERY = 5      # حفظ الحالة والفهرس كل كم دفعة


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


class AccountRun:
    def __init__(self, acc: Account, store: Store, tg: Telegram, dashboard_url: str,
                 deadline: float, backfill_batch: int, fetch_new: bool = True):
        self.acc = acc
        self.store = store
        self.tg = tg
        self.dashboard_url = dashboard_url
        self.deadline = deadline
        self.backfill_batch = backfill_batch
        self.fetch_new = fetch_new
        self.state = store.load_state(acc.id)
        self.idx = IndexBuffer(store, acc.id)
        self.matcher = Matcher(acc.filters)
        self.stats = {"new": 0, "matched": 0, "notified": 0, "backfilled": 0, "skipped": 0}
        self._since_checkpoint = 0

    # ---------- الدورة ----------
    def run(self) -> dict:
        acc = self.acc
        try:
            self._run()
            self.state["last_error"] = None
            self.state.pop("last_error_notified", None)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            log.error("[%s] %s", acc.id, err)
            log.debug(traceback.format_exc())
            self.state["last_error"] = err
            self.state["last_error_at"] = now_iso()
            if self.tg.enabled and self.state.get("last_error_notified") != err:
                if self.tg.send(format_error(acc, err), acc.telegram_chat_id or None):
                    self.state["last_error_notified"] = err
        finally:
            self._checkpoint(force=True)
        return self.summary()

    def _run(self) -> None:
        mb = Mailbox(self.acc)
        try:
            for folder in self.acc.folders:
                uv, _ = mb.select(folder)
                fs = self.state["folders"].get(folder)
                if not fs or fs.get("uidvalidity") != uv:
                    uids = mb.search_uids("ALL")
                    top = max(uids) if uids else 0
                    fs = {
                        "uidvalidity": uv, "last_uid": top, "backfill_cursor": top,
                        "backfill_total": len(uids), "backfill_done": 0, "initialized_at": now_iso(),
                    }
                    self.state["folders"][folder] = fs
                    log.info("[%s] %s: تهيئة — %d رسالة موجودة ستُسحب تدريجيًا", self.acc.id, folder, len(uids))
                elif self.fetch_new:
                    self._fetch_new(mb, folder, fs)
                if self.backfill_batch > 0:
                    self._backfill(mb, folder, fs)
        finally:
            mb.logout()

    def _fetch_new(self, mb: Mailbox, folder: str, fs: dict) -> None:
        last = int(fs.get("last_uid", 0))
        uids = [u for u in mb.search_uids(f"UID {last + 1}:*") if u > last]
        if not uids:
            return
        log.info("[%s] %s: %d رسالة جديدة", self.acc.id, folder, len(uids))
        for chunk in _chunks(uids, FETCH_CHUNK):
            for item in mb.fetch(chunk):
                self._handle(folder, fs["uidvalidity"], item, is_new=True)
            fs["last_uid"] = max(int(fs["last_uid"]), max(chunk))
            self._checkpoint()

    def _backfill(self, mb: Mailbox, folder: str, fs: dict) -> None:
        cursor = int(fs.get("backfill_cursor", 0) or 0)
        if cursor <= 0 or time.time() > self.deadline:
            return
        uids = [u for u in mb.search_uids(f"UID 1:{cursor}") if u <= cursor]
        if not uids:
            fs["backfill_cursor"] = 0
            return
        uids.sort(reverse=True)
        take = uids[:self.backfill_batch]
        for chunk in _chunks(take, FETCH_CHUNK):
            if time.time() > self.deadline:
                break
            for item in mb.fetch(chunk):
                self._handle(folder, fs["uidvalidity"], item, is_new=False)
                self.stats["backfilled"] += 1
            fs["backfill_cursor"] = min(chunk) - 1
            fs["backfill_done"] = int(fs.get("backfill_done", 0)) + len(chunk)
            self._checkpoint()
        if int(fs["backfill_cursor"]) < min(uids):
            fs["backfill_cursor"] = 0
            log.info("[%s] %s: اكتمل سحب الأرشيف القديم", self.acc.id, folder)

    # ---------- رسالة واحدة ----------
    def _handle(self, folder: str, uv, item: dict, is_new: bool) -> None:
        acc = self.acc
        mid = make_id(acc.id, folder, uv, item["uid"])
        if self.store.message_exists(acc.id, mid):
            self.stats["skipped"] += 1
            return
        raw = item.get("raw") or b""
        try:
            data, atts = parse_message(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] تعذر تحليل الرسالة UID %s: %s", acc.id, item["uid"], e)
            data = {
                "message_id": "", "in_reply_to": "", "references": [], "date": None,
                "from": {"name": "", "email": ""}, "reply_to": [], "to": [], "cc": [],
                "subject": "(تعذر تحليل الرسالة)", "text": "", "html": "", "snippet": "",
                "attachments": [], "size": len(raw),
            }
            atts = []
        flags = [f.lower() for f in item.get("flags", [])]
        data.update({
            "id": mid, "account": acc.id, "folder": folder, "uid": item["uid"], "uidvalidity": uv,
            "received": item.get("internaldate"),
            "flags": {"seen": "\\seen" in flags, "flagged": "\\flagged" in flags, "answered": "\\answered" in flags},
            "stored_at": now_iso(),
        })
        matched = self.matcher.match(data)
        data["matched"] = matched
        if acc.archive == "matched" and not matched:
            self.stats["skipped"] += 1
            return
        should_notify = is_new and self.tg.enabled and (
            acc.notify == "all" or (acc.notify == "matched" and bool(matched))
        )
        if should_notify:
            data["notified_at"] = now_iso()
        self.store.save_message(
            acc.id, data,
            raw if acc.store_raw else None,
            atts if acc.extract_attachments else [],
        )
        self.idx.upsert(Store.index_entry(data))
        c = self.state["counts"]
        c["total"] = c.get("total", 0) + 1
        if matched:
            c["matched"] = c.get("matched", 0) + 1
        if not data["flags"]["seen"]:
            c["unread"] = c.get("unread", 0) + 1
        if is_new:
            self.stats["new"] += 1
            if matched:
                self.stats["matched"] += 1
        if should_notify and self.tg.send(format_new_mail(acc, data, self.dashboard_url), acc.telegram_chat_id or None):
            self.stats["notified"] += 1

    def _checkpoint(self, force: bool = False) -> None:
        self._since_checkpoint += 1
        if force or self._since_checkpoint >= CHECKPOINT_EVERY:
            self.idx.flush()
            self.state["last_check"] = now_iso()
            self.store.save_state(self.acc.id, self.state)
            self._since_checkpoint = 0

    # ---------- الملخص ----------
    def summary(self) -> dict:
        return account_summary(self.acc, self.store, self.state, self.stats)


def account_summary(acc: Account, store: Store, state: dict, stats: dict | None = None) -> dict:
    folders = state.get("folders") or {}
    done = sum(int(f.get("backfill_done", 0)) for f in folders.values())
    total = sum(int(f.get("backfill_total", 0)) for f in folders.values())
    remaining = sum(
        max(0, int(f.get("backfill_total", 0)) - int(f.get("backfill_done", 0)))
        for f in folders.values() if int(f.get("backfill_cursor", 0) or 0) > 0
    )
    return {
        **acc.summary(),
        "last_check": state.get("last_check"),
        "last_error": state.get("last_error"),
        "last_error_at": state.get("last_error_at"),
        "counts": state.get("counts") or {"total": 0, "matched": 0, "unread": 0},
        "backfill": {"remaining": remaining, "done": done, "total": total},
        "months": store.list_months(acc.id),
        "run": stats or {},
    }


def run_poll(cfg: Config, store: Store, tg: Telegram, *, backfill_batch: int = 100,
             time_budget_min: float = 8.0, workers: int = 8, fetch_new: bool = True,
             only: list[str] | None = None) -> dict:
    dashboard_url = os.environ.get("DASHBOARD_URL", "").strip()
    for acc in cfg.accounts:
        tg.allow(acc.telegram_chat_id)

    if fetch_new:
        try:
            n = process_telegram_commands(cfg, store, tg)
            if n:
                log.info("تم تنفيذ %d أمر من تليجرام", n)
        except Exception:  # noqa: BLE001
            log.exception("فشل معالجة أوامر تليجرام")
        try:
            n = process_queue(cfg, store)
            if n:
                log.info("تم تنفيذ %d أمر من قائمة الانتظار", n)
        except Exception:  # noqa: BLE001
            log.exception("فشل معالجة قائمة الأوامر")

    deadline = time.time() + time_budget_min * 60
    targets = [a for a in cfg.enabled_accounts if not only or a.id in only]
    summaries: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {
            ex.submit(AccountRun(acc, store, tg, dashboard_url, deadline, backfill_batch, fetch_new).run): acc
            for acc in targets
        }
        for fut, acc in futures.items():
            try:
                summaries[acc.id] = fut.result()
            except Exception as e:  # noqa: BLE001
                log.exception("[%s] فشل غير متوقع", acc.id)
                st = store.load_state(acc.id)
                st["last_error"] = f"{type(e).__name__}: {e}"
                summaries[acc.id] = account_summary(acc, store, st)

    for acc in cfg.accounts:
        if acc.id not in summaries:
            summaries[acc.id] = account_summary(acc, store, store.load_state(acc.id))

    accounts = [summaries[a.id] for a in cfg.accounts]
    totals = {
        "accounts": len(accounts),
        "enabled": sum(1 for a in accounts if a.get("enabled")),
        "errors": sum(1 for a in accounts if a.get("last_error")),
        "total": sum(a["counts"].get("total", 0) for a in accounts),
        "matched": sum(a["counts"].get("matched", 0) for a in accounts),
        "unread": sum(a["counts"].get("unread", 0) for a in accounts),
        "backfill_remaining": sum(a["backfill"]["remaining"] for a in accounts),
    }
    run = {k: sum(a.get("run", {}).get(k, 0) for a in accounts) for k in ("new", "matched", "notified", "backfilled")}
    payload = {"updated": now_iso(), "accounts": accounts, "totals": totals, "last_run": run}
    store.write_accounts(payload)
    log.info("انتهى الفحص: %d جديدة، %d مطابقة، %d تنبيه، %d من الأرشيف، %d حساب به خطأ",
             run["new"], run["matched"], run["notified"], run["backfilled"], totals["errors"])
    return payload
