"""TTS client — OpenAI-compatible /v1/audio/speech.

Target: Google Neural2-F on gx10-39e5:11438
Returns raw PCM (strips WAV header from response).
"""
from __future__ import annotations

import logging

import httpx

from .audio import wav_to_pcm

log = logging.getLogger(__name__)


class TTSClient:
    """Async httpx client for OpenAI-compatible TTS."""

    def __init__(self, base_url: str, voice: str = "neural2-F",
                 model: str = "tts-1", api_key: str = "unused",
                 response_format: str = "wav", timeout: float = 15.0):
        self._base = base_url.rstrip("/")
        self._voice = voice
        self._model = model
        self._format = response_format
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def synthesize(self, text: str) -> bytes:
        """Synthesize text to audio. Returns raw PCM bytes."""
        resp = await self._client.post("/audio/speech", json={
            "model": self._model,
            "input": text,
            "voice": self._voice,
            "response_format": self._format,
        })
        resp.raise_for_status()

        audio_data = resp.content
        # If WAV, strip header to get raw PCM
        if self._format == "wav" and audio_data[:4] == b"RIFF":
            pcm, _, _, _ = wav_to_pcm(audio_data)
            log.debug("TTS: stripped WAV header, %d bytes PCM", len(pcm))
            return pcm

        log.debug("TTS: %d bytes raw audio", len(audio_data))
        return audio_data

    async def aclose(self):
        await self._client.aclose()
