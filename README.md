# Hermes Desk

An ambient AI agent terminal built on the M5Stack Tab5, powered by [Hermes Agent](https://hermes-agent.nousresearch.com).

The agent sits on your desk. It listens, speaks, displays, and proactively reaches out. Zero friction, always present.

## Features

- **Voice Conversation** — push-to-talk, STT → Hermes → TTS, streamed back as PCM audio
- **Proactive Notifications** — Hermes initiates: reminders, alerts, nudges, check-ins
- **Passive Dashboard** — always-on display: time, weather, task count, build status
- **Active Visuals** — Hermes renders diagrams, charts, code, and pushes them to the screen

## Architecture

```
Tab5 (ESP32-P4)  <──WebSocket──>  Bridge (Docker)  <──HTTP──>  Hermes Gateway (:8642)
  dual mics / speaker                  ├── STT  Nemotron 0.6B  (gx10-fd05 :11436)
  5" IPS 1024x600                      └── TTS  Google Neural2-F (gx10-39e5 :11438)
```

The bridge is stateless per turn — Hermes owns conversation memory. See [`docs/PROTOCOL.md`](docs/PROTOCOL.md) for the wire protocol and [`bridge/README.md`](bridge/README.md) for the bridge service.

## Hardware

- M5Stack Tab5 (ESP32-P4, 5" IPS 1024×600, dual mics, speaker, WiFi 6)
- Host machine running Docker (same LAN)

## Milestones

- **M1: Voice Terminal** (In Progress)
  - [MOT-79] Bridge service — WebSocket server + STT/TTS pipeline ✅
  - [MOT-80] Gateway integration — API server protocol docs ✅
  - [MOT-78] Tab5 firmware — WebSocket client + audio capture/playback (next)
  - [MOT-81] Hardware setup and network documentation
- **M2: Proactive Notifications** (Planned)
- **M3: Passive Display** (Planned)
- **M4: Active Display** (Planned)

## Quick Start

```bash
# Bridge (Docker)
cp bridge/.env.example bridge/.env  # edit secrets
docker compose up -d --build

# Bridge (local dev)
cd bridge && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml  # edit endpoints
python -m hermes_bridge.server -c config.yaml
```

## License

MIT
