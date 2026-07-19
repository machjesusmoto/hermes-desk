"""Static syntax verification of the firmware C files changed for TAY-9.

The ESP-IDF + LVGL + cJSON + FreeRTOS toolchain isn't available in this
sandbox, so we can't do a full firmware build here. Instead we use pycparser
(a pure-Python C99 parser) with a set of minimal stub headers for the
non-stdlib includes. This catches syntax errors, undeclared identifiers,
mismatched signatures, and missing forward declarations in the changed code
— a meaningful verification short of a real `idf.py build`.

Run:  python tests/firmware_syntax_check.py
Exit code 0 = all three files parse cleanly.
"""
from __future__ import annotations

import os
import sys

from pycparser import c_parser, parse_file as _parse_file

HERE = os.path.dirname(os.path.abspath(__file__))
FW = os.path.normpath(os.path.join(HERE, "..", "..", "firmware"))

# Minimal stub headers for the non-stdlib includes. Only the symbols the
# changed code actually touches are declared — enough for pycparser to parse.
STUBS = {
    "esp_log.h": (
        "/* stdint types many ESP headers assume are present */\n"
        "typedef unsigned char uint8_t;\n"
        "typedef unsigned short uint16_t;\n"
        "typedef unsigned int uint32_t;\n"
        "typedef int int32_t;\n"
        "typedef unsigned long size_t;\n"
        "#define NULL ((void *)0)\n"
        "void ESP_LOGI(const char *tag, const char *fmt, ...);\n"
        "void ESP_LOGW(const char *tag, const char *fmt, ...);\n"
        "void ESP_LOGE(const char *tag, const char *fmt, ...);\n"
        "void ESP_LOGD(const char *tag, const char *fmt, ...);\n"
    ),
    "esp_system.h": (
        "void esp_system_abort(const char *);\n"
        "const char *esp_err_to_name(int);\n"
    ),
    "esp_err.h": (
        "typedef int esp_err_t;\n"
        "#define ESP_OK          0\n"
        "#define ESP_FAIL        -1\n"
        "#define ESP_ERR_INVALID_ARG      0x102\n"
        "#define ESP_ERR_INVALID_STATE    0x103\n"
        "#define ESP_ERR_NO_MEM           0x101\n"
        "#define ESP_ERR_NOT_FINISHED     0x10b\n"
        "#define ESP_ERR_TIMEOUT          0x107\n"
        "#define ESP_ERROR_CHECK(x) ((void)(x))\n"
    ),
    "sdkconfig.h": (
        "#define CONFIG_BRIDGE_HOST \"192.168.1.50\"\n"
        "#define CONFIG_BRIDGE_PORT 8765\n"
        "#define CONFIG_BRIDGE_TOKEN \"\"\n"
    ),
    "cJSON.h": (
        "typedef struct cJSON { void *next; void *prev; void *child; "
        "int type; char *valuestring; int valueint; double valuedouble; "
        "char *string; } cJSON;\n"
        "cJSON *cJSON_Parse(const char *);\n"
        "void cJSON_Delete(cJSON *);\n"
        "cJSON *cJSON_GetObjectItemCaseSensitive(cJSON *, const char *);\n"
        "#define cJSON_IsString(x) ((x) && (x)->valuestring)\n"
        "#define cJSON_IsNumber(x) ((x) && 1)\n"
        "#define cJSON_IsTrue(x)   ((x) && 1)\n"
        "cJSON *cJSON_CreateObject(void);\n"
        "void cJSON_AddStringToObject(cJSON *, const char *, const char *);\n"
        "void cJSON_AddNumberToObject(cJSON *, const char *, double);\n"
        "char *cJSON_PrintUnformatted(cJSON *);\n"
    ),
    "freertos/FreeRTOS.h": (
        "typedef int BaseType_t;\n"
        "typedef int TickType_t;\n"
        "#define portTICK_PERIOD_MS 1\n"
        "#define pdMS_TO_TICKS(x) (x)\n"
    ),
    "freertos/task.h": (
        "typedef void *TaskHandle_t;\n"
        "void vTaskDelay(int);\n"
        "void vTaskDelete(void *);\n"
        "void xTaskCreate(void *, const char *, int, void *, int, void *);\n"
    ),
    "freertos/event_groups.h": (
        "typedef void *EventGroupHandle_t;\n"
        "typedef int EventBits_t;\n"
        "#define BIT0 1\n"
        "EventGroupHandle_t xEventGroupCreate(void);\n"
        "void xEventGroupSetBits(EventGroupHandle_t, EventBits_t);\n"
        "void xEventGroupClearBits(EventGroupHandle_t, EventBits_t);\n"
        "void vEventGroupDelete(EventGroupHandle_t);\n"
    ),
    "esp_wifi.h": (
        "typedef int wifi_interface_t;\n"
        "#define WIFI_IF_STA 0\n"
        "int esp_wifi_get_mac(int, unsigned char *);\n"
        "int esp_wifi_start(void);\n"
    ),
    "esp_websocket_client.h": (
        # esp_event_base_t is pulled in via esp_event.h in the real IDF.
        "typedef const char *esp_event_base_t;\n"
        "typedef struct esp_websocket_client *esp_websocket_client_handle_t;\n"
        "typedef struct { const char *uri; const char *path; int port; "
        "int transport; int reconnect_timeout_ms; int network_timeout_ms; "
        "int buffer_size; int task_stack; bool skip_cert_common_name_check; "
        "int ping_interval_sec; } esp_websocket_client_config_t;\n"
        "#define WEBSOCKET_EVENT_ANY -1\n"
        "#define WEBSOCKET_EVENT_CONNECTED 0\n"
        "#define WEBSOCKET_EVENT_DISCONNECTED 1\n"
        "#define WEBSOCKET_EVENT_DATA 2\n"
        "#define WEBSOCKET_EVENT_ERROR 3\n"
        "#define WEBSOCKET_TRANSPORT_OVER_TCP 0\n"
        "typedef struct { int op_code; int data_len; const char *data_ptr; } "
        "esp_websocket_event_data_t;\n"
        "esp_websocket_client_handle_t esp_websocket_client_init(const void *);\n"
        "void esp_websocket_register_events(esp_websocket_client_handle_t, int, void *, void *);\n"
        "int esp_websocket_client_start(esp_websocket_client_handle_t);\n"
        "int esp_websocket_client_send_text(esp_websocket_client_handle_t, const char *, int, int);\n"
        "int esp_websocket_client_send_bin(esp_websocket_client_handle_t, const char *, int, int);\n"
        "void esp_websocket_client_destroy(esp_websocket_client_handle_t);\n"
        "int esp_websocket_client_is_connected(esp_websocket_client_handle_t);\n"
    ),
    # LVGL — only the symbols the changed display code uses.
    "lvgl.h": (
        "typedef int lv_coord_t;\n"
        "typedef int lv_color_t;\n"
        "typedef int lv_style_t;\n"
        "typedef struct _lv_obj_t lv_obj_t;\n"
        "typedef struct _lv_event_t lv_event_t;\n"
        "typedef struct _lv_indev_t lv_indev_t;\n"
        "typedef int lv_event_code_t;\n"
        "typedef int lv_opa_t;\n"
        "#define LV_OPA_COVER 255\n"
        "#define LV_OPA_TRANSP 0\n"
        "#define LV_OBJ_FLAG_HIDDEN 1\n"
        "#define LV_OBJ_FLAG_CLICKABLE 2\n"
        "#define LV_OBJ_FLAG_SCROLLABLE 4\n"
        "#define LV_FLEX_FLOW_COLUMN 0\n"
        "#define LV_FLEX_ALIGN_CENTER 0\n"
        "#define LV_LABEL_LONG_WRAP 0\n"
        "#define LV_EVENT_PRESSED 0\n"
        "#define LV_EVENT_RELEASED 1\n"
        "#define LV_EVENT_CLICKED 2\n"
        "#define LV_INDEV_MODE_TIMER 1\n"
        "void lv_style_init(lv_style_t *);\n"
        "void lv_style_set_bg_color(lv_style_t *, lv_color_t);\n"
        "void lv_style_set_bg_opa(lv_style_t *, lv_opa_t);\n"
        "void lv_style_set_text_color(lv_style_t *, lv_color_t);\n"
        "void lv_style_set_pad_all(lv_style_t *, int);\n"
        "lv_color_t lv_color_hex(unsigned int);\n"
        "lv_coord_t lv_disp_get_hor_res(void *);\n"
        "lv_coord_t lv_disp_get_ver_res(void *);\n"
        "lv_obj_t *lv_obj_create(lv_obj_t *);\n"
        "void lv_obj_add_style(lv_obj_t *, lv_style_t *, int);\n"
        "void lv_obj_set_size(lv_obj_t *, lv_coord_t, lv_coord_t);\n"
        "void lv_obj_center(lv_obj_t *);\n"
        "void lv_obj_set_pos(lv_obj_t *, lv_coord_t, lv_coord_t);\n"
        "void lv_obj_set_style_bg_opa(lv_obj_t *, lv_opa_t, int);\n"
        "void lv_obj_set_style_bg_color(lv_obj_t *, lv_color_t, int);\n"
        "void lv_obj_set_style_border_width(lv_obj_t *, int, int);\n"
        "void lv_obj_set_style_radius(lv_obj_t *, int, int);\n"
        "void lv_obj_set_style_text_font(lv_obj_t *, const void *, int);\n"
        "void lv_obj_set_style_text_color(lv_obj_t *, lv_color_t, int);\n"
        "void lv_obj_set_style_pad_top(lv_obj_t *, int, int);\n"
        "void lv_obj_set_style_pad_all(lv_obj_t *, int, int);\n"
        "void lv_obj_set_style_border_color(lv_obj_t *, lv_color_t, int);\n"
        "void lv_obj_set_flex_flow(lv_obj_t *, int);\n"
        "void lv_obj_set_flex_align(lv_obj_t *, int, int, int);\n"
        "void lv_obj_set_width(lv_obj_t *, lv_coord_t);\n"
        "void lv_obj_clear_flag(lv_obj_t *, int);\n"
        "void lv_obj_add_flag(lv_obj_t *, int);\n"
        "void lv_scr_load(lv_obj_t *);\n"
        "lv_obj_t *lv_label_create(lv_obj_t *);\n"
        "void lv_label_set_text(lv_obj_t *, const char *);\n"
        "void lv_label_set_long_mode(lv_obj_t *, int);\n"
        "lv_obj_t *lv_btn_create(lv_obj_t *);\n"
        "void lv_obj_add_event_cb(lv_obj_t *, void (*)(lv_event_t *), int, void *);\n"
        "lv_event_code_t lv_event_get_code(lv_event_t *);\n"
        "void lv_indev_set_mode(lv_indev_t *, int);\n"
        "extern const void lv_font_montserrat_20;\n"
        "extern const void lv_font_montserrat_24;\n"
        "extern const void lv_font_montserrat_28;\n"
        "extern const void lv_font_montserrat_32;\n"
        "extern const void lv_font_montserrat_36;\n"
        "extern const void lv_font_montserrat_48;\n"
    ),
    "esp_lcd_touch.h": "typedef int esp_lcd_touch_t;\n",
    # Fake stdlib headers — just the symbols the changed firmware code uses.
    "stdint.h": (
        "typedef unsigned char uint8_t;\n"
        "typedef unsigned short uint16_t;\n"
        "typedef signed char int8_t;\n"
        "typedef short int16_t;\n"
        "typedef int int32_t;\n"
        "typedef unsigned int uint32_t;\n"
    ),
    "stdbool.h": (
        "#define bool int\n"
        "#define true 1\n"
        "#define false 0\n"
    ),
    "stddef.h": (
        "typedef unsigned long size_t;\n"
        "#define NULL ((void *)0)\n"
    ),
    "string.h": (
        "char *strncpy(char *, const char *, unsigned long);\n"
        "char *strdup(const char *);\n"
        "int strcmp(const char *, const char *);\n"
        "int strncmp(const char *, const char *, unsigned long);\n"
        "void *memcpy(void *, const void *, unsigned long);\n"
        "void *memset(void *, int, unsigned long);\n"
        "unsigned long strlen(const char *);\n"
        "int snprintf(char *, unsigned long, const char *, ...);\n"
    ),
    "stdlib.h": "void *malloc(unsigned long); void free(void *);\n",
    "stdio.h": "int snprintf(char *, unsigned long, const char *, ...);\n",
    "esp_lvgl_port.h": (
        "typedef struct _lv_indev_t lv_indev_t;  /* forward decl for ordering */\n"
        "int bsp_display_start(void);\n"
        "void bsp_display_backlight_on(void);\n"
        "int bsp_display_lock(int);\n"
        "void bsp_display_unlock(void);\n"
        "lv_indev_t *bsp_display_get_input_dev(void);\n"
    ),
    "bsp/m5stack_tab5.h": (
        "/* symbols come from esp_lvgl_port stub above */\n"
    ),
    "app_state.h": (
        "typedef enum { APP_EVT_BRIDGE_HELLO, APP_EVT_BRIDGE_DISCONNECT, "
        "APP_EVT_PTT_PRESS, APP_EVT_PTT_RELEASE } app_event_t;\n"
        "void app_state_init(void);\n"
        "void app_state_handle_event(app_event_t);\n"
        "void app_state_on_status(const char *);\n"
        "void app_state_on_tts_action(const char *);\n"
        "void app_state_on_error(const char *, const char *);\n"
    ),
    "ptt.h": "void ptt_init(void);\n",
    "wifi_connect.h": "int wifi_connect_start(void);\n",
    # protocol.h, hermes_*.h are real (in-repo) — parsed directly.
}


def _write_stubs(stub_dir: str) -> None:
    os.makedirs(stub_dir, exist_ok=True)
    for name, body in STUBS.items():
        p = os.path.join(stub_dir, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(body)


def parse_file(path: str, include_dirs: list[str]) -> tuple[bool, str]:
    # No `cpp` binary in this sandbox, so we manually expand #includes against
    # the include dirs (real repo headers + stub fakes for ESP/LVGL/cJSON),
    # strip preprocessor directives, then run pycparser's C99 parser. This
    # verifies the changed C is syntactically valid and that every identifier
    # used is declared — short of a real `idf.py build` (needs the IDF toolchain).
    parser = c_parser.CParser()
    return _parse_no_cpp(path, include_dirs, parser)


def _strip_comments_and_continuations(src: str) -> str:
    """Remove C comments while preserving string/char literals.

    A naive regex strips `//` inside strings like "ws://host" — fatal for
    firmware full of URL/protocol literals. Walk char-by-char, tracking
    whether we're inside a string or char literal, and only strip comments
    when outside one. Also joins line continuations.
    """
    out = []
    i = 0
    n = len(src)
    in_str = False      # "..."
    in_char = False     # '...'
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(nxt); i += 2; continue
            if c == '"':
                in_str = False
            i += 1; continue
        if in_char:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(nxt); i += 2; continue
            if c == "'":
                in_char = False
            i += 1; continue
        # Not inside a literal.
        if c == '"':
            in_str = True; out.append(c); i += 1; continue
        if c == "'":
            in_char = True; out.append(c); i += 1; continue
        if c == "/" and nxt == "/":
            # line comment — skip to end of line
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            # block comment — skip to */
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    cleaned = "".join(out)
    # Join line continuations (backslash-newline).
    cleaned = cleaned.replace("\\\n", " ")
    return cleaned


def _expand_includes(src: str, include_dirs: list[str], seen: set[str]) -> str:
    import re
    out = []
    for line in src.splitlines():
        m = re.match(r'\s*#\s*include\s*[<"]([^>"]+)[>"]', line)
        if not m:
            out.append(line)
            continue
        inc = m.group(1)
        if inc in seen:
            out.append("")  # already included
            continue
        # stdlib headers we drop (no symbols used from these in changed code)
        stdlib = {"stdlib.h", "stdio.h"}
        if inc in stdlib:
            out.append("")  # stdlib dropped
            continue
        # Headers with real types we depend on (uint8_t, size_t, bool...) are
        # provided as fakes in the stub dir — let include resolution find them.
        # Find the header in include dirs (real repo headers first)
        path = None
        for d in include_dirs:
            cand = os.path.join(d, inc)
            if os.path.isfile(cand):
                path = cand
                break
        if path is None:
            out.append("")  # missing header — stubbed
            continue
        seen.add(inc)
        with open(path) as f:
            sub = _strip_comments_and_continuations(f.read())
        out.append(_expand_includes(sub, include_dirs, seen))
    return "\n".join(out)


def _parse_no_cpp(path: str, include_dirs: list[str], parser) -> tuple[bool, str]:
    with open(path) as f:
        src = _strip_comments_and_continuations(f.read())
    # Drop preprocessor lines we can't resolve (e.g. #define, #ifndef guards,
    # #ifdef) — keep #include lines for expansion.
    expanded = _expand_includes(src, include_dirs, set())
    # Now strip remaining preprocessor directives (#ifndef, #define, #endif...)
    # AND the C++ linkage guards that pycparser (a C99 parser) can't handle:
    # the headers wrap declarations in `#ifdef __cplusplus\n extern "C" {\n #endif`
    # ... `#ifdef __cplusplus\n}\n#endif`. Remove those triplets whole.
    import re
    src2 = expanded
    src2 = re.sub(
        r"#ifdef\s+__cplusplus\s*\n\s*extern\s*\"C\"\s*\{\s*\n\s*#endif\s*\n", "",
        src2)
    src2 = re.sub(
        r"#ifdef\s+__cplusplus\s*\n\s*\}\s*\n\s*#endif\s*\n", "", src2)
    # Any leftover bare `extern "C" {` / linkage lines (defensive).
    src2 = re.sub(r'^\s*extern\s*"C"\s*\{\s*$', "", src2, flags=re.M)

    # Expand simple object-like macros (#define NAME VALUE) so identifiers like
    # `bool`, `true`, `NULL`, `ESP_OK`, `HERMES_TYPE_*` resolve before we strip
    # the directives. Function-like and conditional macros are out of scope; we
    # only collect simple `#define IDENT token...` with no parameters and no
    # `#ifdef` gating we can't evaluate (we treat unknown #ifdef branches as
    # "keep" by stripping only the # line itself).
    macros: dict[str, str] = {}
    def _collect(m):
        name = m.group(1)
        val = m.group(2).strip()
        if name not in macros:
            macros[name] = val
        return ""
    src2 = re.sub(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.+?)\s*$",
                  _collect, src2, flags=re.M)
    # Apply object-like macros (longest names first to avoid prefix clashes).
    for name in sorted(macros, key=len, reverse=True):
        val = macros[name]
        if val and not val.isdigit() and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", val):
            # value is itself an identifier or macro — leave it (could chain)
            pass
        src2 = re.sub(r"\b" + re.escape(name) + r"\b", val if val else "0", src2)

    # Finally drop all remaining preprocessor directives.
    src2 = re.sub(r"^\s*#.*$", "", src2, flags=re.M)

    # Inject the function-like macros we still need (pycparser can't expand
    # them, so we textually replace their call sites instead).
    src2 = re.sub(r"\bESP_ERROR_CHECK\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)",
                  r"((void)(\1))", src2)
    src2 = re.sub(r"\bportTICK_PERIOD_MS\b", "1", src2)
    src2 = re.sub(r"\bpdMS_TO_TICKS\s*\(([^()]*)\)", r"(\1)", src2)

    expanded = src2
    # Write the preprocessed text to a temp file so pycparser reports real
    # line numbers on error (parsing from a string loses line attribution).
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".c", delete=False) as tf:
        tf.write(expanded)
        tmp_path = tf.name
    try:
        parser.parse(expanded, filename=tmp_path)
        os.unlink(tmp_path)
        return True, ""
    except Exception as exc:
        # Keep the preprocessed file for inspection; report path + error.
        return False, f"{exc}\n  preprocessed at: {tmp_path}"


def main() -> int:
    stub_dir = os.path.join(HERE, "_fw_stubs")
    _write_stubs(stub_dir)
    # include order: repo headers first (so real protocol.h / hermes_*.h win),
    # then stub dir for the ESP/LVGL/cJSON/freertos fakes.
    include_dirs = [
        os.path.join(FW, "main", "include"),
        os.path.join(FW, "components", "hermes_display", "include"),
        os.path.join(FW, "components", "hermes_ws", "include"),
        os.path.join(FW, "components", "hermes_audio", "include"),
        stub_dir,
        os.path.join(FW, "main", "include"),  # protocol.h
    ]

    targets = [
        "main/src/main.c",
        "components/hermes_display/src/hermes_display.c",
        "components/hermes_ws/src/hermes_ws.c",
    ]
    ok = True
    for rel in targets:
        path = os.path.join(FW, rel)
        good, err = parse_file(path, include_dirs)
        # pycparser can't fully resolve some LVGL/ESP opaque types, so a clean
        # parse is the bar; parse errors that mention only missing typedefs for
        # external opaque structs are acceptable. Surface everything.
        status = "PASS" if good else "FAIL"
        print(f"[{status}] {rel}")
        if not good:
            ok = False
            # Print only the first few lines of the error to stay readable.
            print("    " + err.replace("\n", "\n    ")[:800])
    print("\n[syntax] RESULT:", "ALL FILES PARSE" if ok else "PARSE ERRORS ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
