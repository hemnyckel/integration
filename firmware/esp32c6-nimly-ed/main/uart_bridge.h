// SPDX-License-Identifier: MIT
// UART-bro: C6 (enbart Zigbee) <-> bryggan (ESP32-C3/ESP32, WiFi/MQTT). Radbaserat JSON.
// Se docs/bridge-arkitektur.md (lösning B).
#pragma once

#include <stdbool.h>
#include <stdint.h>

// Startar UART1 (TX=GPIO6, RX=GPIO7, 115200) och en lästråd som tolkar rader.
void uart_bridge_start(void);

// Skicka till bryggan: händelse, låsstatus, batteri och PIN-händelser (för spegling).
void uart_bridge_send_event(uint16_t slot, uint8_t action, uint8_t source);
void uart_bridge_send_state(bool locked);
void uart_bridge_send_battery(uint8_t percentage);
void uart_bridge_send_pin(uint16_t slot, const char *pin);
void uart_bridge_send_pin_clear(uint16_t slot);

// Fingeravtrycks-begäran från appen (0x71) -> speglas till riktiga låset.
void uart_bridge_send_fp_enroll(uint16_t slot);
void uart_bridge_send_tag_scan(uint16_t arg);
void uart_bridge_send_tag_clear(uint16_t arg);

// Fingeravtrycks-radering från appen (0x72) -> speglas till riktiga låset.
void uart_bridge_send_fp_clear(uint16_t slot);

// Inställningar som appen skriver direkt till modulen (ZCL Write Attributes):
// ljudvolym (0x0024) och auto-relock (0x0023) -> speglas till riktiga låset.
void uart_bridge_send_volume(uint8_t volume);
void uart_bridge_send_autolock(uint32_t seconds);

// Hälsning vid uppstart: firmware-version + egen IEEE (integrationen visar versionen).
void uart_bridge_send_hello(const char *fw, const uint8_t ieee[8], int factory_new);

// OTA-status till bryggan (fas 3): {"ev":"ota","phase":..,"status":..,"seq":..,"message":..}.
void uart_bridge_send_ota(const char *phase, const char *status, int seq, const char *message);

// Nätstatus till bryggan: {"ev":"net","joined":true|false}.
void uart_bridge_send_net(bool joined);

// Antal mottagna rader sedan start (rollback-verifieringen kräver att bryggan hörs).
uint32_t uart_bridge_rx_count(void);

// Bekräftelse att en ny IEEE provisionerats (enheten startar om direkt efteråt).
void uart_bridge_send_ieee_set(const uint8_t ieee[8]);

// Registrera callback för inkommande JSON-rad från bryggan.
void uart_bridge_set_command_handler(void (*cb)(const char *json, int len));
