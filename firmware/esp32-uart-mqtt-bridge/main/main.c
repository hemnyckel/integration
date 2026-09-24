// SPDX-License-Identifier: MIT
// MQTT<->UART-bro: WiFi/MQTT (HA) <-> UART (ESP32-C6 Zigbee-emulator). Lösning B.
// Bygger för både ESP32-C3 (mini) och ESP32 (KinCony KC868-A6).
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "mqtt_bridge.h"
#include "uart_link.h"
#include "ota.h"
#include "ota_c6.h"
#include "improv_link.h"

static const char *TAG = "uart_mqtt_bridge";

// Plockar ut en sträng "key":"value" ur en JSON-rad (samma enkla stil som
// resten av firmwaren; kommandona är korta och välformade).
static bool json_pick_string(const char *json, const char *key, char *out, size_t n)
{
    const char *p = strstr(json, key);
    if (p == NULL) {
        return false;
    }
    p = strchr(p, ':');
    if (p == NULL) {
        return false;
    }
    p++;
    while (*p == ' ') {
        p++;
    }
    if (*p != '"') {
        return false;
    }
    p++;
    const char *end = strchr(p, '"');
    if (end == NULL || end <= p) {
        return false;
    }
    size_t len = (size_t)(end - p);
    if (len >= n) {
        len = n - 1;
    }
    memcpy(out, p, len);
    out[len] = '\0';
    return true;
}

// HA -> bryggan (MQTT) -> C6 (UART). OTA-kommandon stannar hos bryggan.
static void on_mqtt_command(const char *json, int len)
{
    (void)len;
    if (strstr(json, "\"ota_c6\"") != NULL) {
        // OTA till C6:an: bryggan laddar ner och streamar över UART:en.
        ota_c6_request_from_json(json);
        return;
    }
    if (strstr(json, "\"ota\"") != NULL) {
        const char *p = strstr(json, "\"url\"");
        if (p != NULL) {
            p = strchr(p, ':');
        }
        if (p != NULL) {
            p++;
            while (*p == ' ') {
                p++;
            }
            if (*p == '"') {
                p++;
                const char *end = strchr(p, '"');
                if (end != NULL && end > p && (size_t)(end - p) < 300) {
                    char url[320];
                    size_t n = (size_t)(end - p);
                    memcpy(url, p, n);
                    url[n] = '\0';
                    ESP_LOGI(TAG, "OTA begärd: %s", url);
                    ota_request(url);
                    return;
                }
            }
        }
        ESP_LOGW(TAG, "OTA: kunde inte tolka url");
        return;
    }
    if (strstr(json, "\"set_prefix\"") != NULL) {
        // Prefixbyte (provisionering/migrering): spara i NVS och starta om så
        // att prenumerationer och last-will hamnar under det nya prefixet.
        char value[48];
        if (json_pick_string(json, "\"value\"", value, sizeof(value)) &&
            mqtt_bridge_set_prefix(value)) {
            ESP_LOGI(TAG, "Nytt MQTT-prefix sparat: %s – startar om", value);
            vTaskDelay(pdMS_TO_TICKS(300));
            esp_restart();
        } else {
            ESP_LOGW(TAG, "set_prefix: ogiltigt prefix");
        }
        return;
    }
    ESP_LOGI(TAG, "HA -> C6: %s", json);
    uart_link_send(json);
}

// C6 (UART) -> bryggan -> HA (MQTT). Status speglas till det retainade state-topicen,
// allt annat (t.ex. operation events) vidaresänds som händelser.
static void on_uart_line(const char *line, int len)
{
    (void)len;
    ota_c6_note_uart_line(line); // OTA-kvitton från C6:an till ferjan
    if (strstr(line, "\"hello\"") != NULL) {
        // C6:an hälsar (som svar på get_state): cacha fw + IEEE till infon.
        char fw[16];
        char ieee[24];
        if (json_pick_string(line, "\"fw\"", fw, sizeof(fw))) {
            if (!json_pick_string(line, "\"ieee\"", ieee, sizeof(ieee))) {
                ieee[0] = '\0';
            }
            mqtt_bridge_set_c6_info(fw, ieee[0] ? ieee : NULL);
        }
        // faller igenom: hello speglas även till HA (integrations-lagret använder den)
    }
    if (strstr(line, "\"state\"") != NULL) {
        // C6 skickar {"state":"locked"|"unlocked"}. "unlocked" innehåller "locked".
        mqtt_bridge_publish_state(strstr(line, "unlocked") == NULL);
        return;
    }
    if (strstr(line, "\"battery\"") != NULL) {
        const char *p = strchr(line, ':');
        if (p) {
            mqtt_bridge_publish_battery(atoi(p + 1));
        }
        return;
    }
    if (strstr(line, "\"pin_set\"") != NULL || strstr(line, "\"pin_clear\"") != NULL) {
        mqtt_bridge_publish_pin(line);
        return;
    }
    mqtt_bridge_publish_from_c6(line);
}

// Vid MQTT-anslutning: begär aktuell status från C6 (state-resync).
static void on_mqtt_connected(void)
{
    uart_link_send("{\"cmd\":\"get_state\"}");
}

void app_main(void)
{
    ESP_ERROR_CHECK(nvs_flash_init());
    ota_mark_valid();
#if defined(CONFIG_IDF_TARGET_ESP32C3)
    ESP_LOGI(TAG, "MQTT<->UART-bro startar (ESP32-C3)");
#elif defined(CONFIG_IDF_TARGET_ESP32)
    ESP_LOGI(TAG, "MQTT<->UART-bro startar (ESP32 / KC868-A6)");
#else
    ESP_LOGI(TAG, "MQTT<->UART-bro startar");
#endif

    uart_link_set_line_handler(on_uart_line);
    uart_link_start();

    mqtt_bridge_set_command_handler(on_mqtt_command);
    mqtt_bridge_set_connect_handler(on_mqtt_connected);
    mqtt_bridge_start();

    // Improv på USB-porten: WiFi-uppsättning i webbläsaren (ESP Web Tools) eller HA.
    improv_link_start();
}
