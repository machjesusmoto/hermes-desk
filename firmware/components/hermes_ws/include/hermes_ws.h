/**
 * @file hermes_ws.h
 * @brief WebSocket client for the Hermes Desk bridge.
 *
 * Wraps espressif/esp_websocket_client. Handles:
 *   - Connect/reconnect to ws://<bridge>:8765/ws?token=<token>
 *   - Send the device->bridge `hello` handshake on connect
 *   - Send JSON control frames (text) and PCM audio frames (binary)
 *   - Dispatch inbound text (JSON) frames to a registered handler
 *   - Feed inbound binary (TTS PCM) frames to the audio playback layer
 *
 * Wire format: docs/PROTOCOL.md. Binary frames are raw PCM; text frames are
 * JSON control messages parsed by the registered callback.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Called for each inbound TEXT (JSON) frame. `json` is NUL-terminated and
 * valid only for the duration of the call. The handler should parse and
 * route to app_state / display as needed (see protocol.h for the schema).
 */
typedef void (*hermes_ws_text_cb_t)(const char *json, size_t len);

/**
 * Called for each inbound BINARY (PCM) frame. `data`/`len` are raw PCM
 * (16-bit/16k/mono) and valid only for the duration of the call. The handler
 * (audio playback) should copy or queue the bytes.
 */
typedef void (*hermes_ws_binary_cb_t)(const uint8_t *data, size_t len);

/** Connection lifecycle callback. */
typedef void (*hermes_ws_conn_cb_t)(bool connected);

typedef struct {
    const char *host;        /* bridge host or IP, e.g. "192.168.1.50" */
    uint16_t    port;        /* bridge port, default 8765 */
    const char *path;        /* WS path, default "/ws" */
    const char *token;       /* auth token; appended as ?token= (may be NULL/"") */
    hermes_ws_text_cb_t   on_text;     /* may be NULL */
    hermes_ws_binary_cb_t on_binary;   /* may be NULL */
    hermes_ws_conn_cb_t   on_conn;     /* may be NULL */
} hermes_ws_config_t;

/**
 * Start the WebSocket client. Begins an async connect attempt with auto
 * reconnect. On a successful connect, sends the `hello` handshake and fires
 * on_conn(true). Network (WiFi) must be up first.
 */
esp_err_t hermes_ws_start(const hermes_ws_config_t *cfg);

/** Send the hello handshake (device -> bridge). Called on connect. */
esp_err_t hermes_ws_send_hello(void);

/** Task wrapper for sending hello — spawns, sends, self-deletes. */
void hermes_ws_send_hello_task(void *arg);

/** Stop and destroy the client. */
esp_err_t hermes_ws_stop(void);

/** True if the WS link is currently open (handshake sent). */
bool hermes_ws_is_connected(void);

/**
 * Send a JSON control message as a text frame (device -> bridge).
 * `json` must be a complete JSON object. Returns ESP_OK on enqueue.
 */
esp_err_t hermes_ws_send_text(const char *json);

/** Convenience: send the listen start/stop control frame. */
esp_err_t hermes_ws_send_listen_start(void);
esp_err_t hermes_ws_send_listen_stop(void);

/** Send a barge-in abort (cancels TTS playback on the bridge). */
esp_err_t hermes_ws_send_abort(void);

/**
 * Send a notify_ack (device -> bridge) for a proactive notification, e.g.
 * when the user taps the dismiss button on a notification card. The bridge
 * uses this to mark the notification acknowledged and stop awaiting ack.
 * `notification_id` is the id from the inbound notify frame (NUL-terminated).
 */
esp_err_t hermes_ws_send_notify_ack(const char *notification_id);

/**
 * Send a binary PCM audio frame (device -> bridge). `pcm` must be a whole
 * number of 20 ms frames (multiples of 640 B) per the protocol, though the
 * bridge simply accumulates bytes, so partial frames are tolerated.
 */
esp_err_t hermes_ws_send_audio(const uint8_t *pcm, size_t len);

#ifdef __cplusplus
}
#endif
