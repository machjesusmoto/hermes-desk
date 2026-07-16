"""Connected-device registry for proactive /notify pushes."""
from __future__ import annotations

import logging
from typing import Optional, Protocol

log = logging.getLogger(__name__)


class Notifiable(Protocol):
    """Anything the registry can push a notification to (a live WS connection).

    The bridge sends protocol messages as pre-serialized JSON text frames.
    """
    async def send_text(self, message: str) -> None: ...


class Session:
    """A connected device."""

    def __init__(self, device_id: str, session_id: str, connection: Notifiable):
        self.device_id = device_id
        self.session_id = session_id
        self.connection = connection
        self.is_listening = False
        self.is_speaking = False


class SessionRegistry:
    """Tracks connected Tab5 devices."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def register(self, session: Session) -> None:
        self._sessions[session.device_id] = session
        log.info("device registered: %s (session %s)", session.device_id, session.session_id)

    def unregister(self, device_id: str) -> None:
        if device_id in self._sessions:
            del self._sessions[device_id]
            log.info("device unregistered: %s", device_id)

    def get(self, device_id: str) -> Optional[Session]:
        return self._sessions.get(device_id)

    async def any_session(self) -> Optional[Session]:
        """Return any connected session (for /notify)."""
        for session in self._sessions.values():
            return session
        return None

    @property
    def count(self) -> int:
        return len(self._sessions)

    def clear(self) -> None:
        self._sessions.clear()
