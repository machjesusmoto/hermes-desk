"""Tests for the session registry."""
import pytest

from hermes_bridge.session import Session, SessionRegistry


class FakeConn:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)


@pytest.mark.asyncio
async def test_register_unregister():
    reg = SessionRegistry()
    conn = FakeConn()
    session = Session("device-1", "sess-1", conn)

    reg.register(session)
    assert reg.count == 1
    assert reg.get("device-1") is session

    reg.unregister("device-1")
    assert reg.count == 0
    assert reg.get("device-1") is None


def test_unregister_nonexistent():
    reg = SessionRegistry()
    reg.unregister("nonexistent")  # should not raise
    assert reg.count == 0


@pytest.mark.asyncio
async def test_any_session():
    reg = SessionRegistry()
    assert await reg.any_session() is None

    conn = FakeConn()
    session = Session("device-1", "sess-1", conn)
    reg.register(session)
    result = await reg.any_session()
    assert result is session


def test_clear():
    reg = SessionRegistry()
    reg.register(Session("d1", "s1", FakeConn()))
    reg.register(Session("d2", "s2", FakeConn()))
    assert reg.count == 2
    reg.clear()
    assert reg.count == 0
