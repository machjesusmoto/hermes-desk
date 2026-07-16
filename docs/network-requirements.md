# Network Requirements

## Topology

```
Tab5 (WiFi) ──── LAN ──── Bridge Host (moto-agent-host)
                              │
                              ├── :8765  WebSocket (Tab5 ↔ Bridge)
                              ├── :8642  Hermes API Server
                              ├── :11436 STT (Nemotron 0.6B on GB10 fd05)
                              └── :11438 TTS (Google Neural2 on GB10 39e5)
```

## Bridge Host Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Linux (any) | Ubuntu 22.04+ |
| Docker | 20.10+ | Latest stable |
| RAM | 256 MB free | 512 MB free |
| Network | Same LAN as Tab5 | Wired Ethernet |
| Ports | 8765 (WebSocket) | 8765 + 8000 (health) |

## Service Endpoints

All endpoints must be reachable from the bridge host:

| Service | Endpoint | Protocol | Auth |
|---------|----------|----------|------|
| Hermes API | `http://localhost:8642/v1/chat/completions` | HTTP/SSE | Bearer token |
| STT | `http://10.0.2.60:11436/v1/audio/transcriptions` | HTTP (multipart) | None |
| TTS | `http://10.0.2.61:11438/v1/audio/speech` | HTTP (JSON→WAV) | None |
| Health | `http://localhost:8000/health` | HTTP | None |

## Tab5 → Bridge Connection

- Protocol: WebSocket (RFC 6455)
- Default URL: `ws://<bridge_host_ip>:8765`
- Audio format: PCM 16kHz 16-bit mono
- Frame size: 320 samples (20ms) per binary message
- Keepalive: Bridge sends ping every 30s, Tab5 responds with pong

## WiFi Requirements

- Band: 2.4 GHz (the ESP32-C6 does not support 5 GHz)
- Security: WPA2-PSK minimum
- DHCP: Required (Tab5 requests an IP via DHCP)
- Latency: <10ms to bridge host for responsive voice interaction
- Bandwidth: ~256 kbps bidirectional during active voice streaming

## Firewall Rules

If running a firewall on the bridge host, allow:

```bash
# WebSocket from LAN
sudo ufw allow from 10.0.2.0/24 to any port 8765 proto tcp

# Health check (optional, internal only)
sudo ufw allow from 127.0.0.1 to any port 8000 proto tcp
```

## Port Forwarding

Not required. The Tab5 connects outbound to the bridge. No inbound
connections needed on the Tab5. The bridge does not need to be
internet-facing.

## DNS / mDNS

The Tab5 connects by IP address. mDNS hostname resolution is possible
but not required. Configure the bridge IP in the Tab5 firmware via
`menuconfig` or environment variable.

## Network Troubleshooting

```bash
# Verify Tab5 can reach bridge
ping <bridge_host_ip>

# Verify bridge is listening
ss -tlnp | grep 8765

# Verify STT endpoint is up
curl -s http://10.0.2.60:11436/v1/models | jq .

# Verify TTS endpoint is up
curl -s http://10.0.2.61:11438/health | jq .

# Verify Hermes API is up
curl -s http://localhost:8642/health | jq .
```
