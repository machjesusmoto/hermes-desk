# Hermes Desk Bridge

Bridge service connecting the M5Stack Tab5 to the Hermes Agent gateway. Runs
on the host machine (Docker) and orchestrates the voice turn:

```
Tab5 <──WebSocket──> Bridge <──HTTP──> Hermes Gateway (:8642)
                        ├──> STT  (Nemotron 0.6B, gx10-fd05 :11436)
                        └──> TTS  (Google Neural2-F, gx10-39e5 :11438)
```

The bridge is **stateless per turn** — it holds no conversation memory. Hermes
owns that. The bridge only forwards a per-connection `session_id` so Hermes can
group a conversation.

## Status

M1 (MOT-79) — implemented and tested. 57 tests passing. The wire protocol is
defined in [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md); the Tab5 firmware
(MOT-78) implements the device side against it.

## Layout

```
bridge/
├── hermes_bridge/
│   ├── __init__.py
│   ├── config.py        # YAML + env layered config
│   ├── protocol.py      # JSON control messages + PCM framing
│   ├── audio.py         # PCM<->WAV, resample safety net, chunking
│   ├── stt.py           # OpenAI-compatible /v1/audio/transcriptions client
│   ├── tts.py           # OpenAI-compatible /v1/audio/speech client
│   ├── gateway.py       # Hermes gateway chat client
│   ├── session.py       # connected-device registry (for /notify)
│   ├── pipeline.py      # the turn: STT -> gateway -> TTS -> stream
│   └── server.py        # FastAPI app: /ws, /health, /notify + CLI
├── tests/               # pytest suite (57 tests)
├── config.example.yaml
├── .env.example
├── requirements.txt
├── requirements-test.txt
├── Dockerfile
└── pytest.ini
```

## Setup

### Local (dev)

```bash
cd bridge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit endpoints/keys
python -m hermes_bridge.server --config config.yaml
```

Any config value can be overridden by an env var without editing the file:
`HERMES_DESK_<SECTION>__<KEY>` (e.g. `HERMES_DESK_STT__BASE_URL`). See
`.env.example` for the full list.

### Docker (prod)

From the repo root:

```bash
cp bridge/.env.example bridge/.env   # edit secrets
docker compose up -d --build
```

The compose file wires `host.docker.internal` to the host gateway so the
container can reach the Hermes gateway at `localhost:8642` on the host. STT and
TTS hostnames (`gx10-fd05`, `gx10-39e5`) must resolve from inside the container
— put them in the host's `/etc/hosts` or a Docker network alias.

## Configuration

| Section  | Key              | Default                              | Notes |
|----------|------------------|--------------------------------------|-------|
| server   | host             | 0.0.0.0                              | |
| server   | port             | 8765                                 | |
| server   | token            | ""                                   | Tab5 auth token; empty = no auth |
| stt      | base_url         | http://gx10-fd05:11436/v1            | Nemotron 0.6B (OpenAI-compatible) |
| stt      | model            | nvidia/nemotron-0.6b-stt             | |
| tts      | base_url         | http://gx10-39e5:11438/v1            | Google Neural2-F (OpenAI-compatible) |
| tts      | voice            | neural2-F                            | |
| tts      | response_format  | wav                                  | bridge strips WAV header → raw PCM |
| gateway  | base_url         | http://host.docker.internal:8642     | Hermes gateway |
| gateway  | chat_path        | /v1/chat/completions                 | |
| audio    | frame_bytes      | 640                                  | 20ms @ 16k/16bit/mono |
| logging  | json             | false                                | structured logs for containers |

The Hermes gateway API key lives in Vault at
`secret/paperclip/services/hermes-gateway`. Inject it via
`HERMES_DESK_GATEWAY__API_KEY` (or the `.env` file) — never commit it.

## API

### WebSocket `/ws` — Tab5

See [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md) for the full message set.
- **Auth:** `?token=<BRIDGE_TOKEN>` query param or `Authorization: Bearer`.
- **Binary frames:** raw PCM 16-bit/16kHz/mono.
- **Text frames:** JSON control messages (`hello`, `listen`, `abort` in; `hello`,
  `stt`, `llm`, `tts`, `status`, `error`, `notify` out).

### HTTP

| Method | Path      | Auth | Purpose |
|--------|-----------|------|---------|
| GET    | `/health` | none | Liveness + dependency reachability + connected-device count |
| POST   | `/notify` | Bearer | Push a proactive notification to the connected Tab5 (`202` queued / `503` no device) |

`POST /notify` body: `{"title","body","level"}` (level: `info`|`warning`|`urgent`).

## Testing

```bash
cd bridge
pip install -r requirements-test.txt
pytest -q
```

The suite (57 tests) covers config loading + env overrides, protocol
parse/build, audio PCM<->WAV + resample, the STT/TTS/gateway clients against a
mocked httpx transport, the conversation pipeline (happy path + all failure
modes + barge-in), the session registry, and an end-to-end WebSocket turn via
FastAPI's TestClient.

## Error handling

Each pipeline stage maps to a protocol `error` code so the device can surface
the right state:

| Failure                 | Code              |
|-------------------------|-------------------|
| STT unreachable/failed  | `stt_failed`      |
| TTS unreachable/failed  | `tts_failed`      |
| Gateway 4xx/5xx         | `gateway_failed`  |
| Gateway timeout         | `gateway_timeout` |
| Malformed control frame | `bad_message`     |
| Bad auth token          | connection closed (1008) |

On any stage failure the turn ends and the bridge sends `status: idle` so the
device returns to a known state.
