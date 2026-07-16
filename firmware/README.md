# Hermes Desk — M5Stack Tab5 Firmware

ESP32-P4 firmware for the **M5Stack Tab5** that turns it into a voice + display
terminal for the Hermes Agent. The Tab5 captures audio from its dual mics,
streams it to the Hermes Desk **bridge** over WebSocket, and renders the
bridge's display commands on its 5" IPS panel with LVGL.

This is **MOT-78** (Milestone M1 — Voice Terminal). The bridge service it talks
to is **MOT-79** (already complete, in `bridge/hermes_bridge/`).

```
Tab5 (ESP32-P4)  ──WebSocket──▶  Bridge (Docker)  ──HTTP──▶  Hermes Gateway
  dual mics / speaker                  STT → gateway → TTS        (:8642)
  5" IPS 1280x720 + LVGL               PCM 20ms frames back
  PTT button (GPIO35)
```

## Hardware

| Part | Detail |
|------|--------|
| Main SoC | ESP32-P4 (RISC-V), 16 MB Flash, 32 MB PSRAM — **no built-in radio** |
| WiFi/BLE | ESP32-C6-MINI-1U co-processor over **SDIO** (`esp_hosted`) |
| Display | 5" 1280×720 IPS, MIPI-DSI, GT911 touch (I2C) |
| Audio | ES8388 (speaker DAC) + ES7210 (dual-mic ADC w/ AEC) over I2S |
| Button | BOOT button on GPIO35 (used as push-to-talk) |

> **Note on display resolution:** `docs/PROTOCOL.md` mentions a 1024×600 panel,
> but the shipping M5Stack Tab5 has a **1280×720** MIPI-DSI panel. The LVGL UI
> is resolution-independent (it reads the live panel size from the BSP), so it
> works on either. Flagged for the docs owner to reconcile.

## Architecture

```
firmware/
├── CMakeLists.txt            # project (IDF)
├── idf_component.yml         # managed-component deps (BSP, WS, esp_hosted, LVGL)
├── sdkconfig.defaults        # P4+C6 SDIO pins, audio rate, LVGL, LWIP tuning
├── partitions.csv            # 16 MB layout incl. slave_fw (C6) partition
├── wifi_c6_fw/               # drop the C6 esp_hosted slave .bin here
├── main/
│   ├── include/              # protocol.h, app_state.h, wifi_connect.h, ptt.h
│   ├── src/
│   │   ├── main.c            # boot sequence + inbound JSON dispatch
│   │   ├── app_state.c       # device FSM (idle/listen/process/speak/error)
│   │   ├── wifi_connect.c    # esp_hosted → esp_wifi bring-up (order matters!)
│   │   └── ptt.c             # GPIO35 push-to-talk (debounced ISR → FSM)
│   └── Kconfig.projbuild     # WiFi SSID/pass + bridge host/token menuconfig
└── components/
    ├── hermes_ws/            # WebSocket client (text=JSON, binary=PCM)
    ├── hermes_audio/         # I2S capture (ES7210→mono) + playback (ES8388)
    └── hermes_display/       # LVGL: status, voice_transcript, status_card, dashboard
```

### Wire protocol

See `docs/PROTOCOL.md` for the full spec. Summary (mirrored in
`main/include/protocol.h`):

- **Transport:** WebSocket `ws://<bridge>:8765/ws?token=<token>`
- **Audio:** raw PCM, 16-bit signed LE, 16 kHz, mono, 640 B (20 ms) frames
- **Binary frames** = audio; **text frames** = JSON control messages
- **Device→Bridge:** `hello`, `listen` (start/stop), `abort`
- **Bridge→Device:** `hello`, `stt`, `llm`, `tts` (start/stop), `status`
  (idle/listening/processing), `error`, `notify`
- **Flow:** hello → listen:start → PCM → listen:stop → stt → llm → tts:start
  → PCM → tts:stop → status:idle. Barge-in: `abort` during TTS.

### State machine

`main/src/app_state.c` owns one FSM. Inputs (PTT, bridge messages) funnel
through it so the display, audio, and WS layers stay in sync:

```
IDLE --ptt_press--> LISTENING --ptt_release--> PROCESSING
 ^                                                 |
 |  PROCESSING --tts:start--> SPEAKING --tts:stop--|
 |                    `--error/status:idle---------|
 `<--abort(during SPEAKING)--'
```

## Prerequisites

1. **ESP-IDF v5.4+** (developed against 5.5.x). Install per the
   [official guide](https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/get-started/):
   ```bash
   git clone --recursive https://github.com/espressif/esp-idf.git
   cd esp-idf && ./install.sh esp32p4
   . ./export.sh
   ```
2. A USB-C cable to the Tab5's USB-UART port for flashing/monitoring.
3. The Hermes Desk bridge running and reachable on your network
   (`bridge/hermes_bridge/`, port 8765). See `bridge/README.md`.

## Configure

```bash
cd firmware
idf.py set-target esp32p4
idf.py menuconfig
```

Under **Hermes Desk Configuration** set:

| Option | Example | Notes |
|--------|---------|-------|
| `WIFI_SSID` | `moto-net` | Your network |
| `WIFI_PASS` | `••••••••` | WPA2 passphrase |
| `BRIDGE_HOST` | `192.168.1.50` | Host/IP of the bridge |
| `BRIDGE_PORT` | `8765` | WS port |
| `BRIDGE_TOKEN` | *(blank if no auth)* | Matches `server.token` in bridge config |

> WiFi creds can also be set at runtime via NVS provisioning (planned in
> MOT-81). For the first bring-up, menuconfig is the fastest path.

## Flash the ESP32-C6 co-processor (ONE TIME)

The ESP32-P4 has no radio — WiFi lives on the C6, which must run Espressif's
`esp_hosted` **slave** firmware. M5Stack ships a prebuilt image.

1. Download the C6 slave firmware from the
   [M5Tab5-UserDemo repo](https://github.com/m5stack/M5Tab5-UserDemo/tree/main/platforms/tab5/wifi_c6_fw):
   `ESP32C6-WiFi-SDIO-Interface-V1.4.1-96bea3a_0x0.bin`.
2. Place it at `firmware/wifi_c6_fw/` (git-ignored) **or** use M5Stack's
   `flash.sh` to program the C6 directly over its PROG header (requires an
   ESP-Prog or a USB cable to the C6's port — see the M5Stack docs).
3. The `slave_fw` partition in `partitions.csv` reserves 2 MB so the P4 can
   hold and load the C6 image over SDIO at boot.

If WiFi fails to come up with `sdmmc_init_ocr: send_op_cond returned 0x107` and
a zero MAC, the cause is almost always: wrong SDIO GPIOs, the C6 unpowered, or
the C6 not running slave firmware. The SDIO pin map and reset polarity are set
in `sdkconfig.defaults` (CLK=12, CMD=13, D0–D3=11/10/9/8, RST=15, active-high).
See `esp-hosted-mcu` issue #127 for background.

## Build & flash

```bash
cd firmware
idf.py build
idf.py -p /dev/cu.usbmodem* -b 921600 flash monitor   # macOS; adjust port for Linux/Windows
```

(`Ctrl+]` exits the monitor.)

## Using it

1. On boot the screen shows WiFi connect progress, then **Ready**.
2. **Hold the BOOT button (side)** to talk — the screen shows **Listening…**
   and your speech streams to the bridge.
3. **Release** to send — the bridge transcribes, queries Hermes, and streams
   the spoken reply back. The screen shows your transcript + Hermes's reply.
4. **Press the button during a reply** to barge-in (interrupt TTS).
5. Proactive `notify` messages from Hermes pop up as a card on screen.

## Development notes

- **BSP dependency:** built on the official `espressif/m5stack_tab5` BSP,
  which abstracts the MIPI-DSI panel, GT911 touch, ES8388/ES7210 codecs, and
  the PI4IOE5V6408 IO expanders (which also power the C6 and speaker amp).
  Managed components are fetched on first `idf.py build`.
- **No on-device resampling:** both codec paths open at 16 kHz to match the
  bridge exactly. The dual-mic ES7210 stereo stream is down-mixed to mono
  (L+R averaged) before sending.
- **Cannot compile ESP32-P4 in a generic sandbox** — the ESP-IDF toolchain
  targets the P4 specifically. Build on a machine with ESP-IDF installed.
- **LVGL thread safety:** all display calls lock the LVGL port
  (`lvgl_port_lock`); never call LVGL directly from other tasks.
- **Future (M2–M4):** wake-word (replace PTT with VAD), proactive
  notifications audio chime, image rendering from bridge, always-on dashboard.

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `sdmmc_init_ocr … 0x107`, MAC `00:00…` | C6 not powered / wrong SDIO pins / no slave fw. See "Flash the C6" above. |
| Display blank | Confirm `CONFIG_BSP_DISPLAY_LVGL=y`; the BSP brings up the panel — no manual panel init needed. |
| No audio capture | Ensure `hermes_audio_init` succeeded (check logs for codec open errors); the IO-expander speaker-enable is managed by the BSP. |
| WS never connects | Check `BRIDGE_HOST`/`PORT`, that the bridge is up (`/health`), and that the Tab5 has an IP (boot screen). |
| `bad_message` from bridge | Firmware sent malformed JSON — check `hermes_ws.c` control frame builders. |
