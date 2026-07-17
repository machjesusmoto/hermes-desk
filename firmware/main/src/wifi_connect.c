/**
 * @file wifi_connect.c
 * @brief WiFi bring-up for the ESP32-P4 + ESP32-C6 (esp_hosted) Tab5.
 *
 * INIT ORDER (load-bearing — esp_hosted must precede esp_wifi):
 *   1. nvs_flash_init            (config + WiFi creds)
 *   2. esp_hosted_init / connect_to_slave   (SDIO link to the C6)
 *   3. esp_netif_init + event loop
 *   4. esp_wifi_init + start + connect
 *
 * Get this order wrong and you see `sdmmc_init_ocr: send_op_cond returned
 * 0x107` and a zero MAC — the classic P4+C6 failure mode (esp-hosted #127).
 * The SDIO GPIOs + reset polarity live in sdkconfig.defaults.
 */
#include "wifi_connect.h"
#include "hermes_display.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/event_groups.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

/* esp_hosted transport to the C6 co-processor. */
#include "esp_hosted.h"

static const char *TAG = "wifi";

#define WIFI_CONN_BIT   BIT0
#define WIFI_FAIL_BIT   BIT1
static EventGroupHandle_t s_wifi_events;
static bool s_connected = false;

/* Credentials: Kconfig defaults (set via menuconfig) — see Kconfig.projbuild. */
#ifndef CONFIG_WIFI_SSID
#define CONFIG_WIFI_SSID  ""
#endif
#ifndef CONFIG_WIFI_PASS
#define CONFIG_WIFI_PASS  ""
#endif

static void on_ip(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)base; (void)id;
    ip_event_got_ip_t *evt = (ip_event_got_ip_t *)data;
    ESP_LOGI(TAG, "got IP: " IPSTR, IP2STR(&evt->ip_info.ip));
    s_connected = true;
    if (s_wifi_events) xEventGroupSetBits(s_wifi_events, WIFI_CONN_BIT);
    char buf[48];
    snprintf(buf, sizeof(buf), "Connected: " IPSTR, IP2STR(&evt->ip_info.ip));
    hermes_display_show_boot(buf);
}

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg; (void)data;
    if (base != WIFI_EVENT) return;
    switch (id) {
    case WIFI_EVENT_STA_START:
        ESP_LOGI(TAG, "STA started, connecting\u2026");
        esp_wifi_connect();
        break;
    case WIFI_EVENT_STA_DISCONNECTED:
        ESP_LOGW(TAG, "disconnected, retrying");
        s_connected = false;
        esp_wifi_connect();   /* esp_wifi auto-retries internally; this re-arms */
        break;
    default:
        break;
    }
}

esp_err_t wifi_connect_start(void)
{
    /* 1. NVS — the WiFi stack and esp_hosted both need it. */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    hermes_display_show_boot("Starting radio (C6)\u2026");

    /* 2. esp_hosted: bring up the SDIO link to the C6 BEFORE touching WiFi.
     *    The C6 is powered through a PI4IOE5V6408 pin that the BSP's
     *    esp_hosted init enables (WLAN_PWR_EN on IOX 0x44 P0). */
    ESP_LOGI(TAG, "bringing up esp_hosted (C6 over SDIO)\u2026");
    esp_err_t err = esp_hosted_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_hosted_init failed: %s", esp_err_to_name(err));
        hermes_display_show_boot("C6 link failed");
        return err;
    }
    err = esp_hosted_connect_to_slave();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_hosted_connect_to_slave failed: %s", esp_err_to_name(err));
        hermes_display_show_boot("C6 slave not responding");
        return err;
    }
    ESP_LOGI(TAG, "C6 link up");

    /* 3. Network stack + event loop. */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    s_wifi_events = xEventGroupCreate();

    /* 4. WiFi — now safe to call. */
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t inst_any_id;
    esp_event_handler_instance_t inst_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                        on_wifi_event, NULL, &inst_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                        on_ip, NULL, &inst_got_ip));

    wifi_config_t wifi_cfg = {
        .sta = {
            .threshold.authmode = WIFI_AUTH_WPA2_PSK,
        },
    };
    /* Copy SSID/pass into the sta config (NUL-padded fixed fields). */
    strncpy((char *)wifi_cfg.sta.ssid, CONFIG_WIFI_SSID, sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, CONFIG_WIFI_PASS, sizeof(wifi_cfg.sta.password) - 1);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "wifi STA started — waiting for IP (ssid=%s)", CONFIG_WIFI_SSID);
    hermes_display_show_boot("WiFi: connecting\u2026");

    /* Block until IP (or fail). Give it generous time for the C6 link. */
    EventBits_t bits = xEventGroupWaitBits(s_wifi_events,
                                           WIFI_CONN_BIT | WIFI_FAIL_BIT,
                                           pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(30000));

    if (bits & WIFI_CONN_BIT) {
        ESP_LOGI(TAG, "connected");
        return ESP_OK;
    }
    ESP_LOGE(TAG, "wifi connect timed out");
    hermes_display_show_boot("WiFi: connect timeout");
    return ESP_ERR_TIMEOUT;
}

bool wifi_is_connected(void)
{
    return s_connected;
}
