/**
 * @file ptt.c
 * @brief Push-to-talk via the Tab5 side/BOOT button (GPIO35).
 *
 * The BOOT button is active-low (board pull-up). Falling edge = press,
 * rising edge = release. We debounce in the ISR with a simple time window
 * and forward press/release events to the app state machine, which owns the
 * listen:start/stop + audio capture orchestration.
 */
#include "ptt.h"
#include "app_state.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_log.h"

static const char *TAG = "ptt";

#define PTT_GPIO        GPIO_NUM_35   /* Tab5 BOOT button */
#define DEBOUNCE_MS     60
#define PTT_QUEUE_LEN   8

static QueueHandle_t s_evt_queue;

/* PTT events delivered to the state machine. */
typedef enum { PTT_PRESSED, PTT_RELEASED } ptt_kind_t;

static void IRAM_ATTR ptt_isr_handler(void *arg)
{
    (void)arg;
    static uint32_t last_tick = 0;
    uint32_t now = xTaskGetTickCountFromISR();
    if ((now - last_tick) < pdMS_TO_TICKS(DEBOUNCE_MS)) return;   /* debounce */
    last_tick = now;

    int level = gpio_get_level(PTT_GPIO);
    ptt_kind_t k = (level == 0) ? PTT_PRESSED : PTT_RELEASED;
    BaseType_t hp = pdFALSE;
    xQueueSendFromISR(s_evt_queue, &k, &hp);
    if (hp) portYIELD_FROM_ISR();
}

/* Task that drains the ISR queue and calls into the FSM. Kept off the ISR
 * so the FSM mutex + display updates are safe. */
static void ptt_task(void *arg)
{
    (void)arg;
    ptt_kind_t k;
    for (;;) {
        if (xQueueReceive(s_evt_queue, &k, portMAX_DELAY) == pdTRUE) {
            if (k == PTT_PRESSED) {
                app_state_handle_event(APP_EVT_PTT_PRESS);
            } else {
                app_state_handle_event(APP_EVT_PTT_RELEASE);
            }
        }
    }
}

esp_err_t ptt_init(void)
{
    s_evt_queue = xQueueCreate(PTT_QUEUE_LEN, sizeof(ptt_kind_t));
    if (!s_evt_queue) return ESP_ERR_NO_MEM;

    gpio_config_t io = {
        .pin_bit_mask = BIT64(PTT_GPIO),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    ESP_ERROR_CHECK(gpio_config(&io));

    /* Install the GPIO ISR service if not already installed. */
    esp_err_t r = gpio_install_isr_service(ESP_INTR_FLAG_LEVEL1);
    if (r != ESP_OK && r != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "isr service install: %s", esp_err_to_name(r));
        return r;
    }
    ESP_ERROR_CHECK(gpio_isr_handler_add(PTT_GPIO, ptt_isr_handler, NULL));

    xTaskCreate(ptt_task, "ptt", 3072, NULL, 6, NULL);
    ESP_LOGI(TAG, "PTT ready on GPIO%d (BOOT button)", PTT_GPIO);
    return ESP_OK;
}
