# Hermes Desk Bridge

Bridge service connecting the M5Stack Tab5 to Hermes Agent.

## Setup
pip install -r requirements.txt
cp config.example.yaml config.yaml
python hermes_bridge.py

## Architecture
Tab5 <--WebSocket--> Bridge <--HTTP--> Hermes Gateway
                      |
                      +---> STT service (Nemotron 0.6B)
                      +---> TTS service (Google TTS)

## API
### WebSocket (Tab5)
- Binary: PCM audio (16-bit, 16kHz)
- Text: JSON control messages

### HTTP (Hermes push)
- POST /notify -- proactive notification to Tab5
- GET /health -- bridge health check
