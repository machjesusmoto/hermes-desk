# Hermes Desk — Project Context

## Overview
Desktop AI assistant built on M5Stack Tab5 (ESP32-P4). Voice + display terminal for Hermes Agent. Physical presence on the desk — ambient, proactive, zero-friction.

## GitHub
Repo: https://github.com/machjesusmoto/hermes-desk (under machjesusmoto personal account)
Note: Originally created under eos-hermes-agent, transferred to Motoyuki-Solutions org, then transferred to machjesusmoto personal account when the org was deleted. The eos-hermes-agent GitHub account was also deleted.

## Linear
Project: "Hermes Desk" (ID: a6a832ec-e39e-4971-8abe-af26816958b2)
Team: Motoyuki-dev (e9848ffc-af19-4bf6-a567-29e9557282a3)

### Milestones
- M1: Voice Terminal (MOT-78, MOT-79, MOT-80, MOT-81)
- M2: Proactive Notifications (MOT-82, MOT-83)
- M3: Passive Display (MOT-84)
- M4: Active Display (MOT-85)

### Issues (42 points total)
| Issue | Title | Points | Status |
|-------|-------|--------|--------|
| MOT-78 | Tab5 firmware: WebSocket client + audio capture/playback | 8 | Todo |
| MOT-79 | Bridge service: WebSocket server + STT/TTS pipeline | 8 | Done |
| MOT-80 | Hermes gateway integration: voice channel handling | 3 | Done |
| MOT-81 | Hardware setup and network documentation | 2 | Todo |
| MOT-82 | Proactive notification pipeline | 5 | Todo |
| MOT-83 | Hermes cron jobs and event handlers | 3 | Todo |
| MOT-84 | Passive dashboard: always-on status display | 5 | Todo |
| MOT-85 | Active display: rich visual output | 8 | Todo |

## Architecture
```
Tab5 (ESP32-P4)  <──WebSocket──>  Bridge (Docker)  <──HTTP──>  Hermes Gateway (:8642)
  dual mics / speaker                  ├── STT  Nemotron 0.6B  (gx10-fd05 :11436)
  5" IPS 1024x600                      └── TTS  Google Neural2-F (gx10-39e5 :11438)
```

## Bridge Service (MOT-79 — DONE)
Package: bridge/hermes_bridge/ (9 modules)
- config.py: YAML + env-layered config (HERMES_DESK_<SECTION>__<KEY>)
- protocol.py: JSON control messages + PCM framing (docs/PROTOCOL.md)
- audio.py: PCM<->WAV, resample, chunking
- stt.py: OpenAI-compatible /v1/audio/transcriptions (httpx async)
- tts.py: OpenAI-compatible /v1/audio/speech, WAV→PCM
- gateway.py: Hermes gateway chat client (stateless per turn)
- session.py: connected-device registry for /notify
- pipeline.py: STT→gateway→TTS→stream with barge-in
- server.py: FastAPI /ws + /health + /notify

Tests: 57 passing (config, protocol, audio, clients, pipeline, session, server)
Packaging: Dockerfile, docker-compose.yml, .env.example, config.example.yaml

## Gateway Integration (MOT-80 — DONE)
No new platform adapter needed. Bridge talks to existing Hermes API server at localhost:8642/v1/chat/completions. Auth via API_SERVER_KEY, session via X-Hermes-Session-Id header.

## Key Decisions
- PCM 16k/16bit/mono (not Opus) — simpler on ESP32, no codec dependency
- Bridge stateless per turn — Hermes owns conversation memory
- Docker on moto-agent-host — isolated, standard deployment
- Tab5 auth via ?token= query param (easier for ESP32 WebSocket clients)
- Gateway API key from Vault: secret/paperclip/services/hermes-gateway, injected as HERMES_DESK_GATEWAY__API_KEY

## Bolt's Contribution
Bolt built the bridge service in his sandbox (55 tests) but couldn't push because his GitHub identity (bolt-hyperagent) was deleted. Eos reconstructed the bridge from the architecture discussion and pushed it directly. Bolt's Tab5 firmware work (MOT-78) is next.
