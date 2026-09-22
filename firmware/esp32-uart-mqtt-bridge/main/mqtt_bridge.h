// SPDX-License-Identifier: MIT
// WiFi + MQTT-bro för Nimly-emulatorn. Se docs/bridge-arkitektur.md.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Startar WiFi (station) + MQTT-klient. No-op om ingen SSID är konfigurerad i secrets.h.
void mqtt_bridge_start(void);

// Publicerar C6:ns Zigbee-sida till HA.
void mqtt_bridge_publish_state(bool locked);

// Publicerar batteriprocent till nimly/proxy/battery (retainad).
void mqtt_bridge_publish_battery(int percentage);

// Publicerar PIN-händelser (pin_set/pin_clear) till nimly/proxy/pin.
void mqtt_bridge_publish_pin(const char *json);

// Publicerar en rå rad från C6:an (UART) till nimly/proxy/bridge_to_ha.
void mqtt_bridge_publish_from_c6(const char *json);

// Publicerar (retainat) bryggans identitet på det välkända nimly/info, så att
// nimly_shadow-wizarden hittar emulatorn automatiskt.
void mqtt_bridge_publish_info(void);

// Publicerar OTA-status (downloading/done/error) på nimly/proxy/ota.
void mqtt_bridge_publish_ota(const char *json);

// WiFi-provisionering (Improv): sätter nya uppgifter (sparas i NVS) och återansluter.
void mqtt_bridge_wifi_apply(const char *ssid, const char *pass);

// Sant när WiFi-stationen har IP.
bool mqtt_bridge_wifi_connected(void);

// Sant när WiFi-uppgifter finns (NVS eller secrets.h).
bool mqtt_bridge_wifi_configured(void);

// Registrerar callback för inkommande JSON på nimly/proxy/ha_to_bridge (HA -> C6).
void mqtt_bridge_set_command_handler(void (*cb)(const char *json, int len));

// Registrerar callback som körs när MQTT anslutit (t.ex. begär state-resync från C6).
void mqtt_bridge_set_connect_handler(void (*cb)(void));

#ifdef __cplusplus
}
#endif
