"""YAML + env-layered configuration for the bridge."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8765
    token: str = ""  # empty = no auth


@dataclass
class STTConfig:
    base_url: str = "http://gx10-fd05:11436/v1"
    model: str = "nvidia/nemotron-0.6b-stt"
    api_key: str = "unused"
    timeout: float = 10.0


@dataclass
class TTSConfig:
    base_url: str = "http://gx10-39e5:11438/v1"
    voice: str = "neural2-F"
    model: str = "tts-1"
    api_key: str = "unused"
    response_format: str = "wav"
    timeout: float = 15.0


@dataclass
class GatewayConfig:
    base_url: str = "http://host.docker.internal:8642"
    chat_path: str = "/v1/chat/completions"
    api_key: str = ""
    model: str = "hermes"
    session_id: str = "hermes-desk"
    system_prompt: str = "You are Hermes, an AI assistant. Respond concisely for voice output."
    timeout: float = 30.0


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    bits: int = 16
    channels: int = 1
    frame_bytes: int = 640  # 20ms @ 16k/16bit/mono


@dataclass
class LogConfig:
    level: str = "INFO"
    json: bool = False


@dataclass
class BridgeConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    log: LogConfig = field(default_factory=LogConfig)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "BridgeConfig":
        """Load config: defaults <- YAML file <- env vars."""
        data: dict = {}
        if path and Path(path).exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}

        cfg = cls()
        for section_name in ("server", "stt", "tts", "gateway", "audio", "log"):
            section_data = data.get(section_name, {})
            section_obj = getattr(cfg, section_name)
            for key, value in section_data.items():
                if hasattr(section_obj, key):
                    setattr(section_obj, key, value)

        # Env overlay: HERMES_DESK_<SECTION>__<KEY>
        for key, value in os.environ.items():
            if key.startswith("HERMES_DESK_"):
                parts = key[len("HERMES_DESK_"):].lower().split("__", 1)
                if len(parts) == 2 and hasattr(cfg, parts[0]):
                    section_obj = getattr(cfg, parts[0])
                    if hasattr(section_obj, parts[1]):
                        current = getattr(section_obj, parts[1])
                        typed_value = _coerce(value, type(current))
                        setattr(section_obj, parts[1], typed_value)

        return cfg


def _coerce(value: str, target_type):
    """Coerce string env var to the target field type."""
    if target_type is bool:
        return value.lower() in ("1", "true", "yes")
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    return value
