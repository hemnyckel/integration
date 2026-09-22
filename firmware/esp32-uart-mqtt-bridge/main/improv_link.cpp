// SPDX-License-Identifier: MIT
// Improv Wi-Fi för bryggan – både seriell (USB, ESP Web Tools) och BLE (Home Assistant).
// WiFi-uppgifter sparas i NVS och ansluts direkt. Protokollet hanteras av Espressifs SDK.
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "sdkconfig.h"

#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_att.h"
#include "os/os_mbuf.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"

#include "improv.h"

#include "improv_link.h"
#include "mqtt_bridge.h"

static const char *TAG = "improv";

static constexpr uart_port_t IMPROV_UART = UART_NUM_0;
static constexpr int64_t PROVISION_TIMEOUT_US = 30LL * 1000 * 1000;

static void ble_improv_advertise(void);

static uint8_t s_state = improv::STATE_STOPPED;
static uint8_t s_last_error = improv::ERROR_NONE;
static volatile bool s_provisioning = false;
static int64_t s_deadline_us = 0;

// --- UUID:n (samma som Improv-specen/SDK:t) ---------------------------------
// 00467768-6228-2272-4663-2774782680xx – BLE_UUID128_INIT tar bytes LSB först.
#define IMPROV_UUID128(last)                                                   \
    BLE_UUID128_INIT(0x##last, 0x80, 0x26, 0x78, 0x74, 0x27, 0x63, 0x46, 0x72, \
                     0x22, 0x28, 0x62, 0x68, 0x77, 0x46, 0x00)

static const ble_uuid128_t s_svc_uuid = IMPROV_UUID128(00);
static const ble_uuid128_t s_status_uuid = IMPROV_UUID128(01);
static const ble_uuid128_t s_error_uuid = IMPROV_UUID128(02);
static const ble_uuid128_t s_rpc_cmd_uuid = IMPROV_UUID128(03);
static const ble_uuid128_t s_rpc_res_uuid = IMPROV_UUID128(04);
static const ble_uuid128_t s_caps_uuid = IMPROV_UUID128(05);

enum improv_chr { CHR_STATUS, CHR_ERROR, CHR_RPC_CMD, CHR_RPC_RESULT, CHR_CAPS };

static uint16_t s_handle_status;
static uint16_t s_handle_error;
static uint16_t s_handle_rpc_cmd;
static uint16_t s_handle_rpc_result;
static uint16_t s_handle_caps;
static uint16_t s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
static std::vector<uint8_t> s_rpc_result;

// --- sändning (delas av seriell + BLE) --------------------------------------

static void send_bytes(const std::vector<uint8_t> &frame)
{
    uart_write_bytes(IMPROV_UART, frame.data(), frame.size());
}

// Full Improv-serialram: "IMPROV" + version + typ + längd + nyttolast + kontrollsumma.
static std::vector<uint8_t> make_frame(uint8_t type, const std::vector<uint8_t> &payload)
{
    std::vector<uint8_t> frame = { 'I', 'M', 'P', 'R', 'O', 'V', improv::IMPROV_SERIAL_VERSION, type,
                                   static_cast<uint8_t>(payload.size()) };
    frame.insert(frame.end(), payload.begin(), payload.end());
    uint8_t sum = 0;
    for (uint8_t b : frame) {
        sum = static_cast<uint8_t>(sum + b);
    }
    frame.push_back(sum);
    return frame;
}

static void ble_notify(uint16_t value_handle, const std::vector<uint8_t> &data)
{
    if (s_conn_handle == BLE_HS_CONN_HANDLE_NONE || data.empty()) {
        return;
    }
    struct os_mbuf *om = ble_hs_mbuf_from_flat(data.data(), data.size());
    if (om != nullptr) {
        ble_gatts_notify_custom(s_conn_handle, value_handle, om);
    }
}

static void publish_state(void)
{
    send_bytes(make_frame(improv::TYPE_CURRENT_STATE, { s_state }));
    ble_notify(s_handle_status, { s_state });
}

static void publish_error(uint8_t error)
{
    s_last_error = error;
    send_bytes(make_frame(improv::TYPE_ERROR_STATE, { error }));
    ble_notify(s_handle_error, { error });
}

static void publish_rpc(improv::Command command, const std::vector<std::string> &datum)
{
    // Seriell: rå RPC-nyttolast (cmd + längd + data), ramen lägger på header + checksumma.
    std::vector<uint8_t> rpc = improv::build_rpc_response(command, datum, false);
    if (!rpc.empty()) {
        rpc.pop_back();
    }
    send_bytes(make_frame(improv::TYPE_RPC_RESPONSE, rpc));

    // BLE: resultatet ligger i RPC_RESULT och meddelas.
    std::vector<uint8_t> result = improv::build_rpc_response(command, datum, true);
    s_rpc_result = result;
    ble_notify(s_handle_rpc_result, result);
}

// --- kommandon (delas av seriell + BLE) --------------------------------------

static bool handle_command(improv::ImprovCommand command)
{
    switch (command.command) {
    case improv::WIFI_SETTINGS: {
        if (command.ssid.empty()) {
            publish_error(improv::ERROR_INVALID_RPC);
            return true;
        }
        ESP_LOGI(TAG, "WiFi-uppgifter mottagna via Improv (%s) – ansluter",
                 s_conn_handle == BLE_HS_CONN_HANDLE_NONE ? "seriell" : "BLE");
        s_state = improv::STATE_PROVISIONING;
        publish_state();
        s_provisioning = true;
        s_deadline_us = esp_timer_get_time() + PROVISION_TIMEOUT_US;
        mqtt_bridge_wifi_apply(command.ssid.c_str(), command.password.c_str());
        return true;
    }
    case improv::GET_CURRENT_STATE:
        publish_state();
        return true;
    case improv::GET_DEVICE_INFO: {
        const esp_app_desc_t *desc = esp_app_get_description();
        publish_rpc(improv::GET_DEVICE_INFO,
                    { "Nimly Shadow Bridge", desc ? desc->version : "?", CONFIG_IDF_TARGET, "nimly-bridge" });
        return true;
    }
    case improv::GET_WIFI_NETWORKS: {
        wifi_scan_config_t scan = {};
        if (esp_wifi_scan_start(&scan, true) != ESP_OK) {
            publish_error(improv::ERROR_UNKNOWN);
            return true;
        }
        uint16_t count = 0;
        esp_wifi_scan_get_ap_num(&count);
        if (count > 20) {
            count = 20;
        }
        auto *records = static_cast<wifi_ap_record_t *>(calloc(count ? count : 1, sizeof(wifi_ap_record_t)));
        if (records == nullptr) {
            publish_error(improv::ERROR_UNKNOWN);
            return true;
        }
        esp_wifi_scan_get_ap_records(&count, records);
        std::vector<std::string> names;
        for (int i = 0; i < count; i++) {
            if (records[i].ssid[0] != '\0') {
                names.emplace_back(reinterpret_cast<const char *>(records[i].ssid));
            }
        }
        free(records);
        publish_rpc(improv::GET_WIFI_NETWORKS, names);
        return true;
    }
    default:
        publish_error(improv::ERROR_UNKNOWN_RPC);
        return false;
    }
}

// --- seriell mottagning -----------------------------------------------------

static void improv_task(void *arg)
{
    (void)arg;
    uint8_t chunk[256];
    static uint8_t frame[288];
    size_t position = 0;

    for (;;) {
        int n = uart_read_bytes(IMPROV_UART, chunk, sizeof(chunk), pdMS_TO_TICKS(150));
        for (int i = 0; i < n; i++) {
            uint8_t byte = chunk[i];
            bool keep = improv::parse_improv_serial_byte(
                position, byte, frame,
                [](improv::ImprovCommand cmd) { return handle_command(cmd); },
                [](improv::Error err) { publish_error(err); });
            if (keep && position < sizeof(frame)) {
                frame[position++] = byte;
            } else {
                position = 0;
            }
        }

        if (s_provisioning) {
            if (mqtt_bridge_wifi_connected()) {
                s_provisioning = false;
                s_state = improv::STATE_PROVISIONED;
                publish_state();
                ESP_LOGI(TAG, "Improv: WiFi anslutet – provisionering klar");
            } else if (esp_timer_get_time() > s_deadline_us) {
                s_provisioning = false;
                s_state = improv::STATE_STOPPED;
                publish_error(improv::ERROR_UNABLE_TO_CONNECT);
                publish_state();
                ESP_LOGW(TAG, "Improv: kunde inte ansluta inom tidsgränsen");
            }
        }
    }
}

// --- BLE (Improv över GATT) --------------------------------------------------

static int gatt_access(uint16_t conn_handle, uint16_t attr_handle, struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn_handle;
    (void)attr_handle;
    auto which = static_cast<enum improv_chr>(reinterpret_cast<intptr_t>(arg));

    switch (ctxt->op) {
    case BLE_GATT_ACCESS_OP_READ_CHR:
        switch (which) {
        case CHR_STATUS:
            return os_mbuf_append(ctxt->om, &s_state, sizeof(s_state)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
        case CHR_ERROR:
            return os_mbuf_append(ctxt->om, &s_last_error, sizeof(s_last_error)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
        case CHR_RPC_RESULT:
            if (s_rpc_result.empty()) {
                return BLE_ATT_ERR_UNLIKELY;
            }
            return os_mbuf_append(ctxt->om, s_rpc_result.data(), s_rpc_result.size()) == 0 ? 0
                                                                                          : BLE_ATT_ERR_INSUFFICIENT_RES;
        case CHR_CAPS: {
            // Inga särskilda förmågor (t.ex. identify) – 0 räcker för provisionering.
            uint8_t caps = 0;
            return os_mbuf_append(ctxt->om, &caps, sizeof(caps)) == 0 ? 0 : BLE_ATT_ERR_INSUFFICIENT_RES;
        }
        default:
            return BLE_ATT_ERR_UNLIKELY;
        }

    case BLE_GATT_ACCESS_OP_WRITE_CHR:
        if (which == CHR_RPC_CMD) {
            uint16_t len = OS_MBUF_PKTLEN(ctxt->om);
            if (len == 0 || len > 512) {
                return BLE_ATT_ERR_INVALID_ATTR_VALUE_LEN;
            }
            uint8_t buf[512];
            if (os_mbuf_copydata(ctxt->om, 0, len, buf) != 0) {
                return BLE_ATT_ERR_UNLIKELY;
            }
            // BLE-klienten skickar RPC-nyttolasten inkl. kontrollsumma.
            improv::ImprovCommand cmd = improv::parse_improv_data(buf, len, true);
            if (cmd.command == improv::UNKNOWN || cmd.command == improv::BAD_CHECKSUM) {
                publish_error(cmd.command == improv::BAD_CHECKSUM ? improv::ERROR_INVALID_RPC
                                                                  : improv::ERROR_UNKNOWN_RPC);
                return BLE_ATT_ERR_UNLIKELY;
            }
            handle_command(cmd);
            return 0;
        }
        return BLE_ATT_ERR_UNLIKELY;

    default:
        return BLE_ATT_ERR_UNLIKELY;
    }
}

static const struct ble_gatt_chr_def s_chr_defs[] = {
    { .uuid = &s_status_uuid.u,
      .access_cb = gatt_access,
      .arg = reinterpret_cast<void *>(CHR_STATUS),
      .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
      .val_handle = &s_handle_status },
    { .uuid = &s_error_uuid.u,
      .access_cb = gatt_access,
      .arg = reinterpret_cast<void *>(CHR_ERROR),
      .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
      .val_handle = &s_handle_error },
    { .uuid = &s_rpc_cmd_uuid.u,
      .access_cb = gatt_access,
      .arg = reinterpret_cast<void *>(CHR_RPC_CMD),
      .flags = BLE_GATT_CHR_F_WRITE,
      .val_handle = &s_handle_rpc_cmd },
    { .uuid = &s_rpc_res_uuid.u,
      .access_cb = gatt_access,
      .arg = reinterpret_cast<void *>(CHR_RPC_RESULT),
      .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
      .val_handle = &s_handle_rpc_result },
    { .uuid = &s_caps_uuid.u,
      .access_cb = gatt_access,
      .arg = reinterpret_cast<void *>(CHR_CAPS),
      .flags = BLE_GATT_CHR_F_READ,
      .val_handle = &s_handle_caps },
    { 0 },
};

static const struct ble_gatt_svc_def s_svc_defs[] = {
    { .type = BLE_GATT_SVC_TYPE_PRIMARY,
      .uuid = &s_svc_uuid.u,
      .characteristics = s_chr_defs },
    { 0 },
};

static int gap_event(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        s_conn_handle = (event->connect.status == 0) ? event->connect.conn_handle : BLE_HS_CONN_HANDLE_NONE;
        ESP_LOGI(TAG, "BLE: %s", event->connect.status == 0 ? "ansluten" : "anslutning misslyckades");
        if (event->connect.status != 0) {
            ble_improv_advertise();
        }
        return 0;
    case BLE_GAP_EVENT_DISCONNECT:
        s_conn_handle = BLE_HS_CONN_HANDLE_NONE;
        ESP_LOGI(TAG, "BLE: frånkopplad – annonserar igen");
        ble_improv_advertise();
        return 0;
    case BLE_GAP_EVENT_ADV_COMPLETE:
        ble_improv_advertise();
        return 0;
    default:
        return 0;
    }
}

static void ble_improv_advertise(void)
{
    struct ble_hs_adv_fields fields = {};
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.uuids128 = &s_svc_uuid;
    fields.num_uuids128 = 1;
    fields.uuids128_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        ESP_LOGW(TAG, "BLE: kunde inte sätta annonsfält (%d)", rc);
        return;
    }

    struct ble_hs_adv_fields rsp = {};
    const char *name = ble_svc_gap_device_name();
    rsp.name = reinterpret_cast<const uint8_t *>(name);
    rsp.name_len = strlen(name);
    rsp.name_is_complete = 1;
    rc = ble_gap_adv_rsp_set_fields(&rsp);
    if (rc != 0) {
        ESP_LOGW(TAG, "BLE: kunde inte sätta scan-svar (%d)", rc);
    }

    struct ble_gap_adv_params adv = {};
    adv.conn_mode = BLE_GAP_CONN_MODE_UND;
    adv.disc_mode = BLE_GAP_DISC_MODE_GEN;
    rc = ble_gap_adv_start(BLE_OWN_ADDR_PUBLIC, nullptr, BLE_HS_FOREVER, &adv, gap_event, nullptr);
    if (rc != 0 && rc != BLE_HS_EALREADY) {
        ESP_LOGW(TAG, "BLE: kunde inte starta annonsering (%d)", rc);
    } else {
        ESP_LOGI(TAG, "BLE: annonserar Improv-tjänsten");
    }
}

static void ble_on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);
    if (rc != 0) {
        ESP_LOGW(TAG, "BLE: kunde inte säkra adress (%d)", rc);
        return;
    }
    ble_improv_advertise();
}

static void ble_host_task(void *arg)
{
    (void)arg;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void ble_improv_init(void)
{
    if (nimble_port_init() != ESP_OK) {
        ESP_LOGW(TAG, "BLE: nimble_port_init misslyckades – BLE av");
        return;
    }
    ble_svc_gap_init();
    ble_svc_gatt_init();

    ble_hs_cfg.sync_cb = ble_on_sync;
    ble_hs_cfg.gatts_register_cb = nullptr;

    int rc = ble_gatts_count_cfg(s_svc_defs);
    if (rc != 0) {
        ESP_LOGW(TAG, "BLE: ble_gatts_count_cfg misslyckades (%d)", rc);
        return;
    }
    rc = ble_gatts_add_svcs(s_svc_defs);
    if (rc != 0) {
        ESP_LOGW(TAG, "BLE: ble_gatts_add_svcs misslyckades (%d)", rc);
        return;
    }
    rc = ble_svc_gap_device_name_set("nimly-bridge");
    if (rc != 0) {
        ESP_LOGW(TAG, "BLE: kunde inte sätta enhetsnamn (%d)", rc);
    }
    nimble_port_freertos_init(ble_host_task);
}

// --- start ------------------------------------------------------------------

void improv_link_start(void)
{
    uart_config_t cfg = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(IMPROV_UART, 1024, 0, 0, nullptr, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "Kunde inte installera UART0: %d – seriell Improv av", (int)err);
    } else {
        uart_param_config(IMPROV_UART, &cfg);
    }
    xTaskCreate(improv_task, "improv", 4096, nullptr, 4, nullptr);
    ESP_LOGI(TAG, "Improv (seriell) startad – väntar på WiFi-uppgifter");

    ble_improv_init();
}

void improv_link_set_state(uint8_t state)
{
    s_state = state;
    publish_state();
}
