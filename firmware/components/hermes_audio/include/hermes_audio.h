/**
 * @file hermes_audio.h
 * @brief Audio capture + playback for the Hermes Desk Tab5.
 *
 * Two halves, one I2S codec stack:
 *   CAPTURE  — dual-mic (ES7210) -> 16k/16bit/mono PCM -> callback (stream up)
 *   PLAYBACK — PCM (from bridge TTS) -> ES8388 DAC -> speaker
 *
 * Both run at the bridge's native rate (16 kHz / 16-bit / mono) so there is
 * no on-device resampling. Capture and playback are mutually exclusive in
 * practice (push-to-talk half-duplex), but the API supports full-duplex for
 * a future wake-word / AEC path.
 */
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Called for each captured PCM frame (~20 ms / 640 B at 16k/16bit/mono).
 * `pcm` is valid only during the call; the handler (WS TX) must send or copy
 * it synchronously. Runs on the audio capture task — keep it fast.
 */
typedef void (*hermes_audio_capture_cb_t)(const uint8_t *pcm, size_t len);

/**
 * Initialize the codec stack (ES8388 + ES7210) via the BSP and the I2S
 * channels. Safe to call once at boot. Does not start capture/playback.
 */
esp_err_t hermes_audio_init(void);

/**
 * Start microphone capture. `cb` is invoked for each 20 ms PCM frame on the
 * audio task. Capture continues until hermes_audio_capture_stop().
 */
esp_err_t hermes_audio_capture_start(hermes_audio_capture_cb_t cb);

/** Stop microphone capture. */
esp_err_t hermes_audio_capture_stop(void);

/**
 * Enqueue PCM from the bridge TTS stream for speaker playback. `pcm` is a
 * chunk of raw 16k/16bit/mono PCM. Thread-safe; may be called from the WS
 * task as frames arrive. Playback is double-buffered through I2S DMA.
 *
 * If a barge-in abort is requested (hermes_audio_playback_abort), pending
 * buffered audio is dropped.
 */
esp_err_t hermes_audio_playback_write(const uint8_t *pcm, size_t len);

/** Signal that a TTS stream is starting (prepares the playback path). */
esp_err_t hermes_audio_playback_start(void);

/** Signal that a TTS stream ended (drains + idles the playback path). */
esp_err_t hermes_audio_playback_stop(void);

/** Barge-in: immediately stop playback and flush any buffered PCM. */
esp_err_t hermes_audio_playback_abort(void);

/** Deinitialize everything (mostly for testing / OTA). */
esp_err_t hermes_audio_deinit(void);

#ifdef __cplusplus
}
#endif
