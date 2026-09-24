// SPDX-License-Identifier: MIT
// OTA för bryggan: HTTP -> nästa OTA-partition -> omstart. Se docs/ota.md.
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_ota_ops.h"
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"

#include "mqtt_bridge.h"
#include "ota.h"

static const char *TAG = "ota";

static void publish(const char *state, int pct, const char *msg)
{
    char buf[160];
    if (msg) {
        snprintf(buf, sizeof(buf), "{\"state\":\"%s\",\"msg\":\"%s\"}", state, msg);
    } else {
        snprintf(buf, sizeof(buf), "{\"state\":\"%s\",\"pct\":%d}", state, pct);
    }
    mqtt_bridge_publish_ota(buf);
}

static void ota_task(void *arg)
{
    char *url = (char *)arg;
    ESP_LOGI(TAG, "OTA från %s", url);
    publish("downloading", 0, NULL);

    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (part == NULL) {
        publish("error", 0, "ingen OTA-partition");
        goto done;
    }
    ESP_LOGI(TAG, "Skriver till %s (0x%lx)", part->label, (unsigned long)part->address);

    esp_http_client_config_t cfg = {
        .url = url,
        .timeout_ms = 20000,
        .keep_alive_enable = true,
        .buffer_size = 4096,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (client == NULL) {
        publish("error", 0, "http-init");
        goto done;
    }

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        publish("error", 0, "kunde inte öppna url");
        esp_http_client_cleanup(client);
        goto done;
    }
    int content_len = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if (status != 200) {
        ESP_LOGW(TAG, "HTTP-status %d", status);
        publish("error", 0, "http-status");
        esp_http_client_cleanup(client);
        goto done;
    }
    ESP_LOGI(TAG, "Nedladdning: %d byte", content_len);

    esp_ota_handle_t handle = 0;
    err = esp_ota_begin(part, content_len > 0 ? (size_t)content_len : OTA_SIZE_UNKNOWN, &handle);
    if (err != ESP_OK) {
        publish("error", 0, "ota-begin");
        esp_http_client_cleanup(client);
        goto done;
    }

    char buf[1024];
    int total = 0;
    int last_bucket = -1;
    int empty_reads = 0;
    while (true) {
        int r = esp_http_client_read(client, buf, sizeof(buf));
        if (r < 0) {
            publish("error", 0, "lasfel");
            esp_ota_abort(handle);
            esp_http_client_cleanup(client);
            goto done;
        }
        if (r == 0) {
            // En tom läsning med kvarvarande body kan vara transient; ge
            // läsaren några chanser innan nedladdningen klassas som kort.
            if (content_len > 0 && total < content_len
                && !esp_http_client_is_complete_data_received(client)
                && empty_reads++ < 3) {
                vTaskDelay(pdMS_TO_TICKS(100));
                continue;
            }
            break; // klart
        }
        if (esp_ota_write(handle, buf, (size_t)r) != ESP_OK) {
            publish("error", 0, "skrivfel");
            esp_ota_abort(handle);
            esp_http_client_cleanup(client);
            goto done;
        }
        total += r;
        if (content_len > 0) {
            int pct = (total * 100) / content_len;
            if (pct / 10 != last_bucket) {
                last_bucket = pct / 10;
                publish("downloading", pct, NULL);
            }
        }
    }
    bool complete = esp_http_client_is_complete_data_received(client);
    esp_http_client_cleanup(client);

    if (!complete || (content_len > 0 && total != content_len)) {
        // En kort eller avbruten body får aldrig bli en halvskriven OTA-slot.
        char msg[80];
        snprintf(msg, sizeof(msg), "kort nedladdning %d/%d", total, content_len);
        publish("error", 0, msg);
        esp_ota_abort(handle);
        goto done;
    }

    err = esp_ota_end(handle);
    if (err != ESP_OK) {
        char msg[80];
        snprintf(msg, sizeof(msg), "ota-end fel 0x%x (%d byte)", (unsigned)err, total);
        publish("error", 0, msg);
        goto done;
    }
    err = esp_ota_set_boot_partition(part);
    if (err != ESP_OK) {
        publish("error", 0, "set-boot");
        goto done;
    }

    ESP_LOGW(TAG, "OTA klar (%d byte) – startar om", total);
    publish("done", 100, NULL);
    vTaskDelay(pdMS_TO_TICKS(600));
    esp_restart();

done:
    free(url);
    vTaskDelete(NULL);
}

void ota_request(const char *url)
{
    if (url == NULL || *url == '\0') {
        return;
    }
    char *copy = strdup(url);
    if (copy == NULL) {
        return;
    }
    if (xTaskCreate(ota_task, "ota", 8192, copy, 5, NULL) != pdPASS) {
        ESP_LOGE(TAG, "kunde inte starta OTA-uppgift");
        free(copy);
    }
}

void ota_mark_valid(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (esp_ota_get_state_partition(running, &state) == ESP_OK
        && state == ESP_OTA_IMG_PENDING_VERIFY) {
        if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
            ESP_LOGI(TAG, "Ny firmware bekräftad (rollback avväpnad)");
        }
    }
}
