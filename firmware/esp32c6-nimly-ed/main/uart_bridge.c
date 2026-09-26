// SPDX-License-Identifier: MIT
// UART-bro på C6:an (enbart Zigbee) <-> bryggan (ESP32-C3/ESP32, WiFi/MQTT). Radbaserat JSON.
#include <string.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_log.h"

#include "uart_bridge.h"

static const char *TAG = "uart_bridge";

#define UART_PORT    UART_NUM_1
#define UART_TX_GPIO 6   // C6 TX -> bryggan RX
#define UART_RX_GPIO 7   // C6 RX <- bryggan TX
#define UART_BAUD    115200
#define RX_BUF       4096

static void (*s_cmd_cb)(const char *json, int len) = NULL;
static char s_line[8192]; // OTA-chunkar är ~1,4 kB per rad; statisk så den inte ligger på stacken
static volatile uint32_t s_rx_count = 0;

uint32_t uart_bridge_rx_count(void)
{
    return s_rx_count;
}

void uart_bridge_set_command_handler(void (*cb)(const char *json, int len))
{
    s_cmd_cb = cb;
}

static void uart_send_line(const char *s)
{
    uart_write_bytes(UART_PORT, s, strlen(s));
    uart_write_bytes(UART_PORT, "\n", 1);
}

void uart_bridge_send_event(uint16_t slot, uint8_t action, uint8_t source)
{
    char buf[96];
    snprintf(buf, sizeof(buf), "{\"ev\":\"action\",\"slot\":%u,\"action\":%u,\"source\":%u}",
             slot, action, source);
    uart_send_line(buf);
}

void uart_bridge_send_state(bool locked)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"state\":\"%s\"}", locked ? "locked" : "unlocked");
    uart_send_line(buf);
}

void uart_bridge_send_battery(uint8_t percentage)
{
    char buf[48];
    snprintf(buf, sizeof(buf), "{\"battery\":%u}", percentage);
    uart_send_line(buf);
}

void uart_bridge_send_pin(uint16_t slot, const char *pin)
{
    char buf[96];
    // Koden skickas vidare (behövs för spegling mot riktiga låset) men loggas aldrig.
    snprintf(buf, sizeof(buf), "{\"ev\":\"pin_set\",\"slot\":%u,\"code\":\"%s\"}",
             slot, pin ? pin : "");
    uart_send_line(buf);
}

void uart_bridge_send_pin_clear(uint16_t slot)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"ev\":\"pin_clear\",\"slot\":%u}", slot);
    uart_send_line(buf);
}

void uart_bridge_send_fp_enroll(uint16_t slot)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"ev\":\"fp_enroll\",\"slot\":%u}", slot);
    uart_send_line(buf);
}

void uart_bridge_send_tag_scan(uint16_t arg)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"ev\":\"tag_scan\",\"arg\":%u}", arg);
    uart_send_line(buf);
}

// Tagg-/credential-radering från appen (0x18) -> speglas till riktiga låset.
void uart_bridge_send_tag_clear(uint16_t arg)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"ev\":\"tag_clear\",\"arg\":%u}", arg);
    uart_send_line(buf);
}

void uart_bridge_send_fp_clear(uint16_t slot)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"ev\":\"fp_clear\",\"slot\":%u}", slot);
    uart_send_line(buf);
}

void uart_bridge_send_volume(uint8_t volume)
{
    char buf[48];
    snprintf(buf, sizeof(buf), "{\"ev\":\"volume\",\"value\":%u}", volume);
    uart_send_line(buf);
}

void uart_bridge_send_autolock(uint32_t seconds)
{
    char buf[64];
    snprintf(buf, sizeof(buf), "{\"ev\":\"autolock\",\"value\":%lu}", (unsigned long)seconds);
    uart_send_line(buf);
}

void uart_bridge_send_hello(const char *fw, const uint8_t ieee[8], int factory_new)
{
    char buf[200];
    snprintf(buf, sizeof(buf),
             "{\"ev\":\"hello\",\"fw\":\"%s\",\"ieee\":\"%02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x\",\"factory_new\":%d}",
             fw ? fw : "?", ieee[7], ieee[6], ieee[5], ieee[4],
             ieee[3], ieee[2], ieee[1], ieee[0], factory_new ? 1 : 0);
    uart_send_line(buf);
}

void uart_bridge_send_ieee_set(const uint8_t ieee[8])
{
    char buf[96];
    snprintf(buf, sizeof(buf),
             "{\"ev\":\"ieee_set\",\"ieee\":\"%02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x\"}",
             ieee[7], ieee[6], ieee[5], ieee[4], ieee[3], ieee[2], ieee[1], ieee[0]);
    uart_send_line(buf);
}

void uart_bridge_send_ota(const char *phase, const char *status, int seq, const char *message)
{
    char buf[192];
    snprintf(buf, sizeof(buf),
             "{\"ev\":\"ota\",\"phase\":\"%s\",\"status\":\"%s\",\"seq\":%d,\"message\":\"%s\"}",
             phase ? phase : "?", status ? status : "ok", seq, message ? message : "");
    uart_send_line(buf);
}

void uart_bridge_send_net(bool joined)
{
    char buf[48];
    snprintf(buf, sizeof(buf), "{\"ev\":\"net\",\"joined\":%s}", joined ? "true" : "false");
    uart_send_line(buf);
}

static void uart_rx_task(void *arg)
{
    (void)arg;
    uint8_t data[256];
    int len = 0;

    for (;;) {
        int n = uart_read_bytes(UART_PORT, data, sizeof(data), pdMS_TO_TICKS(100));
        for (int i = 0; i < n; i++) {
            char c = (char)data[i];
            if (c == '\n' || c == '\r') {
                if (len > 0) {
                    s_line[len] = '\0';
                    if (strncmp(s_line, "{\"cmd\":\"ota_data\"", 17) == 0) {
                        // OTA-chunkar loggas utan innehåll (storlek räcker för felsökning).
                        ESP_LOGI(TAG, "UART in: ota_data (%d B)", len);
                    } else {
                        ESP_LOGI(TAG, "UART in: %s", s_line);
                    }
                    s_rx_count++;
                    if (s_cmd_cb) {
                        s_cmd_cb(s_line, len);
                    }
                    len = 0;
                }
            } else if (len < (int)sizeof(s_line) - 1) {
                s_line[len++] = c;
            } else {
                ESP_LOGW(TAG, "RAD-OVERFLOW (%d B) – raden kastas", len);
                len = 0; // overflow -> kasta raden
            }
        }
    }
}

void uart_bridge_start(void)
{
    uart_config_t cfg = {
        .baud_rate = UART_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    ESP_ERROR_CHECK(uart_driver_install(UART_PORT, RX_BUF * 2, 0, 0, NULL, 0));
    ESP_ERROR_CHECK(uart_param_config(UART_PORT, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_PORT, UART_TX_GPIO, UART_RX_GPIO,
                                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_LOGI(TAG, "UART-bro startad: TX=GPIO%d RX=GPIO%d %d baud", UART_TX_GPIO, UART_RX_GPIO, UART_BAUD);
    xTaskCreate(uart_rx_task, "uart_rx", 8192, NULL, 5, NULL);
}
