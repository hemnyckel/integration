// SPDX-License-Identifier: MIT
// OTA: hämtar en firmware-binär över HTTP, skriver till nästa app-partition och startar om.
// Anropas av HA via MQTT: {"cmd":"ota","url":"http://…/nimly-bridge.bin"}.
#pragma once

// Startar en bakgrundsuppgift som laddar ner och flashar url. Rapporterar till
// nimly/proxy/ota. Enheten startar om själv när det lyckats.
void ota_request(const char *url);

// Bekräftar en ny firmware efter uppstart (avväpnar rollback). Anropas tidigt i app_main.
void ota_mark_valid(void);
