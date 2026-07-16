/**
 * @file hermes_audio.c
 * @brief Audio capture + playback for the Hermes Desk Tab5 (ESP32-P4).
 *
 * Hardware (via espressif/m5stack_tab5 BSP):
 *   - Speaker: ES8388 DAC (bsp_audio_codec_speaker_init)
 *   - Mics:    ES7210 ADC, dual-channel w/ AEC front-end
 *              (bsp_audio_codec_microphone_init)
 *   - I2S link shared between both codecs (MCLK/SCLK/LRCK).
 *
 * Both paths run at the bridge's native 16 kHz / 16-bit / mono so there is
 * no on-device resampling. Capture reads 20 ms (640 B) frames from I2S RX and
 * hands them to the WS TX callback. Playback accepts PCM from the WS RX path
 * and writes it to I2S TX, double-buffered through a ring buffer so the WS
 * task and the I2S DMA task stay decoupled.
 *
 * Codec API notes (esp_codec_dev ~1.5, the BSP-pinned version):
 *   esp_codec_dev_open(handle, FS, BITS, CHANNELS, volume) sets the format;
 *   esp_codec_dev_read/write() are blocking PCM I/O against the I2S DMA.
 *   blocking PCM I/O against the I2S DMA. The mic returns stereo frames from
 *   the ES7210's two channels; we down-mix to mono by averaging L+R so the
 *   bridge gets a single channel as the protocol requires.
 *
 * If a future BSP version changes these entry points, this file is the only
 * place that needs updating — the rest of the app talks hermes_audio_*.
 */
#include "hermes_audio.h"
#include "protocol.h"

#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/ringbuf.h"
#include "esp_log.h"
#include "esp_err.h"

/* BSP + codec stack. The exact headers live under managed_components/ after
 * the first build; these are the canonical include paths for esp-bsp. */
#include "bsp/m5stack_tab5.h"
#include "esp_codec_dev.h"
#include "esp_codec_dev_defaults.h"

static const char *TAG = "hermes_audio";

#define CAPTURE_TASK_STACK  4096
#define CAPTURE_PRIORITY    5
#define PLAYBACK_RB_BYTES   (HERMES_AUDIO_FRAME_BYTES * 32)  /* ~640 ms buffer */

typedef struct {
    bool                inited;
    esp_codec_dev_handle_t spk;     /* ES8388 DAC */
    esp_codec_dev_handle_t mic;     /* ES7210 ADC */
    /* capture */
    bool                capturing;
    TaskHandle_t        cap_task;
    hermes_audio_capture_cb_t cap_cb;
    /* playback */
    RingbufHandle_t     pb_ring;    /* PCM from bridge -> I2S TX */
    bool                playing;
    bool                abort_req;
    SemaphoreHandle_t   pb_mtx;
} audio_ctx_t;

static audio_ctx_t s_a;

/* ---------------------------------------------------------------------------
 * Capture task: read 20 ms stereo frames from ES7210, down-mix to mono,
 * invoke the WS TX callback with a 640 B (mono) frame.
 *
 * ES7210 is configured for 2 channels (dual-mic array). At 16k/16bit that is
 * 320 stereo samples = 1280 B per 20 ms. We average each L/R pair into one
 * 16-bit sample -> 320 mono samples = 640 B, exactly one protocol frame.
 * ------------------------------------------------------------------------- */
static void capture_task(void *arg)
{
    (void)arg;
    const size_t stereo_bytes = HERMES_AUDIO_FRAME_SAMPLES * 2 * 2; /* 320*2ch*2B */
    int16_t *stereo = heap_caps_malloc(stereo_bytes, MALLOC_CAP_DEFAULT);
    int16_t *mono   = heap_caps_malloc(HERMES_AUDIO_FRAME_BYTES, MALLOC_CAP_DEFAULT);

    if (!stereo || !mono) {
        ESP_LOGE(TAG, "capture: alloc failed");
        free(stereo); free(mono);
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "capture task running — 20ms/640B mono frames");
    while (s_a.capturing) {
        /* esp_codec_dev_read blocks until a DMA period is available. */
        int got = esp_codec_dev_read(s_a.mic, stereo, stereo_bytes);
        if (got != (int)stereo_bytes) {
            /* Short read happens on stop/abort; just loop. */
            if (s_a.capturing) {
                ESP_LOGW(TAG, "capture short read: %d/%u", got, (unsigned)stereo_bytes);
            }
            continue;
        }
        /* Down-mix stereo -> mono (average L+R). */
        for (int i = 0; i < HERMES_AUDIO_FRAME_SAMPLES; i++) {
            int32_t mix = (int32_t)stereo[2 * i] + (int32_t)stereo[2 * i + 1];
            mono[i] = (int16_t)(mix >> 1);
        }
        if (s_a.cap_cb) {
            s_a.cap_cb((const uint8_t *)mono, HERMES_AUDIO_FRAME_BYTES);
        }
    }

    free(stereo);
    free(mono);
    s_a.cap_task = NULL;
    vTaskDelete(NULL);
}

/* ---------------------------------------------------------------------------
 * Playback drain task: pulls PCM from the ring buffer (fed by the WS task)
 * and writes it to the ES8388 DAC. Runs only while `playing` is true.
 * ------------------------------------------------------------------------- */
static void playback_task(void *arg)
{
    (void)arg;
    uint8_t *buf = heap_caps_malloc(HERMES_AUDIO_FRAME_BYTES, MALLOC_CAP_DEFAULT);
    if (!buf) {
        ESP_LOGE(TAG, "playback: alloc failed");
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "playback task running");
    while (s_a.playing) {
        size_t item_size = 0;
        uint8_t *item = (uint8_t *)xRingbufferReceive(s_a.pb_ring, &item_size, pdMS_TO_TICKS(100));
        if (item) {
            if (!s_a.abort_req) {
                /* Write to codec; if abort was requested mid-stream, drop. */
                int wrote = esp_codec_dev_write(s_a.spk, item, item_size);
                if (wrote != (int)item_size) {
                    ESP_LOGW(TAG, "playback short write: %d/%u", wrote, (unsigned)item_size);
                }
            }
            vRingbufferReturnItem(s_a.pb_ring, item);
        } else if (s_a.abort_req) {
            /* No more buffered data and abort requested -> exit promptly. */
            break;
        }
        /* else: underrun (bridge slower than playback) — loop and wait. */
    }

    free(buf);
    ESP_LOGI(TAG, "playback task exit");
    vTaskDelete(NULL);
}

/* ---------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------- */
esp_err_t hermes_audio_init(void)
{
    if (s_a.inited) return ESP_OK;
    memset(&s_a, 0, sizeof(s_a));

    /* BSP brings up I2C, the IO expanders (which power the codecs + speaker
     * amp), and the shared I2S channel. We explicitly init I2C first (the
     * ES8388/ES7210 are I2C-controlled), then ask for the two codec devices.
     * These BSP calls are idempotent. */
    ESP_LOGI(TAG, "initializing codec stack via BSP");
    bsp_i2c_init();
    s_a.spk = bsp_audio_codec_speaker_init();
    s_a.mic = bsp_audio_codec_microphone_init();
    if (!s_a.spk || !s_a.mic) {
        ESP_LOGE(TAG, "BSP codec init failed (spk=%p mic=%p)", s_a.spk, s_a.mic);
        return ESP_FAIL;
    }

    /* Open both codecs at the bridge's native format. esp_codec_dev_open
     * signature (v1.x): (handle, sample_rate, bit_resolution, channel).
     * Volume/gain are set separately. The ES7210 reports 2 channels (dual
     * mic); we down-mix to mono in the capture task before sending. */
    esp_err_t e1 = esp_codec_dev_open(s_a.spk, HERMES_AUDIO_SAMPLE_RATE,
                                      HERMES_AUDIO_BITS, 2 /* stereo DAC path */);
    esp_err_t e2 = esp_codec_dev_open(s_a.mic, HERMES_AUDIO_SAMPLE_RATE,
                                      HERMES_AUDIO_BITS, 2 /* dual-mic ADC */);
    if (e1 != ESP_OK || e2 != ESP_OK) {
        ESP_LOGE(TAG, "codec open failed: spk=%s mic=%s",
                 esp_err_to_name(e1), esp_err_to_name(e2));
        return ESP_FAIL;
    }
    /* Comfortable listening volume; tune via menuconfig later. */
    esp_codec_dev_set_out_vol(s_a.spk, 70);

    s_a.pb_ring = xRingbufferCreate(PLAYBACK_RB_BYTES, RINGBUF_TYPE_BYTEBUF);
    s_a.pb_mtx  = xSemaphoreCreateMutex();
    if (!s_a.pb_ring || !s_a.pb_mtx) {
        ESP_LOGE(TAG, "playback ring/mutex alloc failed");
        return ESP_ERR_NO_MEM;
    }

    s_a.inited = true;
    ESP_LOGI(TAG, "audio ready — 16k/16bit, capture dual-mic->mono, playback stereo DAC");
    return ESP_OK;
}

esp_err_t hermes_audio_capture_start(hermes_audio_capture_cb_t cb)
{
    if (!s_a.inited || !cb) return ESP_ERR_INVALID_STATE;
    if (s_a.capturing) return ESP_OK;
    s_a.cap_cb = cb;
    s_a.capturing = true;
    BaseType_t ok = xTaskCreate(capture_task, "audio_cap", CAPTURE_TASK_STACK,
                                NULL, CAPTURE_PRIORITY, &s_a.cap_task);
    return (ok == pdPASS) ? ESP_OK : ESP_FAIL;
}

esp_err_t hermes_audio_capture_stop(void)
{
    if (!s_a.inited) return ESP_ERR_INVALID_STATE;
    s_a.capturing = false;
    /* The task will self-delete once the blocking read returns. */
    return ESP_OK;
}

esp_err_t hermes_audio_playback_start(void)
{
    if (!s_a.inited) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_a.pb_mtx, portMAX_DELAY);
    s_a.abort_req = false;
    s_a.playing = true;
    /* If a drain task isn't already running, start one. */
    static TaskHandle_t pb_task = NULL;
    if (!pb_task) {
        xTaskCreate(playback_task, "audio_pb", 4096, NULL, 5, &pb_task);
    }
    xSemaphoreGive(s_a.pb_mtx);
    return ESP_OK;
}

esp_err_t hermes_audio_playback_write(const uint8_t *pcm, size_t len)
{
    if (!s_a.inited || !pcm || !len) return ESP_ERR_INVALID_ARG;
    if (!s_a.playing) return ESP_ERR_INVALID_STATE;
    /* Copy into the ring buffer (the WS task owns `pcm` only for this call). */
    BaseType_t ok = xRingbufferSend(s_a.pb_ring, pcm, len, pdMS_TO_TICKS(50));
    return (ok == pdTRUE) ? ESP_OK : ESP_ERR_TIMEOUT;
}

esp_err_t hermes_audio_playback_stop(void)
{
    if (!s_a.inited) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(s_a.pb_mtx, portMAX_DELAY);
    s_a.playing = false;
    s_a.abort_req = false;
    /* Drain any leftover PCM so the next utterance starts clean. */
    UBaseType_t n;
    while (xRingbufferReceive(s_a.pb_ring, &n, 0)) {
        vRingbufferReturnItem(s_a.pb_ring, (void *)n);
    }
    xSemaphoreGive(s_a.pb_mtx);
    return ESP_OK;
}

esp_err_t hermes_audio_playback_abort(void)
{
    if (!s_a.inited) return ESP_ERR_INVALID_STATE;
    s_a.abort_req = true;
    /* Flush the ring buffer so playback halts immediately. */
    size_t item_size = 0;
    while (xRingbufferReceive(s_a.pb_ring, &item_size, 0)) {
        vRingbufferReturnItem(s_a.pb_ring, (void *)item_size);
    }
    return ESP_OK;
}

esp_err_t hermes_audio_deinit(void)
{
    if (!s_a.inited) return ESP_OK;
    hermes_audio_capture_stop();
    hermes_audio_playback_stop();
    esp_codec_dev_close(s_a.spk);
    esp_codec_dev_close(s_a.mic);
    if (s_a.pb_ring) {
        vRingbufferDelete(s_a.pb_ring);
        s_a.pb_ring = NULL;
    }
    if (s_a.pb_mtx) {
        vSemaphoreDelete(s_a.pb_mtx);
        s_a.pb_mtx = NULL;
    }
    s_a.inited = false;
    return ESP_OK;
}
