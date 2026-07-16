"""Voice turn pipeline — STT -> Gateway -> TTS -> stream PCM back.

Stateless per turn. Handles barge-in (abort during TTS).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from .config import AudioConfig
from .stt import STTClient
from .tts import TTSClient
from .gateway import GatewayClient
from .audio import chunk_pcm

log = logging.getLogger(__name__)


class StreamWriter(Protocol):
    """Protocol for sending frames back to the device."""
    async def send_text(self, message: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


class Pipeline:
    """Orchestrates a single voice turn."""

    def __init__(self, stt: STTClient, tts: TTSClient, gateway: GatewayClient,
                 audio_cfg: AudioConfig):
        self.stt = stt
        self.tts = tts
        self.gateway = gateway
        self.audio_cfg = audio_cfg
        self._abort = asyncio.Event()

    def request_abort(self):
        """Called when the device sends an abort during TTS playback."""
        self._abort.set()

    def reset(self):
        """Reset abort state for a new turn."""
        self._abort.clear()

    async def run_turn(self, pcm_data: bytes, session_id: str,
                       writer: StreamWriter) -> str:
        """Execute a full voice turn.

        1. STT the audio
        2. Send transcript to gateway
        3. TTS the reply
        4. Stream PCM frames back to the device

        Returns the LLM reply text. Raises on STT/TTS/gateway failure.
        """
        from . import protocol as p

        self.reset()

        # 1. STT
        log.info("STT: transcribing %d bytes PCM", len(pcm_data))
        stt_result = await self.stt.transcribe(pcm_data, sample_rate=self.audio_cfg.sample_rate)
        transcript = stt_result.text.strip()
        log.info("STT result: %s", transcript[:100])

        if not transcript:
            await writer.send_text(p.stt_out("", is_final=True))
            await writer.send_text(p.status_out("idle"))
            return ""

        await writer.send_text(p.stt_out(transcript, is_final=True))

        # 2. Gateway
        log.info("gateway: sending to Hermes")
        reply = await self.gateway.chat(session_id, transcript)
        reply_text = reply.text.strip()
        log.info("gateway reply: %s", reply_text[:100])

        if not reply_text:
            await writer.send_text(p.status_out("idle"))
            return ""

        await writer.send_text(p.llm_out(reply_text))

        # 3. TTS
        log.info("TTS: synthesizing %d chars", len(reply_text))
        pcm_audio = await self.tts.synthesize(reply_text)
        log.info("TTS: got %d bytes PCM", len(pcm_audio))

        # 4. Stream PCM back
        await writer.send_text(p.tts_start_out(sample_rate=self.audio_cfg.sample_rate))

        for chunk in chunk_pcm(pcm_audio, self.audio_cfg.frame_bytes):
            if self._abort.is_set():
                log.info("TTS playback aborted (barge-in)")
                break
            await writer.send_bytes(chunk)

        await writer.send_text(p.tts_stop_out())
        await writer.send_text(p.status_out("idle"))

        return reply_text
