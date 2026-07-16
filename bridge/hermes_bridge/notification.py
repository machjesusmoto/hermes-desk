"""Proactive notification system — queue, priority, delivery, ack, quiet hours.

Notifications flow from Hermes (via /notify or /admin/notifications/send)
to the connected Tab5. Each notification has a priority level, optional
acknowledgment requirement, and display configuration.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine, Optional

log = logging.getLogger(__name__)


class Priority(IntEnum):
    """Notification priority — higher value = more urgent."""
    LOW = 0       # Background ticker, no chime
    NORMAL = 1    # Standard notification, single chime
    HIGH = 2      # Important, persistent until ack
    URGENT = 3    # Breaks quiet hours, persistent + repeated chime


@dataclass
class Notification:
    """A single proactive notification."""
    id: str
    title: str
    body: str = ""
    level: str = "info"            # info, warning, error, success
    priority: Priority = Priority.NORMAL
    requires_ack: bool = False     # Tab5 must send ack back
    category: str = "general"      # calendar, linear, deploy, reminder
    display_type: str = "card"     # card, chime, status, dashboard
    source: str = "hermes"         # originating system
    created_at: float = field(default_factory=time.time)
    delivered_at: Optional[float] = None
    acked_at: Optional[float] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class QuietHours:
    """Quiet hours configuration — suppresses non-urgent notifications."""
    enabled: bool = False
    start_hour: int = 22   # 10 PM
    end_hour: int = 7      # 7 AM

    def is_quiet_now(self) -> bool:
        if not self.enabled:
            return False
        import datetime
        now = datetime.datetime.now().hour
        if self.start_hour > self.end_hour:
            return now >= self.start_hour or now < self.end_hour
        return self.start_hour <= now < self.end_hour


class NotificationQueue:
    """Manages notification lifecycle: enqueue, deliver, ack, expire."""

    def __init__(
        self,
        quiet_hours: Optional[QuietHours] = None,
        max_history: int = 100,
        ack_timeout: float = 30.0,
    ):
        self.quiet_hours = quiet_hours or QuietHours()
        self.max_history = max_history
        self.ack_timeout = ack_timeout
        self._pending: asyncio.Queue[Notification] = asyncio.Queue()
        self._history: list[Notification] = []
        self._awaiting_ack: dict[str, Notification] = {}
        self._ack_events: dict[str, asyncio.Event] = {}

    async def enqueue(self, notification: Notification) -> bool:
        """Enqueue a notification. Returns False if suppressed by quiet hours."""
        if self.quiet_hours.is_quiet_now() and notification.priority < Priority.URGENT:
            log.info("notification suppressed by quiet hours: %s", notification.title)
            self._record(notification)
            return False

        await self._pending.put(notification)
        log.info("enqueued [%s] %s (priority=%s)",
                 notification.category, notification.title, notification.priority.name)
        return True

    async def dequeue(self) -> Notification:
        """Wait for and return the next notification to deliver."""
        return await self._pending.get()

    def delivered(self, notification: Notification) -> None:
        """Mark a notification as delivered to the device."""
        notification.delivered_at = time.time()
        if notification.requires_ack:
            self._awaiting_ack[notification.id] = notification
            self._ack_events[notification.id] = asyncio.Event()
        self._record(notification)

    def ack(self, notification_id: str) -> bool:
        """Acknowledge a notification. Returns True if found."""
        notif = self._awaiting_ack.pop(notification_id, None)
        if notif:
            notif.acked_at = time.time()
            event = self._ack_events.pop(notification_id, None)
            if event:
                event.set()
            log.info("ack received for %s", notification_id)
            return True
        return False

    async def wait_for_ack(self, notification_id: str) -> bool:
        """Wait for ack with timeout. Returns True if acked."""
        event = self._ack_events.get(notification_id)
        if not event:
            return False
        try:
            await asyncio.wait_for(event.wait(), timeout=self.ack_timeout)
            return True
        except asyncio.TimeoutError:
            self._awaiting_ack.pop(notification_id, None)
            self._ack_events.pop(notification_id, None)
            log.warning("ack timeout for %s", notification_id)
            return False

    @property
    def pending_count(self) -> int:
        return self._pending.qsize()

    @property
    def awaiting_ack_count(self) -> int:
        return len(self._awaiting_ack)

    @property
    def history(self) -> list[Notification]:
        return list(self._history)

    def _record(self, notification: Notification) -> None:
        self._history.append(notification)
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]
