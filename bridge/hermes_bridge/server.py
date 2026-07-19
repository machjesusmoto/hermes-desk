"""FastAPI server — /ws (Tab5), /health, /notify + CLI entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from typing import Optional

from .config import BridgeConfig
from . import protocol as p
from .audio import chunk_pcm
from .stt import STTClient
from .tts import TTSClient
from .gateway import GatewayClient
from .pipeline import Pipeline
from .session import Session, SessionRegistry
from .notification import Notification, NotificationQueue, Priority, QuietHours

log = logging.getLogger(__name__)


def _announcement_text(notif: Notification) -> str:
    """Build a short spoken announcement for a high-priority notification.

    Concise and voice-friendly: leads with an urgency cue, then the title.
    The body is included only if it is short enough to read aloud once.
    """
    prefix = "Heads up." if notif.priority >= Priority.URGENT else "Notification."
    text = f"{prefix} {notif.title}."
    body = (notif.body or "").strip()
    # Keep the spoken cue short — long bodies stay on-screen only.
    if body and len(body) <= 120:
        text += f" {body}"
    return text


class BridgeState:
    """Holds all runtime state, wired at startup."""

    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
        # Notification queue
        qh_cfg = cfg.notification.quiet_hours
        quiet = QuietHours(
            enabled=qh_cfg.enabled,
            start_hour=qh_cfg.start_hour,
            end_hour=qh_cfg.end_hour,
        )
        self.notifications = NotificationQueue(
            quiet_hours=quiet,
            max_history=cfg.notification.max_history,
            ack_timeout=cfg.notification.ack_timeout,
        )
        self.stt = STTClient(
            base_url=cfg.stt.base_url, model=cfg.stt.model,
            api_key=cfg.stt.api_key, timeout=cfg.stt.timeout,
        )
        self.tts = TTSClient(
            base_url=cfg.tts.base_url, voice=cfg.tts.voice,
            model=cfg.tts.model, api_key=cfg.tts.api_key,
            response_format=cfg.tts.response_format, timeout=cfg.tts.timeout,
        )
        self.gateway = GatewayClient(
            base_url=cfg.gateway.base_url, chat_path=cfg.gateway.chat_path,
            api_key=cfg.gateway.api_key, model=cfg.gateway.model,
            session_id=cfg.gateway.session_id,
            system_prompt=cfg.gateway.system_prompt,
            timeout=cfg.gateway.timeout,
        )
        self.pipeline = Pipeline(self.stt, self.tts, self.gateway, cfg.audio)
        self.registry = SessionRegistry()

    async def deliver_notification(self, notif: Notification,
                                   session: Optional[Session] = None) -> bool:
        """Push a notification to a connected Tab5.

        Sends the notify control frame, and — for HIGH/URGENT notifications —
        follows it with a short TTS announcement streamed as PCM (the same
        framing the voice pipeline uses: tts start -> PCM chunks -> tts stop).
        Marks the notification delivered in the queue and returns True on
        success. Failures (no session, send error) are logged and returned as
        False; they are not raised so /notify stays best-effort.
        """
        from . import protocol as p

        if session is None:
            session = await self.registry.any_session()
        if session is None:
            log.info("notify %s: no device connected — queued only", notif.id)
            return False

        # 1. Notify control frame (always; drives the on-screen card).
        try:
            await session.connection.send_text(p.notify_out(
                title=notif.title, body=notif.body, level=notif.level,
                notification_id=notif.id, priority=int(notif.priority),
                requires_ack=notif.requires_ack, category=notif.category,
                display_type=notif.display_type,
            ))
        except Exception as exc:
            log.warning("notify %s: control frame failed: %s", notif.id, exc)
            return False

        self.notifications.delivered(notif)

        # 2. Audio cue for high-priority notifications (task 4).
        if notif.priority >= Priority.HIGH:
            await self._announce(notif, session.connection)
        return True

    async def _announce(self, notif: Notification, writer) -> None:
        """Synthesize + stream a short TTS announcement for a high-prio notif.

        Mirrors Pipeline.run_turn's TTS streaming so the firmware audio path is
        identical to a voice reply: tts start -> 20 ms PCM chunks -> tts stop.
        Failures are logged and swallowed — the on-screen card already showed.
        """
        from . import protocol as p

        text = _announcement_text(notif)
        try:
            pcm = await self.tts.synthesize(text)
        except Exception as exc:
            log.warning("notify %s: TTS cue failed: %s", notif.id, exc)
            return
        try:
            await writer.send_text(p.tts_start_out(
                sample_rate=self.cfg.audio.sample_rate))
            for chunk in chunk_pcm(pcm, self.cfg.audio.frame_bytes):
                await writer.send_bytes(chunk)
            await writer.send_text(p.tts_stop_out())
            log.info("notify %s: announced %d bytes PCM (%r)",
                     notif.id, len(pcm), text[:60])
        except Exception as exc:
            log.warning("notify %s: announcement stream failed: %s", notif.id, exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state: BridgeState = app.state.bridge
    log.info("bridge starting — STT=%s TTS=%s gateway=%s",
             state.cfg.stt.base_url, state.cfg.tts.base_url, state.cfg.gateway.base_url)
    yield
    await state.stt.aclose()
    await state.tts.aclose()
    await state.gateway.aclose()
    log.info("bridge stopped")


def create_app(cfg: BridgeConfig) -> FastAPI:
    app = FastAPI(title="Hermes Desk Bridge", lifespan=lifespan)
    bridge = BridgeState(cfg)
    app.state.bridge = bridge

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "devices": bridge.registry.count,
            "stt": bridge.cfg.stt.base_url,
            "tts": bridge.cfg.tts.base_url,
            "gateway": bridge.cfg.gateway.base_url,
        }

    @app.post("/notify")
    async def notify(request_body: dict):
        """Enqueue a proactive notification for delivery to the Tab5."""
        import uuid as _uuid
        notif = Notification(
            id=request_body.get("notification_id", str(_uuid.uuid4())[:8]),
            title=request_body.get("title", "Notification"),
            body=request_body.get("body", ""),
            level=request_body.get("level", "info"),
            priority=Priority(request_body.get("priority", 1)),
            requires_ack=request_body.get("requires_ack", False),
            category=request_body.get("category", "general"),
            display_type=request_body.get("display_type", "card"),
            source=request_body.get("source", "hermes"),
            metadata=request_body.get("metadata", {}),
        )
        was_enqueued = await bridge.notifications.enqueue(notif)
        if not was_enqueued:
            return JSONResponse(
                {"status": "suppressed", "reason": "quiet_hours"},
                status_code=status.HTTP_202_ACCEPTED,
            )
        # Try immediate delivery if a device is connected. High/urgent
        # notifications also get a short TTS announcement (see deliver_notification).
        delivered = await bridge.deliver_notification(notif)
        status_msg = "delivered" if delivered else "queued"
        return JSONResponse(
            {"status": status_msg, "notification_id": notif.id},
            status_code=status.HTTP_202_ACCEPTED,
        )

    @app.get("/notify/history")
    async def notify_history():
        """Return notification history."""
        history = bridge.notifications.history
        return {
            "count": len(history),
            "notifications": [
                {
                    "id": n.id, "title": n.title, "body": n.body,
                    "level": n.level, "priority": n.priority.name,
                    "category": n.category, "delivered_at": n.delivered_at,
                    "acked_at": n.acked_at,
                }
                for n in history[-20:]
            ],
        }

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()

        # Auth check
        if bridge.cfg.server.token:
            token = ws.query_params.get("token", "")
            auth_header = ws.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
            if token != bridge.cfg.server.token:
                await ws.close(code=1008, reason="unauthorized")
                return

        session_id = str(uuid.uuid4())[:8]
        device_id = "unknown"
        pcm_buffer = bytearray()
        current_session: Session | None = None

        try:
            while True:
                msg = await ws.receive()

                if msg.get("type") == "websocket.receive":
                    # Text frame = JSON control message
                    if "text" in msg:
                        try:
                            parsed = p.parse_inbound(msg["text"])
                        except ValueError as exc:
                            await ws.send_text(p.error_out("bad_message", str(exc)))
                            continue

                        if isinstance(parsed, p.HelloIn):
                            device_id = parsed.device_id
                            current_session = Session(device_id, session_id, ws)
                            bridge.registry.register(current_session)
                            await ws.send_text(p.hello_out(1, session_id, {
                                "sample_rate": bridge.cfg.audio.sample_rate,
                                "bits": bridge.cfg.audio.bits,
                                "channels": bridge.cfg.audio.channels,
                            }))
                            await ws.send_text(p.status_out("idle"))

                        elif isinstance(parsed, p.ListenStart):
                            pcm_buffer.clear()
                            if current_session:
                                current_session.is_listening = True
                            await ws.send_text(p.status_out("listening"))

                        elif isinstance(parsed, p.ListenStop):
                            if current_session:
                                current_session.is_listening = False
                            await ws.send_text(p.status_out("processing"))
                            # Run the voice turn
                            try:
                                audio = bytes(pcm_buffer)
                                await bridge.pipeline.run_turn(audio, session_id, ws)
                            except Exception as exc:
                                log.error("pipeline error: %s", exc)
                                code = "stt_failed" if "stt" in str(type(exc).__name__).lower() else "gateway_failed"
                                await ws.send_text(p.error_out(code, str(exc)))
                                await ws.send_text(p.status_out("idle"))

                        elif isinstance(parsed, p.Abort):
                            bridge.pipeline.request_abort()

                        elif isinstance(parsed, p.NotifyAck):
                            bridge.notifications.ack(parsed.notification_id)
                            await ws.send_text(json.dumps({
                                "type": "ack_received",
                                "notification_id": parsed.notification_id,
                            }))

                    # Binary frame = PCM audio chunk
                    elif "bytes" in msg:
                        pcm_buffer.extend(msg["bytes"])

        except WebSocketDisconnect:
            log.info("device disconnected: %s", device_id)
        except Exception as exc:
            log.error("ws error: %s", exc)
        finally:
            if current_session:
                bridge.registry.unregister(device_id)

    return app


def main():
    parser = argparse.ArgumentParser(description="Hermes Desk Bridge")
    parser.add_argument("--config", "-c", default=None, help="Path to config.yaml")
    parser.add_argument("--host", default=None, help="Override server host")
    parser.add_argument("--port", type=int, default=None, help="Override server port")
    args = parser.parse_args()

    cfg = BridgeConfig.load(args.config)
    if args.host:
        cfg.server.host = args.host
    if args.port:
        cfg.server.port = args.port

    # Setup logging
    log_level = getattr(logging, cfg.log.level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    app = create_app(cfg)
    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level=cfg.log.level.lower())


if __name__ == "__main__":
    main()
