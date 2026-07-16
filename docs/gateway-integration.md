# Hermes Gateway Integration Protocol

How the Hermes Desk bridge connects to the Hermes Agent gateway API.

## Architecture

```
Tab5 (ESP32-P4)
    │ WebSocket
    ▼
Bridge Service (Docker, moto-agent-host)
    │ HTTP (OpenAI-compatible)
    ▼
Hermes Gateway API (localhost:8642)
    │ Internal agent pipeline
    ▼
Hermes Agent (Eos/Zephyr/Bolt routing, tools, memory)
```

## Gateway API Endpoint

The bridge uses the Hermes gateway's OpenAI-compatible API server:

- **Base URL:** `http://localhost:8642/v1`
- **Endpoint:** `POST /v1/chat/completions`
- **Auth:** `Authorization: Bearer *** for auth
- `X-Hermes-Session-Id: hermes-desk` for persistent session continuity
- Standard OpenAI Chat Completions format
- SSE streaming for real-time responses

This means MOT-80 is documentation + configuration, not code changes. Let me write the integration spec.<tool_call>
<function=write_file>
<parameter=content># Hermes Gateway Integration — Hermes Desk Bridge

## Architecture

```
Tab5 (ESP32-P4)  ←→  Bridge (Docker)  ←→  Hermes Gateway API (localhost:8642)
  voice/display        STT + TTS            agent processing
```

The bridge uses the **existing Hermes gateway API server** — no new platform adapter needed.

## API Endpoints

### Primary: Chat Completions (reactive conversation)

```
POST http://localhost:8642/v1/chat/completions
Authorization: Bearer <API_SERVER_KEY>
Content-Type: application/json
X-Hermes-Session-Id: hermes-desk
X-Hermes-Source: desk
```

Request body (OpenAI format):
```json
{
  "model": "default",
  "messages": [
    {"role": "user", "content": "<transcribed text from STT>"}
  ],
  "stream": true
}
```

Response: SSE stream of chat completion chunks. The bridge collects the full response text, sends it to TTS, and streams audio back to Tab5.

### Session Continuity

The `X-Hermes-Session-Id: hermes-desk` header ensures all desk conversations persist in a single session. This means:
- Hermes remembers context across interactions
- The session appears in `hermes sessions list` as "hermes-desk"
- Memory and skills load as normal

### Source Tagging

The `X-Hermes-Source: desk` header tags the interaction source. This lets:
- The agent know it's talking through the desk device
- Analytics/insights distinguish desk interactions from Discord/Slack/etc.
- Future routing rules target desk-specific behavior

### Auth

Use the existing `API_SERVER_KEY` from `~/.hermes/.env`. The bridge reads this from its Docker environment.

## Proactive Messages (M2 — MOT-82)

For Hermes-initiated conversation (reminders, alerts, nudges), the bridge exposes an HTTP endpoint that Hermes can call:

```
POST http://bridge:8080/api/notify
Content-Type: application/json

{
  "type": "notification|reminder|alert",
  "title": "Meeting in 15 minutes",
  "body": "You have a standup at 2pm",
  "priority": "normal|high|urgent",
  "audio": true,
  "display": true
}
```

The bridge:
1. Plays a notification chime on Tab5
2. Displays the message on screen
3. If `audio: true`, runs TTS and speaks the message
4. Waits for user acknowledgment (tap or voice)

### Webhook Subscription (for proactive pipeline)

```bash
hermes webhook subscribe desk-notifications \
  --prompt "Forward to Hermes Desk: {title} - {body}" \
  --deliver http://bridge:8080/api/notify \
  --secret "<generated-secret>"
```

## Audio Pipeline

### STT (Tab5 → Text)

1. Tab5 captures audio from dual mics
2. Streams raw audio over WebSocket to bridge
3. Bridge sends audio to STT service:
   - **Endpoint:** `http://10.0.2.60:11436/v1/audio/transcriptions` (Nemotron 0.6B on fd05)
   - **Format:** OpenAI Whisper-compatible API
4. Returns transcribed text

### TTS (Text → Tab5)

1. Bridge receives agent response text
2. Sends text to TTS service:
   - **Endpoint:** `http://10.0.2.61:11438/v1/audio/speech` (Google TTS on 39e5)
   - **Voice:** en-US-Neural2-F
   - **Format:** OpenAI TTS-compatible API
3. Receives audio bytes (MP3/PCM)
4. Streams audio back to Tab5 over WebSocket

## Display Commands

The bridge sends structured display commands to Tab5 over the WebSocket:

```json
{"type": "status", "state": "listening"}
{"type": "status", "state": "processing"}
{"type": "status", "state": "speaking"}
{"type": "transcript", "text": "You said: ..."}
{"type": "response", "text": "Hermes says: ..."}
{"type": "card", "title": "Reminder", "body": "Meeting in 15 min", "icon": "🔔"}
{"type": "error", "message": "STT failed, retrying..."}
```

Tab5 firmware (LVGL) renders these into appropriate screen layouts.

## Docker Configuration

The bridge runs as a Docker container on moto-agent-host:

```yaml
# docker-compose.yml
services:
  hermes-desk-bridge:
    build: .
    ports:
      - "8080:8080"    # WebSocket (Tab5) + HTTP API (proactive)
    environment:
      - HERMES_GATEWAY_URL=http://host.docker.internal:8642
      - HERMES_GATEWAY_KEY=${API_SERVER_KEY}
      - STT_URL=http://10.0.2.60:11436
      - TTS_URL=http://10.0.2.61:11438
      - TTS_VOICE=en-US-Neural2-F
      - SESSION_ID=hermes-desk
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
```

## Network Requirements

| Source | Dest | Port | Protocol | Purpose |
|--------|------|------|----------|---------|
| Tab5 (WiFi) | moto-agent-host | 8080 | WebSocket | Audio + display |
| Bridge (Docker) | moto-agent-host | 8642 | HTTP | Gateway API |
| Bridge (Docker) | fd05 (10.0.2.60) | 11436 | HTTP | STT |
| Bridge (Docker) | 39e5 (10.0.2.61) | 11438 | HTTP | TTS |

All traffic stays on the local network. No external endpoints.
