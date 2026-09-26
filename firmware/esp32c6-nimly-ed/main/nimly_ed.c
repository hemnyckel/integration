// SPDX-License-Identifier: MIT
//
// Nimly ED – ESP32-C6 Zigbee EndDevice som emulerar en Nimly Connect Module (ZMNC010).
//
// Milstolpe M1: joina Home Assistant (ZHA via SkyConnect) och dyka upp som ett lås
// med Manufacturer "Onesti Products AS" och Model "NimlyPRO24".
// Milstolpe M2: hantera LockDoor/UnlockDoor och rapportera LockState.
//
// Loggningen är medvetet rik: vi vill fånga ALLT som händer vid parning mot HA, så vi
// har maximalt med underlag när vi senare försöker mot Nimlys gateway.
// Se docs/emulator-spec.md.

#include "esp_app_desc.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "driver/gpio.h"

#include <stdbool.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"

#include "esp_zigbee.h"
#include "ezbee/nwk.h"

// OBS: varken v2 (ezb_zcl_report_attr_cmd_req) eller v1 (esp_zb_zcl_report_attr_cmd_req)
// kunde rapportera mfg-attributet 0x0100 i denna SDK-version (båda gav NOT_FOUND).
// Vi skickar därför rå ZCL via APS-lagret (ezb_apsde_data_request) – full kontroll.

#include "nimly_zcl.h"
#include "ota_uart.h"
#include "uart_bridge.h"

// Valfria hemligheter/överstyrningar (gitignorade). Definiera t.ex. NIMLY_INSTALL_CODE
// eller NIMLY_EXT_PANID där för att para mot en gateway som kräver det.
#if defined(__has_include)
#  if __has_include("secrets.h")
#    include "secrets.h"
#  endif
#endif

#include <stdio.h>
#include <string.h>

static const char *TAG = "nimly_ed";

// M3: lagring för Onesti-attributen 0x0100 (Operation Event) och 0x0101 (Last PIN).
// Statiska så att pekaren är giltig under hela programmets livstid.
static uint32_t s_operation_event = 0;
static uint8_t s_last_pin[8] = { 0 }; // ZCL octet string: [längd][...], 0 = tom

// Optional standard-DoorLock-attribut som SDK:t inte skapar automatiskt men som
// onesti-lock läser vid setup. Riktiga modulen rapporterar 50/8/4.
static uint16_t s_num_pin_users = 50; // 0x0012 NumberOfPINUsersSupported
static uint8_t s_max_pin_len = 8;     // 0x0017 MaxPINCodeLength
static uint8_t s_min_pin_len = 4;     // 0x0018 MinPINCodeLength

// Satt när enheten kommit in i ett Zigbee-nät (används av OTA-verifieringen).
static bool s_zigbee_joined = false;

// Standard DoorLock-konfigurationsattribut som riktiga modulen/quirk:en exponerar. Utan
// dem får t.ex. ZHA:s autorelock-switch och sound_volume-number UNSUPPORTED_ATTRIBUTE.
static uint16_t s_auto_relock_time = 0;                 // 0x0023 (sekunder)
static uint8_t s_sound_volume = 2;                      // 0x0024 (0-2; riktiga modulen rapporterar 2)
static uint8_t s_operating_mode = 0;                    // 0x0025
static uint16_t s_supported_operating_modes = 0xFFF6;   // 0x0026
static uint8_t s_enable_one_touch_locking = 0;          // 0x0029
static uint8_t s_enable_inside_status_led = 0;          // 0x002A
static uint8_t s_enable_privacy_mode_button = 0;        // 0x002B
static uint8_t s_wrong_code_entry_limit = 0;            // 0x0030
static uint8_t s_user_code_temp_disable_time = 0;       // 0x0031
static uint8_t s_send_pin_over_the_air = 0;             // 0x0032
static uint8_t s_require_pin_for_rf_operation = 0;      // 0x0033

// Batteri (Power Configuration 0x0001). Emulatorn är nätmatad men utger sig för att vara
// batteridriven som den riktiga modulen. Värden verifieras mot riktiga modulen i fas 0.
static uint8_t s_battery_voltage = NIMLY_BATTERY_MV100;     // 0x0020, enhet 100 mV
static uint8_t s_battery_percentage = NIMLY_BATTERY_PERCENT; // 0x0021, raw 0-100 (ZHA-quirken dubblar)

// ---------------------------------------------------------------------------
// Persistent applikationsstatus (NVS) – så att LockState/OperationEvent överlever
// omstart. Zigbee-nätverket ligger i samma NVS-partition men annan namespace.
// ---------------------------------------------------------------------------
static nvs_handle_t s_nvs = 0;
static bool s_state_loaded = false;
static bool s_lock_state = true; // default: LOCKED

static void nimly_state_init(void)
{
    esp_err_t err = nvs_open("nimly", NVS_READWRITE, &s_nvs);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "Kunde inte öppna NVS-namespace 'nimly' (%d) – status persisteras ej", (int)err);
        s_nvs = 0;
        return;
    }
    uint8_t st = 1;
    if (nvs_get_u8(s_nvs, "lock", &st) == ESP_OK) {
        s_lock_state = st != 0;
        s_state_loaded = true;
    }
    uint32_t ev = 0;
    if (nvs_get_u32(s_nvs, "opev", &ev) == ESP_OK) {
        s_operation_event = ev;
        s_state_loaded = true;
    }
    if (s_state_loaded) {
        ESP_LOGI(TAG, "Återställde status: lock=%s op_event=0x%08lx",
                 s_lock_state ? "locked" : "unlocked", (unsigned long)s_operation_event);
    }
}

static void nimly_state_save_lock(bool locked)
{
    s_lock_state = locked;
    if (!s_nvs) {
        return;
    }
    nvs_set_u8(s_nvs, "lock", locked ? 1 : 0);
    nvs_commit(s_nvs);
}

static void nimly_state_save_event(void)
{
    if (!s_nvs) {
        return;
    }
    nvs_set_u32(s_nvs, "opev", s_operation_event);
    nvs_commit(s_nvs);
}

// Rå ZCL-transaktionssekvens.
static uint8_t s_raw_tsn = 0;
static TickType_t s_last_fp_mirror_tick = 0;
static TickType_t s_last_fp_clear_tick = 0;

// PIN-status per slot (längd 0 = ledig). Låset lagrar koderna själva; emulatorn håller
// bara statusen så att appens slot-vy (GetPINCode/GetUserStatus) stämmer.
#define NIMLY_PIN_SLOTS 50
static uint8_t s_pin_len[NIMLY_PIN_SLOTS] = { 0 };
static char s_pin_code[NIMLY_PIN_SLOTS][9] = { { 0 } };
// Testkrok: neka nästa SetPINCode (sätts via {"cmd":"nack_next_pin"} över UART).
static bool s_nack_next_pin = false;

// Koordinatorns ZCL-endpoint. Lärs in från inkommande kommandon (rapporter som skickas
// till 0x0000 ska gå till rätt endpoint – Hermes/ZHA använder 1/11, en gateway kan
// använda en annan). Default 1; det inlärda värdet persisteras.
static uint8_t s_coord_ep = 1;

static void nimly_state_load_coord_ep(void)
{
    if (!s_nvs) {
        return;
    }
    uint8_t cep = 1;
    if (nvs_get_u8(s_nvs, "cep", &cep) == ESP_OK && cep != 0) {
        s_coord_ep = cep;
        ESP_LOGI(TAG, "Koordinator-endpoint återställd: %u", s_coord_ep);
    }
}

static void nimly_state_save_coord_ep(void)
{
    if (!s_nvs) {
        return;
    }
    nvs_set_u8(s_nvs, "cep", s_coord_ep);
    nvs_commit(s_nvs);
}

// IEEE-override (provisionering): om satt i NVS används den i stället för NIMLY_IEEE_ADDR
// vid uppstart. Skrivs av integrations-wizarden ("set_ieee") eftersom Nimlys moln
// whitelistar kända modulers adresser. Lagras little-endian som resten av stacken.
static uint8_t s_ieee_override[8];
static bool s_ieee_override_set = false;

// A re-provision must survive the reboot that applies it. We keep the new address
// in RTC memory and, on the next boot (before the Zigbee stack starts), wipe the
// NVS partition so the stack cannot restore its old extended address - only then
// does it adopt the override. This folds the documented factory-reset -> set_ieee
// -> reboot dance into set_ieee itself.
#define NIMLY_REPROVISION_MAGIC 0x1EEB7A11u
RTC_DATA_ATTR static uint8_t s_rtc_new_ieee[8];
RTC_DATA_ATTR static uint32_t s_rtc_reprovision_magic;

static void nimly_state_load_ieee(void)
{
    if (!s_nvs) {
        return;
    }
    size_t len = sizeof(s_ieee_override);
    if (nvs_get_blob(s_nvs, "ieee", s_ieee_override, &len) == ESP_OK
        && len == sizeof(s_ieee_override)) {
        s_ieee_override_set = true;
        ESP_LOGI(TAG, "IEEE-override i NVS: %02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x",
                 s_ieee_override[7], s_ieee_override[6], s_ieee_override[5], s_ieee_override[4],
                 s_ieee_override[3], s_ieee_override[2], s_ieee_override[1], s_ieee_override[0]);
    }
}

static void nimly_state_save_ieee(const uint8_t ieee[8])
{
    memcpy(s_ieee_override, ieee, sizeof(s_ieee_override));
    s_ieee_override_set = true;
    if (!s_nvs) {
        return;
    }
    nvs_set_blob(s_nvs, "ieee", s_ieee_override, sizeof(s_ieee_override));
    nvs_commit(s_nvs);
}

// Hex-dump (bounded) för diagnostik. Används aldrig på PIN-kommandon.
static void log_hex(const char *what, const uint8_t *p, uint16_t len)
{
    char buf[3 * 24 + 1];
    uint16_t m = len < 24 ? len : 24;
    int n = 0;
    for (uint16_t i = 0; i < m; i++) {
        n += snprintf(buf + n, sizeof(buf) - (size_t)n, "%02x ", p[i]);
    }
    buf[n] = '\0';
    ESP_LOGI(TAG, "%s (%u byte): %s", what, len, buf);
}

// Skicka en rå ZCL-frame via APS till koordinatorn (0x0000), NWK-krypterad.
static void nimly_aps_send_ex(uint16_t cluster, const uint8_t *asdu, uint8_t len)
{
    ezb_apsde_data_req_t req = {
        .dst_address = { .addr_mode = EZB_ADDR_MODE_SHORT, .u.short_addr = 0x0000 },
        .src_endpoint = NIMLY_EP_ID,
        .dst_endpoint = s_coord_ep,
        .cluster_id = cluster,
        .profile_id = NIMLY_HA_PROFILE_ID,
        .radius = 8,
        .tx_options = EZB_APSDE_TX_OPT_SECURITY_ENABLED,
        .asdu_length = len,
        .asdu = (uint8_t *)asdu,
    };
    log_hex("  APS OUT", asdu, len);
    ESP_LOGI(TAG, "  APS send cluster=0x%04x dst_ep=%u len=%u -> %d", cluster, s_coord_ep, len,
             (int)ezb_apsde_data_request(&req));
}

// Leveransbekräftelse för våra APS-sändningar (status 0 = OK, annars misslyckad).
static void nimly_aps_confirm_handler(const ezb_apsde_data_confirm_t *c)
{
    if (!c) {
        return;
    }
    ESP_LOGI(TAG, "APS CONFIRM cluster=0x%04x dst_ep=%u status=0x%02x len=%u",
             c->cluster_id, c->dst_endpoint, c->status, c->asdu_length);
}

static void nimly_aps_send(const uint8_t *asdu, uint8_t len)
{
    nimly_aps_send_ex(NIMLY_CLUSTER_DOORLOCK, asdu, len);
}

// Rå ZCL Report Attributes för LockState (0x0000, enum8) – standard-attribut.
static void nimly_send_raw_lockstate(uint8_t state)
{
    uint8_t asdu[10];
    uint8_t n = 0;
    asdu[n++] = 0x18; // frame control: general, server->client, no default rsp
    asdu[n++] = s_raw_tsn++;
    asdu[n++] = 0x0A; // Report Attributes
    asdu[n++] = (uint8_t)(EZB_ZCL_ATTR_DOOR_LOCK_LOCK_STATE_ID & 0xFF);
    asdu[n++] = (uint8_t)(EZB_ZCL_ATTR_DOOR_LOCK_LOCK_STATE_ID >> 8);
    asdu[n++] = 0x30; // enum8
    asdu[n++] = state;
    nimly_aps_send(asdu, n);
}

// Rå ZCL Report Attributes för batteri (PowerConfig 0x0001): procent (0x0021) + spänning
// (0x0020, 100 mV). Skickas proaktivt för att bryggan/appen ska få en batterinivå.
static void nimly_send_raw_battery(void)
{
    uint8_t asdu[12];
    uint8_t n = 0;
    asdu[n++] = 0x18; // frame control: general, server->client, no default rsp
    asdu[n++] = s_raw_tsn++;
    asdu[n++] = 0x0A; // Report Attributes
    asdu[n++] = (uint8_t)(EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID & 0xFF);
    asdu[n++] = (uint8_t)(EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID >> 8);
    asdu[n++] = 0x20; // uint8
    asdu[n++] = s_battery_percentage;
    asdu[n++] = (uint8_t)(EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_VOLTAGE_ID & 0xFF);
    asdu[n++] = (uint8_t)(EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_VOLTAGE_ID >> 8);
    asdu[n++] = 0x20; // uint8
    asdu[n++] = s_battery_voltage;
    nimly_aps_send_ex(NIMLY_CLUSTER_POWER_CFG, asdu, n);
    uart_bridge_send_battery(s_battery_percentage);
    ESP_LOGI(TAG, "Batterirapport: %u%% / %u (100mV)", s_battery_percentage, s_battery_voltage);
}

static TimerHandle_t s_batt_timer = NULL;
static void batt_report_cb(TimerHandle_t timer)
{
    (void)timer;
    nimly_send_raw_battery();
}

// ---------------------------------------------------------------------------
// Logghjälpare
// ---------------------------------------------------------------------------

static void log_eui64(const char *what, const ezb_extaddr_t *e)
{
    ESP_LOGI(TAG, "%s = %02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x",
             what, e->u8[7], e->u8[6], e->u8[5], e->u8[4],
             e->u8[3], e->u8[2], e->u8[1], e->u8[0]);
}

static void log_network_info(void)
{
    ezb_extaddr_t own;
    ezb_extpanid_t epan;
    ezb_nwk_get_extended_address(&own);
    ezb_nwk_get_extended_panid(&epan);
    log_eui64("own IEEE", &own);
    log_eui64("ext PAN ID", &epan);
    ESP_LOGI(TAG, "PAN ID=0x%04hx channel=%d short=0x%04hx",
             ezb_nwk_get_panid(), ezb_nwk_get_current_channel(), ezb_nwk_get_short_address());
}

static void log_cmd_hdr(const char *what, const ezb_zcl_cmd_hdr_t *h)
{
    if (!h) {
        ESP_LOGI(TAG, "%s: (ingen header)", what);
        return;
    }
    ESP_LOGI(TAG, "%s: src=0x%04hx src_ep=%d dst_ep=%d cluster=0x%04x profile=0x%04x cmd=0x%02x tsn=%d rssi=%d manuf=0x%04x",
             what, h->src_addr.u.short_addr, h->src_ep, h->dst_ep, h->cluster_id,
             h->profile_id, h->cmd_id, h->tsn, h->rssi, h->manuf_code);
}

// ---------------------------------------------------------------------------
// Diagnostics: readable BDB status + "what does the radio hear?"
// ---------------------------------------------------------------------------

// The bare status code is what made the pairing blocker hard to read: 0x03 and
// 0x0A look alike at a glance but mean completely different things. Name them.
static const char *bdb_status_name(int status)
{
    switch (status) {
    case EZB_BDB_STATUS_SUCCESS: return "SUCCESS";
    case EZB_BDB_STATUS_IN_PROGRESS: return "IN_PROGRESS";
    case EZB_BDB_STATUS_NOT_AA_CAPABLE: return "NOT_AA_CAPABLE";
    case EZB_BDB_STATUS_NO_NETWORK: return "NO_NETWORK";
    case EZB_BDB_STATUS_TARGET_FAILURE: return "TARGET_FAILURE";
    case EZB_BDB_STATUS_FORMATION_FAILURE: return "FORMATION_FAILURE";
    case EZB_BDB_STATUS_NO_IDENTIFY_QUERY_RESPONSE: return "NO_IDENTIFY_QUERY_RESPONSE";
    case EZB_BDB_STATUS_BINDING_TABLE_FULL: return "BINDING_TABLE_FULL";
    case EZB_BDB_STATUS_NO_SCAN_RESPONSE: return "NO_SCAN_RESPONSE";
    case EZB_BDB_STATUS_NOT_PERMITTED: return "NOT_PERMITTED";
    case EZB_BDB_STATUS_TCLK_EX_FAILURE: return "TCLK_EX_FAILURE";
    case EZB_BDB_STATUS_NOT_ON_A_NETWORK: return "NOT_ON_A_NETWORK";
    case EZB_BDB_STATUS_ON_A_NETWORK: return "ON_A_NETWORK";
    case EZB_BDB_STATUS_CANCELLED: return "CANCELLED";
    case EZB_BDB_STATUS_DEV_ANNCE_SEND_FAILURE: return "DEV_ANNCE_SEND_FAILURE";
    default: return "UNKNOWN";
    }
}

// Log every beacon the radio hears (channel, PAN, ext-PAN, permit, capacity).
// This is the C6's own view of the air - the sniffer shows the raw frames, this
// shows what the Zigbee stack actually made of them. Called before steering so
// "no network" vs "network but window closed" is visible in our own console.
static volatile bool s_boot_scan_steered = false;

static void scan_result_cb(ezb_nwk_active_scan_result_t *result, void *user_ctx)
{
    (void)user_ctx;
    if (result == NULL) {
        ESP_LOGI(TAG, "SCAN: finished");
        // Steer only after the scan has finished, so the MAC scan and the BDB
        // commissioning never run on top of each other.
        if (!s_boot_scan_steered) {
            s_boot_scan_steered = true;
            ESP_LOGI(TAG, "SCAN: starting NETWORK_STEERING after the scan");
            ezb_bdb_start_top_level_commissioning(EZB_BDB_MODE_NETWORK_STEERING);
        }
        return;
    }
    ESP_LOGI(TAG,
             "SCAN: ch=%u pan=0x%04x extpan=%02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x "
             "permit=%u router_cap=%u enddev_cap=%u coord=0x%04x",
             result->channel_number, result->panid,
             result->extpanid.u8[0], result->extpanid.u8[1],
             result->extpanid.u8[2], result->extpanid.u8[3],
             result->extpanid.u8[4], result->extpanid.u8[5],
             result->extpanid.u8[6], result->extpanid.u8[7],
             result->permit_join, result->router_capacity,
             result->enddev_capacity, result->shortaddr);
}

static void nimly_scan_networks(void)
{
    ezb_nwk_scan_req_t req = {
        .scan_type = EZB_NWK_SCAN_TYPE_ACTIVE,
        .scan_duration = 3,
        .scan_channels = NIMLY_PRIMARY_CHANNEL_MASK,
        .active_scan_cb = scan_result_cb,
        .user_ctx = NULL,
    };
    ezb_err_t err = ezb_nwk_scan(&req);
    ESP_LOGI(TAG, "SCAN: active scan on 0x%08lx (dur=%u) -> %d",
             (unsigned long)req.scan_channels, req.scan_duration, (int)err);
}

// ---------------------------------------------------------------------------
// Join-retry
// ---------------------------------------------------------------------------

// Fortsätter försöka NETWORK_STEERING med jämna mellanrum tills enheten kommit in i
// ett nät. Gör parningen smidig mot både ZHA och (senare) Nimlys gateway.
static TimerHandle_t s_steer_retry_timer = NULL;
static uint32_t s_steer_attempts = 0;

static void steer_retry_cb(TimerHandle_t timer)
{
    (void)timer;
    ESP_LOGI(TAG, "Join-retry: nytt NETWORK_STEERING-försök (#%lu)",
             (unsigned long)++s_steer_attempts);
    ezb_bdb_start_top_level_commissioning(EZB_BDB_MODE_NETWORK_STEERING);
}

// ---------------------------------------------------------------------------
// Bindning till koordinatorn (så att 0x0100 kan rapporteras)
// ---------------------------------------------------------------------------

static void bind_req_cb(const ezb_zdp_bind_req_result_t *result, void *user_ctx)
{
    (void)user_ctx;
    if (result && result->rsp) {
        ESP_LOGI(TAG, "Bind-svar: status=0x%02x", result->rsp->status);
    } else {
        ESP_LOGW(TAG, "Bind misslyckades (error=%d)", result ? (int)result->error : -999);
    }
}

static void ieee_addr_cb(const ezb_zdo_ieee_addr_req_result_t *result, void *user_ctx)
{
    (void)user_ctx;
    if (!result || !result->rsp || result->rsp->status != 0) {
        ESP_LOGW(TAG, "IEEE-adress-svar misslyckades");
        return;
    }
    ezb_extaddr_t coord = result->rsp->ieee_addr_remote_dev;
    ezb_extaddr_t own;
    ezb_nwk_get_extended_address(&own);
    log_eui64("koordinator IEEE", &coord);

    // The real module does not bind to the coordinator: the bridge binds the
    // Door Lock cluster to its own EUI right after the interview, and an
    // outbound bind only earns a NOT_SUPPORTED (docs/protocol.md).
    (void)own;
    (void)coord;
    (void)bind_req_cb;
}

static void request_coordinator_bind(void)
{
    ezb_zdo_ieee_addr_req_t req = {
        .dst_nwk_addr = 0x0000,
        .field = { .nwk_addr_of_interest = 0x0000, .request_type = 0x00, .start_index = 0 },
        .cb = ieee_addr_cb,
        .user_ctx = NULL,
    };
    ESP_LOGI(TAG, "IEEE-adressförfrågan (0x0000) -> %d", (int)ezb_zdo_ieee_addr_req(&req));
}

// ---------------------------------------------------------------------------
// App-signaler (nätverk, join) – logga ALLT
// ---------------------------------------------------------------------------

// Sätts när VI begär fabriksåterställning (knapp/kommando). En LEAVE som kommer utan
// denna flagga är koordinatorinitierad (t.ex. borttagen i appen) och ska INTE följas av
// automatisk rejoin – då stannar enheten fabriksny tills användaren parar om.
static volatile bool s_local_reset_requested = false;

// Se till att enheten kommer tillbaka in i nätet efter LEAVE/orphan. Plug-n-play: om
// vi tappar föräldern (men fortfarande är parade) ska vi kunna återansluta automatiskt.
static void nimly_schedule_rejoin(const char *why)
{
    ESP_LOGW(TAG, "%s – startar join-retry (%d s)", why, NIMLY_STEER_RETRY_SEC);
    if (s_steer_retry_timer) {
        xTimerStart(s_steer_retry_timer, 0);
    }
}

static void nimly_request_factory_reset(const char *why)
{
    if (ezb_bdb_is_factory_new()) {
        ESP_LOGW(TAG, "Fabriksåterställning (%s) – redan fabriksny, börjar steer:a", why);
        nimly_schedule_rejoin("redan fabriksny");
        return;
    }
    ESP_LOGW(TAG, "Fabriksåterställning (%s) – lämnar nätet", why);
    s_local_reset_requested = true;
    ezb_bdb_reset_via_local_action();
}

// Rapportera aktuell (NVS-återställd) LockState till koordinatorn efter (åter)anslutning,
// så att HA/appen inte visar ett inaktuellt läge efter en omstart av emulatorn.
static void nimly_report_current_lockstate(void)
{
    uint8_t st = s_lock_state ? EZB_ZCL_DOOR_LOCK_LOCK_STATE_LOCKED
                              : EZB_ZCL_DOOR_LOCK_LOCK_STATE_UNLOCKED;
    nimly_send_raw_lockstate(st);
    nimly_send_raw_battery();
    ESP_LOGI(TAG, "Rapporterar aktuell LockState (%s) efter (åter)anslutning",
             s_lock_state ? "locked" : "unlocked");
}

static bool app_signal_handler(const ezb_app_signal_t *app_signal)
{
    ezb_app_signal_type_t signal_type = ezb_app_signal_get_type(app_signal);

    switch (signal_type) {
    case EZB_ZDO_SIGNAL_SKIP_STARTUP:
        ESP_LOGI(TAG, "SIGNAL: SKIP_STARTUP -> startar INITIALIZATION");
        ezb_bdb_start_top_level_commissioning(EZB_BDB_MODE_INITIALIZATION);
        break;

    case EZB_BDB_SIGNAL_DEVICE_FIRST_START:
    case EZB_BDB_SIGNAL_DEVICE_REBOOT: {
        ezb_bdb_comm_status_t status = *((ezb_bdb_comm_status_t *)ezb_app_signal_get_params(app_signal));
        ESP_LOGI(TAG, "SIGNAL: %s status=0x%02x factory_new=%d",
                 ezb_app_signal_to_string(signal_type), status, ezb_bdb_is_factory_new());
        if (status != EZB_BDB_STATUS_SUCCESS) {
            // A rejoin can fail for a moment (the coordinator still booting, the
            // device evicted from a re-formed network): keep trying instead of
            // stranding the module until someone resets it.
            ESP_LOGW(TAG, "%s misslyckades (0x%02x/%s) - nytt forsok om %d s",
                     ezb_app_signal_to_string(signal_type), status,
                     bdb_status_name(status), NIMLY_STEER_RETRY_SEC);
            nimly_schedule_rejoin("misslyckad ateranslutning");
            break;
        }
        if (ezb_bdb_is_factory_new()) {
            ESP_LOGI(TAG, "Fabriksny – skannar natet, startar sedan NETWORK_STEERING");
            nimly_scan_networks();
            // Fallback: if the scan never reports finished, steer anyway.
            if (s_steer_retry_timer) {
                xTimerStart(s_steer_retry_timer, 0);
            }
        } else {
            ESP_LOGI(TAG, "Startad (redan parad), försöker återansluta");
            s_zigbee_joined = true;
            uart_bridge_send_net(true);
            log_network_info();
            request_coordinator_bind();
            nimly_report_current_lockstate();
        }
    } break;

    case EZB_BDB_SIGNAL_STEERING: {
        ezb_bdb_comm_status_t status = *((ezb_bdb_comm_status_t *)ezb_app_signal_get_params(app_signal));
        if (status == EZB_BDB_STATUS_SUCCESS) {
            ESP_LOGI(TAG, "SIGNAL: STEERING OK – enheten är parad");
            s_zigbee_joined = true;
            uart_bridge_send_net(true);
            log_network_info();
            request_coordinator_bind();
            nimly_report_current_lockstate();
            if (s_steer_retry_timer) {
                xTimerStop(s_steer_retry_timer, 0);
            }
        } else {
            ESP_LOGW(TAG, "SIGNAL: STEERING misslyckades (0x%02x/%s) factory_new=%d – nytt försök om %d s",
                     status, bdb_status_name(status), ezb_bdb_is_factory_new(),
                     NIMLY_STEER_RETRY_SEC);
            if (s_steer_retry_timer) {
                xTimerStart(s_steer_retry_timer, 0);
            }
        }
    } break;

    case EZB_BDB_SIGNAL_FORMATION:
        ESP_LOGI(TAG, "SIGNAL: FORMATION (oväntat för EndDevice)");
        break;

    case EZB_BDB_SIGNAL_FINDING_AND_BINDING_INITIATOR_FINISHED:
    case EZB_BDB_SIGNAL_FINDING_AND_BINDING_TARGET_FINISHED:
        ESP_LOGI(TAG, "SIGNAL: Finding & Binding klar");
        break;

    case EZB_ZDO_SIGNAL_DEVICE_ANNCE: {
        const ezb_zdo_signal_device_annce_params_t *p = ezb_app_signal_get_params(app_signal);
        ESP_LOGI(TAG, "SIGNAL: DEVICE_ANNCE short=0x%04hx", p->short_addr);
    } break;

    case EZB_ZDO_SIGNAL_DEVICE_UPDATE:
        ESP_LOGI(TAG, "SIGNAL: DEVICE_UPDATE");
        break;

    case EZB_ZDO_SIGNAL_DEVICE_AUTHORIZED:
        ESP_LOGI(TAG, "SIGNAL: DEVICE_AUTHORIZED");
        break;

    case EZB_ZDO_SIGNAL_DEVICE_UNAVAILABLE:
        ESP_LOGW(TAG, "SIGNAL: DEVICE_UNAVAILABLE");
        break;

    case EZB_ZDO_SIGNAL_LEAVE: {
        const ezb_zdo_signal_leave_params_t *p = ezb_app_signal_get_params(app_signal);
        ezb_zdo_leave_type_t lt = p ? p->leave_type : EZB_ZDO_LEAVE_TYPE_RESET;
        ESP_LOGW(TAG, "SIGNAL: LEAVE (type=%d, local_reset=%d)", lt, s_local_reset_requested);
        s_zigbee_joined = false;
        uart_bridge_send_net(false);
        if (lt == EZB_ZDO_LEAVE_TYPE_REJOIN) {
            nimly_schedule_rejoin("LEAVE (rejoin)");
        } else if (s_local_reset_requested) {
            s_local_reset_requested = false;
            nimly_schedule_rejoin("LEAVE (lokal återställning)");
        } else {
            // The coordinator removed us (for example after a bridge reset). The
            // device is factory new again, so steering keeps looking for the
            // bridge's joining window instead of waiting to be told twice.
            ESP_LOGW(TAG, "Borttagen av koordinatorn - borjar steer:a mot ny parning");
            nimly_schedule_rejoin("borttagen av koordinatorn");
        }
    } break;

    case EZB_ZDO_SIGNAL_LEAVE_INDICATION: {
        const ezb_zdo_signal_leave_indication_params_t *p = ezb_app_signal_get_params(app_signal);
        ESP_LOGW(TAG, "SIGNAL: LEAVE_INDICATION short=0x%04hx type=%d",
                 p->short_addr, p->leave_type);
        if (p->short_addr == ezb_nwk_get_short_address() && !ezb_bdb_is_factory_new()) {
            s_zigbee_joined = false;
            uart_bridge_send_net(false);
            nimly_schedule_rejoin("LEAVE_INDICATION (oss)");
        }
    } break;

    case EZB_ZDO_SIGNAL_ERROR:
        ESP_LOGE(TAG, "SIGNAL: ZDO_ERROR");
        break;

    case EZB_NWK_SIGNAL_DEVICE_ASSOCIATED:
        ESP_LOGI(TAG, "SIGNAL: NWK DEVICE_ASSOCIATED");
        break;

    case EZB_NWK_SIGNAL_NO_ACTIVE_LINKS_LEFT:
        ESP_LOGW(TAG, "SIGNAL: NWK NO_ACTIVE_LINKS_LEFT (föräldralös!)");
        if (!ezb_bdb_is_factory_new()) {
            nimly_schedule_rejoin("NO_ACTIVE_LINKS_LEFT");
        } else {
            ESP_LOGW(TAG, "Fabriksny – väntar på parning istället för rejoin");
        }
        break;

    case EZB_NWK_SIGNAL_PANID_CONFLICT_DETECTED:
        ESP_LOGW(TAG, "SIGNAL: NWK PANID_CONFLICT");
        break;

    case EZB_NWK_SIGNAL_NETWORK_STATUS:
        ESP_LOGI(TAG, "SIGNAL: NWK NETWORK_STATUS");
        break;

    case EZB_NWK_SIGNAL_PERMIT_JOIN_STATUS: {
        uint8_t duration = *(uint8_t *)ezb_app_signal_get_params(app_signal);
        ESP_LOGI(TAG, "SIGNAL: PERMIT_JOIN %d sekunder", duration);
    } break;

    default:
        // Allt annat loggas också – vi vill inte missa någon signal vid parning.
        ESP_LOGI(TAG, "SIGNAL: %s (0x%02x)", ezb_app_signal_to_string(signal_type), signal_type);
        break;
    }
    return true;
}

// ---------------------------------------------------------------------------
// ZCL core action – attribute-writes, kommandon, default-responses
// ---------------------------------------------------------------------------

static void zcl_set_attr_value_handler(ezb_zcl_set_attr_value_message_t *message)
{
    ESP_RETURN_ON_FALSE(message, , TAG, "tomt message");
    uint16_t attr = message->in.attribute.id;
    uint8_t type = message->in.attribute.data.type;
    uint16_t size = message->in.attribute.data.size;
    const uint8_t *raw = (const uint8_t *)message->in.attribute.data.value;
    ESP_LOGI(TAG, "ATTR WRITE: dst_ep=%d cluster=0x%04x role=%d status=0x%02x attr=0x%04x type=0x%02x size=%d",
             message->info.dst_ep, message->info.cluster_id, message->info.cluster_role,
             message->info.status, attr, type, size);

    // The vendor app writes settings straight to the module. Keep the local behaviour in
    // sync and tell Home Assistant, so the real lock follows (native, both directions).
    if (raw == NULL) {
        return;
    }
    if (attr == EZB_ZCL_ATTR_DOOR_LOCK_SOUND_VOLUME_ID && size >= 1) {
        s_sound_volume = raw[0];
        uart_bridge_send_volume(s_sound_volume);
        ESP_LOGI(TAG, "Appen satte volym: %u", s_sound_volume);
    } else if (attr == EZB_ZCL_ATTR_DOOR_LOCK_AUTO_RELOCK_TIME_ID && size >= 4) {
        memcpy(&s_auto_relock_time, raw, sizeof(s_auto_relock_time));
        uart_bridge_send_autolock(s_auto_relock_time);
        ESP_LOGI(TAG, "Appen satte auto-lock: %lu s", (unsigned long)s_auto_relock_time);
    }
}

static void nimly_autolock_arm(bool unlock);

// Sätt LockState och rapportera till koordinatorn så HA uppdateras.
static void door_lock_update_state(bool locked, const ezb_zcl_cmd_hdr_t *hdr)
{
    ezb_zcl_attr_desc_t attr = ezb_zcl_get_attr_desc(NIMLY_EP_ID, NIMLY_CLUSTER_DOORLOCK,
                                                     EZB_ZCL_CLUSTER_SERVER,
                                                     EZB_ZCL_ATTR_DOOR_LOCK_LOCK_STATE_ID,
                                                     EZB_ZCL_STD_MANUF_CODE);
    if (attr == EZB_INVALID_ZCL_ATTR_DESC) {
        ESP_LOGW(TAG, "LockState-attributet saknas");
        return;
    }
    uint8_t state = locked ? EZB_ZCL_DOOR_LOCK_LOCK_STATE_LOCKED
                           : EZB_ZCL_DOOR_LOCK_LOCK_STATE_UNLOCKED;
    ezb_zcl_attr_desc_set_value(attr, &state);
    nimly_state_save_lock(locked);
    nimly_autolock_arm(!locked);

    // Rå rapport till koordinatorn (0x0000) + publicera till HA via MQTT.
    (void)hdr;
    nimly_send_raw_lockstate(state);
    uart_bridge_send_state(locked);
    ESP_LOGI(TAG, "LockState -> %s", locked ? "LAST" : "UPPAST");
}

// Generisk rå ZCL Report Attributes (valfritt kluster/attribut/typ) – används av spegling
// av volym (0x0024), auto-lock (0x0023) och batteri (0x0021) från HA.
static void nimly_report_attr_ex(uint16_t cluster, uint16_t attr, uint8_t type,
                                 const uint8_t *val, uint8_t vlen)
{
    uint8_t asdu[16];
    uint8_t n = 0;
    asdu[n++] = 0x18; // frame control: general, server->client, no default rsp
    asdu[n++] = s_raw_tsn++;
    asdu[n++] = 0x0A; // Report Attributes
    asdu[n++] = (uint8_t)(attr & 0xFF);
    asdu[n++] = (uint8_t)(attr >> 8);
    asdu[n++] = type;
    for (uint8_t i = 0; i < vlen && n < sizeof(asdu); i++) {
        asdu[n++] = val[i];
    }
    nimly_aps_send_ex(cluster, asdu, n);
}

static void nimly_report_u8(uint16_t cluster, uint16_t attr, uint8_t v)
{
    nimly_report_attr_ex(cluster, attr, 0x20, &v, 1);
}

static void nimly_report_u32(uint16_t cluster, uint16_t attr, uint32_t v)
{
    uint8_t b[4] = { (uint8_t)(v & 0xFF), (uint8_t)((v >> 8) & 0xFF),
                     (uint8_t)((v >> 16) & 0xFF), (uint8_t)((v >> 24) & 0xFF) };
    nimly_report_attr_ex(cluster, attr, 0x23, b, 4);
}

// Speglar riktiga låsets ljudvolym (0x0024) till app-sidan.
static void nimly_mirror_volume(uint8_t volume)
{
    if (volume > 2) {
        volume = 2;
    }
    s_sound_volume = volume;
    ezb_zcl_set_attr_value(NIMLY_EP_ID, NIMLY_CLUSTER_DOORLOCK, EZB_ZCL_CLUSTER_SERVER,
                           EZB_ZCL_ATTR_DOOR_LOCK_SOUND_VOLUME_ID, 0, &s_sound_volume, false);
    nimly_report_u8(NIMLY_CLUSTER_DOORLOCK, EZB_ZCL_ATTR_DOOR_LOCK_SOUND_VOLUME_ID, volume);
    ESP_LOGI(TAG, "Speglar volym: %u", volume);
}

// Speglar riktiga låsets auto-lock (0x0023, sekunder) – 0 = av.
static void nimly_mirror_autolock(uint32_t seconds)
{
    s_auto_relock_time = seconds;
    ezb_zcl_set_attr_value(NIMLY_EP_ID, NIMLY_CLUSTER_DOORLOCK, EZB_ZCL_CLUSTER_SERVER,
                           EZB_ZCL_ATTR_DOOR_LOCK_AUTO_RELOCK_TIME_ID, 0, &s_auto_relock_time, false);
    nimly_report_u32(NIMLY_CLUSTER_DOORLOCK, EZB_ZCL_ATTR_DOOR_LOCK_AUTO_RELOCK_TIME_ID, seconds);
    ESP_LOGI(TAG, "Speglar auto-lock: %lu s", (unsigned long)seconds);
}

// Speglar riktiga låsets batterinivå (0x0021) + uppskattad spänning (0x0020, 100 mV).
// Spänningen linjäriseras 3,0 V (0 %) .. 3,6 V (100 %), samma intervall som modulen rapporterar.
static void nimly_mirror_battery(uint8_t percentage)
{
    if (percentage > 100) {
        percentage = 100;
    }
    s_battery_percentage = percentage;
    s_battery_voltage = (uint8_t)(30 + (percentage * 6) / 100);
    ezb_zcl_set_attr_value(NIMLY_EP_ID, NIMLY_CLUSTER_POWER_CFG, EZB_ZCL_CLUSTER_SERVER,
                           EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID, 0,
                           &s_battery_percentage, false);
    nimly_send_raw_battery();
    ESP_LOGI(TAG, "Speglar batteri: %u%% (~%u.%u V)", percentage, s_battery_voltage / 10,
             s_battery_voltage % 10);
}

// Rå ZCL Report Attributes för Operation Event (0x0100) – fallback om SDK-API:t felar.
static void nimly_send_raw_operation_event(void)
{
    uint8_t asdu[16];
    uint8_t n = 0;
    asdu[n++] = 0x1C; // frame control: general, mfg-spec, server->client, no default rsp
    asdu[n++] = (uint8_t)(NIMLY_MFG_CODE & 0xFF);
    asdu[n++] = (uint8_t)(NIMLY_MFG_CODE >> 8);
    asdu[n++] = s_raw_tsn++;
    asdu[n++] = 0x0A; // Report Attributes
    asdu[n++] = (uint8_t)(NIMLY_ATTR_OPERATION_EVENT & 0xFF);
    asdu[n++] = (uint8_t)(NIMLY_ATTR_OPERATION_EVENT >> 8);
    asdu[n++] = 0x1B; // bitmap32
    asdu[n++] = (uint8_t)(s_operation_event & 0xFF);
    asdu[n++] = (uint8_t)((s_operation_event >> 8) & 0xFF);
    asdu[n++] = (uint8_t)((s_operation_event >> 16) & 0xFF);
    asdu[n++] = (uint8_t)((s_operation_event >> 24) & 0xFF);
    nimly_aps_send(asdu, n);
}

// M3: uppdatera Operation Event-attributet (0x0100) och rapportera det med mfg-kod.
static void nimly_publish_operation_event(uint16_t slot, uint8_t action, uint8_t source,
                                          bool report, bool emit_uart)
{
    s_operation_event = nimly_operation_event(slot, action, source);
    nimly_state_save_event();

    ezb_zcl_status_t set_st = ezb_zcl_set_attr_value(NIMLY_EP_ID, NIMLY_CLUSTER_DOORLOCK,
                                                     EZB_ZCL_CLUSTER_SERVER,
                                                     NIMLY_ATTR_OPERATION_EVENT, NIMLY_MFG_CODE,
                                                     &s_operation_event, false);

    if (report) {
        // Föredra SDK-API:t. Rapporten är mfg-specifik OCH server->client, vilket kräver
        // att fc.direction sätts – annars härleds fel roll och SDK:t svarar
        // EZB_ERR_NOT_FOUND (precis det som tidigare tvingade fram rå APS för 0x0100).
        ezb_zcl_report_attr_cmd_t rep = {
            .cmd_ctrl = {
                .dst_addr = { .addr_mode = EZB_ADDR_MODE_SHORT, .u.short_addr = 0x0000 },
                .dst_ep = s_coord_ep,
                .src_ep = NIMLY_EP_ID,
                .cluster_id = NIMLY_CLUSTER_DOORLOCK,
                .manuf_code = NIMLY_MFG_CODE,
                .fc = { .manuf_specific = 1, .direction = 1, .dis_default_rsp = 1 },
            },
            .payload = { .attr_id = NIMLY_ATTR_OPERATION_EVENT },
        };
        ezb_err_t rst = ezb_zcl_report_attr_cmd_req(&rep);
        if (rst != EZB_ERR_NONE) {
            ESP_LOGW(TAG, "  0x0100 via API fel 0x%02x – rå APS-fallback", (unsigned)rst);
            nimly_send_raw_operation_event();
        }
    }
    if (emit_uart) {
        uart_bridge_send_event(slot, action, source);
    }
    ESP_LOGI(TAG, "OperationEvent=0x%08lx (slot=%u action=%u source=%u) set=0x%02x",
             (unsigned long)s_operation_event, slot, action, source, (unsigned)set_st);
}

// Auto-relock (0x0023): om >0, lås automatiskt N sekunder efter upplåsning, med
// source=unattributed (0x05) – samma som riktiga NimlyPRO24 rapporterar för sitt
// eget auto-relock (mätt 2026-09-21 via journalen på det riktiga låset).
static TimerHandle_t s_autolock_timer = NULL;
// Verklig fördröjning (sekunder) som timern armeras med. AutoRelockTime-attributet är
// på/av i modellen (appen skriver 1 för "på"), men riktiga låset väntar några sekunder –
// NIMLY_AUTOLOCK_ON_SECONDS. Ett värde > 1 tolkas som en faktisk sekundangivelse.
static uint32_t s_autolock_effective = 0;

static void nimly_autolock_cb(TimerHandle_t timer)
{
    (void)timer;
    if (s_lock_state) {
        return; // redan låst
    }
    ESP_LOGI(TAG, "Auto-relock (%lu s) – låser", (unsigned long)s_autolock_effective);
    door_lock_update_state(true, NULL);
    nimly_publish_operation_event(0, NIMLY_ACTION_LOCK, NIMLY_SRC_UNATTRIBUTED, true, true);
}

static void nimly_autolock_arm(bool unlock)
{
    if (!s_autolock_timer) {
        return;
    }
    if (unlock && s_auto_relock_time > 0) {
        s_autolock_effective = (s_auto_relock_time == 1) ? NIMLY_AUTOLOCK_ON_SECONDS
                                                         : (uint32_t)s_auto_relock_time;
        xTimerChangePeriod(s_autolock_timer, pdMS_TO_TICKS(s_autolock_effective * 1000), 0);
        xTimerStart(s_autolock_timer, 0);
    } else {
        xTimerStop(s_autolock_timer, 0);
    }
}

// Svara med en ZCL Default Response (SUCCESS) på ett kommando vi tagit emot men saknar
// egen handler för (DoorLock PIN-kommandon i SDK v2).
static void nimly_send_default_response(const ezb_apsde_data_ind_t *ind, uint8_t tsn,
                                        uint8_t rsp_to_cmd, uint8_t status)
{
    (void)ind;
    // Rå ZCL Default Response (server->client): [FC, TSN, cmd 0x0B, rsp_to_cmd, status].
    uint8_t asdu[5];
    asdu[0] = 0x18; // frame control: general, server->client, disable default rsp
    asdu[1] = tsn;
    asdu[2] = 0x0B; // Default Response
    asdu[3] = rsp_to_cmd;
    asdu[4] = status;
    nimly_aps_send(asdu, 5);
}

// SDK v2 saknar callback för SetPINCode/GetPINCode/ClearPINCode. Vi lyssnar på rå APS-nivå,
// loggar ramen och svarar med en Default Response. Returnerar true för PIN-kommandona
// (vi hanterar dem) och false för övrigt så att stacken kör sin normala hantering.
static bool aps_data_indication_handler(const ezb_apsde_data_ind_t *ind)
{
    if (!ind || !ind->asdu) {
        return false;
    }
    // Lär in koordinatorns ZCL-endpoint så att våra rapporter går till rätt mottagare.
    if (ind->src_endpoint && ind->src_endpoint != s_coord_ep) {
        s_coord_ep = ind->src_endpoint;
        nimly_state_save_coord_ep();
    }
    ESP_LOGI(TAG, "APS IN: src_ep=%u cluster=0x%04x profile=0x%04x len=%u lqi=%u rssi=%d",
             ind->src_endpoint, ind->cluster_id, ind->profile_id,
             ind->asdu_length, ind->lqi, ind->rssi);

    if (ind->cluster_id != NIMLY_CLUSTER_DOORLOCK) {
        log_hex("  APS IN raw", ind->asdu, ind->asdu_length);
        return false;
    }
    if (ind->asdu_length < 3) {
        return false;
    }

    const uint8_t *p = ind->asdu;
    uint16_t len = ind->asdu_length;
    uint16_t i = 0;
    uint8_t frame_control = p[i++];
    bool manuf_spec = (frame_control & 0x04U) != 0;
    bool cluster_spec = (frame_control & 0x03U) == 0x01U;
    uint16_t manuf = 0;
    if (manuf_spec && (uint16_t)(i + 2) <= len) {
        manuf = (uint16_t)(p[i] | (p[i + 1] << 8));
        i += 2;
    }
    if (!cluster_spec || (uint16_t)(i + 2) > len) {
        return false;
    }
    uint8_t tsn = p[i++];
    uint8_t cmd = p[i++];
    ESP_LOGI(TAG, "  ZCL cmd=0x%02x tsn=%u manuf=0x%04x", cmd, tsn, manuf);

    // Hex-dump av inkommande ram (utom SetPINCode, som innehåller koden i klartext).
    if (cmd != EZB_ZCL_CMD_DOOR_LOCK_SET_PIN_CODE_ID) {
        log_hex("  APS IN raw", ind->asdu, ind->asdu_length);
    }

    // Standard LockDoor/UnlockDoor sköts av SDK:ts core-callback (den fyrar bara för
    // icke-mfg). Mfg-specifika Lock/Unlock samt Toggle/UnlockWithTimeout saknar callback
    // och hanteras därför här – annars skulle en gateway som använder mfg-kommandon inte
    // kunna styra låset.
    if (!manuf_spec && (cmd == EZB_ZCL_CMD_DOOR_LOCK_LOCK_DOOR_ID ||
                        cmd == EZB_ZCL_CMD_DOOR_LOCK_UNLOCK_DOOR_ID)) {
        return false; // låt stacken/callbacken sköta standardkommandot
    }
    if (cmd == EZB_ZCL_CMD_DOOR_LOCK_LOCK_DOOR_ID ||
        cmd == EZB_ZCL_CMD_DOOR_LOCK_UNLOCK_DOOR_ID ||
        cmd == EZB_ZCL_CMD_DOOR_LOCK_TOGGLE_ID ||
        cmd == EZB_ZCL_CMD_DOOR_LOCK_UNLOCK_WITH_TIMEOUT_ID) {
        bool locked = (cmd == EZB_ZCL_CMD_DOOR_LOCK_TOGGLE_ID)
                          ? !s_lock_state
                          : (cmd == EZB_ZCL_CMD_DOOR_LOCK_LOCK_DOOR_ID);
        ESP_LOGI(TAG, "  DoorLock-kommando 0x%02x (mfg=%d) -> %s",
                 cmd, manuf, locked ? "lock" : "unlock");
        door_lock_update_state(locked, NULL);
        nimly_publish_operation_event(0, locked ? NIMLY_ACTION_LOCK : NIMLY_ACTION_UNLOCK,
                                      NIMLY_SRC_UNATTRIBUTED, true, true);
        nimly_send_default_response(ind, tsn, cmd, 0x00);
        return true;
    }

    switch (cmd) {
    case EZB_ZCL_CMD_DOOR_LOCK_SET_PIN_CODE_ID:
    case EZB_ZCL_CMD_DOOR_LOCK_GET_PIN_CODE_ID:
    case EZB_ZCL_CMD_DOOR_LOCK_CLEAR_PIN_CODE_ID:
    case EZB_ZCL_CMD_DOOR_LOCK_CLEAR_ALL_PIN_CODES_ID:
    case EZB_ZCL_CMD_DOOR_LOCK_GET_USER_STATUS_ID: {
        uint16_t user_id = 0;
        if ((uint16_t)(i + 2) <= len) {
            user_id = (uint16_t)(p[i] | (p[i + 1] << 8));
        }
        bool occupied = (user_id < NIMLY_PIN_SLOTS && s_pin_len[user_id] > 0);
        if (cmd == EZB_ZCL_CMD_DOOR_LOCK_SET_PIN_CODE_ID) {
            // Testkrok: neka nästa SetPINCode för att mäta hur bryggan/appen
            // reagerar på en FAILURE (se docs/ota-ferry.md).
            if (s_nack_next_pin) {
                s_nack_next_pin = false;
                ESP_LOGW(TAG, "  NACK-test: nekar SetPINCode slot=%u", user_id);
                nimly_send_default_response(ind, tsn, cmd, 0x01); // FAILURE
                return true;
            }
            // Payload: user_id(2) user_status(1) user_type(1) pin(octet string).
            uint16_t j = (uint16_t)(i + 4);
            if ((uint16_t)(j + 1) <= len && p[j] < 32 && (uint16_t)(j + 1 + p[j]) <= len) {
                uint8_t plen = p[j++];
                char pin[33];
                memcpy(pin, &p[j], plen);
                pin[plen] = '\0';
                ESP_LOGI(TAG, "  SetPINCode slot=%u len=%u (koden loggas aldrig)", user_id, plen);
                if (user_id < NIMLY_PIN_SLOTS) {
                    memcpy(s_pin_code[user_id], pin, plen);
                    s_pin_code[user_id][plen] = '\0';
                    s_pin_len[user_id] = plen;
                }
                uart_bridge_send_pin(user_id, pin); // speglas till riktiga låset i HA
            } else {
                ESP_LOGI(TAG, "  SetPINCode slot=%u (kunde inte tolka PIN-fältet)", user_id);
            }
            nimly_send_default_response(ind, tsn, cmd, 0x00);
        } else if (cmd == EZB_ZCL_CMD_DOOR_LOCK_CLEAR_PIN_CODE_ID) {
            ESP_LOGI(TAG, "  ClearPINCode slot=%u", user_id);
            if (user_id < NIMLY_PIN_SLOTS) {
                s_pin_len[user_id] = 0;
                s_pin_code[user_id][0] = '\0';
            }
            uart_bridge_send_pin_clear(user_id);
            nimly_send_default_response(ind, tsn, cmd, 0x00);
        } else if (cmd == EZB_ZCL_CMD_DOOR_LOCK_CLEAR_ALL_PIN_CODES_ID) {
            ESP_LOGI(TAG, "  ClearAllPINCodes");
            memset(s_pin_len, 0, sizeof(s_pin_len));
            nimly_send_default_response(ind, tsn, cmd, 0x00);
        } else if (cmd == EZB_ZCL_CMD_DOOR_LOCK_GET_PIN_CODE_ID) {
            // GetPINCodeResponse: user_id(2) user_status(1) user_type(1) pin(octet string).
            uint8_t rsp[16];
            uint8_t rn = 0;
            rsp[rn++] = 0x19; rsp[rn++] = tsn; rsp[rn++] = cmd;
            rsp[rn++] = (uint8_t)(user_id & 0xFF);
            rsp[rn++] = (uint8_t)(user_id >> 8);
            rsp[rn++] = occupied ? 0x01 : 0x00;
            rsp[rn++] = 0x00; // user_type = unrestricted
            if (occupied) {
                uint8_t l = s_pin_len[user_id];
                rsp[rn++] = l;
                memcpy(&rsp[rn], s_pin_code[user_id], l);
                rn += l;
            } else {
                rsp[rn++] = 0x00;
            }
            ESP_LOGI(TAG, "  GetPINCode slot=%u -> %s", user_id, occupied ? "upptagen" : "ledig");
            nimly_aps_send(rsp, rn);
        } else { // EZB_ZCL_CMD_DOOR_LOCK_GET_USER_STATUS_ID
            uint8_t rsp[8];
            uint8_t rn = 0;
            rsp[rn++] = 0x19; rsp[rn++] = tsn; rsp[rn++] = cmd;
            rsp[rn++] = (uint8_t)(user_id & 0xFF);
            rsp[rn++] = (uint8_t)(user_id >> 8);
            rsp[rn++] = occupied ? 0x01 : 0x00;
            ESP_LOGI(TAG, "  GetUserStatus slot=%u -> %s", user_id, occupied ? "upptagen" : "ledig");
            nimly_aps_send(rsp, rn);
        }
        return true;
    }
    case 0x70: {
        // Appens tagg-scan (hittad 2026-09-23): spegla till riktiga låset via HA.
        // Svaret till appen är provisoriskt (som finger-grenen) tills låsets
        // eget svar på 0x70 är känt och kan ferjas tillbaka med UID:n.
        uint16_t arg = 0;
        if ((uint16_t)(i + 2) <= len) {
            arg = (uint16_t)(p[i] | (p[i + 1] << 8));
        }
        uart_bridge_send_tag_scan(arg);
        ESP_LOGI(TAG, "  0x70 (tagg-scan) arg=0x%04x -> speglar till riktiga låset", arg);
        uint8_t rsp70[4] = {0x19, tsn, cmd, 0x01};
        nimly_aps_send(rsp70, sizeof(rsp70));
        return true;
    }
    case 0x18: {
        // Appens credential-radering (samma id som enrollen): spegla till
        // riktiga låset och svara som modulen gör — ett resultat på 1 byte.
        uint16_t arg = 0;
        if ((uint16_t)(i + 2) <= len) {
            arg = (uint16_t)(p[i] | (p[i + 1] << 8));
        }
        uart_bridge_send_tag_clear(arg);
        ESP_LOGI(TAG, "  0x18 (radering) arg=0x%04x -> speglar till riktiga låset", arg);
        uint8_t rsp18[4] = {0x19, tsn, cmd, 0x00};
        nimly_aps_send(rsp18, sizeof(rsp18));
        return true;
    }
    case 0x71:
    case 0x72: {
        // Nimly proprietära credential-/fingeravtryckskommandon. Appen skickar
        // 0x72 arg 0x0d, 0x71 arg 0x0e, 0x71 arg 0x0f. Riktiga låsets svar
        // (uppmätt via raw-ZCL): 0x00 / 0x01 / 0x01 0x0f 0x00.
        uint16_t arg = 0;
        if ((uint16_t)(i + 2) <= len) {
            arg = (uint16_t)(p[i] | (p[i + 1] << 8));
        }
        uint8_t rsp[8];
        uint8_t rn = 0;
        rsp[rn++] = 0x19; // FC: cluster-specifik, server->client, ingen default-rsp
        rsp[rn++] = tsn;
        rsp[rn++] = cmd;
        if (cmd == 0x72) {
            rsp[rn++] = 0x00;
            // Appens 0x72 = radera fingeravtryck (slot): spegla till riktiga låset.
            TickType_t nowc = xTaskGetTickCount();
            if (nowc - s_last_fp_clear_tick > pdMS_TO_TICKS(15000)) {
                s_last_fp_clear_tick = nowc;
                uart_bridge_send_fp_clear(arg);
                ESP_LOGI(TAG, "  -> speglar fp_clear (slot=%u) till riktiga låset", arg);
            }
        } else {
            rsp[rn++] = 0x01; // riktiga låset svarar 0x01 på 0x71 (uppmätt)
            // Appens 0x71 = fingerprint-begäran: spegla till riktiga låset (debounce 15 s).
            TickType_t now = xTaskGetTickCount();
            if (now - s_last_fp_mirror_tick > pdMS_TO_TICKS(15000)) {
                s_last_fp_mirror_tick = now;
                uart_bridge_send_fp_enroll(arg);
                ESP_LOGI(TAG, "  -> speglar enroll (slot=%u) till riktiga låset", arg);
            }
        }
        ESP_LOGI(TAG, "  0x%02x arg=0x%04x -> svar (%u byte)", cmd, arg, rn);
        nimly_aps_send(rsp, rn);
        return true;
    }
    default:
        // Logga okända DoorLock-kommandon med payload, så nya vendor-flöden
        // (t.ex. tagg-registrering) kan identifieras i konsolen. Koden loggas aldrig.
        ESP_LOGW(TAG, "OKÄNT DoorLock-kommando cmd=0x%02x tsn=%u manuf=0x%04x len=%u", cmd, tsn, manuf, len);
        if (len > i) {
            log_hex("  payload", &p[i], (uint16_t)(len - i));
        }
        break;
    }
    return false;
}

static void zcl_core_action_handler(ezb_zcl_core_action_callback_id_t callback_id, void *message)
{
    switch (callback_id) {
    case EZB_ZCL_CORE_SET_ATTR_VALUE_CB_ID:
        zcl_set_attr_value_handler((ezb_zcl_set_attr_value_message_t *)message);
        break;
    case EZB_ZCL_CORE_DOOR_LOCK_LOCK_DOOR_CB_ID: {
        ezb_zcl_door_lock_lock_door_message_t *msg = (ezb_zcl_door_lock_lock_door_message_t *)message;
        log_cmd_hdr("CMD LockDoor", msg->in.header);
        door_lock_update_state(true, msg->in.header);
        nimly_publish_operation_event(0, NIMLY_ACTION_LOCK, NIMLY_SRC_UNATTRIBUTED, true, true);
        msg->out.result = EZB_ZCL_STATUS_SUCCESS;
    } break;
    case EZB_ZCL_CORE_DOOR_LOCK_UNLOCK_DOOR_CB_ID: {
        ezb_zcl_door_lock_lock_door_message_t *msg = (ezb_zcl_door_lock_lock_door_message_t *)message;
        log_cmd_hdr("CMD UnlockDoor", msg->in.header);
        door_lock_update_state(false, msg->in.header);
        nimly_publish_operation_event(0, NIMLY_ACTION_UNLOCK, NIMLY_SRC_UNATTRIBUTED, true, true);
        msg->out.result = EZB_ZCL_STATUS_SUCCESS;
    } break;
    case EZB_ZCL_CORE_DEFAULT_RSP_CB_ID: {
        ezb_zcl_cmd_default_rsp_message_t *rsp = (ezb_zcl_cmd_default_rsp_message_t *)message;
        ESP_LOGI(TAG, "DEFAULT RSP status=0x%02x", rsp->in.status_code);
    } break;
    default:
        // Logga alla övriga ZCL-callbacks – avslöjar vad koordinatorn gör vid intervjun.
        ESP_LOGI(TAG, "ZCL CALLBACK id=0x%04lx", (unsigned long)callback_id);
        break;
    }
}

// ---------------------------------------------------------------------------
// Enhetsbeskrivning
// ---------------------------------------------------------------------------

static esp_err_t create_nimly_device(void)
{
    ESP_LOGI(TAG, "Skapar enhet: %s / %s, endpoint %d",
             "Onesti Products AS", "NimlyPRO24", NIMLY_EP_ID);

    // Node descriptor manufacturer code = 0x1234. Koordinatorn (ZHA-quirken/gatewayen)
    // använder den för att tolka manufacturer-specifika attribut (0x0100/0x0101).
    ezb_af_node_desc_set_manuf_code(NIMLY_MFG_CODE);
    ESP_LOGI(TAG, "  node descriptor manufacturer code = 0x%04x", NIMLY_MFG_CODE);

    ezb_af_device_desc_t dev_desc = ezb_af_create_device_desc();

    // --- Basic (0x0000): identitet ---
    ezb_zcl_basic_cluster_server_config_t basic_cfg = {
        // The real module answers ZCL version 2; the SDK default is 8.
        .zcl_version = 2,
        .power_source = EZB_ZCL_BASIC_POWER_SOURCE_BATTERY,
    };
    ezb_zcl_cluster_desc_t basic_desc = ezb_zcl_basic_create_cluster_desc(&basic_cfg, EZB_ZCL_CLUSTER_SERVER);
    ezb_zcl_basic_cluster_desc_add_attr(basic_desc, EZB_ZCL_ATTR_BASIC_MANUFACTURER_NAME_ID, (void *)NIMLY_MANUFACTURER_NAME);
    ezb_zcl_basic_cluster_desc_add_attr(basic_desc, EZB_ZCL_ATTR_BASIC_MODEL_IDENTIFIER_ID, (void *)NIMLY_MODEL_IDENTIFIER);
    ESP_LOGI(TAG, "  + Basic 0x0000 (manufacturer/model satta)");

    // --- Identify (0x0003) ---
    ezb_zcl_identify_cluster_server_config_t identify_cfg = {
        .identify_time = EZB_ZCL_IDENTIFY_IDENTIFY_TIME_DEFAULT_VALUE,
    };
    ezb_zcl_cluster_desc_t identify_desc = ezb_zcl_identify_create_cluster_desc(&identify_cfg, EZB_ZCL_CLUSTER_SERVER);

    // --- Door Lock (0x0101) ---
    ezb_zcl_door_lock_cluster_server_config_t lock_cfg = {
        .lock_state = s_lock_state ? EZB_ZCL_DOOR_LOCK_LOCK_STATE_LOCKED
                                   : EZB_ZCL_DOOR_LOCK_LOCK_STATE_UNLOCKED,
        .lock_type = EZB_ZCL_DOOR_LOCK_LOCK_TYPE_MORTISE,
        .actuator_enabled = true,
    };
    ezb_zcl_cluster_desc_t lock_desc = ezb_zcl_door_lock_create_cluster_desc(&lock_cfg, EZB_ZCL_CLUSTER_SERVER);
    ESP_LOGI(TAG, "  + DoorLock 0x0101 (LOCKED/MORTISE)");

    // Standard-attribut som onesti-lock läser vid setup (0x0012/0x0017/0x0018). De är
    // optional och skapas inte av SDK:t, så vi lägger dem explicit (annars saknas de).
    ezb_err_t cap_err =
        ezb_zcl_door_lock_cluster_desc_add_attr(lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_NUMBER_OF_PIN_USERS_SUPPORTED_ID, &s_num_pin_users);
    ezb_zcl_door_lock_cluster_desc_add_attr(lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_MAX_PIN_CODE_LENGTH_ID, &s_max_pin_len);
    ezb_zcl_door_lock_cluster_desc_add_attr(lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_MIN_PIN_CODE_LENGTH_ID, &s_min_pin_len);
    ESP_LOGI(TAG, "  + cap 0x0012=%u 0x0017=%u 0x0018=%u (err 0x%x)",
             s_num_pin_users, s_max_pin_len, s_min_pin_len, (unsigned)cap_err);

    // Standard-konfigurationsattribut (quirk:ens autorelock-switch, sound_volume m.m.).
    // Summerar ev. fel så vi ser i loggen om SDK:t inte stödjer någon av dem.
    int cfg_err = 0;
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_AUTO_RELOCK_TIME_ID, &s_auto_relock_time);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_SOUND_VOLUME_ID, &s_sound_volume);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_OPERATING_MODE_ID, &s_operating_mode);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_SUPPORTED_OPERATING_MODES_ID, &s_supported_operating_modes);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_ENABLE_ONE_TOUCH_LOCKING_ID, &s_enable_one_touch_locking);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_ENABLE_INSIDE_STATUS_LED_ID, &s_enable_inside_status_led);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_ENABLE_PRIVACY_MODE_BUTTON_ID, &s_enable_privacy_mode_button);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_WRONG_CODE_ENTRY_LIMIT_ID, &s_wrong_code_entry_limit);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_USER_CODE_TEMPORARY_DISABLE_TIME_ID, &s_user_code_temp_disable_time);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_SEND_PIN_OVER_THE_AIR_ID, &s_send_pin_over_the_air);
    cfg_err += (int)ezb_zcl_door_lock_cluster_desc_add_attr(
        lock_desc, EZB_ZCL_ATTR_DOOR_LOCK_REQUIRE_PI_NFOR_RF_OPERATION_ID, &s_require_pin_for_rf_operation);
    ESP_LOGI(TAG, "  + cfg-attribut 0x0023-0x0033 registrerade (summerade fel=%d)", cfg_err);

#if NIMLY_ENABLE_MFG_ATTRS
    // M3: Onesti-egna attribut på DoorLock (mfg code 0x1234). door_lock-desc:ens eget
    // add_attr tar ingen manuf_code/type, så vi använder det generiska manuf-API:t.
    s_operation_event = s_state_loaded
                            ? s_operation_event
                            : nimly_operation_event(0, NIMLY_ACTION_LOCK, NIMLY_SRC_ZIGBEE);
    ezb_err_t mfg_ev = ezb_zcl_cluster_desc_add_manuf_attr(
        lock_desc, NIMLY_ATTR_OPERATION_EVENT, EZB_ZCL_ATTR_TYPE_MAP32,
        EZB_ZCL_ATTR_ACCESS_READ | EZB_ZCL_ATTR_ACCESS_REPORTING,
        NIMLY_MFG_CODE, &s_operation_event);
    ezb_err_t mfg_pin = ezb_zcl_cluster_desc_add_manuf_attr(
        lock_desc, NIMLY_ATTR_LAST_PIN, EZB_ZCL_ATTR_TYPE_OCTSTR,
        EZB_ZCL_ATTR_ACCESS_READ, NIMLY_MFG_CODE, s_last_pin);
    ESP_LOGI(TAG, "  + Onesti-attribut 0x0100/0x0101 (mfg 0x%04x) ev=%d pin=%d",
             NIMLY_MFG_CODE, (int)mfg_ev, (int)mfg_pin);
#endif

    // --- Power Configuration (0x0001): batteri ---
    ezb_zcl_power_config_cluster_server_config_t ps_cfg = {
        .mains_voltage = 0,
        .mains_voltage_min_threshold = 0,
        .mains_voltage_max_threshold = 0,
    };
    ezb_zcl_cluster_desc_t ps_desc = ezb_zcl_power_config_create_cluster_desc(&ps_cfg, EZB_ZCL_CLUSTER_SERVER);
    ezb_zcl_power_config_cluster_desc_add_attr(ps_desc, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_VOLTAGE_ID, &s_battery_voltage);
    ezb_zcl_power_config_cluster_desc_add_attr(ps_desc, EZB_ZCL_ATTR_POWER_CONFIG_BATTERY_PERCENTAGE_REMAINING_ID, &s_battery_percentage);
    ESP_LOGI(TAG, "  + PowerConfig 0x0001 (battery %u%%, %u mV)", s_battery_percentage, s_battery_voltage * 100);

    // --- OTA Upgrade (0x0019) som CLIENT (riktiga modulen exponerar OTA/firmware) ---
    ezb_zcl_ota_upgrade_cluster_client_config_t ota_cfg = {
        .upgrade_server_id = 0,
        .file_offset = 0,
        .image_upgrade_status = EZB_ZCL_OTA_UPGRADE_IMAGE_UPGRADE_STATUS_NORMAL,
        .manufacturer_id = NIMLY_MFG_CODE,
        .image_type_id = 0x0000,
    };
    ezb_zcl_cluster_desc_t ota_desc =
        ezb_zcl_ota_upgrade_create_cluster_desc(&ota_cfg, EZB_ZCL_CLUSTER_CLIENT);
    ESP_LOGI(TAG, "  + OTA Upgrade 0x0019 (client, CurrentFileVersion=0)");

    // --- Manufacturer-specifikt kluster (0xFEA2) – tom stub tills vi vet innehållet ---
    ezb_zcl_custom_cluster_config_t mfg_cfg = {
        .cluster_id = NIMLY_CLUSTER_MFG,
        .init_func = NULL,
        .deinit_func = NULL,
    };
    ezb_zcl_cluster_desc_t mfg_desc = ezb_zcl_custom_create_cluster_desc(&mfg_cfg, EZB_ZCL_CLUSTER_SERVER);
    ESP_LOGI(TAG, "  + mfg-kluster 0xFEA2 (stub)");

    // --- Endpoint 11 ---
    ezb_af_ep_config_t ep_cfg = {
        .ep_id = NIMLY_EP_ID,
        .app_profile_id = NIMLY_HA_PROFILE_ID,
        .app_device_id = NIMLY_DEVICE_ID_DOOR_LOCK,
        .app_device_version = 0,
    };
    ezb_af_ep_desc_t ep_desc = ezb_af_create_endpoint_desc(&ep_cfg);

    ESP_ERROR_CHECK(ezb_af_endpoint_add_cluster_desc(ep_desc, basic_desc));
    ESP_ERROR_CHECK(ezb_af_endpoint_add_cluster_desc(ep_desc, identify_desc));
    ESP_ERROR_CHECK(ezb_af_endpoint_add_cluster_desc(ep_desc, lock_desc));
    ESP_ERROR_CHECK(ezb_af_endpoint_add_cluster_desc(ep_desc, ps_desc));
    ESP_ERROR_CHECK(ezb_af_endpoint_add_cluster_desc(ep_desc, ota_desc));
    ESP_ERROR_CHECK(ezb_af_endpoint_add_cluster_desc(ep_desc, mfg_desc));

    ESP_ERROR_CHECK(ezb_af_device_add_endpoint_desc(dev_desc, ep_desc));
    ESP_ERROR_CHECK(ezb_af_device_desc_register(dev_desc));

#if NIMLY_ENABLE_MFG_ATTRS
    // Konfigurera rapportering för Operation Event (0x0100). Utan detta rapporterar
    // ezb_zcl_report_attr_cmd_req inget (till skillnad från LockState som ZHA konfigurerar).
    ezb_zcl_reporting_info_t ev_rep = ezb_zcl_reporting_info_find(
        NIMLY_EP_ID, NIMLY_CLUSTER_DOORLOCK, EZB_ZCL_CLUSTER_SERVER,
        NIMLY_ATTR_OPERATION_EVENT, NIMLY_MFG_CODE);
    if (ev_rep != EZB_ZCL_INVALID_REPORTING_INFO) {
        ezb_zcl_attr_variable_t ev_delta;
        ev_delta.u32 = 1; // rapportera vid varje ändring
        int u = (int)ezb_zcl_reporting_info_update(ev_rep, 0, 0xFFFF, &ev_delta);
        int s = (int)ezb_zcl_reporting_start_attr_report(ev_rep);
        ESP_LOGI(TAG, "  + rapportering 0x0100 startad (update=%d start=%d)", u, s);
    } else {
        ESP_LOGW(TAG, "  - ingen rapporteringsinfo för 0x0100");
    }
#endif

    ezb_zcl_core_action_handler_register(zcl_core_action_handler);
    ezb_apsde_data_indication_handler_register(aps_data_indication_handler);
    ezb_apsde_data_confirm_handler_register(nimly_aps_confirm_handler);

    ESP_LOGI(TAG, "Enhet registrerad.");
    return ESP_OK;
}

static esp_err_t setup_commissioning(void)
{
    ESP_LOGI(TAG, "Commissioning: chan_mask=0x%08lx (prim)/0x%08lx (sek), distributed_security=av",
             (unsigned long)NIMLY_PRIMARY_CHANNEL_MASK, (unsigned long)NIMLY_SECONDARY_CHANNEL_MASK);
    // Set the IEEE from NVS (provisioned by Home Assistant) when present, or from
    // NIMLY_IEEE_ADDR when the build defines one. With neither, the stack's own
    // MAC-derived EUI-64 is left in place, which is unique per board.
    {
        ezb_extaddr_t ea;
        const bool have_override = s_ieee_override_set;
#if defined(NIMLY_IEEE_ADDR)
        static const uint8_t def[8] = NIMLY_IEEE_ADDR;
        const bool have_default = true;
#else
        static const uint8_t def[8] = { 0 };
        const bool have_default = false;
#endif
        if (have_override) {
            memcpy(ea.u8, s_ieee_override, sizeof(ea.u8));
            ESP_LOGI(TAG, "IEEE från NVS (provisionerad av integrationen)");
        } else if (have_default) {
            memcpy(ea.u8, def, sizeof(ea.u8));
        }
        if (have_override || have_default) {
            ezb_nwk_set_extended_address(&ea);
            ESP_LOGI(TAG, "IEEE satt till %02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x",
                     ea.u8[7], ea.u8[6], ea.u8[5], ea.u8[4], ea.u8[3], ea.u8[2], ea.u8[1], ea.u8[0]);
        } else {
            ESP_LOGI(TAG, "IEEE: behaller stackens MAC-harledda adress (provisionera vid behov)");
        }
    }
    ezb_aps_secur_enable_distributed_security(false);
    ESP_ERROR_CHECK(ezb_bdb_set_primary_channel_set(NIMLY_PRIMARY_CHANNEL_MASK));
    ESP_ERROR_CHECK(ezb_bdb_set_secondary_channel_set(NIMLY_SECONDARY_CHANNEL_MASK));

#if defined(NIMLY_EXT_PANID)
    // Pinna join till ett specifikt nät (t.ex. gatewayns ext PAN ID).
    ezb_extpanid_t epan = { .u8 = NIMLY_EXT_PANID };
    ezb_set_use_extended_panid(&epan);
    ESP_LOGI(TAG, "Ext PAN ID pinnad till %02x:%02x:%02x:%02x:%02x:%02x:%02x:%02x",
             epan.u8[0], epan.u8[1], epan.u8[2], epan.u8[3],
             epan.u8[4], epan.u8[5], epan.u8[6], epan.u8[7]);
#endif

#if defined(NIMLY_INSTALL_CODE)
    // Install code för join (16 byte data + 2 byte CRC = totalt 18 byte).
    static const uint8_t s_install_code[18] = NIMLY_INSTALL_CODE;
    ezb_err_t ic = ezb_secur_ic_set(EZB_SECUR_IC_TYPE_128, s_install_code);
    ESP_LOGI(TAG, "Install code satt (typ 128): 0x%02x", (unsigned)ic);
#endif

    ESP_ERROR_CHECK(ezb_app_signal_add_handler(app_signal_handler));

    s_steer_retry_timer = xTimerCreate("steer_retry",
                                       pdMS_TO_TICKS(NIMLY_STEER_RETRY_SEC * 1000),
                                       pdFALSE, NULL, steer_retry_cb);
    if (!s_steer_retry_timer) {
        ESP_LOGE(TAG, "Kunde inte skapa join-retry-timer");
    }

    // Proaktiv batterirapport (periodisk).
    s_batt_timer = xTimerCreate("batt_rep", pdMS_TO_TICKS(NIMLY_BATTERY_REPORT_SEC * 1000),
                                pdTRUE, NULL, batt_report_cb);
    if (s_batt_timer) {
        xTimerStart(s_batt_timer, 0);
    } else {
        ESP_LOGE(TAG, "Kunde inte skapa batteri-timer");
    }

    // Auto-relock-timer (one-shot, startas vid upplåsning om 0x0023 > 0).
    s_autolock_timer = xTimerCreate("autolock", pdMS_TO_TICKS(1000), pdFALSE, NULL, nimly_autolock_cb);
    if (!s_autolock_timer) {
        ESP_LOGE(TAG, "Kunde inte skapa autolock-timer");
    }
    return ESP_OK;
}

static void zigbee_main_task(void *arg)
{
    // ESP_ZIGBEE_DEFAULT_CONFIG() kommer från esp-zigbee-sdk:s exempelkomponent
    // (example_common) som vi inte använder – därför byggs config-structen manuellt.
    esp_zigbee_config_t config = {
        .device_config = {
            .device_type = EZB_NWK_DEVICE_TYPE_END_DEVICE,
            .install_code_policy = false,
            .zed_config = {
                .ed_timeout = EZB_NWK_ED_TIMEOUT_8MIN,
                .keep_alive = NIMLY_KEEP_ALIVE_MS,
            },
        },
        .platform_config = {
            .storage_partition_name = "nvs",
            .radio_config = {
                .radio_mode = ESP_ZIGBEE_RADIO_MODE_NATIVE,
            },
        },
    };

    ESP_LOGI(TAG, "Initierar Zigbee-stack (EndDevice, native radio, nvs)");
    ESP_ERROR_CHECK(esp_zigbee_init(&config));

    // Fidelitet mot riktiga modulen (raw mac_cap 0x8c; ZHA-quirken normaliserar till 136).
    ezb_set_rx_on_when_idle(NIMLY_RX_ON_WHEN_IDLE);
#if NIMLY_RX_ON_WHEN_IDLE
    ESP_LOGI(TAG, "ED: rx_on_when_idle=true (trogen riktiga modulen; raw mac_cap loggas nedan)");
#else
    ezb_nwk_set_fast_poll_interval(1000);
    ESP_LOGI(TAG, "Sleepy ED: rx_on_when_idle=false, fast poll 1000 ms");
#endif

    ESP_ERROR_CHECK(setup_commissioning());
    ESP_ERROR_CHECK(create_nimly_device());

    // Verifiera den annonserade node-flaggan mot riktiga modulen (AllocateAddress|RxOnWhenIdle = 0x88).
    const ezb_af_node_desc_t *nd = ezb_af_get_node_desc();
    if (nd) {
        ESP_LOGI(TAG, "Node descriptor: manuf=0x%04x mac_cap=0x%02x server_mask=0x%04x",
                 nd->manufacturer_code, nd->mac_capability_flags, nd->server_mask);
    }

    ESP_LOGI(TAG, "Startar Zigbee (autostart=false; commissioning styrs av signaler)");
    ESP_ERROR_CHECK(esp_zigbee_start(false));

    esp_zigbee_launch_mainloop();

    esp_zigbee_deinit();
    vTaskDelete(NULL);
}

// Spegla låsstatus till bryggan (kommando från HA) utan att eka tillbaka till A6.
static void nimly_mirror_lockstate(bool locked)
{
    ezb_zcl_attr_desc_t attr = ezb_zcl_get_attr_desc(NIMLY_EP_ID, NIMLY_CLUSTER_DOORLOCK,
                                                     EZB_ZCL_CLUSTER_SERVER,
                                                     EZB_ZCL_ATTR_DOOR_LOCK_LOCK_STATE_ID,
                                                     EZB_ZCL_STD_MANUF_CODE);
    uint8_t st = locked ? EZB_ZCL_DOOR_LOCK_LOCK_STATE_LOCKED
                        : EZB_ZCL_DOOR_LOCK_LOCK_STATE_UNLOCKED;
    if (attr != EZB_INVALID_ZCL_ATTR_DESC) {
        ezb_zcl_attr_desc_set_value(attr, &st);
    }
    nimly_state_save_lock(locked);
    nimly_autolock_arm(!locked);
    nimly_send_raw_lockstate(st);
    // Spegla även statusraden till bryggan (C3/A6) så att nimly/proxy/state uppdateras
    // för HA-initierade låsningar – inte bara händelsen (0x0100).
    uart_bridge_send_state(locked);
    // Rapportera även 0x0100 så att appens händelsehistorik får en post för
    // HA-initierade låsningar (annars speglas bara LockState). Källan är samma
    // som riktiga PRO24:an rapporterar för Zigbee-kommandon (0x05).
    nimly_publish_operation_event(0, locked ? NIMLY_ACTION_LOCK : NIMLY_ACTION_UNLOCK,
                                  NIMLY_SRC_UNATTRIBUTED, true, false);
    ESP_LOGI(TAG, "Speglar LockState -> %s till bridgen", locked ? "LAST" : "UPPAST");
}

// JSON från bryggan (A6/C3) över UART. Minimal tolkning: lås/lås-upp samt
// fabriksåterställning (för att flytta emulatorn från ZHA till en gateway utan esptool).
// Matchar exakt {"cmd":"<namn>"} (eller med annan ordning på fälten).
// Hälsning till integrationen (version + egen IEEE). Skickas varje gång bryggan/HA
// frågar efter status (get_state, ~1/min) så att den självläker efter en HA-omstart.
static void nimly_send_hello(void)
{
    ezb_extaddr_t own = { 0 };
    ezb_nwk_get_extended_address(&own);
    const esp_app_desc_t *desc = esp_app_get_description();
    uart_bridge_send_hello(desc ? desc->version : "?", own.u8, ezb_bdb_is_factory_new());
}

static int hex_nibble(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

// "aa:bb:cc:dd:ee:ff:00:11" (MSB first) -> little-endian u8[8], as the stack expects.
static bool parse_ieee_string(const char *s, uint8_t out[8])
{
    if (!s) {
        return false;
    }
    int idx = 0;
    while (*s && idx < 8) {
        while (*s == ':' || *s == '-' || *s == ' ') {
            s++;
        }
        int hi = hex_nibble(*s);
        if (hi < 0) {
            return false;
        }
        s++;
        int lo = hex_nibble(*s);
        if (lo < 0) {
            return false;
        }
        s++;
        out[7 - idx] = (uint8_t)((hi << 4) | lo);
        idx++;
    }
    return idx == 8;
}

// Strängvärdet efter "<key>" (utan citattecken), eller NULL.
static const char *json_str_value(const char *json, const char *key)
{
    const char *p = strstr(json, key);
    if (!p) {
        return NULL;
    }
    p += strlen(key);
    while (*p == ' ' || *p == ':') {
        p++;
    }
    if (*p != '"') {
        return NULL;
    }
    return p + 1;
}

static bool json_cmd_is(const char *json, const char *cmd)
{
    const char *p = strstr(json, "\"cmd\"");
    if (!p) {
        return false;
    }
    p = strchr(p, ':');
    if (!p) {
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
    size_t n = strlen(cmd);
    return strncmp(p, cmd, n) == 0 && p[n] == '"';
}

// Plockar ut ett heltal efter "<key>:" (t.ex. "\"value\":"), -1 om det saknas.
static int json_int(const char *json, const char *key)
{
    const char *p = strstr(json, key);
    if (!p) {
        return -1;
    }
    p += strlen(key);
    while (*p == ' ' || *p == ':') {
        p++;
    }
    bool neg = false;
    if (*p == '-') {
        neg = true;
        p++;
    }
    int v = 0;
    while (*p >= '0' && *p <= '9') {
        v = v * 10 + (*p - '0');
        p++;
    }
    return neg ? -v : v;
}

// Returnerar true/false for "<key>":true|false, -1 om det saknas.
static int json_bool(const char *json, const char *key)
{
    const char *p = strstr(json, key);
    if (!p) {
        return -1;
    }
    p += strlen(key);
    while (*p == ' ' || *p == ':') {
        p++;
    }
    if (strncmp(p, "true", 4) == 0 || *p == '1') {
        return 1;
    }
    if (strncmp(p, "false", 5) == 0 || *p == '0') {
        return 0;
    }
    return -1;
}

static void uart_command_handler(const char *json, int len)
{
    (void)len;
    if (json_cmd_is(json, "get_state")) {
        uart_bridge_send_state(s_lock_state);
        uart_bridge_send_net(s_zigbee_joined);
        nimly_send_hello(); // en gång per uppstart: version + IEEE till integrationen
    } else if (json_cmd_is(json, "factory_reset")) {
        nimly_request_factory_reset("kommando");
    } else if (json_cmd_is(json, "unlock")) {
        nimly_mirror_lockstate(false);
    } else if (json_cmd_is(json, "lock")) {
        nimly_mirror_lockstate(true);
    } else if (json_cmd_is(json, "volume")) {
        int v = json_int(json, "\"value\":");
        if (v >= 0) {
            nimly_mirror_volume((uint8_t)v);
        }
    } else if (json_cmd_is(json, "autolock")) {
        int v = json_int(json, "\"value\":");
        if (v >= 0) {
            nimly_mirror_autolock((uint32_t)v);
        }
    } else if (json_cmd_is(json, "battery")) {
        int v = json_int(json, "\"value\":");
        if (v >= 0) {
            nimly_mirror_battery((uint8_t)v);
        }
    } else if (json_cmd_is(json, "event")) {
        // Händelse från riktiga låset: rapportera 0x0100 med riktig action/källa/slot så
        // appens notiser visar vem och hur. emit_uart=false – annars studsar den tillbaka.
        int a = json_int(json, "\"action\":");
        int s = json_int(json, "\"source\":");
        int slot = json_int(json, "\"slot\":");
        if (a >= 0 && s >= 0) {
            nimly_publish_operation_event((slot < 0) ? 0 : (uint16_t)slot, (uint8_t)a,
                                          (uint8_t)s, true, false);
        }
    } else if (json_cmd_is(json, "pin_status")) {
        // HA cleared/set a PIN in onesti_lock -> mirror the slot occupancy to the app side.
        int slot = json_int(json, "\"slot\":");
        int set = json_bool(json, "\"set\":");
        if (slot >= 3 && slot < NIMLY_PIN_SLOTS && set >= 0) {
            s_pin_len[slot] = set ? 6 : 0;
            if (!set) {
                s_pin_code[slot][0] = '\0';
            } else {
                strncpy(s_pin_code[slot], "000000", sizeof(s_pin_code[slot]) - 1);
            }
            ESP_LOGI(TAG, "PIN-status satt fran HA: slot=%d %s", slot, set ? "upptagen" : "ledig");
        }
    } else if (json_cmd_is(json, "nack_next_pin")) {
        // Testkrok (NACK-experimentet): nästa SetPINCode från bryggan nekas.
        s_nack_next_pin = true;
        ESP_LOGW(TAG, "NACK-test armerat: nasta SetPINCode nekas");
    } else if (json_cmd_is(json, "set_ieee")) {
        // Provisioning from the integration: {"cmd":"set_ieee","value":"aa:bb:.."}.
        // Stored in NVS and applied at boot (the stack reads the address at start).
        uint8_t ieee[8];
        const char *val = json_str_value(json, "\"value\"");
        if (parse_ieee_string(val, ieee)) {
            memcpy(s_rtc_new_ieee, ieee, sizeof(s_rtc_new_ieee));
            s_rtc_reprovision_magic = NIMLY_REPROVISION_MAGIC;
            uart_bridge_send_ieee_set(ieee);
            ESP_LOGW(TAG, "IEEE provisionerad - startar om och rensar NVS for att tillampa");
            vTaskDelay(pdMS_TO_TICKS(700));
            esp_restart();
        } else {
            ESP_LOGW(TAG, "set_ieee: kunde inte tolka adressen");
        }
    } else if (json_cmd_is(json, "ota_begin")) {
        int size = json_int(json, "\"size\":");
        const char *sp = json_str_value(json, "\"sha256\"");
        const char *se = sp ? strchr(sp, '"') : NULL;
        const char *vp = json_str_value(json, "\"version\"");
        const char *ve = vp ? strchr(vp, '"') : NULL;
        char sha[96];
        char version[48];
        bool ok = size > 0 && sp != NULL && se != NULL && (size_t)(se - sp) < sizeof(sha);
        if (ok) {
            memcpy(sha, sp, (size_t)(se - sp));
            sha[se - sp] = '\0';
            if (vp != NULL && ve != NULL && (size_t)(ve - vp) < sizeof(version)) {
                memcpy(version, vp, (size_t)(ve - vp));
                version[ve - vp] = '\0';
            } else {
                snprintf(version, sizeof(version), "?");
            }
            ota_uart_begin((uint32_t)size, sha, version);
        } else {
            uart_bridge_send_ota("begin", "error", -1, "bad request");
        }
    } else if (json_cmd_is(json, "ota_data")) {
        int seq = json_int(json, "\"seq\":");
        const char *b64 = json_str_value(json, "\"data\"");
        if (seq >= 0 && b64) {
            const char *end = strchr(b64, '"');
            ota_uart_data((uint32_t)seq, b64, end ? (size_t)(end - b64) : strlen(b64));
        } else {
            uart_bridge_send_ota("data", "error", seq, "bad request");
        }
    } else if (json_cmd_is(json, "ota_end")) {
        ota_uart_end();
    }
}

// Fysisk parningsknapp: BOOT (GPIO9 på ESP32-C6-DevKitC-1, aktiv låg) hållen i 5 s ->
// fabriksåterställning. Gör att emulatorn kan flyttas mellan ZHA och en gateway utan
// UART-verktyg (LEAVE-signalen startar sedan auto-rejoin).
#define NIMLY_PAIR_BUTTON_GPIO 9
#define NIMLY_PAIR_HOLD_MS 5000

static void pair_button_task(void *arg)
{
    (void)arg;
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << NIMLY_PAIR_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&io);

    int held = 0;
    for (;;) {
        if (gpio_get_level(NIMLY_PAIR_BUTTON_GPIO) == 0) {
            if (++held >= NIMLY_PAIR_HOLD_MS / 100) {
                ESP_LOGW(TAG, "BOOT-knapp hållen %d s – fabriksåterställning",
                         NIMLY_PAIR_HOLD_MS / 1000);
                nimly_request_factory_reset("BOOT-knapp");
                held = 0;
                vTaskDelay(pdMS_TO_TICKS(3000)); // undvik upprepade anrop
            }
        } else {
            held = 0;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

// ---------------------------------------------------------------------------
// OTA-verifiering (rollback-skydd)
// ---------------------------------------------------------------------------

#define NIMLY_OTA_VERIFY_TIMEOUT_S 180
#define NIMLY_OTA_VERIFY_PERIOD_S  5

static int s_ota_verify_ticks = 0;
static esp_timer_handle_t s_ota_verify_timer = NULL;

static void ota_verify_timer_cb(void *arg)
{
    (void)arg;
    if (s_zigbee_joined && uart_bridge_rx_count() > 0) {
        // Avbildningen bevisade sig: parad mot ett nät OCH bryggan hörs på UART:en.
        esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "OTA-avbildning godkänd (Zigbee parad + UART aktiv)");
        esp_timer_stop(s_ota_verify_timer);
        return;
    }
    if (++s_ota_verify_ticks * NIMLY_OTA_VERIFY_PERIOD_S >= NIMLY_OTA_VERIFY_TIMEOUT_S) {
        ESP_LOGE(TAG, "OTA-avbildningen kunde inte verifieras inom %ds – startar om för rollback",
                 NIMLY_OTA_VERIFY_TIMEOUT_S);
        esp_restart();
    }
}

static void ota_verify_start_if_pending(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t state;
    if (running == NULL ||
        esp_ota_get_state_partition(running, &state) != ESP_OK ||
        state != ESP_OTA_IMG_PENDING_VERIFY) {
        return;
    }
    ESP_LOGW(TAG, "Ny OTA-avbildning väntar på verifiering: Zigbee parad + UART krävs inom %ds",
             NIMLY_OTA_VERIFY_TIMEOUT_S);
    const esp_timer_create_args_t args = {
        .callback = ota_verify_timer_cb,
        .name = "ota_verify",
    };
    esp_timer_create(&args, &s_ota_verify_timer);
    esp_timer_start_periodic(s_ota_verify_timer, NIMLY_OTA_VERIFY_PERIOD_S * 1000 * 1000);
}

void app_main(void)
{
    const bool reprovision = (s_rtc_reprovision_magic == NIMLY_REPROVISION_MAGIC);
    if (reprovision) {
        s_rtc_reprovision_magic = 0;
        ESP_LOGW(TAG, "Re-provisionering: rensar NVS-partitionen innan Zigbee startar");
        nvs_flash_erase_partition("nvs");
    }
    ESP_ERROR_CHECK(nvs_flash_init());
    nimly_state_init();
    nimly_state_load_coord_ep();
    nimly_state_load_ieee();
    if (reprovision) {
        nimly_state_save_ieee(s_rtc_new_ieee);
        ESP_LOGW(TAG, "Ny IEEE skriven efter NVS-rensning - stacken startar utan sparad adress");
    }

    uart_bridge_set_command_handler(uart_command_handler);
    uart_bridge_start();
    // Skicka aktuell status direkt så bryggan/HA får rätt läge även utan resync.
    uart_bridge_send_state(s_lock_state);
    uart_bridge_send_net(s_zigbee_joined);

    // Om vi startar från en ny OTA-avbildning: verifiera eller rulla tillbaka.
    ota_verify_start_if_pending();

    // Fysisk parningsknapp (fabriksåterställning) – oberoende av Zigbee-tråden.
    xTaskCreate(pair_button_task, "pair_btn", 3072, NULL, 4, NULL);

    const esp_app_desc_t *app = esp_app_get_description();
    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, " Nimly ED (ESP32-C6) – Zigbee-emulator");
    ESP_LOGI(TAG, " firmware : %s", app->version);
    ESP_LOGI(TAG, " byggd    : %s %s", app->date, app->time);
    ESP_LOGI(TAG, " IDF      : %s", app->idf_ver);
    ESP_LOGI(TAG, " mål      : Onesti Products AS / NimlyPRO24, endpoint %d", NIMLY_EP_ID);
    ESP_LOGI(TAG, "==================================================");

    xTaskCreate(zigbee_main_task, "zigbee_main", 4096, NULL, 5, NULL);
}
