/**
 * @file hermes_display.c
 * @brief LVGL display manager for the Hermes Desk Tab5.
 *
 * Builds the UI with LVGL 9 containers that size to the live panel resolution
 * (read from the BSP display), so it works whether the panel is the shipping
 * 1280x720 or any future variant. All public functions are thread-safe: they
 * lock the LVGL port (bsp_display_lock) before touching widgets and unlock on
 * return. Never call LVGL directly from outside this file.
 *
 * Layouts (docs/PROTOCOL.md / gateway-integration.md):
 *   STATUS           - big state label + PTT hint (default / idle)
 *   VOICE_TRANSCRIPT - user transcript (top) + Hermes reply (bottom), scroll
 *   STATUS_CARD      - notification: icon + title + body, colored by level
 *   IMAGE            - placeholder splash (M3/M4 will render bridge images)
 *   DASHBOARD        - placeholder grid for the M3 always-on panel
 *
 * Style: dark background, large legible type (this is a desk device read at
 * arm's length). State colors: idle=slate, listening=cyan, processing=amber,
 * speaking=green, error=red.
 */
#include "hermes_display.h"

#include <string.h>
#include "esp_log.h"
#include "esp_lvgl_port.h"
#include "lvgl.h"
#include "bsp/m5stack_tab5.h"

static const char *TAG = "hermes_display";

/* ---- theme --------------------------------------------------------------- */
#define COLOR_BG        lv_color_hex(0x0E1116)
#define COLOR_PANEL     lv_color_hex(0x1A1F27)
#define COLOR_FG        lv_color_hex(0xE6EDF3)
#define COLOR_MUTED     lv_color_hex(0x8B949E)
#define COLOR_IDLE      lv_color_hex(0x6E7681)
#define COLOR_LISTEN    lv_color_hex(0x39D0D8)
#define COLOR_PROCESS   lv_color_hex(0xF0B429)
#define COLOR_SPEAK     lv_color_hex(0x3FB950)
#define COLOR_ERROR     lv_color_hex(0xF85149)
#define COLOR_INFO      lv_color_hex(0x39D0D8)
#define COLOR_WARN      lv_color_hex(0xF0B429)
#define COLOR_URGENT    lv_color_hex(0xF85149)

/* ---- module state -------------------------------------------------------- */
typedef struct {
    lv_obj_t       *screen;
    /* layout containers (one per layout; toggled by set_layout) */
    lv_obj_t       *status_cont;     /* STATUS / IMAGE / DASHBOARD */
    lv_obj_t       *conv_cont;       /* VOICE_TRANSCRIPT */
    lv_obj_t       *card_cont;       /* STATUS_CARD (notifications) */
    /* status layout widgets */
    lv_obj_t       *status_state;    /* big label */
    lv_obj_t       *status_hint;     /* small label under it */
    /* voice transcript widgets */
    lv_obj_t       *conv_transcript; /* user said ... */
    lv_obj_t       *conv_reply;      /* hermes said ... */
    /* status card widgets */
    lv_obj_t       *card_icon;
    lv_obj_t       *card_title;
    lv_obj_t       *card_body;
    lv_obj_t       *boot_status;     /* top-left boot status line */
    hermes_layout_t layout;
    lv_style_t      style_screen;
} disp_ctx_t;

static disp_ctx_t s_d;

/* ---- helpers: run on the LVGL task, thread-safe -------------------------- */
/* The BSP exposes bsp_display_lock(timeout_ms)/bsp_display_unlock() which wrap
 * the LVGL port lock. We use a generous timeout because the LVGL task can be
 * busy flushing a 1280x720 frame. Pass 0 to wait indefinitely. */
static bool display_lock(uint32_t timeout_ms)
{
    return bsp_display_lock(timeout_ms);
}
static void display_unlock(void)
{
    bsp_display_unlock();
}

static lv_color_t state_color(hermes_disp_state_t s)
{
    switch (s) {
    case HERMES_DISP_IDLE:      return COLOR_IDLE;
    case HERMES_DISP_LISTENING: return COLOR_LISTEN;
    case HERMES_DISP_PROCESSING:return COLOR_PROCESS;
    case HERMES_DISP_SPEAKING:  return COLOR_SPEAK;
    case HERMES_DISP_ERROR:     return COLOR_ERROR;
    case HERMES_DISP_BOOT:
    default:                    return COLOR_MUTED;
    }
}

static const char *state_label(hermes_disp_state_t s)
{
    switch (s) {
    case HERMES_DISP_BOOT:       return "Starting\u2026";
    case HERMES_DISP_IDLE:       return "Ready";
    case HERMES_DISP_LISTENING:  return "Listening\u2026";
    case HERMES_DISP_PROCESSING: return "Thinking\u2026";
    case HERMES_DISP_SPEAKING:   return "Speaking\u2026";
    case HERMES_DISP_ERROR:      return "Error";
    default:                     return "";
    }
}

/* Show one layout, hide the others. Called on the LVGL task. */
static void show_layout(hermes_layout_t l)
{
    /* Hide all layout containers, then reveal the chosen one. The boot_status
     * label is always-on (overlays whatever layout is active). */
    if (s_d.status_cont) lv_obj_add_flag(s_d.status_cont, LV_OBJ_FLAG_HIDDEN);
    if (s_d.conv_cont)   lv_obj_add_flag(s_d.conv_cont, LV_OBJ_FLAG_HIDDEN);
    if (s_d.card_cont)   lv_obj_add_flag(s_d.card_cont, LV_OBJ_FLAG_HIDDEN);

    switch (l) {
    case HERMES_LAYOUT_STATUS:
    case HERMES_LAYOUT_IMAGE:
    case HERMES_LAYOUT_DASHBOARD:
        if (s_d.status_cont) lv_obj_clear_flag(s_d.status_cont, LV_OBJ_FLAG_HIDDEN);
        break;
    case HERMES_LAYOUT_VOICE_TRANSCRIPT:
        if (s_d.conv_cont) lv_obj_clear_flag(s_d.conv_cont, LV_OBJ_FLAG_HIDDEN);
        break;
    case HERMES_LAYOUT_STATUS_CARD:
        if (s_d.card_cont) lv_obj_clear_flag(s_d.card_cont, LV_OBJ_FLAG_HIDDEN);
        break;
    }
    s_d.layout = l;
}

/* ---- UI construction (runs once, on the LVGL task) ----------------------- */
static void build_ui(void)
{
    lv_coord_t w = lv_disp_get_hor_res(NULL);
    lv_coord_t h = lv_disp_get_ver_res(NULL);

    lv_style_init(&s_d.style_screen);
    lv_style_set_bg_color(&s_d.style_screen, COLOR_BG);
    lv_style_set_bg_opa(&s_d.style_screen, LV_OPA_COVER);
    lv_style_set_text_color(&s_d.style_screen, COLOR_FG);
    lv_style_set_pad_all(&s_d.style_screen, 0);

    s_d.screen = lv_obj_create(NULL);
    lv_obj_add_style(s_d.screen, &s_d.style_screen, 0);
    lv_obj_set_size(s_d.screen, w, h);
    lv_obj_clear_flag(s_d.screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_scr_load(s_d.screen);

    /* ---- Status layout (default) ---- */
    s_d.status_cont = lv_obj_create(s_d.screen);
    lv_obj_set_size(s_d.status_cont, w, h);
    lv_obj_center(s_d.status_cont);
    lv_obj_set_style_bg_opa(s_d.status_cont, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(s_d.status_cont, 0, 0);
    lv_obj_set_flex_flow(s_d.status_cont, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(s_d.status_cont, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    s_d.status_state = lv_label_create(s_d.status_cont);
    lv_obj_set_style_text_font(s_d.status_state, &lv_font_montserrat_48, 0);
    lv_label_set_text(s_d.status_state, "Ready");
    lv_obj_set_style_text_color(s_d.status_state, COLOR_IDLE, 0);

    s_d.status_hint = lv_label_create(s_d.status_cont);
    lv_obj_set_style_text_font(s_d.status_hint, &lv_font_montserrat_24, 0);
    lv_label_set_text(s_d.status_hint, "Hold the side button to talk");
    lv_obj_set_style_text_color(s_d.status_hint, COLOR_MUTED, 0);
    lv_obj_set_style_pad_top(s_d.status_hint, 24, 0);

    /* ---- Voice transcript layout ---- */
    s_d.conv_cont = lv_obj_create(s_d.screen);
    lv_obj_set_size(s_d.conv_cont, w, h);
    lv_obj_set_pos(s_d.conv_cont, 0, 0);
    lv_obj_set_style_bg_opa(s_d.conv_cont, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(s_d.conv_cont, 0, 0);
    lv_obj_set_flex_flow(s_d.conv_cont, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(s_d.conv_cont, 48, 0);
    lv_obj_add_flag(s_d.conv_cont, LV_OBJ_FLAG_HIDDEN);

    s_d.conv_transcript = lv_label_create(s_d.conv_cont);
    lv_obj_set_style_text_font(s_d.conv_transcript, &lv_font_montserrat_28, 0);
    lv_obj_set_width(s_d.conv_transcript, w - 96);
    lv_label_set_long_mode(s_d.conv_transcript, LV_LABEL_LONG_WRAP);
    lv_label_set_text(s_d.conv_transcript, "");
    lv_obj_set_style_text_color(s_d.conv_transcript, COLOR_LISTEN, 0);

    s_d.conv_reply = lv_label_create(s_d.conv_cont);
    lv_obj_set_style_text_font(s_d.conv_reply, &lv_font_montserrat_32, 0);
    lv_obj_set_width(s_d.conv_reply, w - 96);
    lv_label_set_long_mode(s_d.conv_reply, LV_LABEL_LONG_WRAP);
    lv_label_set_text(s_d.conv_reply, "");
    lv_obj_set_style_text_color(s_d.conv_reply, COLOR_FG, 0);
    lv_obj_set_style_pad_top(s_d.conv_reply, 48, 0);

    /* ---- Status card (notification) layout ---- */
    s_d.card_cont = lv_obj_create(s_d.screen);
    lv_obj_set_size(s_d.card_cont, w * 80 / 100, h * 60 / 100);
    lv_obj_center(s_d.card_cont);
    lv_obj_set_style_bg_color(s_d.card_cont, COLOR_PANEL, 0);
    lv_obj_set_style_bg_opa(s_d.card_cont, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(s_d.card_cont, 24, 0);
    lv_obj_set_style_border_width(s_d.card_cont, 0, 0);
    lv_obj_set_flex_flow(s_d.card_cont, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_all(s_d.card_cont, 48, 0);
    lv_obj_add_flag(s_d.card_cont, LV_OBJ_FLAG_HIDDEN);

    s_d.card_icon = lv_label_create(s_d.card_cont);
    lv_obj_set_style_text_font(s_d.card_icon, &lv_font_montserrat_48, 0);
    lv_label_set_text(s_d.card_icon, "\xF0\x9F\x94\x94");  /* bell */

    s_d.card_title = lv_label_create(s_d.card_cont);
    lv_obj_set_style_text_font(s_d.card_title, &lv_font_montserrat_36, 0);
    lv_label_set_text(s_d.card_title, "");
    lv_obj_set_style_pad_top(s_d.card_title, 24, 0);

    s_d.card_body = lv_label_create(s_d.card_cont);
    lv_obj_set_style_text_font(s_d.card_body, &lv_font_montserrat_24, 0);
    lv_obj_set_width(s_d.card_body, (w * 80 / 100) - 96);
    lv_label_set_long_mode(s_d.card_body, LV_LABEL_LONG_WRAP);
    lv_label_set_text(s_d.card_body, "");
    lv_obj_set_style_text_color(s_d.card_body, COLOR_MUTED, 0);
    lv_obj_set_style_pad_top(s_d.card_body, 16, 0);

    /* ---- Boot overlay (top-left WiFi status) ---- */
    s_d.boot_status = lv_label_create(s_d.screen);
    lv_obj_set_style_text_font(s_d.boot_status, &lv_font_montserrat_20, 0);
    lv_obj_set_pos(s_d.boot_status, 32, 32);
    lv_label_set_text(s_d.boot_status, "Booting\u2026");
    lv_obj_set_style_text_color(s_d.boot_status, COLOR_MUTED, 0);

    /* Show status layout first. */
    show_layout(HERMES_LAYOUT_STATUS);

    (void)h;
}

/* ---- public API ---------------------------------------------------------- */

esp_err_t hermes_display_init(void)
{
    ESP_LOGI(TAG, "starting display via BSP");

    /* Canonical esp-bsp bring-up: bsp_display_start() initializes the panel,
     * GT911 touch, and the LVGL port+task in one call. Then turn the backlight
     * on so the panel is visible. (See esp-bsp examples/display/main.c.) */
    bsp_display_start();
    bsp_display_backlight_on();

    /* Take the display lock and build the UI on the LVGL task. */
    if (!display_lock(2000)) {
        ESP_LOGE(TAG, "LVGL lock timeout during init");
        return ESP_ERR_TIMEOUT;
    }
    build_ui();
    display_unlock();

    ESP_LOGI(TAG, "display ready");
    return ESP_OK;
}

void hermes_display_set_state(hermes_disp_state_t state, const char *hint)
{
    if (!display_lock(500)) return;
    /* Make sure the status layout is visible. */
    lv_label_set_text(s_d.status_state, state_label(state));
    lv_obj_set_style_text_color(s_d.status_state, state_color(state), 0);
    if (hint) lv_label_set_text(s_d.status_hint, hint);
    display_unlock();
}

void hermes_display_set_transcript(const char *text)
{
    if (!text) return;
    if (!display_lock(500)) return;
    lv_label_set_text(s_d.conv_transcript, text);
    display_unlock();
}

void hermes_display_set_reply(const char *text)
{
    if (!text) return;
    if (!display_lock(500)) return;
    lv_label_set_text(s_d.conv_reply, text);
    display_unlock();
}

void hermes_display_clear_conversation(void)
{
    if (!display_lock(500)) return;
    lv_label_set_text(s_d.conv_transcript, "");
    lv_label_set_text(s_d.conv_reply, "");
    display_unlock();
}

void hermes_display_show_notification(const char *title, const char *body,
                                      const char *level)
{
    if (!display_lock(500)) return;
    lv_color_t c = COLOR_INFO;
    const char *icon = "\xF0\x9F\x94\x94"; /* bell */
    if (level && strcmp(level, "warning") == 0)      { c = COLOR_WARN;   }
    else if (level && strcmp(level, "urgent") == 0)  { c = COLOR_URGENT; icon = "\xE2\x9A\xA0\xEF\xB8\x8F"; }

    lv_label_set_text(s_d.card_icon, icon);
    lv_obj_set_style_text_color(s_d.card_icon, c, 0);
    lv_label_set_text(s_d.card_title, title ? title : "Notification");
    lv_label_set_text(s_d.card_body, body ? body : "");
    display_unlock();
}

void hermes_display_show_error(const char *code, const char *message)
{
    if (!display_lock(500)) return;
    char buf[160];
    snprintf(buf, sizeof(buf), "%s%s%s",
             code ? code : "error",
             message && message[0] ? ": " : "",
             message ? message : "");
    lv_label_set_text(s_d.status_state, "Error");
    lv_obj_set_style_text_color(s_d.status_state, COLOR_ERROR, 0);
    lv_label_set_text(s_d.status_hint, buf);
    display_unlock();
}

void hermes_display_set_layout(hermes_layout_t layout)
{
    if (!display_lock(500)) return;
    show_layout(layout);   /* pointer-based; robust to child ordering */
    display_unlock();
}

void hermes_display_show_boot(const char *wifi_status)
{
    if (!display_lock(500)) return;
    lv_label_set_text(s_d.status_state, "Starting\u2026");
    lv_obj_set_style_text_color(s_d.status_state, COLOR_MUTED, 0);
    lv_label_set_text(s_d.status_hint, wifi_status ? wifi_status : "");
    display_unlock();
}
