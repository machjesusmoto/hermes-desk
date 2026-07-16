# Hermes Desk Bridge Protocol v1

Wire protocol between the M5Stack Tab5 (device) and the bridge service.

## Transport

- **WebSocket** at `ws://<bridge>:8765/ws`
- Auth: `?token=<BRIDGE_TOKEN>` query param or `Authorization: Bearer <token>`
- Binary frames: raw PCM audio (16-bit signed LE, 16kHz, mono)
- Text frames: JSON control messages

## Message Types

### Device → Bridge

| Type     | Fields                            | Description |
|----------|-----------------------------------|-------------|
| `hello`  | `version`, `audio_params`, `device_id` | Handshake on connect |
| `listen` | `action: "start"`                 | Begin audio capture (Tab5 starts sending binary PCM frames) |
| `listen` | `action: "stop"`                  | End audio capture (Tab5 stops sending, bridge processes turn) |
| `abort`  |                                   | Barge-in: cancel TTS playback |

### Bridge → Device

| Type     | Fields                            | Description |
|----------|-----------------------------------|-------------|
| `hello`  | `version`, `session_id`, `audio_params` | Handshake response |
| `stt`    | `text`, `is_final`                | Transcript from STT |
| `llm`    | `text`                            | LLM reply text |
| `tts`    | `action: "start"`, `sample_rate` | TTS PCM stream begins |
| `tts`    | `action: "stop"`                  | TTS PCM stream ends |
| `status` | `state`                           | State change: `idle`, `listening`, `processing` |
| `error`  | `code`, `message`                 | Error with code (see below) |
| `notify` | `title`, `body`, `level`          | Proactive notification from Hermes |

## Conversation Flow

```
Device                    Bridge                     STT / Gateway / TTS
  |                         |                           |
  |--- hello -------------->|                           |
  |<-- hello + session_id --|                           |
  |<-- status: idle --------|                           |
  |                         |                           |
  |--- listen: start ------>|                           |
  |--- [PCM binary] ------->|  (buffered)              |
  |--- [PCM binary] ------->|                           |
  |--- listen: stop --------|                           |
  |<-- status: processing --|                           |
  |                         |--- transcribe ---------->|
  |                         |<-- transcript ------------|
  |<-- stt: transcript -----|                           |
  |                         |--- chat(session) -------->|
  |                         |<-- reply text ------------|
  |<-- llm: reply text -----|                           |
  |                         |--- synthesize ----------->|
  |                         |<-- PCM audio -------------|
  |<-- tts: start ----------|                           |
  |<-- [PCM binary] --------|  (streamed, 20ms frames) |
  |<-- [PCM binary] --------|                           |
  |<-- tts: stop -----------|                           |
  |<-- status: idle --------|                           |
```

## Barge-In

If the device sends `abort` during TTS playback, the bridge stops streaming
PCM frames and sends `tts: stop` + `status: idle`. The device should then
be ready for the next `listen: start`.

## Error Codes

| Code              | Meaning |
|-------------------|---------|
| `stt_failed`      | STT unreachable or returned error |
| `tts_failed`      | TTS unreachable or returned error |
| `gateway_failed`  | Gateway 4xx/5xx |
| `gateway_timeout` | Gateway didn't respond in time |
| `bad_message`     | Malformed JSON control frame |

On any error, the bridge sends `status: idle` so the device returns to
a known state.

## Proactive Notifications

Hermes can push notifications to the Tab5 via `POST /notify` on the bridge's
HTTP port. The bridge forwards the notification as a `notify` text frame
to the connected device.

```json
POST /notify
{
  "title": "Meeting in 5 minutes",
  "body": "Standup with the team",
  "level": "urgent"
}
```

Levels: `info`, `warning`, `urgent`. The device should play appropriate
audio/visual feedback per level.

## Audio Format

- Codec: raw PCM (no compression)
- Sample rate: 16000 Hz
- Bit depth: 16-bit signed little-endian
- Channels: 1 (mono)
- Frame size: 640 bytes (20ms of audio)
