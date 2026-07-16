"""Tests for the FastAPI server — WebSocket, /health, /notify."""
import json

import pytest
from fastapi.testclient import TestClient

from hermes_bridge.server import create_app, BridgeState
from hermes_bridge.config import BridgeConfig
from hermes_bridge.stt import STTResult
from hermes_bridge.gateway import GatewayResponse
from hermes_bridge.audio import pcm_to_wav


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSTT:
    async def transcribe(self, pcm, sample_rate=16000, language="en"):
        return STTResult(text="hello world")
    async def aclose(self):
        pass


class FakeTTS:
    async def synthesize(self, text):
        return b"\x00" * 640  # 1 frame of silence
    async def aclose(self):
        pass


class FakeGateway:
    async def chat(self, session_id, text):
        return GatewayResponse(text="echo: " + text)
    async def aclose(self):
        pass


def _make_app(token: str = ""):
    cfg = BridgeConfig()
    cfg.server.token = token
    app = create_app(cfg)
    # Replace clients AND rewire the pipeline to use them
    app.state.bridge.stt = FakeSTT()
    app.state.bridge.tts = FakeTTS()
    app.state.bridge.gateway = FakeGateway()
    app.state.bridge.pipeline.stt = app.state.bridge.stt
    app.state.bridge.pipeline.tts = app.state.bridge.tts
    app.state.bridge.pipeline.gateway = app.state.bridge.gateway
    return app


def _hello():
    return json.dumps({
        "type": "hello", "version": 1,
        "audio_params": {"sample_rate": 16000, "bits": 16, "channels": 1},
        "device_id": "test-tab5",
    })


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health():
    app = _make_app()
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["devices"] == 0


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

def test_ws_hello():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "hello"
        assert "session_id" in msg
        msg2 = json.loads(ws.receive_text())
        assert msg2["type"] == "status"
        assert msg2["state"] == "idle"


def test_ws_auth_valid():
    app = _make_app(token="secret123")
    client = TestClient(app)
    with client.websocket_connect("/ws?token=secret123") as ws:
        ws.send_text(_hello())
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "hello"


def test_ws_auth_invalid():
    app = _make_app(token="secret123")
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=wrong") as ws:
            ws.receive_text()  # should fail


def test_ws_no_auth_required():
    app = _make_app(token="")
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "hello"


def test_ws_full_turn():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        # Handshake
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        # Start listening
        ws.send_text(json.dumps({"type": "listen", "action": "start"}))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "status"
        assert msg["state"] == "listening"

        # Send PCM audio (binary)
        ws.send_bytes(b"\x00" * 3200)

        # Stop listening
        ws.send_text(json.dumps({"type": "listen", "action": "stop"}))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "status"
        assert msg["state"] == "processing"

        # Collect pipeline responses
        messages = []
        while True:
            try:
                msg = ws.receive()
                if "text" in msg:
                    parsed = json.loads(msg["text"])
                    messages.append(parsed)
                    if parsed.get("type") == "status" and parsed.get("state") == "idle":
                        break
            except Exception:
                break

        types = [m["type"] for m in messages]
        assert "stt" in types
        assert "llm" in types
        assert "tts" in types
        assert "status" in types

        # Verify STT content
        stt_msg = next(m for m in messages if m["type"] == "stt")
        assert stt_msg["text"] == "hello world"

        # Verify LLM content
        llm_msg = next(m for m in messages if m["type"] == "llm")
        assert "echo" in llm_msg["text"]


def test_ws_bad_message():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        ws.send_text("not valid json")
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error"
        assert msg["code"] == "bad_message"


def test_ws_health_after_connect():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        r = client.get("/health")
        assert r.json()["devices"] == 1


# ---------------------------------------------------------------------------
# /notify
# ---------------------------------------------------------------------------

def test_notify_no_device():
    app = _make_app()
    client = TestClient(app)
    r = client.post("/notify", json={"title": "Test", "body": "hello"})
    assert r.status_code == 503


def test_notify_with_device():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        r = client.post("/notify", json={"title": "Meeting", "body": "in 5 min", "level": "urgent"})
        assert r.status_code == 202

        # Device should receive the notify frame
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "notify"
        assert msg["title"] == "Meeting"
        assert msg["level"] == "urgent"
