/**
 * @file hermes_ws.c
 * @brief WebSocket client for the Hermes Desk bridge.
 *
 * Wraps espressif/esp_websocket_client. The bridge expects:
 *   - A `hello` JSON text frame immediately on connect (handshake).
 *   - `listen` start/stop text frames to bracket PCM capture.
 *   - Binary frames of raw PCM (16k/16bit/mono) while listening.
 *   - `abort` to barge-in TTS playback.
 * Inbound:
 *   - Text frames: JSON control (status/stt/llm/tts/error/notify) -> on_text
 *   - Binary frames: TTS PCM -> on_binary (audio playback)
 *
 * Reconnect is handled by esp_websocket_client; on each successful reconnect
 * we re-send `hello` so the bridge opens a fresh session.
 */
#include "hermes_ws.h"
#include "protocol.h"

#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_websocket_client.h"
#include "cJSON.h"

static const char *TAG = "hermes_ws";

#define WS_CONN_BIT  BIT0

typedef struct {
    esp_websocket_client_handle_t  client;
    hermes_ws_text_cb_t            on_text;
    hermes_ws_binary_cb_t          on_binary;
    hermes_ws_conn_cb_t            on_conn;
    char                           host[64];
    char                           path[96];   /* "/ws?token=..." */
    uint16_t                       port;
    bool                           connected;
    EventGroupHandle_t             flags;
    char                           device_id[32];
} ws_ctx_t;

static ws_ctx_t s_ctx;

/* ---- helpers ------------------------------------------------------------- */

/* Build the device->bridge hello JSON. Caller frees. */
static char *build_hello_json(void)
{
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", HERMES_TYPE_HELLO);
    cJSON_AddNumberToObject(root, "version", HERMES_PROTOCOL_VERSION);

    cJSON *ap = cJSON_CreateObject();
    cJSON_AddNumberToObject(ap, "sample_rate", HERMES_AUDIO_SAMPLE_RATE);
    cJSON_AddNumberToObject(ap, "bits", HERMES_AUDIO_BITS);
    cJSON_AddNumberToObject(ap, "channels", HERMES_AUDIO_CHANNELS);
    cJSON_AddItemToObject(root, "audio_params", ap);

    cJSON_AddStringToObject(root, "device_id", s_ctx.device_id);
    char *str = cJSON_PrintUnformatted(root);
    cJSON_Delete(root);
    return str;
}

/* ---- websocket event handler --------------------------------------------- */
static void ws_event_handler(void *arg, esp_event_base_t base,
                             int32_t id, void *data)
{
    esp_websocket_event_data_t *evt = data;

    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "connected to ws://%s:%u%s", s_ctx.host, s_ctx.port, s_ctx.path);
        s_ctx.connected = true;
        if (s_ctx.flags) xEventGroupSetBits(s_ctx.flags, WS_CONN_BIT);
        /* Don't send hello from inside the event handler — send_text blocks
         * and can deadlock the WebSocket task. Signal the main task instead. */
        if (s_ctx.on_conn) s_ctx.on_conn(true);
        break;

    case WEBSOCKET_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "disconnected");
        s_ctx.connected = false;
        if (s_ctx.flags) xEventGroupClearBits(s_ctx.flags, WS_CONN_BIT);
        if (s_ctx.on_conn) s_ctx.on_conn(false);
        break;

    case WEBSOCKET_EVENT_DATA: {
        /* op_code: 1 = text, 2 = binary, 9 = ping, 10 = pong. */
        ESP_LOGD(TAG, "data event: op=%d len=%d", evt->op_code, evt->data_len);
        if (evt->op_code == 1 && evt->data_len > 0 && s_ctx.on_text) {
            /* Ensure NUL-termination for the parser. The client delivers a
             * full message in one event for our small control frames. */
            char *tmp = malloc(evt->data_len + 1);
            if (tmp) {
                memcpy(tmp, evt->data_ptr, evt->data_len);
                tmp[evt->data_len] = '\0';
                s_ctx.on_text(tmp, evt->data_len);
                free(tmp);
            }
        } else if (evt->op_code == 2 && evt->data_len > 0 && s_ctx.on_binary) {
            /* TTS PCM. The bridge streams 20 ms (640 B) chunks. */
            s_ctx.on_binary((const uint8_t *)evt->data_ptr, evt->data_len);
        }
        break;
    }

    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG, "websocket error");
        break;

    default:
        break;
    }
    (void)base;
    (void)arg;
}

/* ---- public API ---------------------------------------------------------- */

esp_err_t hermes_ws_send_hello(void)
{
    if (!s_ctx.client || !s_ctx.connected) {
        return ESP_ERR_NOT_FINISHED;
    }
    char *hello = build_hello_json();
    if (!hello) return ESP_ERR_NO_MEM;
    int sent = esp_websocket_client_send_text(s_ctx.client, hello,
                                              strlen(hello), 2000 / portTICK_PERIOD_MS);
    ESP_LOGI(TAG, "hello sent (%d bytes) — device_id=%s", sent, s_ctx.device_id);
    free(hello);
    return (sent > 0) ? ESP_OK : ESP_FAIL;
}

void hermes_ws_send_hello_task(void *arg)
{
    (void)arg;
    /* Small delay to let the WebSocket event handler return before we send. */
    vTaskDelay(pdMS_TO_TICKS(50));
    hermes_ws_send_hello();
    vTaskDelete(NULL);
}

esp_err_t hermes_ws_start(const hermes_ws_config_t *cfg)
{
    if (!cfg || !cfg->host) {
        return ESP_ERR_INVALID_ARG;
    }

    memset(&s_ctx, 0, sizeof(s_ctx));
    s_ctx.on_text   = cfg->on_text;
    s_ctx.on_binary = cfg->on_binary;
    s_ctx.on_conn   = cfg->on_conn;
    s_ctx.port      = cfg->port ? cfg->port : 8765;
    strncpy(s_ctx.host, cfg->host, sizeof(s_ctx.host) - 1);

    /* Compose path + auth. Bridge accepts ?token= (preferred for ESP32). */
    const char *path = cfg->path ? cfg->path : "/ws";
    if (cfg->token && cfg->token[0]) {
        snprintf(s_ctx.path, sizeof(s_ctx.path), "%s?token=%s", path, cfg->token);
    } else {
        strncpy(s_ctx.path, path, sizeof(s_ctx.path) - 1);
    }

    /* Device id: derived from the WiFi MAC. The MAC is only valid after
     * esp_wifi_start(), so resolve it lazily at hello-send time (which runs
     * post-connect). If unavailable, fall back to a static tag. */
    uint8_t mac[6] = {0};
    if (esp_wifi_get_mac(WIFI_IF_STA, mac) == ESP_OK &&
        (mac[0] || mac[1] || mac[2] || mac[3] || mac[4] || mac[5])) {
        snprintf(s_ctx.device_id, sizeof(s_ctx.device_id), "tab5-%02x%02x%02x",
                 mac[3], mac[4], mac[5]);
    } else if (!s_ctx.device_id[0]) {
        snprintf(s_ctx.device_id, sizeof(s_ctx.device_id), "tab5");
    }

    s_ctx.flags = xEventGroupCreate();

    /* Build the URI. Use ws:// (plain) — the bridge listens on 8765 plain.
     * Switch to wss:// + cert bundle if the bridge ever fronts TLS. */
    char uri[256];
    snprintf(uri, sizeof(uri), "ws://%s:%u%s", s_ctx.host, s_ctx.port, s_ctx.path);

    esp_websocket_client_config_t ws_cfg = {
        .uri = uri,
        .path = NULL,                 /* already embedded in uri */
        .port = s_ctx.port,
        .transport = WEBSOCKET_TRANSPORT_OVER_TCP,
        .reconnect_timeout_ms = 3000,
        .network_timeout_ms = 10000,
        .buffer_size = 8192,          /* >= largest control JSON + PCM frame */
        .task_stack = 12288,
        .skip_cert_common_name_check = true,
        .ping_interval_sec = 5,       /* keepalive — bridge is silent in idle */
    };

    s_ctx.client = esp_websocket_client_init(&ws_cfg);
    if (!s_ctx.client) {
        ESP_LOGE(TAG, "ws client init failed for %s", uri);
        return ESP_FAIL;
    }
    esp_websocket_register_events(s_ctx.client, WEBSOCKET_EVENT_ANY,
                                  ws_event_handler, NULL);

    esp_err_t err = esp_websocket_client_start(s_ctx.client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ws start failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "ws client started — uri=%s", uri);
    return ESP_OK;
}

esp_err_t hermes_ws_stop(void)
{
    if (!s_ctx.client) return ESP_ERR_INVALID_STATE;
    esp_websocket_client_destroy(s_ctx.client);
    s_ctx.client = NULL;
    if (s_ctx.flags) {
        vEventGroupDelete(s_ctx.flags);
        s_ctx.flags = NULL;
    }
    return ESP_OK;
}

bool hermes_ws_is_connected(void)
{
    return s_ctx.connected &&
           esp_websocket_client_is_connected(s_ctx.client);
}

esp_err_t hermes_ws_send_text(const char *json)
{
    if (!s_ctx.client || !json) return ESP_ERR_INVALID_STATE;
    if (!s_ctx.connected) return ESP_ERR_NOT_FINISHED;
    int len = esp_websocket_client_send_text(s_ctx.client, json,
                                             strlen(json), 2000 / portTICK_PERIOD_MS);
    return (len > 0) ? ESP_OK : ESP_FAIL;
}

esp_err_t hermes_ws_send_listen_start(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", HERMES_TYPE_LISTEN);
    cJSON_AddStringToObject(o, "action", HERMES_ACTION_START);
    char *s = cJSON_PrintUnformatted(o);
    esp_err_t r = hermes_ws_send_text(s);
    free(s);
    cJSON_Delete(o);
    return r;
}

esp_err_t hermes_ws_send_listen_stop(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", HERMES_TYPE_LISTEN);
    cJSON_AddStringToObject(o, "action", HERMES_ACTION_STOP);
    char *s = cJSON_PrintUnformatted(o);
    esp_err_t r = hermes_ws_send_text(s);
    free(s);
    cJSON_Delete(o);
    return r;
}

esp_err_t hermes_ws_send_abort(void)
{
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", HERMES_TYPE_ABORT);
    char *s = cJSON_PrintUnformatted(o);
    esp_err_t r = hermes_ws_send_text(s);
    free(s);
    cJSON_Delete(o);
    return r;
}

esp_err_t hermes_ws_send_notify_ack(const char *notification_id)
{
    if (!notification_id || !notification_id[0]) {
        return ESP_ERR_INVALID_ARG;
    }
    cJSON *o = cJSON_CreateObject();
    cJSON_AddStringToObject(o, "type", HERMES_TYPE_NOTIFY_ACK);
    cJSON_AddStringToObject(o, "notification_id", notification_id);
    char *s = cJSON_PrintUnformatted(o);
    esp_err_t r = hermes_ws_send_text(s);
    free(s);
    cJSON_Delete(o);
    return r;
}

esp_err_t hermes_ws_send_audio(const uint8_t *pcm, size_t len)
{
    if (!s_ctx.client || !pcm || !len) return ESP_ERR_INVALID_ARG;
    if (!s_ctx.connected) return ESP_ERR_NOT_FINISHED;
    int sent = esp_websocket_client_send_bin(s_ctx.client, (const char *)pcm, len,
                                             3000 / portTICK_PERIOD_MS);
    return (sent > 0) ? ESP_OK : ESP_FAIL;
}
