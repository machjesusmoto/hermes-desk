"""STT client — OpenAI-compatible /v1/audio/transcriptions.

Target: Nemotron 0.6B on gx10-fd05:11436
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .audio import pcm_to_wav

log = logging.getLogger(__name__)


@dataclass
class STTResult:
    text: str
    language: str = "en"


class STTClient:
    """Async httpx client for OpenAI-compatible STT."""

    def __init__(self, base_url: str, model: str = "nvidia/nemotron-0.6b-stt",
                 api_key: str = "unused", timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def transcribe(self, pcm_data: bytes, sample_rate: int = 16000,
                         language: str = "en") -> STTResult:
        """Send PCM audio, get transcript back."""
        wav_data = pcm_to_wav(pcm_data, sample_rate=sample_rate)
        files = {"file": ("audio.wav", wav_data, "audio/wav")}
        data = {"model": self._model, "language": language}

        resp = await self._client.post(
            "/audio/transcriptions", files=files, data=data
        )
        resp.raise_for_status()
        result = resp.json()
        text = result.get("text", "")
        log.debug("STT result: %s", text[:80])
        return STTResult(text=text, language=result.get("language", language))

    async def aclose(self):
        await self._client.aclose()
