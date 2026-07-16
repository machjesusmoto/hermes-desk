/**
 * @file wifi_connect.h
 * @brief WiFi connection for the ESP32-P4 + ESP32-C6 (esp_hosted) Tab5.
 *
 * The P4 has no radio — WiFi rides the C6 co-processor over SDIO. The init
 * ORDER is load-bearing (see esp-hosted docs): esp_hosted must be up and the
 * SDIO link established BEFORE the WiFi stack is touched, or you get
 * misleading NVS errors / silent hangs. This module encodes that order.
 *
 * Credentials: NVS first (set at runtime by a provisioning flow — TODO in
 * MOT-81), then Kconfig menuconfig defaults (CONFIG_WIFI_SSID/PASS), then a
 * hard-coded fallback for development.
 */
#pragma once
#include "esp_err.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Bring up the full network stack and connect to WiFi. Blocks until either
 * an IP is acquired or the timeout elapses. Updates the display boot line
 * with progress. Returns ESP_OK once IP_EVENT_IP_GAINED has fired.
 */
esp_err_t wifi_connect_start(void);

/** True if we currently have an IP (STA connected). */
bool wifi_is_connected(void);

#ifdef __cplusplus
}
#endif
