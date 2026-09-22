// SPDX-License-Identifier: MIT
// OTA-mottagare på C6:an. Allt innehåll kommer som radbaserad JSON från bryggan;
// avbildningen skickas base64-kodad i 1 KiB-chunkar med sekvensnummer och kvitteras
// per chunk (enkel flödeskontroll utan RTS/CTS). Se docs/ota-ferry.md.
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"

#include "ota_uart.h"
#include "uart_bridge.h"

static const char *TAG = "ota_uart";

#define OTA_CHUNK_TIMEOUT_US (30 * 1000 * 1000)
#define OTA_MAX_IMAGE_BYTES  (4u * 1024u * 1024u)

static esp_ota_handle_t s_handle = 0;
static const esp_partition_t *s_partition = NULL;
static mbedtls_sha256_context s_sha;
static bool s_sha_started = false;
static bool s_active = false;
static uint32_t s_expected_size = 0;
static uint32_t s_received = 0;
static uint32_t s_next_seq = 0;
static char s_expected_sha[65];
static char s_version[32];
static esp_timer_handle_t s_watchdog = NULL;

static void ota_reset(void)
{
    s_active = false;
    if (s_watchdog != NULL) {
        esp_timer_stop(s_watchdog);
    }
    if (s_sha_started) {
        mbedtls_sha256_free(&s_sha);
        s_sha_started = false;
    }
}

static void ota_fail(const char *phase, int seq, const char *message, bool abort_image)
{
    if (abort_image && s_active) {
        esp_ota_abort(s_handle);
    }
    ota_reset();
    uart_bridge_send_ota(phase, "error", seq, message);
    ESP_LOGE(TAG, "OTA misslyckades (%s): %s", phase, message);
}

static void ota_watchdog_cb(void *arg)
{
    (void)arg;
    if (s_active) {
        ota_fail("data", (int)s_next_seq - 1, "timeout", true);
    }
}

static void ota_watchdog_arm(void)
{
    if (s_watchdog == NULL) {
        const esp_timer_create_args_t args = {
            .callback = ota_watchdog_cb,
            .name = "ota_wd",
        };
        esp_timer_create(&args, &s_watchdog);
    }
    esp_timer_stop(s_watchdog);
    esp_timer_start_once(s_watchdog, OTA_CHUNK_TIMEOUT_US);
}

esp_err_t ota_uart_begin(uint32_t size, const char *sha256_hex, const char *version)
{
    if (s_active) {
        uart_bridge_send_ota("begin", "error", -1, "already active");
        return ESP_ERR_INVALID_STATE;
    }
    if (size == 0 || size > OTA_MAX_IMAGE_BYTES || sha256_hex == NULL || strlen(sha256_hex) != 64) {
        uart_bridge_send_ota("begin", "error", -1, "bad request");
        return ESP_ERR_INVALID_ARG;
    }

    s_partition = esp_ota_get_next_update_partition(NULL);
    if (s_partition == NULL) {
        uart_bridge_send_ota("begin", "error", -1, "no update partition");
        return ESP_ERR_NOT_FOUND;
    }

    // Sekventiella skrivningar: partitionen raderas i takt med skrivningarna i stället
    // för helt vid start (snabbare och skonsammare).
    esp_err_t err = esp_ota_begin(s_partition, OTA_WITH_SEQUENTIAL_WRITES, &s_handle);
    if (err != ESP_OK) {
        uart_bridge_send_ota("begin", "error", -1, "esp_ota_begin failed");
        ESP_LOGE(TAG, "esp_ota_begin: %s", esp_err_to_name(err));
        return err;
    }

    mbedtls_sha256_init(&s_sha);
    mbedtls_sha256_starts(&s_sha, 0);
    s_sha_started = true;

    for (int i = 0; i < 64; i++) {
        char c = sha256_hex[i];
        if (c >= 'A' && c <= 'F') {
            c = (char)(c - 'A' + 'a');
        }
        s_expected_sha[i] = c;
    }
    s_expected_sha[64] = '\0';
    snprintf(s_version, sizeof(s_version), "%s", version ? version : "?");

    s_expected_size = size;
    s_received = 0;
    s_next_seq = 0;
    s_active = true;
    ota_watchdog_arm();

    ESP_LOGI(TAG, "OTA startad: %lu byte, version %s -> %s",
             (unsigned long)size, s_version, s_partition->label);
    uart_bridge_send_ota("begin", "ok", -1, s_version);
    return ESP_OK;
}

esp_err_t ota_uart_data(uint32_t seq, const char *b64, size_t b64_len)
{
    if (!s_active) {
        uart_bridge_send_ota("data", "error", (int)seq, "no active ota");
        return ESP_ERR_INVALID_STATE;
    }
    if (seq != s_next_seq) {
        ota_fail("data", (int)seq, "sequence mismatch", true);
        return ESP_ERR_INVALID_ARG;
    }

    // Statisk buffert: RX-uppgiftens stack är liten och en 4 KiB-buffer på
    // stacken kraschade uppgiften (och därmed enheten) vid första chunken.
    static uint8_t out[4096];
    size_t out_len = 0;
    int rc = mbedtls_base64_decode(out, sizeof(out), &out_len,
                                   (const unsigned char *)b64, b64_len);    if (rc != 0 || out_len == 0) {
        ota_fail("data", (int)seq, "bad base64", true);
        return ESP_ERR_INVALID_ARG;
    }
    if (s_received + out_len > s_expected_size) {
        ota_fail("data", (int)seq, "size overflow", true);
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t err = esp_ota_write(s_handle, out, out_len);
    if (err != ESP_OK) {
        ota_fail("data", (int)seq, "flash write failed", true);
        return err;
    }
    mbedtls_sha256_update(&s_sha, out, out_len);
    s_received += out_len;
    s_next_seq++;
    ota_watchdog_arm();
    uart_bridge_send_ota("data", "ok", (int)seq, "");
    return ESP_OK;
}

esp_err_t ota_uart_end(void)
{
    if (!s_active) {
        uart_bridge_send_ota("end", "error", -1, "no active ota");
        return ESP_ERR_INVALID_STATE;
    }
    if (s_received != s_expected_size) {
        ota_fail("end", -1, "size mismatch", true);
        return ESP_ERR_INVALID_ARG;
    }

    uint8_t digest[32];
    mbedtls_sha256_finish(&s_sha, digest);
    s_sha_started = false;
    mbedtls_sha256_free(&s_sha);

    char hex[65];
    for (int i = 0; i < 32; i++) {
        snprintf(hex + i * 2, 3, "%02x", digest[i]);
    }
    if (strcmp(hex, s_expected_sha) != 0) {
        ota_fail("end", -1, "sha256 mismatch", true);
        return ESP_ERR_INVALID_ARG;
    }

    // esp_ota_end frigör handtaget och validerar avbildningens struktur.
    esp_err_t err = esp_ota_end(s_handle);
    if (err != ESP_OK) {
        ota_reset();
        uart_bridge_send_ota("end", "error", -1, "image invalid");
        ESP_LOGE(TAG, "esp_ota_end: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_ota_set_boot_partition(s_partition);
    if (err != ESP_OK) {
        ota_reset();
        uart_bridge_send_ota("end", "error", -1, "set boot failed");
        ESP_LOGE(TAG, "esp_ota_set_boot_partition: %s", esp_err_to_name(err));
        return err;
    }

    ota_reset();
    ESP_LOGI(TAG, "OTA klar: %lu byte, sha256 ok – startar om till %s",
             (unsigned long)s_received, s_partition->label);
    uart_bridge_send_ota("end", "ok", (int)s_next_seq - 1, "rebooting");
    vTaskDelay(pdMS_TO_TICKS(300));
    esp_restart();
    return ESP_OK;
}
