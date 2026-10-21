"""Notifier: bus events become Apprise messages, throttled through the notifications table."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import apprise

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import utcnow

log = logging.getLogger(__name__)

Sender = Callable[[list[str], str, str], bool]
DAY = timedelta(hours=24)

# event -> throttle window (None = once ever)
WINDOWS: dict[str, timedelta | None] = {
    "needs_code": DAY,
    "password_rejected": DAY,
    "repeated_failure": DAY,
    "plan_pending": DAY,
    "space_low": DAY,
    "first_full": None,
}

TITLE_NEEDS_CODE = "icloud2nas：{name} 需要验证码 / needs a 2FA code"
BODY_NEEDS_CODE = (
    "Apple 要求重新验证，请打开网页输入验证码。/ "
    "Apple asked for a new 2FA code; open the web page to enter it."
)
TITLE_PASSWORD_REJECTED = "icloud2nas：{name} 密码错误 / password rejected"
BODY_PASSWORD_REJECTED = (
    "Apple 拒绝了保存的密码，请在网页更新。/ "
    "Apple rejected the stored password; update it on the web page."
)
TITLE_REPEATED_FAILURE = "icloud2nas：{name} 连续失败 / repeated failures"
BODY_REPEATED_FAILURE = (
    "该图库连续 3 次同步失败，正在退避重试。/ 3 consecutive failures; backing off."
)
TITLE_PLAN_PENDING = "icloud2nas：{name} 对账待确认 / reconcile plan awaiting approval"
BODY_PLAN_PENDING = (
    "对账发现 {extraneous} 个多余文件，超过熔断阈值，请在网页查看并放行或拒绝。/ "
    "Reconcile found {extraneous} extraneous files above the fuse; "
    "review and approve or reject on the web page."
)
TITLE_SPACE_LOW = "icloud2nas：空间不足 / low disk space"
BODY_SPACE_LOW = (
    "照片卷剩余空间低于 {min_free_gb} GB，同步已暂停。/ "
    "Free space on the photos volume is below {min_free_gb} GB; syncing is paused."
)
TITLE_FIRST_FULL = "icloud2nas：{name} 初次全量完成 / first full sync complete"
BODY_FIRST_FULL = (
    "该图库已完整镜像一次。/ The library has been mirrored in full for the first time."
)


def send_apprise(urls: list[str], title: str, body: str) -> bool:
    try:
        ap = apprise.Apprise()
        for url in urls:
            ap.add(url)
        return bool(ap.notify(title=title, body=body))
    except Exception:  # noqa: BLE001 - a broken notifier must never take the service down
        log.exception("apprise delivery failed")
        return False


class Notifier:
    def __init__(
        self,
        store: Store,
        settings: Settings,
        bus: EventBus,
        *,
        send: Sender = send_apprise,
        clock: Callable[[], datetime] = utcnow,
        link_fallback: Callable[[], str | None] = lambda: None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.bus = bus
        self.send = send
        self.clock = clock
        self.link_fallback = link_fallback
        self._unsubscribe: Callable[[], None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self._unsubscribe is None:
            self._unsubscribe = self.bus.subscribe(self._on_event)

    async def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await self.drain()

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    def _on_event(self, topic: str, payload: dict[str, Any]) -> None:
        decision = self.decide(topic, payload)
        if decision is None:
            return
        event, subject, title, body = decision
        with self.store.session() as s:
            if repo.notification_sent_within(
                s, event, subject, WINDOWS[event], now=self.clock()
            ):
                return
        task = asyncio.get_running_loop().create_task(self._deliver(event, subject, title, body))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def decide(self, topic: str, payload: dict[str, Any]) -> tuple[str, str, str, str] | None:
        """Map a bus event to (event, subject_key, title, body); None when nothing to send."""
        found = self._classify(topic, payload)
        if found is None:
            return None
        event, subject, title, reason = found
        if not self.settings.notify_enabled(event):
            return None
        lines = [reason]
        link = str(self.settings.get("public_url") or "").strip() or self.link_fallback()
        if link:
            lines.append(link)
        return event, subject, title, "\n".join(lines)

    def _classify(
        self, topic: str, payload: dict[str, Any]
    ) -> tuple[str, str, str, str] | None:
        """Returns (event, subject_key, title, reason) before the enabled/link handling."""
        with self.store.session() as s:
            if topic == "account.status":
                status = payload.get("status")
                if status == "needs_reauth":
                    event, title, reason = "needs_code", TITLE_NEEDS_CODE, BODY_NEEDS_CODE
                elif status == "password_rejected":
                    event = "password_rejected"
                    title, reason = TITLE_PASSWORD_REJECTED, BODY_PASSWORD_REJECTED
                else:
                    return None
                account = repo.get_account(s, str(payload.get("account_id")))
                if account is None:
                    return None
                return event, account.id, title.format(name=account.display_name), reason
            if topic == "plan.pending":
                library = repo.get_library(s, str(payload.get("library_id")))
                if library is None:
                    return None
                return (
                    "plan_pending",
                    library.id,
                    TITLE_PLAN_PENDING.format(name=library.display_name),
                    BODY_PLAN_PENDING.format(extraneous=payload.get("extraneous", "?")),
                )
            if topic == "space.low":
                return (
                    "space_low",
                    "global",
                    TITLE_SPACE_LOW,
                    BODY_SPACE_LOW.format(min_free_gb=payload.get("min_free_gb", "?")),
                )
            if topic == "run.finished":
                return self._classify_run(s, payload)
        return None

    def _classify_run(self, s: Any, payload: dict[str, Any]) -> tuple[str, str, str, str] | None:
        library_id = payload.get("library_id")
        run_id = payload.get("run_id")
        if payload.get("first_full") is True and library_id:
            library = repo.get_library(s, str(library_id))
            if library is None:
                return None
            return (
                "first_full",
                library.id,
                TITLE_FIRST_FULL.format(name=library.display_name),
                BODY_FIRST_FULL,
            )
        if payload.get("status") == "failed" and payload.get("account_failures") == 3:
            run = repo.get_run(s, str(run_id)) if run_id else None
            if library_id:
                library = repo.get_library(s, str(library_id))
                if library is None:
                    return None
                subject, name = library.id, library.display_name
            else:
                account_id = payload.get("account_id") or (run.account_id if run else None)
                account = repo.get_account(s, str(account_id)) if account_id else None
                if account is None:
                    return None
                subject, name = account.id, account.display_name
            reason = BODY_REPEATED_FAILURE
            if run is not None and run.error_message:
                reason += "\n" + run.error_message
            return "repeated_failure", subject, TITLE_REPEATED_FAILURE.format(name=name), reason
        return None

    async def _deliver(self, event: str, subject: str, title: str, body: str) -> None:
        try:
            urls = [
                line.strip()
                for line in str(self.settings.get("apprise_urls")).splitlines()
                if line.strip()
            ]
            if not urls:
                log.debug("no apprise urls configured; skipping %s notification", event)
                return
            delivered = await asyncio.to_thread(self.send, urls, title, body)
            if not delivered:
                log.warning("notification %s for %s was not delivered", event, subject)
                return
            with self.store.session() as s:
                repo.record_notification(s, event, subject, now=self.clock())
        except Exception:  # noqa: BLE001 - never propagate out of a fire-and-forget task
            log.exception("notification %s for %s failed", event, subject)
