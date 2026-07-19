# Hermes Desk — Hardware Setup Guide

Complete setup guide for the Hermes Desk: an ambient AI agent terminal built on
the **M5Stack Tab5** (ESP32-P4) that talks to the [Hermes Agent](https://hermes-agent.nousresearch.com)
through a host-side **bridge** service.

This document covers everything a new contributor needs to replicate the setup:
hardware, firmware, bridge, network, and troubleshooting. It is verified against
the repository's actual configuration, commands, filenames, and environment
variables (commit `bc01cb9`).

> **At a glance**
>
> ```
> Tab5 (ESP32-P4)  <──WebSocket──>  Bridge (Docker)  <──HTTP──>  Hermes Gateway (:8642)
>   dual mics / speaker                  ├── STT  Nemotron 0.6B  (gx10-fd05 :11436)
>   5" IPS 1280x720 + LVGL               └── TTS  Google Neural2-F (gx10-39e5 :11438)
> ```
>
> - **Audio wire format:** raw PCM, 16-bit signed LE, 16 kHz, mono, 640 B (20 ms) frames
> - **Bridge is stateless per turn** — Hermes owns conversation memory

---

## Table of Contents

1. [Hardware](#1-hardware)
2. [Firmware](#2-firmware)
3. [Bridge](#3-bridge)
4. [Network](#4-network)
5. [Troubleshooting](#5-troubleshooting)

---

## 1. Hardware

### 1.1 The terminal: M5Stack Tab5

The Tab5 is the physical desk device. It captures audio, streams it to the
bridge over WebSocket, renders the bridge's display commands on its panel with
LVGL, and plays back TTS audio.

| Part | Detail |
|------|--------|
| Main SoC | **ESP32-P4** (RISC-V), 16 MB Flash, 32 MB PSRAM — **no built-in radio** |
| WiFi/BLE | **ESP32-C6-MINI-1U** co-processor over **SDIO** (`esp_hosted`) |
| Display | 5" **1280×720** IPS, MIPI-DSI, capacitive touch |
| Touch controller | **ST7123** (I2C), driven in **polling mode** (see [§5.2](#52-touch-st7123---currently-non-functional)) |
| Audio — speaker | **ES8388** DAC (I2S) |
| Audio — microphones | **ES7210** ADC, dual-channel with AEC front-end (I2S) |
| IO expanders | Two **PI4IOE5V6408** expanders on I2C — one drives backlight, the other powers the C6 and speaker amp |
| Power | USB-C (~500 mA during audio streaming) |

> **Note on display resolution.** Some older docs (`README.md`, `CONTEXT.md`)
> mention a 1024×600 panel. The **shipping M5Stack Tab5 has a 1280×720 MIPI-DSI
> panel** per the official Espressif BSP, confirmed in `firmware/sdkconfig.defaults`
> (PSRAM-backed 1280×720 framebuffer) and `docs/PROTOCOL.md`. The LVGL UI reads
> the live panel size at init, so code works regardless, but 1280×720 is the
> correct figure.

> **Note on the touch controller.** The Tab5 BSP nominally references a GT911
> touch controller, but board v2 does **not** have a GT911 — an I2C scan found a
> device at 0x5d but GT911 init failed at both 0x5d and 0x14. The actual
> controller is **ST7123**, registered by the BSP in interrupt mode (GPIO 23).
> See [§5.2](#52-touch-st7123---currently-non-functional) for its current status.

### 1.2 Where to buy

- **M5Stack Tab5** — official store: <https://shop.m5stack.com/products/tab5>
  (search "M5Stack Tab5"). Sold direct from M5Stack and through their authorized
  distributors (M5Stack official Amazon store, Mouser, Digi-Key carry select
  M5Stack products — confirm Tab5 SKU availability before ordering).
- **USB-C cable** — data + power, for flashing the P4 firmware and (if using
  M5Stack's `flash.sh`) the C6 co-processor.

> The repo does not pin a specific Tab5 hardware revision; firmware targets
> ESP32-P4 **v1.x (eco2 silicon)** — see `sdkconfig.defaults`
> (`CONFIG_ESP32P4_REV_MIN_0=y`), which deliberately allows any rev so the
> bootloader does not reject v1.3 silicon.

### 1.3 Host machine (bridge)

The bridge runs in Docker on a host on the **same LAN** as the Tab5.

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Linux (any) | Ubuntu 22.04+ |
| Docker | 20.10+ | Latest stable |
| RAM | 256 MB free | 512 MB free |
| Network | Same LAN as Tab5 | Wired Ethernet |
| Reachable services | STT, TTS, Hermes gateway | STT, TTS, Hermes gateway |

The reference deployment host is `moto-agent-host`, which also runs the Hermes
gateway API server on `localhost:8642`.

---

## 2. Firmware

Firmware lives in `firmware/` and targets the ESP32-P4. It is **MOT-78**
(Milestone M1 — Voice Terminal). The bridge it talks to is **MOT-79** (complete,
in `bridge/`).

### 2.1 Prerequisites — ESP-IDF

- **ESP-IDF v5.4+ minimum, developed against v5.5.4.**
  - `firmware/build_and_flash.sh` sets `ESP_IDF_VERSION=5.5`
  - `firmware/CMakeLists.txt`: "Requires ESP-IDF v5.4+ (developed against 5.5.x)"
  - `firmware/idf_component.yml`: `idf: version: ">=5.4"`
  - Commit `e4c9d1f`: "API compatibility fixes for ESP-IDF v5.5.4"

Install per the [official guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/get-started/):

```bash
git clone --recursive https://github.com/espressif/esp-idf.git
cd esp-idf && ./install.sh esp32p4
. ./export.sh
```

You also need a USB-C cable to the Tab5's USB-UART port for flashing/monitoring.

> The ESP-IDF toolchain targets the P4 specifically — **you cannot build the
> P4 firmware in a generic sandbox.** Build on a machine with ESP-IDF installed.

### 2.2 Firmware layout

```
firmware/
├── CMakeLists.txt            # project (IDF)
├── idf_component.yml         # managed-component deps
├── sdkconfig.defaults        # P4+C6 SDIO pins, audio rate, LVGL, LWIP tuning
├── partitions.csv            # 16 MB layout incl. slave_fw (C6) partition
├── build_and_flash.sh        # convenience build+flash script (paths are dev-specific)
├── wifi_c6_fw/               # drop the C6 esp_hosted slave .bin here (git-ignored)
├── main/
│   ├── include/              # protocol.h, app_state.h, wifi_connect.h, ptt.h
│   ├── src/
│   │   ├── main.c            # boot sequence + inbound JSON dispatch
│   │   ├── app_state.c       # device FSM (idle/listen/process/speak/error)
│   │   ├── wifi_connect.c    # esp_hosted → esp_wifi bring-up (order matters!)
│   │   └── ptt.c             # GPIO35 push-to-talk (legacy; see §2.6)
│   └── Kconfig.projbuild     # WiFi SSID/pass + bridge host/token menuconfig
└── components/
    ├── hermes_ws/            # WebSocket client (text=JSON, binary=PCM)
    ├── hermes_audio/         # I2S capture (ES7210→mono) + playback (ES8388)
    └── hermes_display/       # LVGL UI + touch-to-talk
```

Managed components (resolved on first `idf.py build` from `idf_component.yml`):

- `espressif/m5stack_tab5` — BSP: display, touch, audio codecs, IO expanders
- `espressif/esp_lvgl_port` — LVGL port + task
- `espressif/esp_websocket_client` — WebSocket client
- `espressif/esp_hosted` — ESP32-C6 SDIO transport
- `espressif/esp_wifi_remote` — WiFi stack routed over esp_hosted

### 2.3 Flash the ESP32-C6 co-processor (ONE TIME)

The ESP32-P4 has **no radio** — WiFi lives on the C6, which must run Espressif's
`esp_hosted` **slave** firmware. M5Stack ships a prebuilt image.

1. Download the C6 slave firmware from the
   [M5Tab5-UserDemo repo](https://github.com/m5stack/M5Tab5-UserDemo/tree/main/platforms/tab5/wifi_c6_fw):
   **`ESP32C6-WiFi-SDIO-Interface-V1.4.1-96bea3a_0x0.bin`**
   (filename is pinned in `firmware/wifi_c6_fw/.gitkeep`).
2. Place it at `firmware/wifi_c6_fw/` (the directory is git-ignored — only
   `.gitkeep` is committed) **or** use M5Stack's `flash.sh` to program the C6
   directly over its PROG header (requires an ESP-Prog or a USB cable to the
   C6's port — see the M5Stack docs).
3. The `slave_fw` partition in `partitions.csv` reserves 2 MB at offset
   `0x720000` so the P4 can hold and load the C6 image over SDIO at boot.

If you are flashing the C6 binary directly with esptool (e.g. via an ESP-Prog),
the command shape is:

```bash
# From the firmware directory — adjust --port to your C6 PROG port
esptool.py --chip esp32c6 --port /dev/ttyACM0 --baud 921600 \
  write_flash 0x0 wifi_c6_fw/ESP32C6-WiFi-SDIO-Interface-V1.4.1-96bea3a_0x0.bin
```

> **SDIO pin map (authoritative, from `sdkconfig.defaults`).** Do **not** rely on
> older notes that quote GPIO 30–37 / RESET 28 — those are wrong for the Tab5.
> The Tab5's actual esp_hosted SDIO pins are:
>
> | Signal | GPIO | Signal | GPIO |
> |--------|------|--------|------|
> | CMD    | 13   | D2     | 9    |
> | CLK    | 12   | D3     | 8    |
> | D0     | 11   | RESET  | 15   |
> | D1     | 10   |        |      |
>
> Reset polarity is **active-HIGH** (`CONFIG_ESP_HOSTED_SDIO_RESET_ACTIVE_HIGH=y`)
> and the reset strategy is `ONLY_IF_NECESSARY`. Background: `esp-hosted-mcu`
> issue #127.

### 2.4 Configure (menuconfig)

```bash
cd firmware
idf.py set-target esp32p4
idf.py menuconfig
```

Under **Hermes Desk Configuration** set (from `main/Kconfig.projbuild`):

| Option | Default in repo | Notes |
|--------|-----------------|-------|
| `WIFI_SSID` | `motowireless50` | Your 2.4 GHz network SSID |
| `WIFI_PASS` | *(set in Kconfig)* | WPA2 PSK |
| `BRIDGE_HOST` | `192.168.54.193` | Host/IP of the bridge |
| `BRIDGE_PORT` | `8765` | WebSocket port |
| `BRIDGE_TOKEN` | *(blank)* | Matches `server.token` in bridge config; blank = no auth |

> The committed Kconfig defaults contain real WiFi credentials and a bridge IP.
> Treat them as the original author's LAN values — **override them with your
> own** via `menuconfig` before flashing on a different network.

### 2.5 Build and flash the P4 firmware

```bash
cd firmware
idf.py build
idf.py -p /dev/ttyACM0 -b 921600 flash monitor
```

(`Ctrl+]` exits the monitor. On macOS the port is typically `/dev/cu.usbmodem*`.)

A convenience script `firmware/build_and_flash.sh` exists, but its paths
(`IDF_PATH=/home/dtaylor/esp-idf`, the `dtaylor` python env, `/dev/ttyACM1`) are
specific to the original developer's machine — edit it before using it on
yours.

On a successful boot you should see (via `idf.py monitor`):

```
wifi: got IP: 192.168.x.x
hermes_ws: connected to ws://<BRIDGE_HOST>:8765/ws
hermes_ws: hello sent (NN bytes) — device_id=tab5-xxxxxx
main: init complete — idle, waiting for touch
```

### 2.6 Push-to-talk: touch-to-talk (no physical button)

> **Important:** the Tab5 has **no usable physical button** — the only button is
> reset. Push-to-talk is implemented as **touch-to-talk** on the touchscreen:
> press-and-hold the screen = listen, release = send.

This is wired in `firmware/main/src/main.c`:

```c
/* 6. Touch-to-talk (no physical button on the Tab5). */
hermes_display_register_touch_ptt();
```

`hermes_display_register_touch_ptt()` (in `components/hermes_display/src/hermes_display.c`)
registers `LV_EVENT_PRESSED` / `LV_EVENT_RELEASED` callbacks on all layout
containers, which feed `APP_EVT_PTT_PRESS` / `APP_EVT_PTT_RELEASE` into the
device FSM. The FSM itself is unchanged from the original PTT design.

`firmware/main/src/ptt.c` still contains a GPIO35 BOOT-button ISR implementation
(`ptt_init()`), but `main.c` does **not** call it on the Tab5 — it is left as
parallel/legacy code. Do not expect a GPIO35 button to work out of the box.

> **Caveat:** touch-to-talk depends on the ST7123 touch controller, which is
> currently **not detecting touches** at the hardware level. See
> [§5.2](#52-touch-st7123---currently-non-functional). Until that is resolved,
> the device cannot be driven by touch. A workaround is to wire an external
> button to GPIO35 and call `ptt_init()` from `main.c`.

### 2.7 Cold-boot sequence (load-bearing order)

The WiFi bring-up order in `firmware/main/src/wifi_connect.c` is critical and
**must not be reordered**:

1. `nvs_flash_init()` — config + WiFi creds
2. `esp_netif_init()` + default event loop — **must exist before `esp_hosted_init()`**
   (otherwise `assert failed: tcpip_send_msg_wait_sem`)
3. `bsp_feature_enable(BSP_FEATURE_WIFI, true)` — powers on the C6 via the second
   PI4IOE5V6408 IO expander (pin 0, `BSP_WIFI_EN`)
4. `vTaskDelay(500ms)` — let the C6 boot its firmware
5. `esp_hosted_init()` — SDIO transport + protocol handshake
6. `esp_wifi_init()` + `start()` + `connect()`

Pre-app_main auto-init is **disabled**
(`CONFIG_ESP_HOSTED_AUTO_CALL_INIT_BEFORE_APP_MAIN=n`) because it runs before
`app_main`, when the C6 has no power, and corrupts the transport state for the
rest of boot.

---

## 3. Bridge

The bridge is a FastAPI service in `bridge/` that owns the voice turn: it
receives PCM from the Tab5, transcribes it (STT), queries Hermes (gateway),
synthesizes a reply (TTS), and streams audio back. It also exposes an HTTP
endpoint for proactive notifications. It is **stateless per turn** — Hermes owns
conversation memory via the `X-Hermes-Session-Id` header.

### 3.1 Layout

```
bridge/
├── hermes_bridge/
│   ├── config.py        # YAML + env-layered config (HERMES_DESK_<SECTION>__<KEY>)
│   ├── protocol.py      # JSON control messages + PCM framing
│   ├── audio.py         # PCM<->WAV, resample safety net, chunking
│   ├── stt.py           # OpenAI-compatible /v1/audio/transcriptions client
│   ├── tts.py           # OpenAI-compatible /v1/audio/speech client
│   ├── gateway.py       # Hermes gateway chat client
│   ├── session.py       # connected-device registry (for /notify)
│   ├── pipeline.py      # the turn: STT -> gateway -> TTS -> stream
│   ├── notification.py  # proactive notification queue + quiet hours
│   ├── cron_templates.py
│   └── server.py        # FastAPI: /ws, /health, /notify, /notify/history + CLI
├── tests/               # pytest suite
├── config.example.yaml  # canonical config template
├── .env.example         # canonical env template (HERMES_DESK_* keys)
├── requirements.txt
├── requirements-test.txt
├── Dockerfile
├── docker-compose.yml   # NOTE: this file maps port 8080 — stale; use the repo-root compose (port 8765)
└── pytest.ini
```

### 3.2 Configuration system (authoritative)

The bridge loads config in this precedence: **defaults → YAML file → env vars**.

- **YAML:** copy `bridge/config.example.yaml` to `bridge/config.yaml` and edit.
- **Env overlay:** any value can be overridden with
  `HERMES_DESK_<SECTION>__<KEY>` (double underscore). The code that does this is
  `bridge/hermes_bridge/config.py`.

> ⚠️ **Use the `HERMES_DESK_*` keys.** Some files in the repo
> (`bridge/config/example.env` and `docs/systemd-deployment.md`) list a
> different, **legacy** env-var set (`HERMES_GATEWAY_URL`, `STT_URL`,
> `HERMES_API_KEY`, `BRIDGE_PORT`, etc.). The bridge code **does not read
> those** — `config.py` only reads `HERMES_DESK_*`. The legacy files are
> outdated; rely on `bridge/config.example.yaml` and `bridge/.env.example`.

### 3.3 Config reference (from `config.example.yaml` + `config.py` defaults)

| Section   | Key              | Default                          | Notes |
|-----------|------------------|----------------------------------|-------|
| `server`  | host             | `0.0.0.0`                        | |
| `server`  | port             | `8765`                           | WebSocket + HTTP port |
| `server`  | token            | `""`                             | Tab5 auth token; empty = no auth |
| `stt`     | base_url         | `http://gx10-fd05:11436/v1`      | Nemotron 0.6B (OpenAI-compatible) |
| `stt`     | model            | `nvidia/nemotron-0.6b-stt`       | |
| `stt`     | timeout          | `10.0`                           | seconds |
| `tts`     | base_url         | `http://gx10-39e5:11438/v1`      | Google Neural2-F (OpenAI-compatible) |
| `tts`     | voice            | `neural2-F`                      | |
| `tts`     | model            | `tts-1`                          | |
| `tts`     | response_format  | `wav`                            | bridge strips WAV header → raw PCM |
| `tts`     | timeout          | `15.0`                           | seconds |
| `gateway` | base_url         | `http://host.docker.internal:8642` | Hermes gateway |
| `gateway` | chat_path        | `/v1/chat/completions`           | |
| `gateway` | api_key          | `""`                             | from Vault: `secret/paperclip/services/hermes-gateway` |
| `gateway` | model            | `hermes`                         | |
| `gateway` | session_id       | `hermes-desk`                    | forwarded as `X-Hermes-Session-Id` |
| `gateway` | timeout          | `30.0`                           | seconds |
| `audio`   | sample_rate      | `16000`                          | |
| `audio`   | bits             | `16`                             | |
| `audio`   | channels         | `1`                              | mono |
| `audio`   | frame_bytes      | `640`                            | 20 ms @ 16 k/16-bit/mono |
| `log`     | level            | `INFO`                           | |
| `log`     | json             | `false`                          | structured logs for containers |

Env-var equivalents (override any of the above):

```bash
HERMES_DESK_GATEWAY__API_KEY=<vault key>
HERMES_DESK_STT__BASE_URL=http://gx10-fd05:11436/v1
HERMES_DESK_TTS__BASE_URL=http://gx10-39e5:11438/v1
HERMES_DESK_SERVER__TOKEN=<optional tab5 auth token>
HERMES_DESK_GATEWAY__BASE_URL=http://host.docker.internal:8642
HERMES_DESK_GATEWAY__SESSION_ID=hermes-desk
# ...and so on for every key above
```

### 3.4 Docker setup (recommended for production)

The canonical Docker Compose file is at the **repo root** (`docker-compose.yml`),
not `bridge/docker-compose.yml`. It builds from `bridge/Dockerfile` and maps
**port 8765**.

`docker-compose.yml` (repo root):

```yaml
services:
  bridge:
    build:
      context: ./bridge
      dockerfile: Dockerfile
    ports:
      - "8765:8765"
    env_file:
      - ./bridge/.env
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # STT/TTS hostnames must resolve from inside the container.
    # Add to /etc/hosts or use Docker network aliases.
    restart: unless-stopped
```

`bridge/Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hermes_bridge/ hermes_bridge/
EXPOSE 8765
CMD ["python", "-m", "hermes_bridge.server"]
```

Bring it up:

```bash
cp bridge/.env.example bridge/.env   # edit secrets (HERMES_DESK_GATEWAY__API_KEY, etc.)
docker compose up -d --build
docker compose logs -f bridge
```

The compose file wires `host.docker.internal` to the host gateway so the
container can reach the Hermes gateway at `localhost:8642` on the host. STT and
TTS hostnames (`gx10-fd05`, `gx10-39e5`) must resolve from inside the container —
put them in the host's `/etc/hosts` or use a Docker network alias.

> ⚠️ **`bridge/docker-compose.yml` is stale.** It maps port `8080:8080`, uses an
> outdated healthcheck against `:8080/health`, and reads `config/.env`. The code
> listens on **8765** and reads `bridge/.env`. Use the **repo-root**
> `docker-compose.yml` instead.

### 3.5 Local dev (no Docker)

```bash
cd bridge
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit endpoints/keys
python -m hermes_bridge.server --config config.yaml
```

Tests:

```bash
cd bridge
pip install -r requirements-test.txt
pytest -q
```

### 3.6 HTTP / WebSocket endpoints

| Method | Path              | Auth      | Purpose |
|--------|-------------------|-----------|---------|
| WS     | `/ws`             | `?token=` or `Authorization: Bearer` | Tab5 control + PCM channel |
| GET    | `/health`         | none      | Liveness + dependency URLs + connected-device count |
| POST   | `/notify`         | (route)   | Push a proactive notification to the connected Tab5 (`202` queued / `503` no device) |
| GET    | `/notify/history` | (route)   | Last 20 notifications with delivery/ack timestamps |

See `docs/PROTOCOL.md` for the full message set.

---

## 4. Network

### 4.1 Topology

```
Tab5 (WiFi) ──── LAN ──── Bridge Host (moto-agent-host)
                              │
                              ├── :8765  WebSocket (Tab5 ↔ Bridge)  + /health, /notify
                              ├── :8642  Hermes API Server (on the host)
                              ├── :11436 STT (Nemotron 0.6B on gx10-fd05)
                              └── :11438 TTS (Google Neural2-F on gx10-39e5)
```

### 4.2 Service endpoints

All endpoints must be reachable from the bridge host (or, for STT/TTS/gateway,
from inside the bridge container — see [§3.4](#34-docker-setup-recommended-for-production)):

| Service | Endpoint | Protocol | Auth |
|---------|----------|----------|------|
| Bridge WebSocket | `ws://<bridge_host_ip>:8765/ws?token=<BRIDGE_TOKEN>` | WebSocket | `?token=` or `Bearer` |
| Bridge health | `http://<bridge_host_ip>:8765/health` | HTTP | none |
| Hermes gateway | `http://host.docker.internal:8642/v1/chat/completions` | HTTP (OpenAI-compatible) | `Authorization: Bearer <HERMES_DESK_GATEWAY__API_KEY>` |
| STT | `http://gx10-fd05:11436/v1/audio/transcriptions` | HTTP (multipart) | none (`api_key: unused`) |
| TTS | `http://gx10-39e5:11438/v1/audio/speech` | HTTP (JSON → WAV) | none (`api_key: unused`) |

> The bridge forwards the gateway session id as an `X-Hermes-Session-Id:
> hermes-desk` header so all desk turns group into one Hermes conversation.

### 4.3 WiFi requirements

- **Band: 2.4 GHz** — the ESP32-C6 does **not** support 5 GHz.
- **Security:** WPA2-PSK minimum.
- **DHCP:** required (the Tab5 requests an IP via DHCP).
- **Latency:** < 10 ms to the bridge host for responsive voice.
- **Bandwidth:** ~256 kbps bidirectional during active voice streaming.

Configure WiFi in the firmware via `idf.py menuconfig`
(**Hermes Desk Configuration → WiFi SSID / Password**), or override the
Kconfig defaults. See [§2.4](#24-configure-menuconfig).

### 4.4 Tab5 → Bridge connection

- **Protocol:** WebSocket (RFC 6455), plain `ws://` (no TLS)
- **URL:** `ws://<bridge_host_ip>:8765/ws?token=<BRIDGE_TOKEN>`
- **Audio:** raw PCM, 16-bit signed LE, 16 kHz, mono, 640 B (20 ms) per binary frame
- **Control:** text frames = JSON control messages (`hello`, `listen`, `abort` in;
  `hello`, `stt`, `llm`, `tts`, `status`, `error`, `notify` out)
- **Keepalive:** client sends ping every 5 s (`ping_interval_sec=5` in `hermes_ws.c`)
- **Reconnect:** automatic, 3 s interval (`reconnect_timeout_ms=3000`)

### 4.5 Firewall rules

If running a firewall on the bridge host, allow the WebSocket from the LAN:

```bash
# WebSocket from LAN (adjust CIDR to your network)
sudo ufw allow from 10.0.2.0/24 to any port 8765 proto tcp
```

### 4.6 No port forwarding required

The Tab5 connects **outbound** to the bridge. No inbound connections are needed
on the Tab5, and the bridge does not need to be internet-facing.

### 4.7 Verifying the network

```bash
# Bridge host: is the bridge listening?
ss -tlnp | grep 8765

# Bridge host: is STT up?
curl -s http://gx10-fd05:11436/v1/models | jq .

# Bridge host: is TTS up?
curl -s http://gx10-39e5:11438/health

# Bridge host: is the Hermes gateway up?
curl -s http://localhost:8642/health

# Bridge host: bridge self-report (dependency URLs + connected devices)
curl -s http://localhost:8765/health | jq .

# From the Tab5's LAN: can the Tab5 reach the bridge?
ping <bridge_host_ip>
```

---

## 5. Troubleshooting

### 5.1 WebSocket: hello deadlock (fixed in `bc01cb9`)

**Symptom (pre-fix):** the Tab5 connected to the bridge but the `hello`
handshake never completed; the connection timed out and closed.

**Root cause:** the WebSocket event-handler task called
`esp_websocket_client_send_text()` to send `hello`. `send_text` blocks waiting
for the send queue to clear, but the event-handler task is the same task that
processes incoming data — so it deadlocked: `send_text` waited for the queue,
the event task could not process the response, and the connection timed out.

**Fix (commit `bc01cb9`):** `hello` is now sent from a short-lived separate task
(`hermes_ws_send_hello_task`) with a 50 ms delay so the event handler returns
first. The same commit also raised `buffer_size` to 8192, `task_stack` to 12288,
added `ping_interval_sec=5`, and raised `network_timeout_ms` to 10000.

If you reintroduce hello-sending from inside the event handler, you will
re-trigger this deadlock. Keep hello on its own task.

### 5.2 Touch: ST7123 — currently non-functional

**Status:** display, WiFi, audio, bridge link, and the C6 SDIO link all work.
**Touch does not.**

The Tab5's touch controller is the **ST7123** (not GT911 — see [§1.1](#11-the-terminal-m5stack-tab5)).
The BSP registers it in interrupt mode on GPIO 23; the firmware switches it to
**polling mode** (`lv_indev_set_mode(indev, LV_INDEV_MODE_TIMER)` in
`hermes_display_init`) so LVGL reads touch data on every timer tick.

Observed behavior (commit `f43ab90`):

- The ST7123 initializes — I2C responds and the firmware-version register reads.
- The controller is "alive" — the counter at register `0x000b` increments.
- But register `0x0010`'s `adv_info.with_coord` bit **never gets set**, so no
  touch coordinates are ever produced. Touch is not detected at the hardware
  level.

Possible causes (unconfirmed):

- ST7123 touch scanning may need a DSI-side command to enable.
- Display-refresh dependency (touch tied to video frame timing).
- BSP component version mismatch with actual hardware.

**Impact:** because push-to-talk is touch-to-talk on this device
([§2.6](#26-push-to-talk-touch-to-talk-no-physical-button)), the device cannot
currently be driven end-to-end without a workaround.

**Workaround:** wire an external active-low button to **GPIO35** and call
`ptt_init()` from `main.c` (the `ptt.c` ISR implementation already exists and
drives the same `APP_EVT_PTT_PRESS`/`RELEASE` events the FSM expects).

### 5.3 WiFi won't come up: `sdmmc_init_ocr: send_op_cond returned 0x107`, MAC `00:00…`

This means the P4 cannot talk to the C6 over SDIO. Almost always one of:

| Check | Fix |
|-------|-----|
| C6 slave firmware flashed? | Flash `ESP32C6-WiFi-SDIO-Interface-V1.4.1-96bea3a_0x0.bin` to the C6 (see [§2.3](#23-flash-the-esp32-c6-co-processor-one-time)) |
| SDIO GPIOs correct? | Verify against `sdkconfig.defaults`: CMD=13, CLK=12, D0=11, D1=10, D2=9, D3=8, RESET=15 (see [§2.3](#23-flash-the-esp32-c6-co-processor-one-time)) |
| Reset polarity? | Must be active-HIGH (`CONFIG_ESP_HOSTED_SDIO_RESET_ACTIVE_HIGH=y`) |
| C6 powered? | `bsp_feature_enable(BSP_FEATURE_WIFI, true)` must run before `esp_hosted_init()` — see [§2.7](#27-cold-boot-sequence-load-bearing-order) |
| Boot order? | `esp_netif_init()` + event loop **before** `esp_hosted_init()`, or `assert failed: tcpip_send_msg_wait_sem` |
| Pre-app_main auto-init? | Must be **disabled** (`CONFIG_ESP_HOSTED_AUTO_CALL_INIT_BEFORE_APP_MAIN=n`) — it runs before the C6 is powered and corrupts transport state |
| Power cycle | Power-cycle the entire Tab5 (not just reset) after flashing the C6 |

Background: `esp-hosted-mcu` issue #127.

### 5.4 WiFi connects, but Tab5 can't reach the bridge

**Symptom:** `wifi: got IP` succeeds, but `hermes_ws: disconnected` / no bridge `hello_ack`.

| Check | Fix |
|-------|-----|
| Bridge running? | `docker ps \| grep bridge` on the bridge host |
| Port 8765 open? | `ss -tlnp \| grep 8765` on bridge host |
| Firewall? | `sudo ufw status` — allow 8765 from LAN (see [§4.5](#45-firewall-rules)) |
| Correct IP? | Verify `BRIDGE_HOST` in firmware `menuconfig` matches the bridge host |
| Bridge logs? | `docker compose logs --tail 20 bridge` for connection errors |
| Token mismatch? | If `server.token` is set on the bridge, `BRIDGE_TOKEN` in firmware must match |

### 5.5 No audio / silent microphone

| Check | Fix |
|-------|-----|
| Codec init? | Check boot log for codec open errors from `hermes_audio` |
| I2S pinout? | The codec stack is brought up by the BSP — confirm you're on the supported Tab5 revision |
| Mic gain? | The ES7210 default gain may be low; adjust in `hermes_audio.c` |
| Sample rate? | Both codecs run at 16 kHz to match the bridge — no on-device resample. Verify in `hermes_audio.c` |

### 5.6 STT returns garbage / wrong transcription

| Check | Fix |
|-------|-----|
| Sample rate? | Tab5 must send 16 kHz. Check the I2S config in `hermes_audio.c` |
| Byte order? | ESP32 is little-endian; the bridge expects little-endian PCM |
| Mic gain too high? | Clipping breaks STT — reduce gain |
| STT model up? | `curl http://gx10-fd05:11436/v1/models` — confirm Nemotron is serving |

### 5.7 TTS playback is choppy or distorted

| Check | Fix |
|-------|-----|
| WAV header stripped? | TTS returns WAV (`response_format: wav`); the bridge strips the header and sends raw PCM |
| Buffer underrun? | Playback ring buffer is ~640 ms (`HERMES_AUDIO_FRAME_BYTES * 32`). If gaps persist, investigate WS latency |
| Network latency? | > 50 ms RTT causes audible gaps — use wired Ethernet for the bridge host |
| Speaker codec init? | Check boot log for ES8388 init errors |

### 5.8 Bridge crashes on startup

| Check | Fix |
|-------|-----|
| `.env` present? | `bridge/.env` must exist with required `HERMES_DESK_*` values (use the repo-root compose, which reads `./bridge/.env`) |
| Port conflict? | `ss -tlnp \| grep 8765` — another service on 8765? |
| Python traceback? | `docker compose logs bridge` |
| Bad deps? | `docker compose build --no-cache` |

### 5.9 Voice response is slow

| Check | Fix |
|-------|-----|
| STT latency? | Bridge logs `stt` timing — should be < 500 ms |
| Gateway latency? | First token should be < 2 s |
| TTS latency? | Should be < 1 s |
| Network? | `ping` from bridge to STT/TTS/gateway hosts — should be < 1 ms on LAN |
| Cold start? | First request after idle may be slow (model load); subsequent should be fast |

### 5.10 Auto-reconnect not working

| Check | Fix |
|-------|-----|
| Reconnect timer? | Client default is 3 s (`reconnect_timeout_ms=3000` in `hermes_ws.c`) |
| WiFi recovery? | The C6 may need a reset after WiFi loss — check `wifi_connect.c`'s `STA_DISCONNECTED` handler |
| Bridge health? | Bridge must be up for reconnect to succeed — confirm Docker `restart: unless-stopped` |

### 5.11 `bad_message` from bridge

The firmware sent a malformed JSON control frame. Check the control-frame
builders in `firmware/components/hermes_ws/src/hermes_ws.c` and the protocol
mirror in `firmware/main/include/protocol.h` against
`bridge/hermes_bridge/protocol.py`.

### 5.12 Debug logging

**Firmware** — in `sdkconfig.defaults`:

```
CONFIG_LOG_DEFAULT_LEVEL_DEBUG=y
```

Rebuild and flash; logs appear in `idf.py monitor`.

**Bridge** — set the env var and restart:

```bash
# in bridge/.env
HERMES_DESK_LOG__LEVEL=DEBUG
docker compose restart bridge
docker compose logs -f bridge
```

**End-to-end trace** — enable debug on both, then press-to-talk, release, and
follow the bridge log chain: `stt.request` → `stt.response` → `gateway.request`
→ `gateway.chunk` → `tts.request` → `tts.response` → `ws.send_audio`. Any gap
indicates where the turn failed.

---

## Appendix: Quick reference

| What | Value |
|------|-------|
| Repo | `github.com/machjesusmoto/hermes-desk` |
| Terminal | M5Stack Tab5 (ESP32-P4 + ESP32-C6), 5" 1280×720, ES8388/ES7210 |
| ESP-IDF | v5.4+ minimum, developed against v5.5.4 |
| Firmware target | `esp32p4` |
| Flash command | `idf.py -p /dev/ttyACM0 -b 921600 flash monitor` |
| C6 slave firmware | `ESP32C6-WiFi-SDIO-Interface-V1.4.1-96bea3a_0x0.bin` in `firmware/wifi_c6_fw/` |
| SDIO pins | CMD=13, CLK=12, D0=11, D1=10, D2=9, D3=8, RESET=15 (active-high) |
| Audio wire format | PCM 16-bit LE, 16 kHz, mono, 640 B / 20 ms |
| Bridge port | 8765 (WebSocket + HTTP) |
| Bridge config env prefix | `HERMES_DESK_<SECTION>__<KEY>` |
| Hermes gateway | `http://host.docker.internal:8642/v1/chat/completions` |
| STT | `http://gx10-fd05:11436/v1/audio/transcriptions` (Nemotron 0.6B) |
| TTS | `http://gx10-39e5:11438/v1/audio/speech` (Google Neural2-F) |
| Gateway API key | Vault `secret/paperclip/services/hermes-gateway` → `HERMES_DESK_GATEWAY__API_KEY` |
| Push-to-talk | Touch-to-talk on the touchscreen (GPIO35 PTT code exists but is unused) |
| Known-broken | ST7123 touch not detecting touches (commit `f43ab90`) |
| WS hello fix | Commit `bc01cb9` — send hello from a separate task |
