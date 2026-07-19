"""Live end-to-end exercise of the proactive notification pipeline (TAY-9).

Boots the REAL bridge server on a random localhost port with fake STT/TTS/
gateway clients (so no gx10 hosts or Hermes gateway are required), connects
a REAL websockets client as the "Tab5", then drives a real HTTP POST to
/notify via the notify_cli sender and asserts the complete on-the-wire flow:

  HIGH-priority notification:
    bridge -> device:  {"type":"notify",...}
    bridge -> device:  {"type":"tts","action":"start",...}
    bridge -> device:  <binary PCM chunks>
    bridge -> device:  {"type":"tts","action":"stop"}
  Then the device sends {"type":"notify_ack",...} and the bridge replies
  {"type":"ack_received",...} and records the ack in history.

  NORMAL-priority notification:
    bridge -> device:  {"type":"notify",...}   (no TTS cue)

This is the smallest meaningful runtime check of the implemented pipeline.
It is NOT a pytest test (it starts a real server + real sockets) — run it
directly:  python tests/e2e_notify_live.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
import uuid

import httpx
import uvicorn
import websockets

# Ensure the package is importable when run from the bridge/ dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_bridge.server import create_app
from hermes_bridge.config import BridgeConfig
from hermes_bridge.stt import STTResult
from hermes_bridge.gateway import GatewayResponse
from hermes_bridge.notify_cli import send as cli_send


# ---- fakes (no gx10 / no Hermes gateway needed) ----------------------------

class FakeSTT:
    async def transcribe(self, pcm, sample_rate=16000, language="en"):
        return STTResult(text="hello")
    async def aclose(self): pass


class FakeTTS:
    """Returns a fixed PCM blob so the announcement stream is real bytes."""
    def __init__(self): self.calls = []
    async def synthesize(self, text):
        self.calls.append(text)
        return b"\x01\x02" * 320  # 640 bytes of non-zero PCM
    async def aclose(self): pass


class FakeGateway:
    async def chat(self, session_id, text):
        return GatewayResponse(text="ok")
    async def aclose(self): pass


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def device_side(port: int, notif_id_high: str) -> dict:
    """Act as the Tab5: connect, hello, then receive + ack the high-prio notif."""
    uri = f"ws://127.0.0.1:{port}/ws"
    results: dict = {"frames": [], "pcm_bytes": 0, "ack_reply": None}
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type": "hello", "version": 1,
            "audio_params": {"sample_rate": 16000, "bits": 16, "channels": 1},
            "device_id": "tab5-e2e",
        }))
        # hello + status idle
        await ws.recv()  # hello
        await ws.recv()  # status idle

        # Wait for the high-priority notification + TTS cue. The bridge sends:
        # notify, tts start, <pcm chunks>, tts stop.
        got_notify = False
        got_tts_start = got_tts_stop = False
        while not (got_notify and got_tts_start and got_tts_stop):
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            if isinstance(raw, bytes):
                results["pcm_bytes"] += len(raw)
                results["frames"].append(("binary", len(raw)))
            else:
                msg = json.loads(raw)
                results["frames"].append(("text", msg))
                if msg.get("type") == "notify":
                    got_notify = True
                    assert msg["notification_id"] == notif_id_high
                    assert msg["priority"] == 3
                    assert msg["requires_ack"] is True
                elif msg.get("type") == "tts" and msg.get("action") == "start":
                    got_tts_start = True
                elif msg.get("type") == "tts" and msg.get("action") == "stop":
                    got_tts_stop = True

        # Send the ack (as the dismiss button would).
        await ws.send(json.dumps({
            "type": "notify_ack", "notification_id": notif_id_high,
        }))
        ack_reply = await asyncio.wait_for(ws.recv(), timeout=5.0)
        results["ack_reply"] = json.loads(ack_reply)
    return results


def main() -> int:
    port = _free_port()
    cfg = BridgeConfig()
    cfg.server.host = "127.0.0.1"
    cfg.server.port = port
    cfg.server.token = ""
    app = create_app(cfg)
    # Wire fakes into the real server.
    app.state.bridge.stt = FakeSTT()
    app.state.bridge.tts = FakeTTS()
    app.state.bridge.gateway = FakeGateway()
    app.state.bridge.pipeline.stt = app.state.bridge.stt
    app.state.bridge.pipeline.tts = app.state.bridge.tts
    app.state.bridge.pipeline.gateway = app.state.bridge.gateway

    config = uvicorn.Config(app, host="127.0.0.1", port=port,
                            log_level="warning")
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for the server to accept connections.
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            r = httpx.get(base + "/health", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)
    else:
        print("FAIL: bridge did not start", file=sys.stderr)
        return 1

    print(f"[e2e] bridge up on {base} — /health={r.json()}")

    notif_id = "e2e-high-" + uuid.uuid4().hex[:6]
    loop = asyncio.new_event_loop()

    # Kick off the device side first so it's waiting on the socket.
    device_task = loop.create_task(device_side(port, notif_id))

    # Tiny delay so the WS is connected before we POST.
    loop.run_until_complete(asyncio.sleep(0.15))

    # Drive the notification through the real notify_cli sender.
    resp = cli_send(
        base, title="Deploy FAILED: hermes-desk",
        body="prod rollback initiated", priority=3, requires_ack=True,
        category="deploy", notification_id=notif_id,
    )
    print(f"[e2e] POST /notify -> {resp}")

    device_results = loop.run_until_complete(asyncio.wait_for(device_task, timeout=10.0))

    # Now send a NORMAL notification and confirm NO TTS cue follows. We connect
    # the device FIRST, then POST inside the coroutine so the device is
    # registered before /notify runs (no race).
    async def normal_check():
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            await ws.send(json.dumps({
                "type": "hello", "version": 1,
                "audio_params": {"sample_rate": 16000, "bits": 16, "channels": 1},
                "device_id": "tab5-e2e2",
            }))
            await ws.recv(); await ws.recv()  # hello + idle

            # Device is connected & registered — now fire the NORMAL notif.
            r2 = cli_send(
                base, title="Build passed", priority=1, category="deploy",
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            assert msg["type"] == "notify" and msg["priority"] == 1, msg
            assert r2["status"] == "delivered", r2
            # Confirm no TTS framing arrives within a short window.
            try:
                nxt = await asyncio.wait_for(ws.recv(), timeout=0.6)
                return {
                    "post_status": r2["status"],
                    "normal_got_extra": json.loads(nxt) if isinstance(nxt, str) else "binary",
                }
            except asyncio.TimeoutError:
                return {"post_status": r2["status"], "normal_got_extra": None}
    normal_res = loop.run_until_complete(normal_check())
    print(f"[e2e] normal-priority: post={normal_res['post_status']} extra-frame={normal_res['normal_got_extra']}")

    # Pull history from the bridge to confirm the ack landed.
    hist = httpx.get(base + "/notify/history").json()
    print(f"[e2e] /notify/history count={hist['count']}")

    server.should_exit = True
    server_thread.join(timeout=3.0)
    loop.close()

    # ---- assertions + report ----
    ok = True
    def check(cond, label):
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {label}")
        if not cond:
            ok = False

    check(resp["status"] == "delivered", f"high-prio POST status=delivered (got {resp['status']})")
    check(resp["notification_id"] == notif_id, "echoed notification_id matches")
    check(any(f[0] == "text" and f[1].get("type") == "notify" and f[1]["priority"] == 3
              for f in device_results["frames"]),
          "device received notify frame with priority=3")
    check(any(f[0] == "text" and f[1].get("type") == "tts" and f[1].get("action") == "start"
              for f in device_results["frames"]),
          "device received tts start")
    check(any(f[0] == "text" and f[1].get("type") == "tts" and f[1].get("action") == "stop"
              for f in device_results["frames"]),
          "device received tts stop")
    check(device_results["pcm_bytes"] > 0, f"device received PCM bytes ({device_results['pcm_bytes']})")
    check(device_results["ack_reply"] and device_results["ack_reply"]["type"] == "ack_received",
          f"bridge acked: {device_results['ack_reply']}")
    check(any(n["id"] == notif_id and n["acked_at"] is not None for n in hist["notifications"]),
          "ack recorded in /notify/history")
    check(normal_res["normal_got_extra"] is None,
          "normal-priority notification produced NO TTS cue")
    check(len(app.state.bridge.tts.calls) == 1,
          f"TTS synthesized exactly once (high-prio only); calls={app.state.bridge.tts.calls}")

    print("\n[e2e] RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
