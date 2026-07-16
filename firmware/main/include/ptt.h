/**
 * @file ptt.h
 * @brief Push-to-talk button (side / BOOT button) via GPIO interrupt.
 *
 * The Tab5's BOOT button is on GPIO35 (active-low, with the board's pull-up).
 * A press starts capture (listen:start), a release ends it (listen:stop).
 * Events are debounced in the ISR and forwarded to the app state machine.
 *
 * The bridge expects listen:start to bracket the PCM frames, so we drive the
 * state machine from here rather than touching audio/WS directly.
 */
#pragma once
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Initialize the PTT button (GPIO35, falling/rising edge ISR). */
esp_err_t ptt_init(void);

#ifdef __cplusplus
}
#endif
