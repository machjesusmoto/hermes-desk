"""Tests for STT, TTS, and Gateway clients (mocked httpx transport)."""
import json

import pytest
import httpx

from hermes_bridge.stt import STTClient, STTResult
from hermes_bridge.tts import TTSClient
from hermes_bridge.gateway import GatewayClient, GatewayResponse
from hermes_bridge.audio import pcm_to_wav


# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------

class MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request):
        return await self._handler(request)


def _mock_client(base_url: str, handler):
    """Create an httpx.AsyncClient with base_url + mock transport."""
    return httpx.AsyncClient(base_url=base_url, transport=MockTransport(handler))


# ---------------------------------------------------------------------------
# STT tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stt_transcribe():
    async def handler(request):
        assert "/audio/transcriptions" in str(request.url)
        return httpx.Response(200, json={"text": "hello world", "language": "en"})

    client = STTClient("http://test-stt/v1", api_key="test")
    client._client = _mock_client("http://test-stt/v1", handler)

    pcm = b"\x00" * 640
    result = await client.transcribe(pcm)
    assert isinstance(result, STTResult)
    assert result.text == "hello world"
    assert result.language == "en"
    await client.aclose()


@pytest.mark.asyncio
async def test_stt_empty_result():
    async def handler(request):
        return httpx.Response(200, json={"text": ""})

    client = STTClient("http://test-stt/v1", api_key="test")
    client._client = _mock_client("http://test-stt/v1", handler)

    result = await client.transcribe(b"\x00" * 640)
    assert result.text == ""
    await client.aclose()


@pytest.mark.asyncio
async def test_stt_error():
    async def handler(request):
        return httpx.Response(500, text="internal error")

    client = STTClient("http://test-stt/v1", api_key="test")
    client._client = _mock_client("http://test-stt/v1", handler)

    with pytest.raises(httpx.HTTPStatusError):
        await client.transcribe(b"\x00" * 640)
    await client.aclose()


# ---------------------------------------------------------------------------
# TTS tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tts_synthesize_wav():
    wav_data = pcm_to_wav(b"\x00" * 640, sample_rate=16000)

    async def handler(request):
        assert "/audio/speech" in str(request.url)
        return httpx.Response(200, content=wav_data, headers={"content-type": "audio/wav"})

    client = TTSClient("http://test-tts/v1", api_key="test")
    client._client = _mock_client("http://test-tts/v1", handler)

    pcm = await client.synthesize("hello")
    assert isinstance(pcm, bytes)
    # Should have stripped WAV header
    assert pcm[:4] != b"RIFF"
    assert len(pcm) == 640
    await client.aclose()


@pytest.mark.asyncio
async def test_tts_synthesize_raw():
    raw_pcm = b"\x00" * 640

    async def handler(request):
        return httpx.Response(200, content=raw_pcm, headers={"content-type": "audio/pcm"})

    client = TTSClient("http://test-tts/v1", api_key="test", response_format="pcm")
    client._client = _mock_client("http://test-tts/v1", handler)

    pcm = await client.synthesize("hello")
    assert pcm == raw_pcm
    await client.aclose()


@pytest.mark.asyncio
async def test_tts_error():
    async def handler(request):
        return httpx.Response(503, text="unavailable")

    client = TTSClient("http://test-tts/v1", api_key="test")
    client._client = _mock_client("http://test-tts/v1", handler)

    with pytest.raises(httpx.HTTPStatusError):
        await client.synthesize("hello")
    await client.aclose()


# ---------------------------------------------------------------------------
# Gateway tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_chat():
    async def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "hermes"
        assert body["messages"][-1]["role"] == "user"
        assert request.headers.get("x-hermes-session-id") == "test-session"
        return httpx.Response(200, json={
            "model": "hermes",
            "choices": [{"message": {"content": "I can help"}, "finish_reason": "stop"}],
        })

    client = GatewayClient("http://test-gw", api_key="test", session_id="test-session")
    client._client = _mock_client("http://test-gw", handler)

    resp = await client.chat("test-session", "help me")
    assert isinstance(resp, GatewayResponse)
    assert resp.text == "I can help"
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_empty_choices():
    async def handler(request):
        return httpx.Response(200, json={"model": "hermes", "choices": []})

    client = GatewayClient("http://test-gw", api_key="test")
    client._client = _mock_client("http://test-gw", handler)

    resp = await client.chat(None, "test")
    assert resp.text == ""
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_error():
    async def handler(request):
        return httpx.Response(502, text="bad gateway")

    client = GatewayClient("http://test-gw", api_key="test")
    client._client = _mock_client("http://test-gw", handler)

    with pytest.raises(httpx.HTTPStatusError):
        await client.chat(None, "test")
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_with_system_prompt():
    received_messages = []

    async def handler(request):
        body = json.loads(request.content)
        received_messages.extend(body["messages"])
        return httpx.Response(200, json={
            "model": "hermes",
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })

    client = GatewayClient("http://test-gw", api_key="test",
                           system_prompt="You are helpful.")
    client._client = _mock_client("http://test-gw", handler)

    await client.chat(None, "hi")
    assert received_messages[0]["role"] == "system"
    assert received_messages[0]["content"] == "You are helpful."
    assert received_messages[1]["role"] == "user"
    await client.aclose()
