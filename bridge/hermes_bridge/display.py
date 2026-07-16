"""Display commands — structured payloads for the Tab5 screen.

The Tab5 firmware (LVGL) renders these into different screen layouts.
The bridge sends these over the WebSocket text channel as JSON.
"""
from __future__ import annotations

import json
from typing import Any


def card(title: str, body: str, level: str = "info", notification_id: str = "") -> str:
    """Simple text card overlay."""
    return json.dumps({
        "type": "display",
        "layout": "card",
        "title": title,
        "body": body,
        "level": level,
        "notification_id": notification_id,
    })


def status_card(items: list[dict[str, str]]) -> str:
    """Dashboard-style status display with key-value pairs.

    items: [{"label": "Weather", "value": "72°F Sunny"}, ...]
    """
    return json.dumps({
        "type": "display",
        "layout": "status",
        "items": items,
    })


def transcript(text: str, speaker: str = "user") -> str:
    """Live voice transcript line."""
    return json.dumps({
        "type": "display",
        "layout": "transcript",
        "text": text,
        "speaker": speaker,  # user | assistant
    })


def image(data: str, mime: str = "image/png", caption: str = "") -> str:
    """Render an image on the Tab5 screen.

    data: base64-encoded image bytes
    mime: image/png, image/jpeg, etc.
    """
    return json.dumps({
        "type": "display",
        "layout": "image",
        "data": data,
        "mime": mime,
        "caption": caption,
    })


def clear() -> str:
    """Clear the display back to idle state."""
    return json.dumps({
        "type": "display",
        "layout": "clear",
    })
