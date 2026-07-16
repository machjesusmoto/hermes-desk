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
| `notify_ack` | `notification_id`             | Acknowledge a notification |

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
| `notify` | `title`, `body`, `level`, `notification_id`, `priority`, `requires_ack`, `category`, `display_type` | Proactive notification from Hermes |
| `ack_received` | `notification_id`         | Confirmation that ack was received |
| `display` | `layout`, + layout-specific fields | Display command (see below) |

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

Hermes can push notifications to the Tab5 via `POST /notify` on the bridge's HTTP port.
The notification queue supports priority levels, quiet hours, acknowledgment tracking,
and delivery history.

```json
POST /notify
{
  "title": "Meeting in 5 minutes",
  "body": "Standup with the team",
  "level": "info",
  "priority": 2,
  "requires_ack": true,
  "category": "calendar",
  "display_type": "card",
  "source": "hermes-cron",
  "metadata": {}
}
```

### Priority Levels

| Value | Name | Behavior |
|-------|------|----------|
| 0 | LOW | Background ticker, no chime |
| 1 | NORMAL | Standard notification, single chime |
| 2 | HIGH | Important, persistent until ack |
| 3 | URGENT | Breaks quiet hours, persistent + repeated chime |

### Categories

`calendar`, `linear`, `deploy`, `reminder`, `system`, `checkin`, `general`

### Quiet Hours

Non-urgent notifications (priority < URGENT) are suppressed during quiet hours
(`POST /notify` returns `{"status": "suppressed", "reason": "quiet_hours"}`).
Configurable via `config.yaml` `notification.quiet_hours` section.

### Acknowledgment

When `requires_ack: true`, the device must send `notify_ack` back:

```json
{"type": "notify_ack", "notification_id": "abc123"}
```

The bridge confirms with `ack_received`. If no ack arrives within the configured
timeout (default 30s), the notification expires.

### History

`GET /notify/history` returns the last 20 notifications with delivery/ack timestamps.

## Display Commands

The bridge can push display commands to control the Tab5 screen.

| Layout | Fields | Description |
|--------|--------|-------------|
| `card` | `title`, `body`, `level`, `notification_id` | Text card overlay |
| `status` | `items: [{label, value}]` | Dashboard key-value pairs |
| `transcript` | `text`, `speaker` | Live voice transcript |
| `image` | `data` (base64), `mime`, `caption` | Render image on screen |
| `clear` | | Clear display to idle |

```json
{"type": "display", "layout": "card", "title": "Error", "body": "Deploy failed", "level": "error"}
```

## Audio Format

- Codec: raw PCM (no compression)
- Sample rate: 16000 Hz
- Bit depth: 16-bit signed little-endian
- Channels: 1 (mono)
- Frame size: 640 bytes (20ms of audio)

## Display Specifications

| Property | Value |
|----------|-------|
| Panel | 5" IPS, MIPI-DSI |
| Resolution | 1280×720 |
| Touch | GT911 capacitive (I2C) |
| UI Framework | LVGL 9.x |

Note: Gemini's initial research cited 1024×600. The shipping M5Stack Tab5 uses a
1280×720 panel per the official Espressif BSP. LVGL reads the live panel size at
init, so code works regardless, but protocol documentation should use the correct
resolution.
