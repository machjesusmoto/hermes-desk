# Troubleshooting

## Tab5 Won't Connect to WiFi

**Symptom:** Boot log shows `wifi_connect: failed after N attempts`

| Check | Fix |
|-------|-----|
| SSID/password correct? | Re-run `idf.py menuconfig`, verify credentials |
| 2.4 GHz band? | The ESP32-C6 does not support 5 GHz. Ensure your AP broadcasts 2.4 GHz |
| DHCP available? | Tab5 needs a DHCP lease. Check your router's DHCP pool |
| Signal strength? | Move Tab5 closer to the AP. Metal desk surfaces can attenuate signal |
| C6 slave firmware? | Re-flash the C6 (see hardware-setup.md Step 1) |

## Tab5 Connects to WiFi but Not to Bridge

**Symptom:** WiFi connected, but `ws_connect: failed` or no `hello_ack`

| Check | Fix |
|-------|-----|
| Bridge running? | `docker ps \| grep hermes-bridge` on the bridge host |
| Port 8765 open? | `ss -tlnp \| grep 8765` on bridge host |
| Firewall? | `sudo ufw status` — allow port 8765 from LAN |
| Correct IP? | Verify bridge IP in Tab5 firmware config matches bridge host |
| Bridge logs? | `docker logs hermes-bridge --tail 20` for connection errors |

## No Audio / Silent Microphone

**Symptom:** Press PTT button, bridge receives empty audio or silence

| Check | Fix |
|-------|-----|
| ES7210 codec init? | Check boot log for `hermes_audio: mic init ok` |
| I2S pinout? | Verify sdkconfig.defaults matches Tab5 hardware revision |
| Audio gain too low? | The ES7210 default gain may be low. Adjust in `hermes_audio.c` |
| PTT button? | Verify GPIO35 is the correct button for your Tab5 revision |

## STT Returns Garbage / Wrong Transcription

**Symptom:** Audio reaches bridge but transcription is nonsensical

| Check | Fix |
|-------|-----|
| Sample rate mismatch? | Tab5 must send 16kHz. Check `hermes_audio.c` I2S config |
| Byte order? | ESP32 is little-endian. Bridge expects little-endian PCM |
| Audio clipping? | If mic gain is too high, audio clips and STT fails. Reduce gain |
| STT model? | Verify Nemotron 0.6B is running: `curl http://10.0.2.60:11436/v1/models` |

## TTS Playback Is Choppy or Distorted

**Symptom:** Bridge sends audio back but Tab5 speaker sounds wrong

| Check | Fix |
|-------|-----|
| WAV format? | TTS returns 24kHz WAV. Bridge must resample to 16kHz for Tab5 |
| Buffer underrun? | Check Tab5 ring buffer size in `hermes_audio.c` (default 8KB) |
| Network latency? | >50ms RTT causes audible gaps. Use wired Ethernet for bridge host |
| ES8388 init? | Check boot log for `hermes_audio: speaker init ok` |

## Bridge Crashes on Startup

**Symptom:** Docker container exits immediately

| Check | Fix |
|-------|-----|
| Config file? | Ensure `.env` file exists with all required variables |
| Port conflict? | `ss -tlnp \| grep 8765` — another service may be using the port |
| Docker logs? | `docker logs hermes-bridge` for Python traceback |
| Dependencies? | Rebuild: `docker compose build --no-cache` |

## Voice Response Is Slow

**Symptom:** Long pause (>3s) between releasing PTT and hearing a response

| Check | Fix |
|-------|-----|
| STT latency? | Check bridge logs for `stt.duration_ms`. Should be <500ms |
| Gateway latency? | Check `gateway.duration_ms`. Depends on model — first token should be <2s |
| TTS latency? | Check `tts.duration_ms`. Should be <1s |
| Network? | `ping` from bridge to GB10 hosts. Should be <1ms on LAN |
| Cold start? | First request after idle may be slow (model loading). Subsequent should be fast |

## Auto-Reconnect Not Working

**Symptom:** After network hiccup, Tab5 stays disconnected

| Check | Fix |
|-------|-----|
| Reconnect timer? | Default is 5s. Check `hermes_ws.c` reconnect interval |
| WiFi recovery? | The ESP32-C6 may need a reset after WiFi loss. Check `wifi_connect.c` |
| Bridge health? | Bridge must be running for reconnect to succeed. Check Docker restart policy |

## Debug Logging

### Tab5 firmware

Increase log verbosity in `sdkconfig.defaults`:

```
CONFIG_LOG_MAXIMUM_LEVEL_DEBUG=y
```

Rebuild and flash. Logs appear in the serial monitor (`idf.py monitor`).

### Bridge service

Set `LOG_LEVEL=DEBUG` in the `.env` file. Restart the container:

```bash
docker compose restart hermes-bridge
docker logs -f hermes-bridge
```

### End-to-end trace

To capture the full audio pipeline:

1. Enable debug logging on both Tab5 and bridge
2. Press PTT, speak, release
3. Check bridge logs for:
   - `stt.request` → STT request sent
   - `stt.response` → transcription received
   - `gateway.request` → Hermes API call
   - `gateway.chunk` → response streaming
   - `tts.request` → TTS synthesis
   - `tts.response` → audio bytes returned
   - `ws.send_audio` → audio sent to Tab5
4. Any gap in this chain indicates where the failure occurred
