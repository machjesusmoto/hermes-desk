# Hermes Desk Bridge Protocol v1

Wire protocol between the M5Stack Tab5 (device) and the bridge service.

**Source of truth**: This file. The firmware mirror is `firmware/main/include/protocol.h`;
the bridge implementation is `bridge/hermes_bridge/protocol.py` (control messages)
and `bridge/hermes_bridge/display.py` (display commands).

## Transport

- **WebSocket** at `ws://<bridge>:8765/ws`
- Auth: `?token=<BRIDGE_TOKEN>` query param or `Authorization: Bearer <token>` header
- **Text frames**: JSON control messages (UTF-8)
- **Binary frames**: raw PCM audio (16-bit signed LE, 16kHz, mono)
- Ping/pong keepalive: bridge pings every 5s; device auto-responds (ESP-IDF default)
- Auto-reconnect: device retries every 3s on disconnect; re-sends `hello` on each reconnect

## Audio Format (Binary Frames)

| Property     | Value                                  |
|-------------|----------------------------------------|
| Codec       | Raw PCM (no compression)               |
| Sample rate | 16000 Hz                               |
| Bit depth   | 16-bit signed little-endian            |
| Channels    | 1 (mono)                               |
| Frame size  | 640 bytes (20 ms = 320 samples)        |

Binary frames flow in both directions:
- **Device → Bridge**: microphone capture (while `listen: start` → `listen: stop`)
- **Bridge → Device**: TTS playback (while `tts: start` → `tts: stop`)

Frames should be sent as complete 640-byte chunks, but the bridge tolerates partial frames.

---

## Message Types: Device → Bridge

### `hello` — Handshake

Sent immediately on WebSocket connect (and on every reconnect). Identifies the
device and negotiates audio parameters.

**When sent**: By the device, once per connection, as the first message.

```json
{
  "type": "hello",
  "version": 1,
  "audio_params": {
    "sample_rate": 16000,
    "bits": 16,
    "channels": 1
  },
  "device_id": "tab5-a1b2c3"
}
```

| Field          | Type   | Required | Description                                    |
|---------------|--------|----------|------------------------------------------------|
| `type`        | string | yes      | Always `"hello"`                               |
| `version`     | int    | yes      | Protocol version (currently `1`)               |
| `audio_params`| object | yes      | Device audio capabilities                      |
| `audio_params.sample_rate` | int | yes | Sample rate in Hz                         |
| `audio_params.bits`        | int | yes | Bit depth                                 |
| `audio_params.channels`    | int | yes | Channel count                             |
| `device_id`   | string | yes      | Unique device identifier (derived from MAC)    |

### `listen` — Audio Capture Control

Brackets the microphone capture phase. Between `start` and `stop`, the device
streams binary PCM frames to the bridge.

**When sent**: By the device, when the user presses/releases the PTT button.

#### `listen: start`

```json
{
  "type": "listen",
  "action": "start"
}
```

#### `listen: stop`

```json
{
  "type": "listen",
  "action": "stop"
}
```

| Field    | Type   | Required | Values            |
|----------|--------|----------|-------------------|
| `type`   | string | yes      | `"listen"`        |
| `action` | string | yes      | `"start"` or `"stop"` |

### `abort` — Barge-In

Cancels TTS playback. The bridge stops streaming PCM and returns to idle.

**When sent**: By the device, when the user taps during `SPEAKING` state.

```json
{
  "type": "abort"
}
```

| Field  | Type   | Required |
|--------|--------|----------|
| `type` | string | yes      |

### `notify_ack` — Notification Acknowledgment

Confirms the user has seen/dismissed a notification that had `requires_ack: true`.

**When sent**: By the device, after the user acknowledges a notification.

```json
{
  "type": "notify_ack",
  "notification_id": "a1b2c3d4"
}
```

| Field            | Type   | Required | Description                |
|-----------------|--------|----------|----------------------------|
| `type`          | string | yes      | Always `"notify_ack"`      |
| `notification_id` | string | yes    | ID of the notification being acknowledged |

---

## Message Types: Bridge → Device

### `hello` — Handshake Response

Confirms the connection and assigns a session ID.

**When sent**: By the bridge, immediately after receiving the device `hello`.

```json
{
  "type": "hello",
  "version": 1,
  "session_id": "f8a3b2c1",
  "audio_params": {
    "sample_rate": 16000,
    "bits": 16,
    "channels": 1
  }
}
```

| Field          | Type   | Required | Description                          |
|---------------|--------|----------|--------------------------------------|
| `type`        | string | yes      | Always `"hello"`                     |
| `version`     | int    | yes      | Protocol version                     |
| `session_id`  | string | yes      | Bridge-assigned session identifier   |
| `audio_params`| object | yes      | Bridge audio config (should match device) |

### `stt` — Speech-to-Text Transcript

The transcription result from STT processing of the captured audio.

**When sent**: By the bridge, after STT completes processing the captured PCM.
Always sent before `llm`, even if the transcript is empty.

```json
{
  "type": "stt",
  "text": "What's the weather today?",
  "is_final": true
}
```

| Field      | Type   | Required | Description                              |
|-----------|--------|----------|------------------------------------------|
| `type`    | string | yes      | Always `"stt"`                           |
| `text`    | string | yes      | Transcribed text (empty string if silent) |
| `is_final`| bool   | yes      | `true` for final result (no partial streaming currently) |

### `llm` — LLM Reply Text

The text response from the gateway/LLM.

**When sent**: By the bridge, after the gateway returns a reply. Only sent if the
reply is non-empty.

```json
{
  "type": "llm",
  "text": "It's currently 72°F and sunny in your area."
}
```

| Field   | Type   | Required | Description         |
|---------|--------|----------|---------------------|
| `type`  | string | yes      | Always `"llm"`      |
| `text`  | string | yes      | LLM response text   |

### `tts` — Text-to-Speech Control

Brackets the TTS PCM audio stream. Between `start` and `stop`, the bridge sends
binary PCM frames to the device.

**When sent**: By the bridge, after TTS synthesis completes.

#### `tts: start`

```json
{
  "type": "tts",
  "action": "start",
  "sample_rate": 16000
}
```

| Field        | Type   | Required | Description                |
|-------------|--------|----------|----------------------------|
| `type`      | string | yes      | Always `"tts"`             |
| `action`    | string | yes      | `"start"`                  |
| `sample_rate` | int  | yes      | PCM sample rate (Hz)       |

#### `tts: stop`

```json
{
  "type": "tts",
  "action": "stop"
}
```

| Field     | Type   | Required | Description |
|----------|--------|----------|-------------|
| `type`   | string | yes      | `"tts"`     |
| `action` | string | yes      | `"stop"`    |

### `status` — State Change

Reports the bridge's current processing state. The device FSM uses `idle` to
know a voice turn is complete.

**When sent**: By the bridge, at state transitions during the conversation flow.

```json
{
  "type": "status",
  "state": "idle"
}
```

| Field   | Type   | Required | Values                                     |
|---------|--------|----------|--------------------------------------------|
| `type`  | string | yes      | `"status"`                                 |
| `state` | string | yes      | `"idle"`, `"listening"`, `"processing"`    |

Note: `"speaking"` is defined in `protocol.h` but the bridge currently sends
`tts: start`/`tts: stop` instead of `status: speaking`.

### `error` — Error Report

Reports an error from STT, TTS, or the gateway. The bridge always follows an
error with `status: idle` so the device returns to a known state.

**When sent**: By the bridge, when a pipeline step fails.

```json
{
  "type": "error",
  "code": "stt_failed",
  "message": "Connection refused to STT service at http://stt:9000"
}
```

| Field     | Type   | Required | Description              |
|----------|--------|----------|--------------------------|
| `type`   | string | yes      | `"error"`                |
| `code`   | string | yes      | Error code (see table)   |
| `message`| string | no       | Human-readable detail    |

#### Error Codes

| Code              | Meaning                                     |
|-------------------|---------------------------------------------|
| `stt_failed`      | STT unreachable or returned error           |
| `tts_failed`      | TTS unreachable or returned error           |
| `gateway_failed`  | Gateway returned 4xx/5xx                    |
| `gateway_timeout` | Gateway didn't respond in time              |
| `bad_message`     | Malformed JSON control frame from device    |

### `notify` — Proactive Notification

Pushes a notification from Hermes to the Tab5 screen.

**When sent**: By the bridge, when Hermes sends a notification via `POST /notify`
on the HTTP API. Delivered immediately if a device is connected; queued otherwise.

```json
{
  "type": "notify",
  "title": "Meeting in 5 minutes",
  "body": "Standup with the team",
  "level": "info",
  "notification_id": "a1b2c3d4",
  "priority": 2,
  "requires_ack": true,
  "category": "calendar",
  "display_type": "card"
}
```

| Field            | Type   | Required | Description                                  |
|-----------------|--------|----------|----------------------------------------------|
| `type`          | string | yes      | `"notify"`                                   |
| `title`         | string | yes      | Notification title                           |
| `body`          | string | no       | Notification body text                       |
| `level`         | string | yes      | `"info"`, `"warning"`, `"urgent"`            |
| `notification_id` | string | yes    | Unique ID for ack tracking                   |
| `priority`      | int    | yes      | 0=LOW, 1=NORMAL, 2=HIGH, 3=URGENT           |
| `requires_ack`  | bool   | yes      | If `true`, device must send `notify_ack`     |
| `category`      | string | yes      | `"calendar"`, `"linear"`, `"deploy"`, `"reminder"`, `"system"`, `"checkin"`, `"general"` |
| `display_type`  | string | yes      | Display style: `"card"` (default)            |

#### Priority Levels

| Value | Name   | Behavior                                 |
|-------|--------|------------------------------------------|
| 0     | LOW    | Background ticker, no chime              |
| 1     | NORMAL | Standard notification, single chime      |
| 2     | HIGH   | Important, persistent until ack          |
| 3     | URGENT | Breaks quiet hours, persistent + repeated chime |

### `ack_received` — Ack Confirmation

Confirms that the bridge received the device's `notify_ack`.

**When sent**: By the bridge, immediately after processing a `notify_ack` from the device.

```json
{
  "type": "ack_received",
  "notification_id": "a1b2c3d4"
}
```

| Field            | Type   | Required | Description                     |
|-----------------|--------|----------|---------------------------------|
| `type`          | string | yes      | `"ack_received"`                |
| `notification_id` | string | yes    | ID of the acknowledged notification |

### `display` — Display Command

Controls what the Tab5 screen renders. The firmware's LVGL layer processes these
into different screen layouts.

**When sent**: By the bridge, to update the device screen (notifications,
transcripts, status dashboards, images).

#### Layout: `card`

Text card overlay — used for notifications and alerts.

```json
{
  "type": "display",
  "layout": "card",
  "title": "Deploy Complete",
  "body": "hermes-desk v2.1.0 deployed to staging",
  "level": "info",
  "notification_id": "a1b2c3d4"
}
```

#### Layout: `status`

Dashboard with key-value pairs.

```json
{
  "type": "display",
  "layout": "status",
  "items": [
    {"label": "Weather", "value": "72°F Sunny"},
    {"label": "Next Meeting", "value": "2:00 PM — Design Review"},
    {"label": "Build", "value": "✓ passing"}
  ]
}
```

#### Layout: `transcript`

Live voice transcript line (shown during conversation).

```json
{
  "type": "display",
  "layout": "transcript",
  "text": "What's the weather today?",
  "speaker": "user"
}
```

| Field     | Type   | Required | Values                  |
|----------|--------|----------|-------------------------|
| `text`   | string | yes      | Transcript text         |
| `speaker`| string | yes      | `"user"` or `"assistant"` |

#### Layout: `image`

Render a base64-encoded image on screen.

```json
{
  "type": "display",
  "layout": "image",
  "data": "iVBORw0KGgo...",
  "mime": "image/png",
  "caption": "Architecture diagram"
}
```

| Field     | Type   | Required | Description                    |
|----------|--------|----------|--------------------------------|
| `data`   | string | yes      | Base64-encoded image bytes     |
| `mime`   | string | yes      | MIME type (`image/png`, etc.)  |
| `caption`| string | no       | Optional caption text          |

#### Layout: `clear`

Clear the display back to idle state.

```json
{
  "type": "display",
  "layout": "clear"
}
```

---

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

## Device State Machine

```
IDLE --(ptt_press)-----> LISTENING --(ptt_release)--> PROCESSING
 ^                           |                            |
 |                           `--(abort)------------------'|
 |                                                        |
 |   PROCESSING --(tts:start)--> SPEAKING --(tts:stop)----'
 |                       `------(error)-------------------'
 `<---------(abort during SPEAKING)-----------'
```

## Barge-In

If the device sends `abort` during TTS playback, the bridge stops streaming
PCM frames and sends `tts: stop` + `status: idle`. The device should then
be ready for the next `listen: start`.

## HTTP API

### `POST /notify` — Send Notification

Enqueue a proactive notification for delivery to the connected Tab5.

```bash
curl -X POST http://bridge:8765/notify \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Meeting in 5 minutes",
    "body": "Standup with the team",
    "level": "info",
    "priority": 2,
    "requires_ack": true,
    "category": "calendar",
    "display_type": "card",
    "source": "hermes-cron"
  }'
```

**Response** (202 Accepted):

```json
{"status": "queued", "notification_id": "a1b2c3d4"}
```

If suppressed by quiet hours:

```json
{"status": "suppressed", "reason": "quiet_hours"}
```

### `GET /notify/history` — Notification History

Returns the last 20 notifications with delivery/ack timestamps.

```json
{
  "count": 3,
  "notifications": [
    {
      "id": "a1b2c3d4",
      "title": "Meeting in 5 minutes",
      "body": "Standup with the team",
      "level": "info",
      "priority": "HIGH",
      "category": "calendar",
      "delivered_at": 1690000000.0,
      "acked_at": 1690000005.0
    }
  ]
}
```

### `GET /health` — Health Check

```json
{
  "status": "ok",
  "devices": 1,
  "stt": "http://stt:9000",
  "tts": "http://tts:9001",
  "gateway": "http://hermes:3000"
}
```

## Proactive Notifications — Details

### Quiet Hours

Non-urgent notifications (priority < URGENT) are suppressed during quiet hours
(`POST /notify` returns `{"status": "suppressed", "reason": "quiet_hours"}`).
Configurable via `config.yaml` `notification.quiet_hours` section.

Default: 10 PM – 7 AM. URGENT notifications always break through.

### Acknowledgment

When `requires_ack: true`, the device must send `notify_ack` back:

```json
{"type": "notify_ack", "notification_id": "a1b2c3d4"}
```

The bridge confirms with `ack_received`. If no ack arrives within the configured
timeout (default 30s), the notification expires.

## Display Specifications

| Property   | Value                    |
|-----------|--------------------------|
| Panel     | 5" IPS, MIPI-DSI        |
| Resolution| 1280×720                 |
| Touch     | GT911 capacitive (I2C)   |
| UI Framework | LVGL 9.x              |

Note: Gemini's initial research cited 1024×600. The shipping M5Stack Tab5 uses a
1280×720 panel per the official Espressif BSP. LVGL reads the live panel size at
init, so code works regardless, but protocol documentation should use the correct
resolution.
