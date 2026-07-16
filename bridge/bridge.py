"""
Hermes Desk Bridge Service

WebSocket server for M5Stack Tab5 connections.
Handles audio streaming, STT, TTS, and Hermes gateway integration.

Architecture:
    Tab5 (ESP32-P4)  ←WebSocket→  Bridge (this service)  ←HTTP→  Hermes Gateway API
"""

import asyncio
import json
import logging
import os
from typing import Optional

import aiohttp
from aiohttp import web

logger = logging.getLogger("hermes-desk-bridge")

# Configuration from environment
HERMES_GATEWAY_URL = os.getenv("HERMES_GATEWAY_URL", "http://host.docker.internal:8642")
HERMES_GATEWAY_KEY = os.getenv("HERMES_GATEWAY_KEY", "")
STT_URL = os.getenv("STT_URL", "http://10.0.2.60:11436")
TTS_URL = os.getenv("TTS_URL", "http://10.0.2.61:11438")
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-Neural2-F")
SESSION_ID = os.getenv("SESSION_ID", "hermes-desk")
BRIDGE_HOST = os.getenv("BRIDGE_HOST", "0.0.0.0")
BRIDGE_PORT = int(os.getenv("BRIDGE_PORT", "8080"))
AUDIO_SAMPLE_RATE = int(os.getenv("AUDIO_SAMPLE_RATE", "16000"))


class HermesGatewayClient:
    """Client for the Hermes gateway OpenAI-compatible API."""

    def __init__(self, base_url: str, api_key: str, session_id: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def chat(self, message: str) -> str:
        """Send a message to Hermes and get the full response text."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": self.session_id,
            "X-Hermes-Source": "desk",
        }
        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": message}],
            "stream": False,
        }
        async with session.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["choices"][0]["message"]["content"]

    async def chat_stream(self, message: str):
        """Send a message to Hermes and yield response chunks via SSE."""
        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Hermes-Session-Id": self.session_id,
            "X-Hermes-Source": "desk",
        }
        payload = {
            "model": "default",
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        }
        async with session.post(
            f"{self.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.content:
                line = line.decode("utf-8").strip()
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        chunk = json.loads(line[6:])
                        delta = chunk["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class STTClient:
    """Client for STT service (Nemotron 0.6B on GB10 fd05)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe audio bytes to text."""
        session = await self._get_session()
        # TODO: Implement audio upload to STT service
        # The exact format depends on the Nemotron STT API
        # OpenAI Whisper-compatible endpoint: POST /v1/audio/transcriptions
        raise NotImplementedError("STT integration pending — MOT-79")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class TTSClient:
    """Client for TTS service (Google Neural2-F on GB10 39e5)."""

    def __init__(self, base_url: str, voice: str = "en-US-Neural2-F"):
        self.base_url = base_url.rstrip("/")
        self.voice = voice
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def synthesize(self, text: str) -> bytes:
        """Convert text to audio bytes."""
        session = await self._get_session()
        headers = {"Content-Type": "application/json"}
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": self.voice,
        }
        async with session.post(
            f"{self.base_url}/v1/audio/speech",
            headers=headers,
            json=payload,
        ) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


class DeskBridge:
    """Main bridge coordinating Tab5 WebSocket, STT, TTS, and Hermes gateway."""

    def __init__(self):
        self.gateway = HermesGatewayClient(HERMES_GATEWAY_URL, HERMES_GATEWAY_KEY, SESSION_ID)
        self.stt = STTClient(STT_URL)
        self.tts = TTSClient(TTS_URL, TTS_VOICE)
        self._tab5_ws: Optional[web.WebSocketResponse] = None

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        """Handle WebSocket connection from Tab5."""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._tab5_ws = ws
        logger.info("Tab5 connected")

        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # Audio data from Tab5 mic
                    await self._handle_audio(msg.data)
                elif msg.type == aiohttp.WSMsgType.TEXT:
                    # Control message or text input
                    await self._handle_text_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
        finally:
            self._tab5_ws = None
            logger.info("Tab5 disconnected")

        return ws

    async def _handle_audio(self, audio_bytes: bytes):
        """Process incoming audio from Tab5."""
        # TODO: Implement audio buffering and STT (MOT-79)
        # 1. Buffer audio chunks until silence detected
        # 2. Send to STT
        # 3. Send transcribed text to Hermes
        # 4. Get response, synthesize TTS, stream back
        pass

    async def _handle_text_message(self, raw: str):
        """Handle text/control messages from Tab5."""
        try:
            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            
            if msg_type == "ping":
                await self._send_to_tab5({"type": "pong"})
            elif msg_type == "text":
                # Direct text input (e.g., from on-screen keyboard)
                await self._process_text(msg.get("text", ""))
            elif msg_type == "ack":
                # User acknowledged a notification
                logger.info("User acknowledged notification")
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON from Tab5: {raw[:100]}")

    async def _process_text(self, text: str):
        """Send text to Hermes and return response."""
        if not text.strip():
            return

        await self._send_to_tab5({"type": "status", "state": "processing"})

        try:
            # Get full response from Hermes
            response = await self.gateway.chat(text)
            
            # Send text response to Tab5 display
            await self._send_to_tab5({"type": "response", "text": response})
            
            # Synthesize and send audio
            await self._send_to_tab5({"type": "status", "state": "speaking"})
            audio = await self.tts.synthesize(response)
            await self._send_to_tab5(audio, binary=True)
            
            await self._send_to_tab5({"type": "status", "state": "idle"})
            
        except Exception as e:
            logger.error(f"Error processing text: {e}")
            await self._send_to_tab5({"type": "error", "message": str(e)})

    async def _send_to_tab5(self, data, binary: bool = False):
        """Send data to Tab5 over WebSocket."""
        if self._tab5_ws and not self._tab5_ws.closed:
            if binary:
                await self._tab5_ws.send_bytes(data)
            else:
                await self._tab5_ws.send_json(data)

    async def handle_notify(self, request: web.Request) -> web.Response:
        """HTTP endpoint for proactive notifications from Hermes."""
        try:
            data = await request.json()
            await self._send_to_tab5({
                "type": "card",
                "title": data.get("title", "Notification"),
                "body": data.get("body", ""),
                "icon": data.get("icon", "🔔"),
                "priority": data.get("priority", "normal"),
            })
            if data.get("audio", False):
                audio = await self.tts.synthesize(
                    f"{data.get('title', '')}. {data.get('body', '')}"
                )
                await self._send_to_tab5(audio, binary=True)
            return web.json_response({"status": "delivered"})
        except Exception as e:
            logger.error(f"Notification error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        tab5_connected = self._tab5_ws is not None and not self._tab5_ws.closed
        return web.json_response({
            "status": "ok",
            "tab5_connected": tab5_connected,
            "gateway_url": HERMES_GATEWAY_URL,
            "session_id": SESSION_ID,
        })

    def create_app(self) -> web.Application:
        """Create the aiohttp application."""
        app = web.Application()
        app.router.add_get("/ws", self.handle_websocket)
        app.router.add_post("/api/notify", self.handle_notify)
        app.router.add_get("/health", self.handle_health)
        app.on_cleanup.append(self._cleanup)
        return app

    async def _cleanup(self, app: web.Application):
        """Clean up client sessions on shutdown."""
        await self.gateway.close()
        await self.stt.close()
        await self.tts.close()


def main():
    logging.basicConfig(level=logging.INFO)
    bridge = DeskBridge()
    app = bridge.create_app()
    logger.info(f"Starting Hermes Desk Bridge on {BRIDGE_HOST}:{BRIDGE_PORT}")
    web.run_app(app, host=BRIDGE_HOST, port=BRIDGE_PORT)


if __name__ == "__main__":
    main()
