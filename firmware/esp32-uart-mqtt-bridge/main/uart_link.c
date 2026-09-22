// SPDX-License-Identifier: MIT
// UART-länk på bryggan (ESP32-C3/ESP32) mot ESP32-C6 (radbaserat JSON).
#include <string.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "esp_log.h"
#include "sdkconfig.h"

#include "uart_link.h"

static const char *TAG = "uart_link";

// UART-pinnar per mål. Båda målen använder UART1; pinnarna mappas via GPIO-matrisen.
//  - ESP32-C3 (mini): TX=GPIO4, RX=GPIO5
//  - ESP32 (KinCony KC868-A6): den dedikerade "Serial"-porten, TX=GPIO12, RX=GPIO13
//    (KinConys definition: TXD=12, RXD=13). GPIO12 som *utgång* undviker dessutom
//    strapping-risken (C6:ans TX får aldrig driva GPIO12 högt vid boot).
#if defined(CONFIG_IDF_TARGET_ESP32)
#  define UART_TX_GPIO 12
#  define UART_RX_GPIO 13
#elif defined(CONFIG_IDF_TARGET_ESP32C3)
#  define UART_TX_GPIO 4
#  define UART_RX_GPIO 5
#else
#  define UART_TX_GPIO 4
#  define UART_RX_GPIO 5
#endif

#define UART_PORT UART_NUM_1
#define UART_BAUD 115200
#define RX_BUF    1024

static void (*s_line_cb)(const char *line, int len) = NULL;

void uart_link_set_line_handler(void (*cb)(const char *line, int len))
{
    s_line_cb = cb;
}

void uart_link_send(const char *s)
{
    if (!s) {
        return;
    }
    uart_write_bytes(UART_PORT, s, strlen(s));
    uart_write_bytes(UART_PORT, "\n", 1);
}

static void uart_rx_task(void *arg)
{
    (void)arg;
    uint8_t data[128];
    char line[256];
    int len = 0;

    for (;;) {
        int n = uart_read_bytes(UART_PORT, data, sizeof(data), pdMS_TO_TICKS(100));
        for (int i = 0; i < n; i++) {
            char c = (char)data[i];
            if (c == '\n' || c == '\r') {
                if (len > 0) {
                    line[len] = '\0';
                    ESP_LOGI(TAG, "C6 -> bryggan: %s", line);
                    if (s_line_cb) {
                        s_line_cb(line, len);
                    }
                    len = 0;
                }
            } else if (len < (int)sizeof(line) - 1) {
                line[len++] = c;
            } else {
                len = 0;
            }
        }
    }
}

void uart_link_start(void)
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
    ESP_LOGI(TAG, "UART-länk startad: TX=GPIO%d RX=GPIO%d %d baud", UART_TX_GPIO, UART_RX_GPIO, UART_BAUD);
    xTaskCreate(uart_rx_task, "uart_rx", 4096, NULL, 5, NULL);
}
