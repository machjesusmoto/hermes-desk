/**
 * @file main.c
 * @brief Hermes Desk Tab5 firmware entrypoint.
 *
 * Boot sequence:
 *   1. hermes_display_init      (LVGL + panel + touch; show boot screen)
 *   2. wifi_connect_start       (esp_hosted -> C6 -> esp_wifi -> IP)
 *   3. hermes_audio_init        (ES8388/ES7210 codecs via BSP)
 *   4. app_state_init           (FSM)
 *   5. hermes_ws_start          (connect to bridge, send hello)
 *   6. ptt_init                 (BOOT button GPIO35)
 *
 * After init the device sits in IDLE. Inbound bridge JSON is dispatched here
 * to the display + FSM; inbound TTS PCM is routed to the audio playback layer.
 */
#include "protocol.h"
#include "hermes_display.h"
#include "hermes_audio.h"
#include "hermes_ws.h"
#include "wifi_connect.h"
#include "app_state.h"

#include <string.h>
#include "esp_log.h"
#include "esp_system.h"
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

static const char *TAG = "main";

/* Bridge connection config (from Kconfig.projbuild / menuconfig). */
#ifndef CONFIG_BRIDGE_HOST
#define CONFIG_BRIDGE_HOST  "192.168.1.50"
#endif
#ifndef CONFIG_BRIDGE_PORT
#define CONFIG_BRIDGE_PORT  8765
#endif
#ifndef CONFIG_BRIDGE_TOKEN
#define CONFIG_BRIDGE_TOKEN ""
#endif

/* ---- inbound JSON dispatch (text frames from bridge) ------------------- */
static void on_ws_text(const char *json, size_t len)
{
    (void)len;
    cJSON *root = cJSON_Parse(json);
    if (!root) {
        ESP_LOGW(TAG, "bad JSON from bridge: %s", json);
        return;
    }
    cJSON *jtype = cJSON_GetObjectItemCaseSensitive(root, "type");
    const char *type = (jtype && cJSON_IsString(jtype)) ? jtype->valuestring : "";

    if (strcmp(type, HERMES_TYPE_HELLO) == 0) {
        cJSON *sid = cJSON_GetObjectItemCaseSensitive(root, "session_id");
        ESP_LOGI(TAG, "bridge hello — session=%s",
                 (sid && cJSON_IsString(sid)) ? sid->valuestring : "?");
        app_state_handle_event(APP_EVT_BRIDGE_HELLO);

    } else if (strcmp(type, HERMES_TYPE_STATUS) == 0) {
        cJSON *jst = cJSON_GetObjectItemCaseSensitive(root, "state");
        const char *st = (jst && cJSON_IsString(jst)) ? jst->valuestring : "";
        ESP_LOGI(TAG, "status: %s", st);
        app_state_on_status(st);

    } else if (strcmp(type, HERMES_TYPE_STT) == 0) {
        cJSON *jt = cJSON_GetObjectItemCaseSensitive(root, "text");
        const char *txt = (jt && cJSON_IsString(jt)) ? jt->valuestring : "";
        ESP_LOGI(TAG, "stt: %s", txt);
        hermes_display_set_transcript(txt);

    } else if (strcmp(type, HERMES_TYPE_LLM) == 0) {
        cJSON *jt = cJSON_GetObjectItemCaseSensitive(root, "text");
        const char *txt = (jt && cJSON_IsString(jt)) ? jt->valuestring : "";
        ESP_LOGI(TAG, "llm: %s", txt);
        hermes_display_set_reply(txt);

    } else if (strcmp(type, HERMES_TYPE_TTS) == 0) {
        cJSON *ja = cJSON_GetObjectItemCaseSensitive(root, "action");
        const char *act = (ja && cJSON_IsString(ja)) ? ja->valuestring : "";
        ESP_LOGI(TAG, "tts: %s", act);
        app_state_on_tts_action(act);

    } else if (strcmp(type, HERMES_TYPE_ERROR) == 0) {
        cJSON *jc = cJSON_GetObjectItemCaseSensitive(root, "code");
        cJSON *jm = cJSON_GetObjectItemCaseSensitive(root, "message");
        const char *code = (jc && cJSON_IsString(jc)) ? jc->valuestring : "";
        const char *msg  = (jm && cJSON_IsString(jm)) ? jm->valuestring : "";
        ESP_LOGW(TAG, "bridge error: %s %s", code, msg);
        app_state_on_error(code, msg);

    } else if (strcmp(type, HERMES_TYPE_NOTIFY) == 0) {
        cJSON *jt = cJSON_GetObjectItemCaseSensitive(root, "title");
        cJSON *jb = cJSON_GetObjectItemCaseSensitive(root, "body");
        cJSON *jl = cJSON_GetObjectItemCaseSensitive(root, "level");
        const char *title = (jt && cJSON_IsString(jt)) ? jt->valuestring : "Notification";
        const char *body  = (jb && cJSON_IsString(jb)) ? jb->valuestring : "";
        const char *level = (jl && cJSON_IsString(jl)) ? jl->valuestring : "info";
        ESP_LOGI(TAG, "notify: %s / %s / %s", title, body, level);
        hermes_display_show_notification(title, body, level);

    } else {
        ESP_LOGW(TAG, "unknown bridge message type: %s", type);
    }

    cJSON_Delete(root);
}

/* ---- inbound PCM (binary frames = TTS audio) --------------------------- */
static void on_ws_binary(const uint8_t *data, size_t len)
{
    /* Bridge streams 20 ms (640 B) PCM chunks during TTS. Feed the playback
     * ring buffer; the audio task drains it to the speaker. */
    hermes_audio_playback_write(data, len);
}

static void on_ws_conn(bool connected)
{
    if (connected) {
        /* Spawn a short-lived task to send hello. The WebSocket event handler
         * task can't call send_text — it deadlocks waiting for the send queue
         * while the event task is blocked. */
        xTaskCreate((void (*)(void *))hermes_ws_send_hello_task,
                    "ws_hello", 4096, NULL, 5, NULL);
    } else {
        app_state_handle_event(APP_EVT_BRIDGE_DISCONNECT);
    }
}

/* ---- app_main ---------------------------------------------------------- */
void app_main(void)
{
    ESP_LOGI(TAG, "Hermes Desk Tab5 firmware booting (MOT-78, M1)");

    /* 1. Display first so we can show boot progress. */
    ESP_ERROR_CHECK(hermes_display_init());
    hermes_display_show_boot("Booting\u2026");

    /* 2. WiFi (esp_hosted -> C6 -> esp_wifi). Blocks until IP. */
    esp_err_t net = wifi_connect_start();
    if (net != ESP_OK) {
        ESP_LOGE(TAG, "wifi failed — continuing so display can show the error");
        /* We don't reboot; the display now shows the failure. A future
         * provisioning flow (MOT-81) will let the user set creds. */
    }

    /* 3. Audio codecs. */
    ESP_ERROR_CHECK(hermes_audio_init());

    /* 4. State machine. */
    app_state_init();

    /* 5. Connect to the bridge. Auto-reconnects; sends hello on connect. */
    hermes_ws_config_t ws = {
        .host    = CONFIG_BRIDGE_HOST,
        .port    = CONFIG_BRIDGE_PORT,
        .path    = "/ws",
        .token   = CONFIG_BRIDGE_TOKEN,
        .on_text   = on_ws_text,
        .on_binary = on_ws_binary,
        .on_conn   = on_ws_conn,
    };
    esp_err_t wsr = hermes_ws_start(&ws);
    if (wsr != ESP_OK) {
        ESP_LOGE(TAG, "ws start failed: %s", esp_err_to_name(wsr));
    }

    /* 6. Touch-to-talk (no physical button on the Tab5). */
    hermes_display_register_touch_ptt();

    ESP_LOGI(TAG, "init complete — idle, waiting for touch");
    /* main task returns; FreeRTOS keeps the other tasks alive. */
}
