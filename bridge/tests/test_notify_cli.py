"""Tests for the notify_cli sender (Hermes cron/webhook -> bridge /notify).

Exercises the integration surface for MOT-82 task 5 against the real FastAPI
app via an httpx MockTransport, so we verify the exact payload shape the
bridge /notify endpoint expects without starting a server.
"""
from __future__ import annotations

import json

import httpx
import pytest

from hermes_bridge.server import create_app
from hermes_bridge.config import BridgeConfig
from hermes_bridge.stt import STTResult
from hermes_bridge.gateway import GatewayResponse
from hermes_bridge.notify_cli import send, send_template, main
from hermes_bridge.cron_templates import DEPLOY_FAILED


class _FakeSTT:
    async def transcribe(self, pcm, sample_rate=16000, language="en"):
        return STTResult(text="x")
    async def aclose(self): pass


class _FakeTTS:
    def __init__(self): self.calls = []
    async def synthesize(self, text):
        self.calls.append(text)
        return b"\x00" * 640
    async def aclose(self): pass


class _FakeGateway:
    async def chat(self, session_id, text):
        return GatewayResponse(text="x")
    async def aclose(self): pass


def _make_app():
    cfg = BridgeConfig()
    app = create_app(cfg)
    app.state.bridge.stt = _FakeSTT()
    app.state.bridge.tts = _FakeTTS()
    app.state.bridge.gateway = _FakeGateway()
    app.state.bridge.pipeline.stt = app.state.bridge.stt
    app.state.bridge.pipeline.tts = app.state.bridge.tts
    app.state.bridge.pipeline.gateway = app.state.bridge.gateway
    return app


def _transport_for(app):
    """An httpx MockTransport that routes POSTs to the in-process FastAPI app."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            status_code=202,
            json={"status": "queued", "notification_id": "fake-1"},
        )
    )
    return transport


class TestSend:
    def test_send_posts_expected_payload(self, monkeypatch):
        captured = {}

        def app_handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["json"] = json.loads(request.content)
            return httpx.Response(202, json={
                "status": "queued", "notification_id": "n-1"})

        real_client = httpx.Client

        def patched_client(*a, **kw):
            kw["transport"] = httpx.MockTransport(app_handler)
            kw.pop("timeout", None)
            return real_client(timeout=5.0, **kw)

        monkeypatch.setattr("hermes_bridge.notify_cli.httpx.Client", patched_client)

        result = send("http://bridge:8765/",
                      title="Deploy failed", body="rollback",
                      priority=3, requires_ack=True, category="deploy")
        assert result["status"] == "queued"
        assert result["notification_id"] == "n-1"

        # URL is normalized (trailing slash stripped, /notify appended)
        assert captured["url"] == "http://bridge:8765/notify"
        p = captured["json"]
        assert p["title"] == "Deploy failed"
        assert p["priority"] == 3
        assert p["requires_ack"] is True
        assert p["category"] == "deploy"
        assert p["source"] == "hermes-cron"
        # Optional fields are omitted when not supplied (no nulls sent)
        assert "notification_id" not in p
        assert "metadata" not in p

    def test_send_raises_on_non_2xx(self, monkeypatch):
        real_client = httpx.Client

        def patched_client(*a, **kw):
            kw["transport"] = httpx.MockTransport(
                lambda req: httpx.Response(500, text="boom"))
            kw.pop("timeout", None)
            return real_client(timeout=5.0, **kw)

        monkeypatch.setattr("hermes_bridge.notify_cli.httpx.Client", patched_client)
        with pytest.raises(httpx.HTTPStatusError):
            send("http://bridge:8765", title="x")


class TestSendTemplate:
    def test_send_template_renders_and_sends(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["json"] = json.loads(request.content)
            return httpx.Response(202, json={
                "status": "delivered", "notification_id": "n-2"})

        real_client = httpx.Client

        def patched_client(*a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            kw.pop("timeout", None)
            return real_client(timeout=5.0, **kw)

        monkeypatch.setattr("hermes_bridge.notify_cli.httpx.Client", patched_client)

        result = send_template("http://bridge:8765", "deploy_failed",
                               service="hermes-desk", branch="main",
                               error="refused", log_url="https://ci/1")
        assert result["status"] == "delivered"
        p = captured["json"]
        assert p["title"] == "Deploy FAILED: hermes-desk"
        assert p["priority"] == DEPLOY_FAILED.priority  # 2
        assert p["requires_ack"] is True
        assert "refused" in p["body"]

    def test_send_template_unknown_name_raises(self):
        with pytest.raises(KeyError):
            send_template("http://bridge:8765", "nope", x=1)


class TestCLI:
    def test_cli_title_mode(self, monkeypatch, capsys):
        def handler(req): return httpx.Response(202, json={
            "status": "queued", "notification_id": "c-1"})
        real_client = httpx.Client

        def patched_client(*a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            kw.pop("timeout", None)
            return real_client(timeout=5.0, **kw)

        monkeypatch.setattr("hermes_bridge.notify_cli.httpx.Client", patched_client)

        rc = main(["--bridge", "http://b:8765", "--title", "Hello",
                   "--priority", "2", "--requires-ack"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["notification_id"] == "c-1"

    def test_cli_template_mode(self, monkeypatch, capsys):
        captured = {}

        def handler(req):
            captured["json"] = json.loads(req.content)
            return httpx.Response(202, json={
                "status": "queued", "notification_id": "c-2"})

        real_client = httpx.Client

        def patched_client(*a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            kw.pop("timeout", None)
            return real_client(timeout=5.0, **kw)

        monkeypatch.setattr("hermes_bridge.notify_cli.httpx.Client", patched_client)

        rc = main(["--template", "deploy_failed", "--var", "service=svc",
                   "--var", "branch=main", "--var", "error=boom",
                   "--var", "log_url=https://ci/9"])
        assert rc == 0
        assert captured["json"]["title"] == "Deploy FAILED: svc"

    def test_cli_missing_title_errors(self, capsys):
        with pytest.raises(SystemExit) as ex:
            main(["--bridge", "http://b:8765"])
        assert ex.value.code == 2

    def test_cli_bad_var_format(self, capsys):
        with pytest.raises(SystemExit):
            main(["--template", "deploy_failed", "--var", "nope"])
