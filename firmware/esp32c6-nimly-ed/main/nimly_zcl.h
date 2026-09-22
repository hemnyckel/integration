// SPDX-License-Identifier: MIT
// Nimly ED – ZCL-konstanter för emulering av Nimly Connect Module (ZMNC010).
// Se docs/emulator-spec.md för den fullständiga kontraktstabellen.

#pragma once

#include <stdint.h>

// Zigbee endpoint som den riktiga modulen använder.
#define NIMLY_EP_ID 11

// Kanal-mask (2.4 GHz-kanalerna 11-26 = bit 11..26). Används vid join-scan.
#define NIMLY_PRIMARY_CHANNEL_MASK 0x07FFF800U
#define NIMLY_SECONDARY_CHANNEL_MASK 0x00000000U

// Sekunder mellan join-försök när enheten är fabriksny och ännu inte kommit in i ett nät.
#define NIMLY_STEER_RETRY_SEC 10

// Fidelitet: den riktiga modulen är en NON-sleepy EndDevice (radion alltid på, men
// batteridriven). Vår raw mac_capability_flags blir 0x8c
// (AllocateAddress|RxOnWhenIdle|mains-bit); ZHA:s quirk normaliserar till 136. C6:an är
// WiFi-fri i lösning B, så vi behöver inte sova för coex. Sätt till 0 endast för
// lågströmstest på en C6 som samtidigt kör WiFi.
#define NIMLY_RX_ON_WHEN_IDLE 1

// Keep-alive mot föräldern (ms). Riktvärde: ED-timeout/4. Endast relevant för sleepy ED.
#define NIMLY_KEEP_ALIVE_MS 30000

// Batteri-emulering. Den riktiga modulen rapporterar 0-100 % (ZHA-quirken dubblar till 0-200).
// 0x0020 är i 100 mV-enheter. (Testvärde för att se låg-batterivarning i appen.)
#define NIMLY_BATTERY_PERCENT 100 // 0x0021 (startvärde; speglas fran riktiga laset)
// AutoRelockTime = 1 betyder "på" i modellen; den verkliga fördröjningen är kort och fast.
#define NIMLY_AUTOLOCK_ON_SECONDS 7
#define NIMLY_BATTERY_MV100 36    // 0x0020 -> 3.6 V

// Sekunder mellan proaktiva batterirapporter.
#define NIMLY_BATTERY_REPORT_SEC 900

// HA-profil och device id.
#define NIMLY_HA_PROFILE_ID 0x0104
#define NIMLY_DEVICE_ID_DOOR_LOCK 0x000A

// Onesti manufacturer code (placeholder, ej registrerad hos Zigbee Alliance).
#define NIMLY_MFG_CODE 0x1234

// Kluster.
#define NIMLY_CLUSTER_POWER_CFG 0x0001
#define NIMLY_CLUSTER_DOORLOCK 0x0101
#define NIMLY_CLUSTER_MFG 0xFEA2 // manufacturer-specifikt (≥ MIN_CUSTOM)

// Onesti-egna attribut på DoorLock-klustret (mfg code 0x1234).
#define NIMLY_ATTR_OPERATION_EVENT 0x0100 // bitmap32: [slot u16 LE][action][source]
#define NIMLY_ATTR_LAST_PIN 0x0101        // octet string, BCD

// Source codes in the operation event (byte 3). Measured against the vendor cloud on
// 2026-09-20: 0x00 renders as "Key", 0x01 as "Button", 0x02/0x03 as DOORLOCK_UNLOCKED_BY_PIN
// / _BY_FINGER, and 0x0A as DOORLOCK_*_FROM_APP. The cloud records an activity entry for a
// mirrored (HA-initiated) lock/unlock only when the source is 0x00 ("Key"); with 0x0A the
// state updates but no activity entry is created. The mirror therefore keeps 0x00.
#define NIMLY_SRC_ZIGBEE 0x00
#define NIMLY_SRC_KEYPAD 0x02
#define NIMLY_SRC_FINGERPRINT 0x03
#define NIMLY_SRC_RFID 0x04
#define NIMLY_SRC_UNATTRIBUTED 0x05
#define NIMLY_SRC_AUTO 0x0A

// Åtgärder (byte 2).
#define NIMLY_ACTION_LOCK 0x01
#define NIMLY_ACTION_UNLOCK 0x02

// Bygg ett operation-event-värde. slot 0 = system/auto.
static inline uint32_t nimly_operation_event(uint16_t slot, uint8_t action, uint8_t source)
{
    return ((uint32_t)source << 24) | ((uint32_t)action << 16) | (uint32_t)slot;
}

// Basic-klustrets identitet – längdprefixade ZCL-strängar.
// "Onesti Products AS" = 18 tecken -> 0x12 ; "NimlyPRO24" = 10 -> 0x0a.
// (Riktiga Connect Module rapporterar modellen "NimlyPRO24".)
#define NIMLY_MANUFACTURER_NAME "\x12" "Onesti Products AS"
#define NIMLY_MODEL_IDENTIFIER "\x0a" "NimlyPRO24"

// M3: Onesti-attributen läggs på DoorLock via det generiska
// ezb_zcl_cluster_desc_add_manuf_attr() (tar attr_type/access/manuf_code).
// OBS: SDK v2 har ingen core-callback för SetPINCode/GetPINCode/ClearPINCode –
// dessa fångas i stället på rå APS-nivå (se aps_data_indication_handler).
#define NIMLY_ENABLE_MFG_ATTRS 1

// The emulator's IEEE (EUI-64). Leave NIMLY_IEEE_ADDR undefined and the stack
// keeps its own MAC-derived EUI-64, which is unique per board; provision the real
// module's address from Home Assistant instead (nimly.set_ieee), because the
// vendor cloud only accepts the addresses of known modules. A build may still
// hardcode one here if it must - byte order is as sent on the radio
// (little-endian): aa:bb:cc:dd:ee:ff:00:11 -> { 11,00,ff,ee,dd,cc,bb,aa }.
// #define NIMLY_IEEE_ADDR { 0x11, 0x00, 0xff, 0xee, 0xdd, 0xcc, 0xbb, 0xaa }
