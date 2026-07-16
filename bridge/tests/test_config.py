"""Tests for config loading and env overrides."""
import os
import tempfile

import pytest

from hermes_bridge.config import BridgeConfig


def test_defaults():
    cfg = BridgeConfig()
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 8765
    assert cfg.stt.model == "nvidia/nemotron-0.6b-stt"
    assert cfg.tts.voice == "neural2-F"
    assert cfg.audio.sample_rate == 16000
    assert cfg.audio.frame_bytes == 640


def test_yaml_loading(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
server:
  port: 9999
  token: "test-token"
stt:
  base_url: "http://custom-stt:8080/v1"
""")
    cfg = BridgeConfig.load(str(cfg_file))
    assert cfg.server.port == 9999
    assert cfg.server.token == "test-token"
    assert cfg.stt.base_url == "http://custom-stt:8080/v1"
    # Unset values keep defaults
    assert cfg.tts.voice == "neural2-F"


def test_env_override(monkeypatch):
    monkeypatch.setenv("HERMES_DESK_SERVER__PORT", "7777")
    monkeypatch.setenv("HERMES_DESK_STT__BASE_URL", "http://env-stt:1234/v1")
    monkeypatch.setenv("HERMES_DESK_LOG__JSON", "true")

    cfg = BridgeConfig.load()  # load() applies env overlay; plain constructor does not
    assert cfg.server.port == 7777
    assert cfg.stt.base_url == "http://env-stt:1234/v1"
    assert cfg.log.json is True


def test_env_overrides_yaml(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("server:\n  port: 1111\n")
    monkeypatch.setenv("HERMES_DESK_SERVER__PORT", "2222")

    cfg = BridgeConfig.load(str(cfg_file))
    assert cfg.server.port == 2222  # env wins


def test_missing_yaml():
    cfg = BridgeConfig.load("/nonexistent/config.yaml")
    assert cfg.server.port == 8765  # defaults
