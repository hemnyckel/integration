# Nimly ED — ESP32-C6 Zigbee emulator

An ESP-IDF project that makes an **ESP32-C6** behave like a Nimly Connect Module
(ZMNC010) towards a Nimly Connect Bridge, while the lock's real module stays on
ZHA in Home Assistant. The bridge, the app and the cloud keep working; the
integration mirrors everything between the two sides.

- ZCL contract and the measured Nimly behaviour: [docs/protocol.md](../../docs/protocol.md)
- Build, flash, wire and OTA: [docs/flashing.md](../../docs/flashing.md)
- Prebuilt image (no credentials) for browser flashing: [../webflash/](../webflash/)

## Requirements

- ESP-IDF **v5.5.x** (the `esp-zigbee-lib` v2 component is fetched by the IDF
  component manager)
- An ESP32-C6 board (DevKitC-1 or similar)
- A Zigbee coordinator in Home Assistant for the real lock (ZHA)

## Build and flash

```bash
idf.py set-target esp32c6
idf.py build
idf.py -p /dev/ttyACM0 flash monitor
```

`main/secrets.h` is optional and gitignored; copy `secrets.h.example` only if you
need to override anything (the emulator itself has no network credentials).

The emulator is provisioned with the lock's own IEEE address from Home Assistant
(`nimly.set_ieee`), so the vendor cloud recognises it as a known module. The
address survives a factory reset in NVS.

## Updates

Firmware updates are ferried over the C3 bridge's UART link (`ota_uart.c`) and
triggered from Home Assistant's update entity — see
[docs/flashing.md](../../docs/flashing.md).
