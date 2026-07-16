"""Tests for the voice turn pipeline."""
import json

import pytest
import httpx

from hermes_bridge.pipeline import Pipeline
from hermes_bridge.stt import STTClient, STTResult
from hermes_bridge.tts import TTSClient
from hermes_bridge.gateway import GatewayClient, GatewayResponse
from hermes_bridge.config import AudioConfig
from hermes_bridge.audio import pcm_to_wav


class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request):
        return await self._handler(request)


class FakeWriter:
    def __init__(self):
        self.texts = []
        self.bytes_data = []

    async def send_text(self, message):
        self.texts.append(message)

    async def send_bytes(self, data):
        self.bytes_data.append(data)


def _mock_client(base_url, handler):
    return httpx.AsyncClient(base_url=base_url, transport=MockTransport(handler))


def _make_pipeline(stt_text="hello", gateway_reply="I can help"):
    """Create a pipeline with mocked clients."""
    pcm_response = pcm_to_wav(b"\x00" * 640, sample_rate=16000)

    async def stt_handler(request):
        return httpx.Response(200, json={"text": stt_text})

    async def tts_handler(request):
        return httpx.Response(200, content=pcm_response)

    async def gw_handler(request):
        return httpx.Response(200, json={
            "model": "hermes",
            "choices": [{"message": {"content": gateway_reply}, "finish_reason": "stop"}],
        })

    stt = STTClient("http://test-stt/v1", api_key="test")
    stt._client = _mock_client("http://test-stt/v1", stt_handler)

    tts = TTSClient("http://test-tts/v1", api_key="test")
    tts._client = _mock_client("http://test-tts/v1", tts_handler)

    gw = GatewayClient("http://test-gw", api_key="test")
    gw._client = _mock_client("http://test-gw", gw_handler)

    audio_cfg = AudioConfig()
    return Pipeline(stt, tts, gw, audio_cfg), stt, tts, gw


@pytest.mark.asyncio
async def test_full_turn():
    pipeline, stt, tts, gw = _make_pipeline("what time is it", "It's 3 PM")
    writer = FakeWriter()

    result = await pipeline.run_turn(b"\x00" * 3200, "test-session", writer)
    assert result == "It's 3 PM"

    # Check messages sent to device
    types = [json.loads(t)["type"] for t in writer.texts]
    assert "stt" in types
    assert "llm" in types
    assert "tts" in types  # start
    assert "status" in types

    # Check stt message
    stt_msg = json.loads(writer.texts[0])
    assert stt_msg["text"] == "what time is it"
    assert stt_msg["is_final"] is True

    # Check llm message
    llm_idx = types.index("llm")
    llm_msg = json.loads(writer.texts[llm_idx])
    assert llm_msg["text"] == "It's 3 PM"

    # Check TTS audio was streamed
    assert len(writer.bytes_data) > 0

    await stt.aclose()
    await tts.aclose()
    await gw.aclose()


@pytest.mark.asyncio
async def test_empty_transcript():
    pipeline, stt, tts, gw = _make_pipeline(stt_text="")
    writer = FakeWriter()

    result = await pipeline.run_turn(b"\x00" * 3200, "test-session", writer)
    assert result == ""

    types = [json.loads(t)["type"] for t in writer.texts]
    assert "stt" in types
    assert "status" in types
    assert "llm" not in types  # no LLM call if transcript empty

    await stt.aclose()
    await tts.aclose()
    await gw.aclose()


@pytest.mark.asyncio
async def test_empty_gateway_reply():
    pipeline, stt, tts, gw = _make_pipeline(gateway_reply="")
    writer = FakeWriter()

    result = await pipeline.run_turn(b"\x00" * 3200, "test-session", writer)
    assert result == ""

    types = [json.loads(t)["type"] for t in writer.texts]
    assert "stt" in types
    assert "llm" not in types  # no llm_out if reply is empty
    assert "tts" not in types  # no TTS if reply is empty

    await stt.aclose()
    await tts.aclose()
    await gw.aclose()


@pytest.mark.asyncio
async def test_abort():
    pipeline, stt, tts, gw = _make_pipeline("hello", "a long response here")
    writer = FakeWriter()

    # Request abort immediately — should stop TTS playback
    pipeline.request_abort()

    result = await pipeline.run_turn(b"\x00" * 3200, "test-session", writer)
    # Abort only affects TTS playback, not the rest of the pipeline
    assert result == "a long response here"

    await stt.aclose()
    await tts.aclose()
    await gw.aclose()
