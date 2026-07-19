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
    """Records every synthesized string so tests can assert on audio cues."""
    def __init__(self):
        self.calls: list[str] = []
        self.pcm = pcm_to_wav(b"\x00" * 1280)  # 2 frames of silence, WAV-wrapped
    async def synthesize(self, text):
        self.calls.append(text)
        # Return raw PCM (the real client strips the WAV header); exercise both
        # shapes by returning a fixed PCM blob the bridge can chunk.
        return b"\x00" * 1280
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
    assert r.status_code == 202


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


def _connect(client):
    ws = client.websocket_connect("/ws").__enter__()
    ws.send_text(_hello())
    ws.receive_text()  # hello
    ws.receive_text()  # status: idle
    return ws


def _drain_notify(ws):
    """Read the notify frame and any TTS framing that follows it."""
    msg = json.loads(ws.receive_text())
    assert msg["type"] == "notify"
    return msg


def _drain_until(ws, want_type: str, want_action: str | None = None):
    """Read mixed text/binary frames until a JSON frame of the given type.

    The Starlette WS test session surfaces text and binary frames through the
    same receive() stream, so when the bridge interleaves PCM chunks between
    tts start/stop we must skip the binary frames to reach the next text one.
    Returns the matching JSON dict; raises on unexpected close.
    """
    while True:
        msg = ws.receive()
        if "text" in msg:
            data = json.loads(msg["text"])
            if data.get("type") == want_type and (
                    want_action is None or data.get("action") == want_action):
                return data
        # binary PCM chunks are skipped


def _collect_pcm(ws, until_action: str = "stop") -> bytes:
    """Collect binary PCM until the matching tts action frame arrives."""
    out = bytearray()
    while True:
        msg = ws.receive()
        if "bytes" in msg:
            out += msg["bytes"]
        elif "text" in msg:
            data = json.loads(msg["text"])
            if data.get("type") == "tts" and data.get("action") == until_action:
                return bytes(out)


def test_notify_high_priority_triggers_tts_announcement():
    """Task 4: HIGH/URGENT notifications get a spoken cue streamed as PCM."""
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        r = client.post("/notify", json={
            "title": "Deploy failed", "body": "prod rollback",
            "priority": 3,  # URGENT
        })
        assert r.status_code == 202
        assert r.json()["status"] == "delivered"

        # 1. notify control frame
        notify = json.loads(ws.receive_text())
        assert notify["type"] == "notify"
        assert notify["priority"] == 3
        assert notify["notification_id"]  # bridge assigns an id

        # 2. tts start
        tts_start = _drain_until(ws, "tts", "start")
        assert tts_start["type"] == "tts"
        assert tts_start["action"] == "start"

        # 3. one or more binary PCM chunks (announcement audio)
        pcm = _collect_pcm(ws, until_action="stop")
        assert len(pcm) > 0

        # 4. tts stop already consumed by _collect_pcm; verify it was sent by
        #    confirming the synthesized announcement mentions the title.
        tts = app.state.bridge.tts
        assert len(tts.calls) == 1
        assert "Deploy failed" in tts.calls[0]


def test_notify_normal_priority_no_tts_announcement():
    """NORMAL/LOW notifications show the card only — no spoken cue."""
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        r = client.post("/notify", json={
            "title": "Build passed", "priority": 1,  # NORMAL
        })
        assert r.status_code == 202
        assert r.json()["status"] == "delivered"

        notify = json.loads(ws.receive_text())
        assert notify["type"] == "notify"
        assert notify["priority"] == 1

        # No TTS framing should follow for a NORMAL notification. The next
        # readable frame would block — assert TTS was never synthesized.
        assert app.state.bridge.tts.calls == []


def test_notify_delivered_status_when_device_connected():
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()
        ws.receive_text()
        r = client.post("/notify", json={"title": "x", "priority": 1})
        assert r.json()["status"] == "delivered"


def test_notify_queued_when_no_device():
    app = _make_app()
    client = TestClient(app)
    # No WS connection
    r = client.post("/notify", json={"title": "x", "priority": 1})
    assert r.status_code == 202
    assert r.json()["status"] == "queued"


def test_notify_ack_round_trip():
    """Task 3闭环: device sends notify_ack -> bridge records the ack."""
    app = _make_app()
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_text(_hello())
        ws.receive_text()  # hello
        ws.receive_text()  # status: idle

        # requires_ack so the queue tracks it as awaiting ack. Use NORMAL
        # priority so no TTS announcement interleaves with the ack reply
        # (the audio cue is exercised in its own test above).
        r = client.post("/notify", json={
            "title": "Assigned", "priority": 1, "requires_ack": True,
        })
        nid = r.json()["notification_id"]
        assert app.state.bridge.notifications.awaiting_ack_count == 1

        notify = json.loads(ws.receive_text())
        assert notify["requires_ack"] is True
        assert notify["notification_id"] == nid

        # Device acks (e.g. from a dismiss button press)
        ws.send_text(json.dumps({"type": "notify_ack", "notification_id": nid}))
        ack_reply = json.loads(ws.receive_text())
        assert ack_reply["type"] == "ack_received"
        assert ack_reply["notification_id"] == nid

        assert app.state.bridge.notifications.awaiting_ack_count == 0
        hist = app.state.bridge.notifications.history
        assert any(n.id == nid and n.acked_at is not None for n in hist)
