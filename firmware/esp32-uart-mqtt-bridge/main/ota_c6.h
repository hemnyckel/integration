// SPDX-License-Identifier: MIT
// OTA-ferja till C6:an: laddar ner en avbildning och streamar den över UART:en.
// Se docs/ota-ferry.md.
#pragma once

// Tolkar {"cmd":"ota_c6","url":..,"sha256":..,"version":..} och startar ferjan.
void ota_c6_request_from_json(const char *json);

// Matar in C6-rader så ferjan ser {"ev":"ota","phase":..,"status":..}-kvitton.
// Anropas från main.c:s UART-radhanterare innan raden vidaresänds.
void ota_c6_note_uart_line(const char *line);
