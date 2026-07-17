/**
 * @file wifi_connect.c
 * @brief WiFi bring-up for the ESP32-P4 + ESP32-C6 (esp_hosted) Tab5.
 *
 * COLD BOOT SEQUENCE (load-bearing order):
 *   1. nvs_flash_init            (config + WiFi creds)
 *   2. esp_netif_init + event loop  (tcpip thread must exist before esp_hosted)
 *   3. bsp_feature_enable(BSP_FEATURE_WIFI, true)  (powers on the C6 via IO expander)
 *   4. vTaskDelay(500ms)         (let the C6 boot its firmware)
 *   5. esp_hosted_init           (SDIO transport + protocol handshake)
 *   6. esp_wifi_init + start + connect
 *
 * The C6 is powered through a PI4IOE5V6408 IO-expander on the second I2C bus.
 * The BSP's display init initializes the first IO expander (for backlight).
 * bsp_feature_enable(BSP_FEATURE_WIFI, true) initializes the second IO expander
 * and enables the C6 power pin (BSP_WIFI_EN = IO_EXPANDER_PIN_NUM_0).
 *
 * Auto-init is DISABLED in sdkconfig.defaults because it runs before app_main
 * when the C6 has no power, corrupting the transport state.
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

/* BSP for C6 power control via IO expander. */
#include "bsp/m5stack_tab5.h"

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
        ESP_LOGI(TAG, "STA started, connecting…");
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

    /* 2. Network stack + event loop — MUST come before esp_hosted_init()
     *    because esp_hosted_init() triggers LWIP traffic on the SDIO link.
     *    Without esp_netif_init() the tcpip thread doesn't exist yet, causing:
     *    assert failed: tcpip_send_msg_wait_sem. */
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    hermes_display_show_boot("Powering on C6…");

    /* 3. Power on the C6 co-processor via the BSP IO expander.
     *    The C6's 3V3 is controlled by the second PI4IOE5V6408 IO expander,
     *    pin 0 (BSP_WIFI_EN). bsp_feature_enable initializes the expander
     *    and sets the pin high, powering the C6. */
    ESP_LOGI(TAG, "powering on C6 via IO expander…");
    esp_err_t pw = bsp_feature_enable(BSP_FEATURE_WIFI, true);
    if (pw != ESP_OK) {
        ESP_LOGW(TAG, "bsp_feature_enable(WIFI) returned %s — continuing", esp_err_to_name(pw));
    }

    /* 4. Give the C6 time to boot its firmware after power-on. */
    vTaskDelay(pdMS_TO_TICKS(500));

    /* 5. esp_hosted: init the SDIO transport and complete the protocol
     *    handshake. Auto-init is disabled, so this is the first time the
     *    transport touches the C6. The C6 is now powered and ready. */
    ESP_LOGI(TAG, "initializing esp_hosted transport…");
    hermes_display_show_boot("Connecting to C6…");

    esp_err_t err = ESP_FAIL;
    for (int attempt = 1; attempt <= 5; attempt++) {
        /* esp_hosted_init() sets up the SDIO transport and starts the
         * protocol handshake. esp_hosted_connect_to_slave() waits for
         * the INIT event from the C6 firmware. */
        if (attempt == 1) {
            err = esp_hosted_init();
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "esp_hosted_init failed: %s", esp_err_to_name(err));
                /* Retry: power-cycle the C6 */
                bsp_feature_enable(BSP_FEATURE_WIFI, false);
                vTaskDelay(pdMS_TO_TICKS(200));
                bsp_feature_enable(BSP_FEATURE_WIFI, true);
                vTaskDelay(pdMS_TO_TICKS(500));
                err = esp_hosted_init();
            }
        }

        err = esp_hosted_connect_to_slave();
        if (err == ESP_OK) break;
        ESP_LOGW(TAG, "esp_hosted_connect_to_slave attempt %d/5 failed: %s",
                 attempt, esp_err_to_name(err));
        if (attempt < 5) {
            char buf[48];
            snprintf(buf, sizeof(buf), "C6 retry %d/5…", attempt);
            hermes_display_show_boot(buf);
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_hosted_connect_to_slave failed after 5 attempts: %s", esp_err_to_name(err));
        hermes_display_show_boot("C6 slave not responding");
        return err;
    }
    ESP_LOGI(TAG, "C6 link up");

    /* 6. WiFi netif + event handlers. */
    esp_netif_create_default_wifi_sta();

    s_wifi_events = xEventGroupCreate();

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
    hermes_display_show_boot("WiFi: connecting…");

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
