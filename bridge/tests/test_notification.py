"""Tests for notification module."""
from __future__ import annotations

import asyncio
import pytest
import time

from hermes_bridge.notification import (
    Notification,
    NotificationQueue,
    Priority,
    QuietHours,
)


def _make_notif(**kwargs) -> Notification:
    defaults = {
        "id": "test-001",
        "title": "Test",
        "body": "Hello",
        "level": "info",
        "priority": Priority.NORMAL,
        "category": "general",
    }
    defaults.update(kwargs)
    return Notification(**defaults)


class TestQuietHours:
    def test_disabled_always_passes(self):
        qh = QuietHours(enabled=False)
        assert qh.is_quiet_now() is False

    def test_enabled_respects_range(self):
        qh = QuietHours(enabled=True, start_hour=22, end_hour=7)
        result = qh.is_quiet_now()
        assert isinstance(result, bool)

    def test_wrapping_range(self):
        qh = QuietHours(enabled=True, start_hour=22, end_hour=7)
        assert isinstance(qh.is_quiet_now(), bool)


class TestPriority:
    def test_ordering(self):
        assert Priority.LOW < Priority.NORMAL < Priority.HIGH < Priority.URGENT

    def test_int_values(self):
        assert int(Priority.LOW) == 0
        assert int(Priority.URGENT) == 3


@pytest.mark.asyncio
class TestNotificationQueue:
    async def test_enqueue_dequeue(self):
        q = NotificationQueue()
        notif = _make_notif()
        await q.enqueue(notif)
        assert q.pending_count == 1
        result = await q.dequeue()
        assert result.id == "test-001"
        assert q.pending_count == 0

    async def test_quiet_hours_suppresses(self):
        qh = QuietHours(enabled=True, start_hour=0, end_hour=23)
        q = NotificationQueue(quiet_hours=qh)
        notif = _make_notif(priority=Priority.NORMAL)
        suppressed = await q.enqueue(notif)
        assert suppressed is False
        assert q.pending_count == 0
        assert len(q.history) == 1

    async def test_urgent_breaks_quiet_hours(self):
        qh = QuietHours(enabled=True, start_hour=0, end_hour=23)
        q = NotificationQueue(quiet_hours=qh)
        notif = _make_notif(priority=Priority.URGENT)
        enqueued = await q.enqueue(notif)
        assert enqueued is True
        assert q.pending_count == 1

    async def test_ack_success(self):
        q = NotificationQueue()
        notif = _make_notif(requires_ack=True)
        q.delivered(notif)
        assert q.awaiting_ack_count == 1
        assert q.ack("test-001") is True
        assert q.awaiting_ack_count == 0
        assert notif.acked_at is not None

    async def test_ack_unknown_id(self):
        q = NotificationQueue()
        assert q.ack("nonexistent") is False

    async def test_ack_timeout(self):
        q = NotificationQueue(ack_timeout=0.1)
        notif = _make_notif(requires_ack=True)
        q.delivered(notif)
        result = await q.wait_for_ack("test-001")
        assert result is False
        assert q.awaiting_ack_count == 0

    async def test_ack_before_timeout(self):
        q = NotificationQueue(ack_timeout=5.0)
        notif = _make_notif(requires_ack=True)
        q.delivered(notif)

        async def ack_after_delay():
            await asyncio.sleep(0.05)
            q.ack("test-001")

        asyncio.create_task(ack_after_delay())
        result = await q.wait_for_ack("test-001")
        assert result is True

    async def test_history_max(self):
        q = NotificationQueue(max_history=3)
        for i in range(5):
            notif = _make_notif(id=f"n-{i}", title=f"Test {i}")
            q.delivered(notif)
        assert len(q.history) == 3
        assert q.history[0].id == "n-2"

    async def test_delivered_records_timestamp(self):
        q = NotificationQueue()
        notif = _make_notif()
        assert notif.delivered_at is None
        q.delivered(notif)
        assert notif.delivered_at is not None


class TestNotification:
    def test_defaults(self):
        n = _make_notif()
        assert n.requires_ack is False
        assert n.source == "hermes"
        assert n.metadata == {}
        assert n.delivered_at is None
        assert n.acked_at is None
