/**
 * @file protocol.h
 * @brief Firmware-side mirror of the Hermes Desk Bridge wire protocol (v1).
 *
 * Authoritative source: docs/PROTOCOL.md and bridge/hermes_bridge/protocol.py.
 * Keep this file in lockstep with the bridge — the field names and JSON shape
 * must match byte-for-byte (the bridge parses with json.loads, no schema).
 *
 * Transport: WebSocket at ws://<bridge>:8765/ws
 *   - Text frames   = JSON control messages (this file)
 *   - Binary frames = raw PCM audio (see HERMES_AUDIO_* below)
 *
 * Auth: ?token=<BRIDGE_TOKEN> query param (or Authorization: Bearer <token>).
 */
#pragma once
#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Audio format — MUST match bridge AudioConfig (config.py).
 *   PCM 16-bit signed, little-endian, 16 kHz, mono.
 *   20 ms frame = 16000 * 2 * 1 * 0.02 = 640 bytes (320 samples).
 * ------------------------------------------------------------------------- */
#define HERMES_AUDIO_SAMPLE_RATE   16000
#define HERMES_AUDIO_BITS          16
#define HERMES_AUDIO_CHANNELS      1
#define HERMES_AUDIO_FRAME_MS      20
#define HERMES_AUDIO_FRAME_BYTES   640   /* 20 ms @ 16k/16bit/mono */
#define HERMES_AUDIO_FRAME_SAMPLES 320

/* Protocol version sent in the device->bridge `hello`. */
#define HERMES_PROTOCOL_VERSION    1

/* ---------------------------------------------------------------------------
 * Message "type" strings (the JSON `type` field).
 * ------------------------------------------------------------------------- */
#define HERMES_TYPE_HELLO   "hello"
#define HERMES_TYPE_LISTEN  "listen"
#define HERMES_TYPE_ABORT   "abort"
#define HERMES_TYPE_STT     "stt"
#define HERMES_TYPE_LLM     "llm"
#define HERMES_TYPE_TTS     "tts"
#define HERMES_TYPE_STATUS  "status"
#define HERMES_TYPE_ERROR   "error"
#define HERMES_TYPE_NOTIFY  "notify"

/* Sub-values for action / state fields. */
#define HERMES_ACTION_START "start"
#define HERMES_ACTION_STOP  "stop"
#define HERMES_STATE_IDLE       "idle"
#define HERMES_STATE_LISTENING  "listening"
#define HERMES_STATE_PROCESSING "processing"
#define HERMES_STATE_SPEAKING   "speaking"

/* Bridge error codes (bridge -> device `error.code`). */
#define HERMES_ERR_STT_FAILED      "stt_failed"
#define HERMES_ERR_TTS_FAILED      "tts_failed"
#define HERMES_ERR_GATEWAY_FAILED  "gateway_failed"
#define HERMES_ERR_GATEWAY_TIMEOUT "gateway_timeout"
#define HERMES_ERR_BAD_MESSAGE     "bad_message"

/* Notification levels (bridge -> device `notify.level`). */
#define HERMES_LEVEL_INFO    "info"
#define HERMES_LEVEL_WARNING "warning"
#define HERMES_LEVEL_URGENT  "urgent"

#ifdef __cplusplus
}
#endif
