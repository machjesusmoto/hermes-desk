/**
 * @file app_state.h
 * @brief Central device state machine for the Hermes Desk Tab5.
 *
 * One FSM owns the high-level device state and arbitrates who is doing what
 * with the audio hardware (capture vs. playback) and what the display shows.
 * Inputs: PTT button events, bridge control messages, audio pipeline events.
 *
 * The state graph (mirrors docs/PROTOCOL.md "Conversation Flow"):
 *
 *   IDLE --(ptt_press)-----> LISTENING --(ptt_release)--> PROCESSING
 *    ^                           |                            |
 *    |                           `--(abort)-----------------.'|
 *    |                                                        |
 *    |   PROCESSING --(tts:start)--> SPEAKING --(tts:stop)----'
 *    |                       `------(error)-------------------'
 *    `<---------(abort during SPEAKING)-----------'
 *
 * All transitions are funneled through app_state_transition() so the display,
 * audio, and WS layers stay in sync from a single source of truth.
 */
#pragma once
#include "protocol.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    APP_STATE_BOOT,        /* waiting for WiFi + bridge hello */
    APP_STATE_IDLE,        /* ready, waiting for PTT */
    APP_STATE_LISTENING,   /* capturing mic -> streaming PCM up */
    APP_STATE_PROCESSING,  /* sent listen:stop, awaiting stt/llm/tts */
    APP_STATE_SPEAKING,    /* playing TTS PCM back from bridge */
    APP_STATE_ERROR,       /* transient error display; auto-returns to IDLE */
} app_state_t;

/* Events that can drive a transition. */
typedef enum {
    APP_EVT_PTT_PRESS,        /* user pressed push-to-talk */
    APP_EVT_PTT_RELEASE,      /* user released push-to-talk */
    APP_EVT_BRIDGE_HELLO,     /* received hello from bridge */
    APP_EVT_BRIDGE_STATUS,    /* received status:{state} */
    APP_EVT_BRIDGE_TTS_START, /* received tts:action=start */
    APP_EVT_BRIDGE_TTS_STOP,  /* received tts:action=stop */
    APP_EVT_BRIDGE_ERROR,     /* received error (bridge already sends status:idle) */
    APP_EVT_BRIDGE_DISCONNECT,
} app_event_t;

/**
 * Initialize the state machine (creates the internal mutex + display).
 * Called once from main() after WiFi + WS are up.
 */
void app_state_init(void);

/** Current state (thread-safe snapshot). */
app_state_t app_state_current(void);

/**
 * Drive a transition. Safe to call from any task (PTT ISR -> queue, WS task,
 * audio task). Dispatches side effects to the display + audio layers.
 */
void app_state_handle_event(app_event_t evt);

/* Convenience helpers used by the WS dispatcher (app_state.c routes these). */
void app_state_on_status(const char *state);
void app_state_on_tts_action(const char *action);
void app_state_on_error(const char *code, const char *message);

#ifdef __cplusplus
}
#endif
