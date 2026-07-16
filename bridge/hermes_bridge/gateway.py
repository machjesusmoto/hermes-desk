"""Hermes gateway client — stateless per-turn chat.

Talks to the Hermes API server at :8642/v1/chat/completions.
The bridge is stateless — Hermes owns conversation memory via session_id.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, Optional

import httpx

log = logging.getLogger(__name__)


@dataclass
class GatewayResponse:
    text: str
    model: str = ""
    finish_reason: str = ""


class GatewayClient:
    """Async httpx client for the Hermes gateway API."""

    def __init__(self, base_url: str, chat_path: str = "/v1/chat/completions",
                 api_key: str = "", model: str = "hermes",
                 session_id: str = "hermes-desk",
                 system_prompt: str = "", timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._chat_path = chat_path
        self._model = model
        self._session_id = session_id
        self._system_prompt = system_prompt
        self._timeout = timeout
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers=headers,
        )

    async def chat(self, session_id: Optional[str], text: str) -> GatewayResponse:
        """Send a user message, get the assistant reply.

        session_id is forwarded as X-Hermes-Session-Id header so the gateway
        groups turns into the same conversation.
        """
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": text})

        headers = {}
        sid = session_id or self._session_id
        if sid:
            headers["X-Hermes-Session-Id"] = sid

        resp = await self._client.post(
            self._chat_path,
            json={
                "model": self._model,
                "messages": messages,
                "stream": False,
            },
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            return GatewayResponse(text="")

        msg = choices[0].get("message", {})
        return GatewayResponse(
            text=msg.get("content", ""),
            model=data.get("model", ""),
            finish_reason=choices[0].get("finish_reason", ""),
        )

    async def chat_stream(self, session_id: Optional[str], text: str) -> AsyncIterator[str]:
        """Stream assistant reply tokens. Yields text chunks."""
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.append({"role": "user", "content": text})

        headers = {}
        sid = session_id or self._session_id
        if sid:
            headers["X-Hermes-Session-Id"] = sid

        async with self._client.stream(
            "POST",
            self._chat_path,
            json={
                "model": self._model,
                "messages": messages,
                "stream": True,
            },
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        import json
                        delta = json.loads(chunk).get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (ValueError, IndexError, KeyError):
                        continue

    async def aclose(self):
        await self._client.aclose()
