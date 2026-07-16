"""Tests for audio utilities."""
import struct
import wave
import io

import pytest

from hermes_bridge.audio import pcm_to_wav, wav_to_pcm, chunk_pcm, resample_pcm


def _make_pcm(frames: int = 100, freq: float = 440.0, sample_rate: int = 16000) -> bytes:
    """Generate a sine wave PCM signal."""
    import math
    samples = []
    for i in range(frames):
        val = int(16000 * math.sin(2 * math.pi * freq * i / sample_rate))
        samples.append(val)
    return struct.pack(f"<{len(samples)}h", *samples)


def test_pcm_to_wav_header():
    pcm = _make_pcm(100)
    wav_data = pcm_to_wav(pcm, sample_rate=16000)
    assert wav_data[:4] == b"RIFF"
    assert wav_data[8:12] == b"WAVE"


def test_pcm_to_wav_roundtrip():
    pcm = _make_pcm(200)
    wav_data = pcm_to_wav(pcm, sample_rate=16000, bits=16, channels=1)
    pcm_out, sr, bits, channels = wav_to_pcm(wav_data)
    assert sr == 16000
    assert bits == 16
    assert pcm_out == pcm


def test_chunk_pcm():
    pcm = b"\x00" * 1600  # 100ms of silence
    chunks = list(chunk_pcm(pcm, frame_bytes=640))
    assert len(chunks) == 3  # 640 + 640 + 320
    assert len(chunks[0]) == 640
    assert len(chunks[1]) == 640
    assert len(chunks[2]) == 320


def test_chunk_pcm_exact():
    pcm = b"\x00" * 640
    chunks = list(chunk_pcm(pcm, frame_bytes=640))
    assert len(chunks) == 1
    assert len(chunks[0]) == 640


def test_chunk_pcm_empty():
    chunks = list(chunk_pcm(b"", frame_bytes=640))
    assert len(chunks) == 0


def test_resample_same_rate():
    pcm = _make_pcm(100)
    result = resample_pcm(pcm, 16000, 16000)
    assert result == pcm


def test_resample_downsample():
    pcm = _make_pcm(1000, sample_rate=16000)
    result = resample_pcm(pcm, 16000, 8000)
    # Output should be roughly half the size
    assert len(result) < len(pcm)
    assert len(result) > len(pcm) // 4  # but not too small


def test_resample_upsample():
    pcm = _make_pcm(100, sample_rate=8000)
    result = resample_pcm(pcm, 8000, 16000)
    assert len(result) > len(pcm)
