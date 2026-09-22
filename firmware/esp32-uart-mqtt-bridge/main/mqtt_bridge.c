// SPDX-License-Identifier: MIT
// WiFi (station) + MQTT mot HA:s Mosquitto. Se docs/bridge-arkitektur.md.
#include <string.h>
#include <stdio.h>

#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/timers.h"

#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_mac.h"
#include "esp_app_desc.h"
#include "mqtt_client.h"
#include "nvs.h"

#include "secrets.h"
#include "sdkconfig.h"
#include "mqtt_bridge.h"

static const char *TAG = "mqtt_bridge";

#define WIFI_NVS_NS        "nimly_wifi"

static char s_ssid[33];
static char s_pass[65];
static bool s_wifi_connected = false;
static bool s_creds_from_nvs = false;
static int s_fail_count = 0;

static void wifi_connect_now(void);

#define TOPIC_HA_TO_BRIDGE "nimly/proxy/ha_to_bridge"
#define TOPIC_BRIDGE_TO_HA "nimly/proxy/bridge_to_ha"
#define TOPIC_STATE        "nimly/proxy/state"
#define TOPIC_BATTERY      "nimly/proxy/battery"
#define TOPIC_PIN          "nimly/proxy/pin"
#define TOPIC_INFO         "nimly/info"
#define TOPIC_OTA          "nimly/proxy/ota"

static esp_mqtt_client_handle_t s_client = NULL;
static bool s_mqtt_connected = false;
static void (*s_cmd_cb)(const char *json, int len) = NULL;
static void (*s_connect_cb)(void) = NULL;
static TimerHandle_t s_reconn_timer = NULL;

// Ackumulerar MQTT-payload över flera events (en publikation kan fragmenteras).
static char s_rx[512];
static int s_rx_len = 0;

// Mjukt återförsök (10 s) så vi inte hamrar AP:n vid fel.
static void reconn_cb(TimerHandle_t t)
{
    (void)t;
    esp_wifi_connect();
}

void mqtt_bridge_set_command_handler(void (*cb)(const char *json, int len))
{
    s_cmd_cb = cb;
}

void mqtt_bridge_set_connect_handler(void (*cb)(void))
{
    s_connect_cb = cb;
}

void mqtt_bridge_publish_state(bool locked)
{
    if (!s_mqtt_connected) {
        return;
    }
    char msg[64];
    snprintf(msg, sizeof(msg), "{\"lock\":\"%s\"}", locked ? "locked" : "unlocked");
    esp_mqtt_client_publish(s_client, TOPIC_STATE, msg, 0, 1, 1);
}

void mqtt_bridge_publish_from_c6(const char *json)
{
    if (!s_mqtt_connected || !json) {
        return;
    }
    esp_mqtt_client_publish(s_client, TOPIC_BRIDGE_TO_HA, json, 0, 1, 0);
}

void mqtt_bridge_publish_battery(int percentage)
{
    if (!s_mqtt_connected) {
        return;
    }
    char msg[48];
    snprintf(msg, sizeof(msg), "{\"battery\":%d}", percentage);
    esp_mqtt_client_publish(s_client, TOPIC_BATTERY, msg, 0, 1, 1);
}

void mqtt_bridge_publish_pin(const char *json)
{
    if (!s_mqtt_connected || !json) {
        return;
    }
    esp_mqtt_client_publish(s_client, TOPIC_PIN, json, 0, 1, 0);
}

void mqtt_bridge_publish_info(void)
{
    if (!s_mqtt_connected) {
        return;
    }
    uint8_t mac[6] = { 0 };
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    const esp_app_desc_t *desc = esp_app_get_description();
    char msg[224];
    snprintf(msg, sizeof(msg),
             "{\"bridge\":\"%02x%02x%02x%02x%02x%02x\",\"fw\":\"%s\",\"prefix\":\"nimly/proxy\","
             "\"target\":\"%s\",\"model\":\"Nimly Shadow Bridge\"}",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5], desc ? desc->version : "?",
             CONFIG_IDF_TARGET);
    esp_mqtt_client_publish(s_client, TOPIC_INFO, msg, 0, 1, 1); // retainad
    ESP_LOGI(TAG, "Info publicerad: %s", msg);
}

void mqtt_bridge_publish_ota(const char *json)
{
    if (!s_mqtt_connected || !json) {
        return;
    }
    esp_mqtt_client_publish(s_client, TOPIC_OTA, json, 0, 1, 0);
}

static void mqtt_event_handler(void *args, esp_event_base_t base, int32_t id, void *data)
{
    (void)args;
    (void)base;
    esp_mqtt_event_handle_t e = (esp_mqtt_event_handle_t)data;

    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        s_mqtt_connected = true;
        ESP_LOGI(TAG, "MQTT ansluten till %s", NIMLY_MQTT_URI);
        esp_mqtt_client_subscribe(s_client, TOPIC_HA_TO_BRIDGE, 1);
        // Publicera identiteten (retainat) så att nimly_shadow hittar oss automatiskt.
        mqtt_bridge_publish_info();
        // Begär aktuell status från C6 (retainade state-topicen kan vara inaktuell).
        if (s_connect_cb) {
            s_connect_cb();
        }
        break;
    case MQTT_EVENT_DISCONNECTED:
        s_mqtt_connected = false;
        ESP_LOGW(TAG, "MQTT frånkopplad – återansluter automatiskt");
        break;
    case MQTT_EVENT_ERROR:
        if (e->error_handle) {
            ESP_LOGE(TAG, "MQTT fel: type=%d tls=0x%x errno=%d connack=%d",
                     (int)e->error_handle->error_type,
                     (unsigned)e->error_handle->esp_tls_last_esp_err,
                     e->error_handle->esp_transport_sock_errno,
                     (int)e->error_handle->connect_return_code);
        }
        break;
    case MQTT_EVENT_DATA: {
        // En publikation kan komma i flera events: ackumulera tills allt är mottaget.
        if (e->current_data_offset == 0) {
            s_rx_len = 0;
        }
        int space = (int)sizeof(s_rx) - 1 - s_rx_len;
        int n = e->data_len < space ? e->data_len : space;
        if (e->data && n > 0) {
            memcpy(s_rx + s_rx_len, e->data, n);
            s_rx_len += n;
        }
        if (e->current_data_offset + e->data_len >= e->total_data_len) {
            s_rx[s_rx_len] = '\0';
            ESP_LOGI(TAG, "MQTT in: %s", s_rx);
            if (s_cmd_cb) {
                s_cmd_cb(s_rx, s_rx_len);
            }
            s_rx_len = 0;
        }
        break;
    }
    default:
        break;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        ESP_LOGI(TAG, "WiFi STA startad");
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_wifi_connected = false;
        wifi_event_sta_disconnected_t *d = (wifi_event_sta_disconnected_t *)data;
        ESP_LOGW(TAG, "WiFi tappad (reason=%d) – nytt försök om 10 s", d ? d->reason : -1);
        // Skyddsnät: om NVS-uppgifterna (Improv) inte fungerar och secrets.h har giltiga
        // uppgifter, återgå till dem så att enheten inte kan låsas ute.
        s_fail_count++;
        if (s_fail_count >= 6 && s_creds_from_nvs && sizeof(NIMLY_WIFI_SSID) > 1) {
            ESP_LOGW(TAG, "NVS-uppgifterna ansluter inte – återgår till secrets.h");
            nvs_handle_t h;
            if (nvs_open(WIFI_NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
                nvs_erase_all(h);
                nvs_commit(h);
                nvs_close(h);
            }
            strncpy(s_ssid, NIMLY_WIFI_SSID, sizeof(s_ssid) - 1);
            strncpy(s_pass, NIMLY_WIFI_PASS, sizeof(s_pass) - 1);
            s_creds_from_nvs = false;
            s_fail_count = 0;
            wifi_connect_now();
        } else if (s_reconn_timer) {
            xTimerStart(s_reconn_timer, 0);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_wifi_connected = true;
        s_fail_count = 0;
        ip_event_got_ip_t *ev = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "WiFi OK, IP " IPSTR, IP2STR(&ev->ip_info.ip));
    }
}

// Läser WiFi-uppgifter: NVS först (provisionerat via Improv), annars secrets.h.
static void wifi_load_creds(void)
{
    nvs_handle_t h;
    if (nvs_open(WIFI_NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t n = sizeof(s_ssid);
        if (nvs_get_str(h, "ssid", s_ssid, &n) != ESP_OK) {
            s_ssid[0] = '\0';
        }
        n = sizeof(s_pass);
        if (nvs_get_str(h, "pass", s_pass, &n) != ESP_OK) {
            s_pass[0] = '\0';
        }
        nvs_close(h);
    }
    if (s_ssid[0] != '\0') {
        s_creds_from_nvs = true;
        ESP_LOGI(TAG, "WiFi-uppgifter från NVS");
        return;
    }
    if (sizeof(NIMLY_WIFI_SSID) > 1) {
        strncpy(s_ssid, NIMLY_WIFI_SSID, sizeof(s_ssid) - 1);
        strncpy(s_pass, NIMLY_WIFI_PASS, sizeof(s_pass) - 1);
        s_creds_from_nvs = false;
        ESP_LOGI(TAG, "WiFi-uppgifter från secrets.h");
    } else {
        ESP_LOGW(TAG, "Inga WiFi-uppgifter – väntar på Improv-provionering");
    }
}

static void wifi_connect_now(void)
{
    if (s_ssid[0] == '\0') {
        return;
    }
    wifi_config_t wc = { 0 };
    strncpy((char *)wc.sta.ssid, s_ssid, sizeof(wc.sta.ssid) - 1);
    strncpy((char *)wc.sta.password, s_pass, sizeof(wc.sta.password) - 1);
    wc.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    wc.sta.pmf_cfg.capable = false;
    wc.sta.pmf_cfg.required = false;
    wc.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wc));
    s_wifi_connected = false; // ny anslutning – Improv väntar på nytt GOT_IP
    esp_wifi_disconnect();
    esp_wifi_connect();
    ESP_LOGI(TAG, "Ansluter WiFi …");
}

void mqtt_bridge_wifi_apply(const char *ssid, const char *pass)
{
    if (ssid == NULL || *ssid == '\0') {
        return;
    }
    strncpy(s_ssid, ssid, sizeof(s_ssid) - 1);
    s_ssid[sizeof(s_ssid) - 1] = '\0';
    strncpy(s_pass, pass ? pass : "", sizeof(s_pass) - 1);
    s_pass[sizeof(s_pass) - 1] = '\0';
    s_creds_from_nvs = true;
    s_fail_count = 0;

    nvs_handle_t h;
    if (nvs_open(WIFI_NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_str(h, "ssid", s_ssid);
        nvs_set_str(h, "pass", s_pass);
        nvs_commit(h);
        nvs_close(h);
    }
    ESP_LOGI(TAG, "WiFi-uppgifter sparade (SSID satt) – ansluter");
    wifi_connect_now();
}

bool mqtt_bridge_wifi_connected(void)
{
    return s_wifi_connected;
}

bool mqtt_bridge_wifi_configured(void)
{
    return s_ssid[0] != '\0';
}

void mqtt_bridge_start(void)
{
    wifi_load_creds();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());

    // Sänkt TX-effekt (8,5 dBm) – vid mycket stark mottagning (t.ex. RSSI ~-20 dBm) kan
    // max effekt ge mättnad/handskakningsfel (reason 2/201). Enhet: 0.25 dBm -> 34 = 8.5 dBm.
    int8_t tx = 34;
    esp_err_t txerr = esp_wifi_set_max_tx_power(tx);
    ESP_LOGI(TAG, "WiFi max TX-effekt: %d (%.1f dBm), err=%d", tx, tx * 0.25, (int)txerr);

    // Stäng av power-save: modem sleep kan göra att DHCP-OFFER/beacons missas (särskilt med coex).
    esp_wifi_set_ps(WIFI_PS_NONE);
    ESP_LOGI(TAG, "WiFi power-save av (WIFI_PS_NONE)");

    // Tvinga Wi-Fi 4 (11b/g/n) – C6 + Wi-Fi 7-AP (U7 Pro) kan strula med 11ax.
    esp_err_t proterr = esp_wifi_set_protocol(WIFI_IF_STA,
                                              WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
    ESP_LOGI(TAG, "WiFi-protokoll 11b/g/n (ingen 11ax) -> %d", (int)proterr);

    if (!s_reconn_timer) {
        s_reconn_timer = xTimerCreate("wifi_reconn", pdMS_TO_TICKS(10000), pdFALSE, NULL, reconn_cb);
    }

    wifi_connect_now();

    esp_mqtt_client_config_t mc = {
        .broker.address.uri = NIMLY_MQTT_URI,
        .credentials.username = (strlen(NIMLY_MQTT_USER) ? NIMLY_MQTT_USER : NULL),
        .credentials.authentication.password = (strlen(NIMLY_MQTT_PASS) ? NIMLY_MQTT_PASS : NULL),
        .session.last_will.topic = TOPIC_STATE,
        .session.last_will.msg = "{\"lock\":\"unknown\"}",
        .session.last_will.qos = 1,
        .session.last_will.retain = 1,
    };
    s_client = esp_mqtt_client_init(&mc);
    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);
}
