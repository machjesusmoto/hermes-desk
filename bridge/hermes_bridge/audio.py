"""Audio utilities — PCM<->WAV, resample safety net, frame chunking."""
from __future__ import annotations

import io
import struct
import wave
from typing import Generator


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000,
               bits: int = 16, channels: int = 1) -> bytes:
    """Wrap raw PCM in a WAV header."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(bits // 8)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def wav_to_pcm(wav_data: bytes) -> tuple[bytes, int, int, int]:
    """Extract raw PCM from WAV. Returns (pcm, sample_rate, bits, channels)."""
    buf = io.BytesIO(wav_data)
    with wave.open(buf, "rb") as wf:
        pcm = wf.readframes(wf.getnframes())
        return pcm, wf.getframerate(), wf.getsampwidth() * 8, wf.getnframes()


def chunk_pcm(pcm_data: bytes, frame_bytes: int = 640) -> Generator[bytes, None, None]:
    """Yield PCM chunks of exactly frame_bytes (last chunk may be shorter)."""
    offset = 0
    while offset < len(pcm_data):
        yield pcm_data[offset:offset + frame_bytes]
        offset += frame_bytes


def resample_pcm(pcm_data: bytes, src_rate: int, dst_rate: int,
                 bits: int = 16, channels: int = 1) -> bytes:
    """Linear-interpolation resample. Returns resampled PCM.

    This is a safety net for devices that send at non-standard rates.
    For production quality, use ffmpeg or libsamplerate.
    """
    if src_rate == dst_rate:
        return pcm_data

    bytes_per_sample = bits // 8
    frame_size = bytes_per_sample * channels
    num_frames = len(pcm_data) // frame_size

    # Unpack to samples
    fmt = f"<{num_frames * channels}h"
    if bytes_per_sample == 1:
        fmt = f"<{num_frames * channels}b"
    samples = list(struct.unpack(fmt, pcm_data[:num_frames * frame_size]))

    # Calculate output
    ratio = dst_rate / src_rate
    out_frames = int(num_frames * ratio)
    out_samples = []

    for i in range(out_frames * channels):
        src_pos = i / ratio
        src_idx = int(src_pos)
        frac = src_pos - src_idx

        ch = i % channels
        base = src_idx * channels + ch

        if base + channels < len(samples):
            s0 = samples[base]
            s1 = samples[base + channels]
            out_samples.append(int(s0 + frac * (s1 - s0)))
        elif base < len(samples):
            out_samples.append(samples[base])
        else:
            out_samples.append(0)

    pack_fmt = "<" + str(len(out_samples)) + ("b" if bytes_per_sample == 1 else "h")
    return struct.pack(pack_fmt, *out_samples)
