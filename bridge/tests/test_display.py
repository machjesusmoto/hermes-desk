"""Tests for display command helpers."""
from __future__ import annotations

import json
import pytest

from hermes_bridge import display


class TestDisplayCommands:
    def test_card(self):
        msg = json.loads(display.card("Error", "Something broke", level="error"))
        assert msg["type"] == "display"
        assert msg["layout"] == "card"
        assert msg["title"] == "Error"
        assert msg["body"] == "Something broke"
        assert msg["level"] == "error"

    def test_status_card(self):
        items = [
            {"label": "CPU", "value": "45%"},
            {"label": "Memory", "value": "8.2GB"},
        ]
        msg = json.loads(display.status_card(items))
        assert msg["type"] == "display"
        assert msg["layout"] == "status"
        assert len(msg["items"]) == 2
        assert msg["items"][0]["label"] == "CPU"

    def test_transcript_user(self):
        msg = json.loads(display.transcript("Hello Hermes", speaker="user"))
        assert msg["type"] == "display"
        assert msg["layout"] == "transcript"
        assert msg["text"] == "Hello Hermes"
        assert msg["speaker"] == "user"

    def test_transcript_assistant(self):
        msg = json.loads(display.transcript("Hi there!", speaker="assistant"))
        assert msg["speaker"] == "assistant"

    def test_image(self):
        msg = json.loads(display.image("base64data", mime="image/png", caption="Chart"))
        assert msg["type"] == "display"
        assert msg["layout"] == "image"
        assert msg["data"] == "base64data"
        assert msg["mime"] == "image/png"
        assert msg["caption"] == "Chart"

    def test_clear(self):
        msg = json.loads(display.clear())
        assert msg["type"] == "display"
        assert msg["layout"] == "clear"
