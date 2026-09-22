# Nimly firmware

Two small ESP-IDF images keep a Nimly lock on Home Assistant **and** on the vendor
app at the same time. Together with the [integration](../README.md) they form one
product:

| Image | Board | Job |
|---|---|---|
| [`esp32c6-nimly-ed`](esp32c6-nimly-ed/) | ESP32-C6 | Emulates the lock's Connect Module on Zigbee, so the Nimly bridge (and the app) keeps working while the real module stays on ZHA. |
| [`esp32-uart-mqtt-bridge`](esp32-uart-mqtt-bridge/) | ESP32-C3 (or a classic ESP32) | Bridges MQTT ↔ UART to the emulator, ferries firmware updates (dual-OTA on both boards) and offers Improv Wi-Fi setup over Bluetooth. |

The Home Assistant integration talks to the bridge over MQTT; the bridge talks to
the emulator over a plain UART link; the emulator talks Zigbee to the Nimly
Connect Bridge. Nothing in the local path needs the vendor cloud.

## Quick start

1. **Flash the emulator.** Its image contains no credentials and can be written
   straight from a Chromium browser: open [`webflash/`](webflash/) (or the same
   folder on GitHub Pages). Manual flashing with `esptool` and the exact
   offsets are in [docs/flashing.md](../docs/flashing.md).
2. **Build the bridge** with your own Wi-Fi and MQTT credentials:
   `cp main/secrets.h.example main/secrets.h`, fill it in, then
   `idf.py set-target esp32c3 && idf.py build && idf.py -p <port> flash`.
   Its Wi-Fi can also be configured later with Improv over Bluetooth.
3. **Wire the two boards** — UART TX↔RX, RX↔TX and GND; each board powered from
   its own 3V3/USB. See [docs/hardware.md](../docs/hardware.md).
4. **Add the integration** (HACS → custom repository → this repo) and create the
   `cloud` and `mirror` entries. Updates arrive over the air afterwards: the
   integration's update entities read [`webflash/ota.json`](webflash/ota.json).

## Versions

| Image | Source version |
|---|---|
| Emulator (ESP32-C6) | 0.4.9 |
| Bridge (ESP32-C3) | 0.5.1 |

The published OTA manifest is [`webflash/ota.json`](webflash/ota.json); it is also
the integration's default source, and can be repointed to your own copy in the
integration options.

## Layout

```
firmware/
├── esp32c6-nimly-ed/          Zigbee emulator (ESP-IDF project)
├── esp32-uart-mqtt-bridge/    MQTT↔UART bridge + OTA ferry (ESP-IDF project)
└── webflash/                  Browser flashing page, ESP Web Tools manifest,
                               prebuilt emulator image and the OTA manifest
```

Each project keeps its real `main/secrets.h` out of git; commit only changes to
`secrets.h.example`. Build output (`build*/`, `sdkconfig*`, `managed_components/`)
is ignored as well.

## Requirement

ESP-IDF **v5.5.x** with the `esp-zigbee-lib` v2 component (fetched automatically
by the IDF component manager). Both projects build with the plain toolchain:

```bash
idf.py set-target esp32c6   # or esp32c3 for the bridge
idf.py build
```

Protocol details, the ZCL contract and the bring-up sequence live in
[docs/protocol.md](../docs/protocol.md) and
[docs/flashing.md](../docs/flashing.md).
