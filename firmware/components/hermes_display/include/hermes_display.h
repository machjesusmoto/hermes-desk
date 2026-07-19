/**
 * @file hermes_display.h
 * @brief LVGL display manager for the Hermes Desk Tab5.
 *
 * Renders the four display layouts defined in docs/PROTOCOL.md /
 * docs/gateway-integration.md onto the 5" IPS panel (1280x720 via the BSP):
 *
 *   - voice_transcript : live user transcript + Hermes reply (conversation)
 *   - status_card      : idle/listening/processing/speaking state + PTT hint
 *   - status_card(+)   : proactive notification (title / body / level)
 *   - image            : a static image / splash (M3/M4 placeholder)
 *   - dashboard        : always-on status panel (M3 — placeholder grid)
 *
 * All entry points are thread-safe: they marshal work onto the LVGL task via
 * bsp_display_lock/bsp_display_unlock. Call from any task; never touch LVGL
 *
 * NOTE on resolution: docs/PROTOCOL.md mentions a 1024x600 panel, but the
 * shipping M5Stack Tab5 has a 1280x720 MIPI-DSI panel. This component is
 * resolution-independent — it reads the live panel size from the BSP and
 * lays out with LVGL percentage/flex containers so it works on either.
 */
#pragma once
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* The four layout modes the bridge can request. */
typedef enum {
    HERMES_LAYOUT_STATUS,         /* default: state + PTT hint */
    HERMES_LAYOUT_VOICE_TRANSCRIPT,
    HERMES_LAYOUT_STATUS_CARD,    /* notification / card with title+body */
    HERMES_LAYOUT_IMAGE,          /* placeholder for M3/M4 image rendering */
    HERMES_LAYOUT_DASHBOARD,      /* M3 always-on panel (placeholder) */
} hermes_layout_t;

/* High-level device state shown on the status layout. */
typedef enum {
    HERMES_DISP_BOOT,
    HERMES_DISP_IDLE,
    HERMES_DISP_LISTENING,
    HERMES_DISP_PROCESSING,
    HERMES_DISP_SPEAKING,
    HERMES_DISP_ERROR,
} hermes_disp_state_t;

/** Initialize LVGL + the display via the BSP. Call once at boot. */
esp_err_t hermes_display_init(void);

/* ---- status layout ------------------------------------------------------- */

/** Show the given device state with an optional one-line hint. */
void hermes_display_set_state(hermes_disp_state_t state, const char *hint);

/* ---- voice_transcript layout --------------------------------------------- */

/** Show/append the user's transcribed text (STT). Clears prior transcript. */
void hermes_display_set_transcript(const char *text);

/** Show/append the Hermes reply text (LLM). Appended under the transcript. */
void hermes_display_set_reply(const char *text);

/** Clear the conversation view. */
void hermes_display_clear_conversation(void);

/* ---- status_card (notifications) ---------------------------------------- */

/**
 * Show a proactive notification card. `level` is one of
 * "info"/"warning"/"error"/"success" (bridge set) or "urgent" (legacy alias).
 * `notification_id` is the bridge-assigned id used to ack the notification;
 * pass "" if unknown (no ack will be sent on dismiss).
 * `priority` (0..3) controls the accent and whether a "Dismiss" button is
 * shown; HIGH/URGENT cards persist until dismissed, NORMAL/LOW auto-dismiss.
 *
 * Renders a Dismiss button that, when tapped, sends notify_ack for the given
 * notification_id (via the callback registered with
 * hermes_display_set_dismiss_cb) and returns the device to the status layout.
 */
void hermes_display_show_notification(const char *title, const char *body,
                                      const char *level,
                                      const char *notification_id,
                                      int priority);

/**
 * Register the callback invoked when the user taps the Dismiss button on a
 * notification card. The callback receives the notification_id of the card
 * being dismissed. The bridge expects a notify_ack frame for that id; the
 * callback is the firmware's hook to send it (see hermes_ws_send_notify_ack).
 */
typedef void (*hermes_dismiss_cb_t)(const char *notification_id);
void hermes_display_set_dismiss_cb(hermes_dismiss_cb_t cb);

/* ---- error --------------------------------------------------------------- */

/** Show an error banner with a code + message; auto-returns to status. */
void hermes_display_show_error(const char *code, const char *message);

/* ---- layout switching ---------------------------------------------------- */

/** Switch the active layout. Subsequent state/text calls target it. */
void hermes_display_set_layout(hermes_layout_t layout);

/** Boot/splash screen with the WiFi status line (used during connect). */
void hermes_display_show_boot(const char *wifi_status);

/**
 * Register touch-to-talk on the screen. LVEL PRESSED → app_state PTT_PRESS,
 * RELEASED → PTT_RELEASE. Must be called after app_state_init() and
 * hermes_display_init().
 */
void hermes_display_register_touch_ptt(void);

#ifdef __cplusplus
}
#endif
