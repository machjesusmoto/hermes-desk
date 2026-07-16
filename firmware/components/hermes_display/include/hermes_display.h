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

/** Show a proactive notification card. `level` is "info"/"warning"/"urgent". */
void hermes_display_show_notification(const char *title, const char *body,
                                      const char *level);

/* ---- error --------------------------------------------------------------- */

/** Show an error banner with a code + message; auto-returns to status. */
void hermes_display_show_error(const char *code, const char *message);

/* ---- layout switching ---------------------------------------------------- */

/** Switch the active layout. Subsequent state/text calls target it. */
void hermes_display_set_layout(hermes_layout_t layout);

/** Boot/splash screen with the WiFi status line (used during connect). */
void hermes_display_show_boot(const char *wifi_status);

#ifdef __cplusplus
}
#endif
