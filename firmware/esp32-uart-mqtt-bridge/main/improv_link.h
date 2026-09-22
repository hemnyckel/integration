// SPDX-License-Identifier: MIT
// Improv Wi-Fi över den seriella porten (USB) – WiFi-uppsättning i webbläsaren
// (ESP Web Tools) och i Home Assistant. Bygger på Espressifs Improv-SDK.
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Startar Improv (seriell kanal på USB-porten). Idempotent.
void improv_link_start(void);

// Sätter aktuell Improv-state (improv::State) och skickar den till värden.
void improv_link_set_state(uint8_t state);

#ifdef __cplusplus
}
#endif
