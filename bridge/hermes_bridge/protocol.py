"""Wire protocol — JSON control messages + PCM binary framing.

Protocol v1. See docs/PROTOCOL.md for the full specification.

Control messages are JSON text frames. Audio is raw PCM binary frames.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Optional


# ---------------------------------------------------------------------------
# Inbound (Tab5 -> Bridge)
# ---------------------------------------------------------------------------

@dataclass
class HelloIn:
    version: int
    audio_params: dict  # {sample_rate, bits, channels}
    device_id: str
    type: Literal["hello"] = "hello"


@dataclass
class ListenStart:
    type: Literal["listen"] = "listen"
    action: Literal["start"] = "start"


@dataclass
class ListenStop:
    type: Literal["listen"] = "listen"
    action: Literal["stop"] = "stop"


@dataclass
class Abort:
    type: Literal["abort"] = "abort"


@dataclass
class NotifyAck:
    notification_id: str
    type: Literal["notify_ack"] = "notify_ack"


InboundMessage = HelloIn | ListenStart | ListenStop | Abort | NotifyAck


def parse_inbound(raw: str) -> InboundMessage:
    """Parse a text frame from the device. Raises ValueError on bad input."""
    data = json.loads(raw)
    msg_type = data.get("type")
    if msg_type == "hello":
        return HelloIn(
            version=data.get("version", 1),
            audio_params=data.get("audio_params", {}),
            device_id=data.get("device_id", "unknown"),
        )
    if msg_type == "listen":
        action = data.get("action", "start")
        if action == "start":
            return ListenStart()
        if action == "stop":
            return ListenStop()
        raise ValueError(f"unknown listen action: {action}")
    if msg_type == "abort":
        return Abort()
    if msg_type == "notify_ack":
        nid = data.get("notification_id", "")
        if not nid:
            raise ValueError("notify_ack requires notification_id")
        return NotifyAck(notification_id=nid)
    raise ValueError(f"unknown message type: {msg_type}")


# ---------------------------------------------------------------------------
# Outbound (Bridge -> Tab5)
# ---------------------------------------------------------------------------

def hello_out(version: int, session_id: str, audio_params: dict) -> str:
    return json.dumps({
        "type": "hello",
        "version": version,
        "session_id": session_id,
        "audio_params": audio_params,
    })


def stt_out(text: str, is_final: bool = True) -> str:
    return json.dumps({
        "type": "stt",
        "text": text,
        "is_final": is_final,
    })


def llm_out(text: str) -> str:
    return json.dumps({
        "type": "llm",
        "text": text,
    })


def tts_start_out(sample_rate: int = 16000) -> str:
    return json.dumps({
        "type": "tts",
        "action": "start",
        "sample_rate": sample_rate,
    })


def tts_stop_out() -> str:
    return json.dumps({
        "type": "tts",
        "action": "stop",
    })


def status_out(state: str) -> str:
    return json.dumps({
        "type": "status",
        "state": state,
    })


def error_out(code: str, message: str = "") -> str:
    return json.dumps({
        "type": "error",
        "code": code,
        "message": message,
    })


def notify_out(
    title: str,
    body: str = "",
    level: str = "info",
    notification_id: str = "",
    priority: int = 1,
    requires_ack: bool = False,
    category: str = "general",
    display_type: str = "card",
) -> str:
    return json.dumps({
        "type": "notify",
        "title": title,
        "body": body,
        "level": level,
        "notification_id": notification_id,
        "priority": priority,
        "requires_ack": requires_ack,
        "category": category,
        "display_type": display_type,
    })
