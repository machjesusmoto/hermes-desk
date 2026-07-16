"""Tests for protocol message parsing and building."""
import json

import pytest

from hermes_bridge.protocol import (
    parse_inbound, hello_out, stt_out, llm_out,
    tts_start_out, tts_stop_out, status_out, error_out, notify_out,
    HelloIn, ListenStart, ListenStop, Abort,
)


def test_parse_hello():
    msg = parse_inbound(json.dumps({
        "type": "hello", "version": 1,
        "audio_params": {"sample_rate": 16000}, "device_id": "tab5-001"
    }))
    assert isinstance(msg, HelloIn)
    assert msg.version == 1
    assert msg.device_id == "tab5-001"
    assert msg.audio_params["sample_rate"] == 16000


def test_parse_listen_start():
    msg = parse_inbound(json.dumps({"type": "listen", "action": "start"}))
    assert isinstance(msg, ListenStart)


def test_parse_listen_stop():
    msg = parse_inbound(json.dumps({"type": "listen", "action": "stop"}))
    assert isinstance(msg, ListenStop)


def test_parse_abort():
    msg = parse_inbound(json.dumps({"type": "abort"}))
    assert isinstance(msg, Abort)


def test_parse_unknown_type():
    with pytest.raises(ValueError, match="unknown message type"):
        parse_inbound(json.dumps({"type": "unknown"}))


def test_parse_unknown_listen_action():
    with pytest.raises(ValueError, match="unknown listen action"):
        parse_inbound(json.dumps({"type": "listen", "action": "pause"}))


def test_parse_invalid_json():
    with pytest.raises(ValueError):
        parse_inbound("not json")


def test_hello_out():
    data = json.loads(hello_out(1, "abc123", {"sample_rate": 16000}))
    assert data["type"] == "hello"
    assert data["version"] == 1
    assert data["session_id"] == "abc123"


def test_stt_out():
    data = json.loads(stt_out("hello world"))
    assert data["type"] == "stt"
    assert data["text"] == "hello world"
    assert data["is_final"] is True


def test_stt_out_partial():
    data = json.loads(stt_out("hel", is_final=False))
    assert data["is_final"] is False


def test_llm_out():
    data = json.loads(llm_out("I can help with that"))
    assert data["type"] == "llm"
    assert data["text"] == "I can help with that"


def test_tts_start_out():
    data = json.loads(tts_start_out(16000))
    assert data["type"] == "tts"
    assert data["action"] == "start"
    assert data["sample_rate"] == 16000


def test_tts_stop_out():
    data = json.loads(tts_stop_out())
    assert data["type"] == "tts"
    assert data["action"] == "stop"


def test_status_out():
    data = json.loads(status_out("idle"))
    assert data["type"] == "status"
    assert data["state"] == "idle"


def test_error_out():
    data = json.loads(error_out("stt_failed", "connection refused"))
    assert data["type"] == "error"
    assert data["code"] == "stt_failed"
    assert data["message"] == "connection refused"


def test_notify_out():
    data = json.loads(notify_out("Meeting", "in 5 min", "urgent"))
    assert data["type"] == "notify"
    assert data["title"] == "Meeting"
    assert data["body"] == "in 5 min"
    assert data["level"] == "urgent"
