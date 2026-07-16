# Hardware Setup

Complete guide from unboxing to first voice conversation.

## What You Need

| Component | Spec | Notes |
|-----------|------|-------|
| M5Stack Tab5 | ESP32-P4 + ESP32-C6 | 5" 1280×720 IPS, dual mic, speaker |
| USB-C cable | Data + power | For flashing firmware |
| WiFi network | 2.4 GHz | Same LAN as bridge host |
| Bridge host | Docker-capable Linux | moto-agent-host or equivalent |
| ESP-IDF | v5.4+ | For building firmware (one-time) |

## Step 1: Prepare the ESP32-C6 Co-processor

The Tab5's ESP32-P4 has no WiFi radio. WiFi rides the ESP32-C6 over SDIO via
`esp_hosted`. The C6 must run M5Stack's slave firmware.

### Flash the C6 slave firmware

1. Download the slave firmware binary. Place it in `firmware/wifi_c6_fw/`.
2. Connect the Tab5 via USB-C.
3. Flash the C6:

```bash
# From the firmware directory
esptool.py --chip esp32c6 --port /dev/ttyACM0 --baud 921600 \
  write_flash 0x0 wifi_c6_fw/esp_hosted_slave.bin
```

4. Verify: after reset, the C6 should enumerate as an SPI SDIO slave. The P4
   firmware will detect it automatically on boot.

### Common failure: C6 not detected

If the P4 firmware logs `esp_hosted: no response from slave`:
- Confirm the C6 firmware is flashed (not blank)
- Check the SDIO GPIO pinout matches `sdkconfig.defaults` (GPIO30-37)
- Verify the C6 reset GPIO (GPIO28) is wired correctly in hardware
- Power cycle the entire Tab5 (not just reset)

## Step 2: Build and Flash the P4 Firmware

Requires ESP-IDF v5.4+ on your build machine.

```bash
# Install ESP-IDF (one-time)
# See: https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/get-started/

# Set target
idf.py set-target esp32p4

# Configure WiFi credentials
idf.py menuconfig
# → Component config → Hermes Desk → WiFi SSID / Password

# Build
idf.py build

# Flash (USB-C connected)
idf.py -p /dev/ttyACM0 -b 921600 flash monitor
```

On boot you should see:
```
hermes_desk: WiFi connected (192.168.x.x)
hermes_desk: connecting to ws://BRIDGE_IP:8765
hermes_desk: hello sent (model=esp32p4, version=0.1.0)
hermes_desk: ready — press GPIO35 to talk
```

## Step 3: Physical Setup

- **Position:** Place the Tab5 at eye level on your desk, angled toward you.
  The dual microphones work best when facing you directly within arm's reach.
- **Power:** USB-C to a powered hub or wall adapter. The Tab5 draws ~500mA
  during audio streaming.
- **Button:** The side button (GPIO35) is push-to-talk. Press and hold to
  record, release to send. In noisy environments, consider a desk position
  where the button is easy to reach without looking.

## Step 4: First Conversation

1. Ensure the bridge is running (see `docs/network-requirements.md`)
2. Power on the Tab5 — it auto-connects to WiFi and the bridge
3. Press the side button and say something
4. Release — the bridge processes your speech and responds
5. The display shows a transcript as it streams back

If nothing happens, see `docs/troubleshooting.md`.
