# Hermes Desk — M5Stack Tab5 Firmware
#
# ESP32-P4 firmware for the M5Stack Tab5 voice + display terminal.
# Target: esp32p4. Requires ESP-IDF v5.4+ (developed against 5.5.x).
#
# Managed components (resolved from idf_component.yml):
#   - espressif/m5stack_tab5        (BSP: display, touch, audio codecs, IO expanders)
#   - espressif/esp_websocket_client (WebSocket client)
#   - espressif/esp_hosted          (ESP32-C6 co-processor SDIO transport)
#   - espressif/esp_wifi_remote     (WiFi stack routed over esp_hosted)
#   - lvgl/lvgl                     (pulled transitively by esp_lvgl_port)
#
# Build:  idf.py set-target esp32p4 && idf.py build
# Flash:  idf.py -p <PORT> flash monitor

cmake_minimum_required(VERSION 3.16)

# Top-level project. The IDF build system injects the toolchain and the
# managed_components/ directory; we only need to declare our own sources.
include($ENV{IDF_PATH}/tools/cmake/project.cmake OPTIONAL)

project(hermes_desk_firmware)
