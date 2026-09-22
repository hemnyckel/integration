// SPDX-License-Identifier: MIT
// MQTT<->UART-bro: WiFi/MQTT (HA) <-> UART (ESP32-C6 Zigbee-emulator). Lösning B.
// Bygger för både ESP32-C3 (mini) och ESP32 (KinCony KC868-A6).
#include <stdlib.h>
#include <string.h>

#include "esp_log.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "mqtt_bridge.h"
#include "uart_link.h"
#include "ota.h"
#include "ota_c6.h"
#include "improv_link.h"

static const char *TAG = "uart_mqtt_bridge";

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
    ESP_LOGI(TAG, "HA -> C6: %s", json);
    uart_link_send(json);
}

// C6 (UART) -> bryggan -> HA (MQTT). Status speglas till det retainade state-topicen,
// allt annat (t.ex. operation events) vidaresänds som händelser.
static void on_uart_line(const char *line, int len)
{
    (void)len;
    ota_c6_note_uart_line(line); // OTA-kvitton från C6:an till ferjan
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
