/**
 * @file app_state.c
 * @brief Device state machine — the single source of truth for who is doing
 *        what (capture vs playback) and what the display shows.
 *
 * Inputs are funneled through app_state_handle_event() so the display, audio,
 * and WS layers stay consistent. The FSM is small and explicit:
 *
 *   BOOT -(bridge hello)-> IDLE
 *   IDLE  -(ptt press)-> LISTENING  (capture start + listen:start + display)
 *   LISTENING -(ptt release)-> PROCESSING (capture stop + listen:stop + display)
 *   LISTENING -(abort press)-> IDLE  (capture stop + listen:stop)
 *   PROCESSING -(status:idle)-> IDLE  (empty transcript / no reply)
 *   PROCESSING -(tts:start)-> SPEAKING (playback start + display)
 *   SPEAKING -(tts:stop)-> IDLE  (playback stop + display)
 *   SPEAKING -(abort press)-> IDLE (playback abort + ws abort + display)
 *   any -(error)-> ERROR -> IDLE (bridge also sends status:idle)
 */
#include "app_state.h"
#include "hermes_ws.h"
#include "hermes_audio.h"
#include "hermes_display.h"
#include "protocol.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_log.h"

static const char *TAG = "app_state";

/* Internal event queue so ISR + other tasks can post transitions without
 * touching the FSM state from multiple contexts. */
typedef struct {
    app_event_t evt;
    char        *arg;     /* optional string arg (status state, error code) */
    char        *arg2;    /* optional second string arg (message) */
} fsm_event_t;

static QueueHandle_t s_fsm_q;
static app_state_t   s_state = APP_STATE_BOOT;

/* Forward. */
static void capture_cb(const uint8_t *pcm, size_t len);

/* ---- helpers ------------------------------------------------------------- */
static char *dup_or_null(const char *s)
{
    if (!s) return NULL;
    size_t n = strlen(s) + 1;
    char *c = malloc(n);
    if (c) memcpy(c, s, n);
    return c;
}

static void set_disp(app_state_t st)
{
    switch (st) {
    case APP_STATE_BOOT:       hermes_display_set_state(HERMES_DISP_BOOT, NULL); break;
    case APP_STATE_IDLE:       hermes_display_set_state(HERMES_DISP_IDLE, "Hold the side button to talk"); break;
    case APP_STATE_LISTENING:  hermes_display_set_state(HERMES_DISP_LISTENING, "Release to send"); break;
    case APP_STATE_PROCESSING: hermes_display_set_state(HERMES_DISP_PROCESSING, ""); break;
    case APP_STATE_SPEAKING:   hermes_display_set_state(HERMES_DISP_SPEAKING, "Tap button to interrupt"); break;
    case APP_STATE_ERROR:      hermes_display_set_state(HERMES_DISP_ERROR, ""); break;
    }
}

static void enter_listening(void)
{
    hermes_audio_playback_stop();          /* ensure not playing */
    hermes_ws_send_listen_start();
    hermes_audio_capture_start(capture_cb);
    hermes_display_set_layout(HERMES_LAYOUT_VOICE_TRANSCRIPT);
    hermes_display_clear_conversation();
    s_state = APP_STATE_LISTENING;
    set_disp(s_state);
}

static void enter_processing(void)
{
    hermes_audio_capture_stop();
    hermes_ws_send_listen_stop();
    s_state = APP_STATE_PROCESSING;
    set_disp(s_state);
}

static void enter_idle(void)
{
    hermes_audio_capture_stop();
    hermes_audio_playback_stop();
    hermes_display_set_layout(HERMES_LAYOUT_STATUS);
    s_state = APP_STATE_IDLE;
    set_disp(s_state);
}

static void enter_speaking(void)
{
    hermes_audio_playback_start();
    s_state = APP_STATE_SPEAKING;
    set_disp(s_state);
}

/* ---- FSM task ----------------------------------------------------------- */
static void fsm_task(void *arg)
{
    (void)arg;
    fsm_event_t fe;
    for (;;) {
        if (xQueueReceive(s_fsm_q, &fe, portMAX_DELAY) != pdTRUE) continue;

        switch (fe.evt) {
        case APP_EVT_BRIDGE_HELLO:
            if (s_state == APP_STATE_BOOT) enter_idle();
            break;

        case APP_EVT_PTT_PRESS:
            if (s_state == APP_STATE_IDLE) {
                enter_listening();
            } else if (s_state == APP_STATE_SPEAKING) {
                /* Barge-in: stop TTS, tell bridge to abort. */
                hermes_audio_playback_abort();
                hermes_ws_send_abort();
                enter_idle();
            }
            break;

        case APP_EVT_PTT_RELEASE:
            if (s_state == APP_STATE_LISTENING) {
                enter_processing();
            }
            break;

        case APP_EVT_BRIDGE_STATUS:
            /* status:{state} from the bridge. idle is the only one we act on
             * (it signals end of a turn). listening/processing are echoes. */
            if (fe.arg && strcmp(fe.arg, HERMES_STATE_IDLE) == 0) {
                if (s_state == APP_STATE_PROCESSING || s_state == APP_STATE_ERROR) {
                    enter_idle();
                }
            }
            break;

        case APP_EVT_BRIDGE_TTS_START:
            if (s_state == APP_STATE_PROCESSING) {
                enter_speaking();
            }
            break;

        case APP_EVT_BRIDGE_TTS_STOP:
            if (s_state == APP_STATE_SPEAKING) {
                enter_idle();
            }
            break;

        case APP_EVT_BRIDGE_ERROR:
            /* Bridge follows error with status:idle, so we just show it. */
            hermes_display_show_error(fe.arg ? fe.arg : "error",
                                      fe.arg2 ? fe.arg2 : "");
            s_state = APP_STATE_ERROR;
            break;

        case APP_EVT_BRIDGE_DISCONNECT:
            if (s_state != APP_STATE_BOOT) {
                hermes_audio_capture_stop();
                hermes_audio_playback_stop();
                hermes_display_show_boot("Bridge disconnected\u2026");
                s_state = APP_STATE_BOOT;
            }
            break;
        }

        free(fe.arg);
        free(fe.arg2);
    }
}

/* ---- capture -> WS TX --------------------------------------------------- */
static void capture_cb(const uint8_t *pcm, size_t len)
{
    /* Runs on the audio capture task. Just enqueue the PCM onto the WS link.
     * If the WS link is congested we drop the frame rather than block capture. */
    if (hermes_ws_is_connected()) {
        hermes_ws_send_audio(pcm, len);
    }
}

/* ---- public API -------------------------------------------------------- */
void app_state_init(void)
{
    s_fsm_q = xQueueCreate(16, sizeof(fsm_event_t));
    xTaskCreate(fsm_task, "fsm", 4096, NULL, 7, NULL);
    s_state = APP_STATE_BOOT;
    set_disp(s_state);
}

app_state_t app_state_current(void)
{
    return s_state;
}

static void post(app_event_t e, const char *a1, const char *a2)
{
    fsm_event_t fe = { .evt = e, .arg = dup_or_null(a1), .arg2 = dup_or_null(a2) };
    xQueueSend(s_fsm_q, &fe, pdMS_TO_TICKS(100));
}

void app_state_handle_event(app_event_t evt)
{
    post(evt, NULL, NULL);
}

void app_state_on_status(const char *state)
{
    post(APP_EVT_BRIDGE_STATUS, state, NULL);
}

void app_state_on_tts_action(const char *action)
{
    if (action && strcmp(action, HERMES_ACTION_START) == 0) {
        post(APP_EVT_BRIDGE_TTS_START, NULL, NULL);
    } else {
        post(APP_EVT_BRIDGE_TTS_STOP, NULL, NULL);
    }
}

void app_state_on_error(const char *code, const char *message)
{
    post(APP_EVT_BRIDGE_ERROR, code, message);
}
