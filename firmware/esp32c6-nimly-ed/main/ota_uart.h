// SPDX-License-Identifier: MIT
// OTA över UART: tar emot en avbildning i base64-chunkar från bryggan, skriver till
// nästa app-partition, verifierar SHA-256 och sätter boot-partitionen. Se docs/ota-ferry.md.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

// {"cmd":"ota_begin","size":N,"sha256":"<hex>","version":"x.y.z"}
esp_err_t ota_uart_begin(uint32_t size, const char *sha256_hex, const char *version);

// {"cmd":"ota_data","seq":N,"data":"<base64>"} - kvitteras med {"ev":"ota","phase":"data",...}
esp_err_t ota_uart_data(uint32_t seq, const char *b64, size_t b64_len);

// {"cmd":"ota_end"} - verifierar storlek + SHA-256, sätter boot-partition och startar om.
esp_err_t ota_uart_end(void);
