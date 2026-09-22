// SPDX-License-Identifier: MIT
// OTA via UART-ferja (C6:an uppdateras genom bryggan). Bryggan laddar ner avbildningen
// över HTTP och skickar den base64-kodad i 1 KiB-chunkar; C6:an kvitterar varje chunk,
// verifierar SHA-256 och sätter boot-partitionen. Framsteg publiceras på nimly/proxy/ota.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "mbedtls/base64.h"

#include "mqtt_bridge.h"
#include "ota_c6.h"
#include "uart_link.h"

static const char *TAG = "ota_c6";

#define CHUNK_RAW       2048
#define ACK_TIMEOUT_MS  30000
#define END_TIMEOUT_MS  60000

struct ferry_args {
    char url[300];
    char sha[65];
    char version[32];
};

static SemaphoreHandle_t s_ack = NULL;
static volatile bool s_busy = false;
static char s_phase[16];
static volatile int s_seq = -1;
static volatile bool s_ok = false;

static void publish(const char *state, int pct, const char *msg)
{
    char buf[224];
    if (msg) {
        snprintf(buf, sizeof(buf),
                 "{\"target\":\"c6\",\"state\":\"%s\",\"pct\":%d,\"msg\":\"%s\"}", state, pct, msg);
    } else {
        snprintf(buf, sizeof(buf), "{\"target\":\"c6\",\"state\":\"%s\",\"pct\":%d}", state, pct);
    }
    mqtt_bridge_publish_ota(buf);
}

static bool json_field(const char *json, const char *key, char *out, size_t out_len)
{
    const char *p = strstr(json, key);
    if (p == NULL) {
        return false;
    }
    p += strlen(key);
    while (*p == ' ' || *p == ':') {
        p++;
    }
    if (*p != '"') {
        return false;
    }
    p++;
    const char *end = strchr(p, '"');
    if (end == NULL || end == p) {
        return false;
    }
    size_t n = (size_t)(end - p);
    if (n >= out_len) {
        return false;
    }
    memcpy(out, p, n);
    out[n] = '\0';
    return true;
}

void ota_c6_note_uart_line(const char *line)
{
    if (s_ack == NULL || strstr(line, "\"ev\":\"ota\"") == NULL) {
        return;
    }
    if (!json_field(line, "\"phase\"", s_phase, sizeof(s_phase))) {
        return;
    }
    // seq är ett TAL, inte en sträng - json_field klarar bara strängvärden.
    s_seq = -1;
    const char *p = strstr(line, "\"seq\"");
    if (p != NULL) {
        p = strchr(p, ':');
        if (p != NULL) {
            s_seq = atoi(p + 1);
        }
    }
    s_ok = strstr(line, "\"status\":\"ok\"") != NULL;
    ESP_LOGI(TAG, "signal: phase=%s seq=%d ok=%d", s_phase, s_seq, (int)s_ok);
    xSemaphoreGive(s_ack);
}

static bool wait_ack(const char *phase, int seq, int timeout_ms)
{
    TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    for (;;) {
        TickType_t now = xTaskGetTickCount();
        if (now >= deadline) {
            ESP_LOGW(TAG, "timeout: vantade %s seq=%d, sag phase=%s seq=%d ok=%d",
                     phase, seq, s_phase, s_seq, (int)s_ok);
            return false;
        }
        if (xSemaphoreTake(s_ack, deadline - now) != pdTRUE) {
            return false;
        }
        if (strcmp(s_phase, phase) != 0) {
            continue; // annan fas: vänta vidare
        }
        if (seq >= 0 && s_seq != seq) {
            continue; // gammalt/annat kvitto
        }
        ESP_LOGI(TAG, "kvitto: %s seq=%d ok=%d", phase, seq, (int)s_ok);
        return s_ok;
    }
}

static void ota_c6_task(void *arg)
{
    struct ferry_args *a = (struct ferry_args *)arg;
    esp_http_client_handle_t client = NULL;
    bool ok = false;

    ESP_LOGI(TAG, "C6-OTA från %s (version %s)", a->url, a->version);
    publish("downloading", 0, NULL);

    esp_http_client_config_t cfg = {
        .url = a->url,
        .timeout_ms = 20000,
        .keep_alive_enable = true,
        .buffer_size = 4096,
        .crt_bundle_attach = esp_crt_bundle_attach,
    };
    client = esp_http_client_init(&cfg);
    if (client == NULL || esp_http_client_open(client, 0) != ESP_OK) {
        publish("error", 0, "http-open");
        goto done;
    }
    int content_len = esp_http_client_fetch_headers(client);
    if (esp_http_client_get_status_code(client) != 200 || content_len <= 0) {
        publish("error", 0, "http-status");
        goto done;
    }

    char line[192];
    snprintf(line, sizeof(line),
             "{\"cmd\":\"ota_begin\",\"size\":%d,\"sha256\":\"%s\",\"version\":\"%s\"}",
             content_len, a->sha, a->version);
    uart_link_send(line);
    if (!wait_ack("begin", -1, ACK_TIMEOUT_MS)) {
        publish("error", 0, "c6 nekade ota_begin");
        goto done;
    }

    uint8_t raw[CHUNK_RAW];
    char b64[3072];
    static char out[3200]; // statisk: för stor för uppgiftens stack
    int seq = 0;
    int total = 0;
    int last_bucket = -1;
    for (;;) {
        int r = esp_http_client_read(client, (char *)raw, sizeof(raw));
        if (r < 0) {
            publish("error", 0, "lasfel");
            goto done;
        }
        if (r == 0) {
            break;
        }
        size_t b64_len = 0;
        if (mbedtls_base64_encode((unsigned char *)b64, sizeof(b64) - 1, &b64_len,
                                  raw, (size_t)r) != 0) {
            publish("error", 0, "base64");
            goto done;
        }
        b64[b64_len] = '\0';
        snprintf(out, sizeof(out), "{\"cmd\":\"ota_data\",\"seq\":%d,\"data\":\"%s\"}", seq, b64);
        ESP_LOGI(TAG, "chunk %d: %d byte (rad %d B) -> C6", seq, r, (int)strlen(out));
        uart_link_send(out);
        if (!wait_ack("data", seq, ACK_TIMEOUT_MS)) {
            publish("error", 0, "c6 kvitterade inte chunk");
            goto done;
        }
        seq++;
        total += r;
        int pct = (total * 100) / content_len;
        if (pct / 10 != last_bucket) {
            last_bucket = pct / 10;
            publish("flashing", pct, NULL);
        }
    }

    uart_link_send("{\"cmd\":\"ota_end\"}");
    if (!wait_ack("end", -1, END_TIMEOUT_MS)) {
        publish("error", 0, "c6 nekade ota_end");
        goto done;
    }
    publish("done", 100, NULL);
    ESP_LOGW(TAG, "C6-OTA klar (%d byte, %d chunkar) – C6:an startar om", total, seq);
    ok = true;

done:
    if (!ok) {
        ESP_LOGE(TAG, "C6-OTA misslyckades");
    }
    if (client != NULL) {
        esp_http_client_cleanup(client);
    }
    free(a);
    s_busy = false;
    vTaskDelete(NULL);
}

void ota_c6_request_from_json(const char *json)
{
    if (s_busy) {
        publish("error", 0, "upptagen");
        return;
    }
    struct ferry_args *a = calloc(1, sizeof(*a));
    if (a == NULL) {
        return;
    }
    if (!json_field(json, "\"url\"", a->url, sizeof(a->url)) ||
        !json_field(json, "\"sha256\"", a->sha, sizeof(a->sha)) ||
        !json_field(json, "\"version\"", a->version, sizeof(a->version))) {
        free(a);
        publish("error", 0, "bad request");
        return;
    }
    if (s_ack == NULL) {
        s_ack = xSemaphoreCreateBinary();
        if (s_ack == NULL) {
            free(a);
            publish("error", 0, "sem");
            return;
        }
    }
    s_busy = true;
    if (xTaskCreate(ota_c6_task, "ota_c6", 10240, a, 5, NULL) != pdPASS) {
        s_busy = false;
        free(a);
        publish("error", 0, "task");
    }
}
