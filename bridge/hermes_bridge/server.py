"""FastAPI server — /ws (Tab5), /health, /notify + CLI entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse

from .config import BridgeConfig
from . import protocol as p
from .stt import STTClient
from .tts import TTSClient
from .gateway import GatewayClient
from .pipeline import Pipeline
from .session import Session, SessionRegistry

log = logging.getLogger(__name__)


class BridgeState:
    """Holds all runtime state, wired at startup."""

    def __init__(self, cfg: BridgeConfig):
        self.cfg = cfg
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
        """Push a proactive notification to the connected Tab5."""
        title = request_body.get("title", "Notification")
        body = request_body.get("body", "")
        level = request_body.get("level", "info")

        session = await bridge.registry.any_session()
        if session is None:
            return JSONResponse(
                {"error": "no device connected"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            await session.connection.send_text(p.notify_out(title, body, level))
        except Exception as exc:
            log.warning("failed to push notify: %s", exc)
            return JSONResponse(
                {"error": "push failed"},
                status_code=status.HTTP_502_BAD_GATEWAY,
            )
        return JSONResponse({"status": "queued"}, status_code=status.HTTP_202_ACCEPTED)

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
