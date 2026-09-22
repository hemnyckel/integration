// SPDX-License-Identifier: MIT
// UART-länk ESP32 <-> ESP32-C6 (radbaserat JSON).
// Pinnar per mål: ESP32-C3 TX=GPIO4/RX=GPIO5; ESP32 (KC868-A6) TX=GPIO13/RX=GPIO12.
#pragma once

#include <stdbool.h>
#include <stdint.h>

// Startar UART1 (TX=GPIO4, RX=GPIO5, 115200) och en lästråd.
void uart_link_start(void);

// Skicka en rad (utan ny rad) till C6:an.
void uart_link_send(const char *s);

// Registrera callback för inkommande rad från C6:an.
void uart_link_set_line_handler(void (*cb)(const char *line, int len));
