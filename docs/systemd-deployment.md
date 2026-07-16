# Hermes Desk Bridge — Systemd Unit

For running the bridge without Docker, or alongside Docker on moto-agent-host.

## Option A: Docker Compose (recommended)

The bridge ships with a `docker-compose.yml` in `bridge/`. Use Docker's
built-in restart policy:

```bash
cd ~/hermes-desk/bridge
docker compose up -d
```

Docker handles restarts, logging, and lifecycle. No systemd unit needed.

## Option B: Systemd (native Python)

If you prefer running the bridge as a native systemd service:

### Install

```bash
# Copy the unit file
sudo cp bridge/hermes-bridge.service /etc/systemd/system/

# Create a dedicated user (optional)
sudo useradd -r -s /bin/false hermes-bridge

# Install dependencies
cd ~/hermes-desk/bridge
pip install -r requirements.txt

# Edit the unit file to set your config path
sudo nano /etc/systemd/system/hermes-bridge.service
# Update: EnvironmentFile, WorkingDirectory, ExecStart

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable --now hermes-bridge
```

### Manage

```bash
# Status
sudo systemctl status hermes-bridge

# Logs
journalctl -u hermes-bridge -f

# Restart after config change
sudo systemctl restart hermes-bridge

# Stop
sudo systemctl stop hermes-bridge
```

## Environment Variables

Create `/etc/hermes-bridge/env` (or wherever the unit file points):

```env
BRIDGE_WS_HOST=0.0.0.0
BRIDGE_WS_PORT=8765
BRIDGE_HEALTH_PORT=8000
BRIDGE_DEVICE_ID=desk
BRIDGE_DEVICE_NAME=Hermes Desk
BRIDGE_WAKE_WORD_ENABLED=false
HERMES_GATEWAY_URL=http://localhost:8642/v1/chat/completions
HERMES_GATEWAY_TOKEN=<your-api-server-key>
HERMES_GATEWAY_TIMEOUT=30
HERMES_GATEWAY_SESSION_ID=hermes-desk
HERMES_STT_URL=http://10.0.2.60:11436/v1/audio/transcriptions
HERMES_STT_MODEL=nvidia/nemotron-2-0.6b-sas
HERMES_TTS_URL=http://10.0.2.61:11438/v1/audio/speech
HERMES_TTS_MODEL=google/gentalk
HERMES_TTS_VOICE=fenrir
```

## Health Check

```bash
curl -s http://localhost:8000/health | jq .
```

Returns device status, uptime, active sessions, and component health.
