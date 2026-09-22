# esp32-uart-mqtt-bridge

The Wi-Fi/MQTT half of the Nimly bridge: connects **MQTT (Home Assistant) ↔ UART ↔
ESP32-C6** (the Zigbee emulator), and ferries firmware images to the emulator over
that same UART link.

One code base supports two targets:

| Target | Board | UART TX | UART RX |
|---|---|---|---|
| `esp32c3` | ESP32-C3 SuperMini | GPIO4 | GPIO5 |
| `esp32` | KinCony KC868-A6 (ESP32-WROOM-32) | GPIO12 | GPIO13 |

The pins are chosen in `main/uart_link.c` from `CONFIG_IDF_TARGET_*`. Wiring and the
hardware list: [docs/hardware.md](../../docs/hardware.md).

## Configure and build

```bash
cp main/secrets.h.example main/secrets.h   # Wi-Fi + MQTT (+ optional extras)
idf.py set-target esp32c3
idf.py build
idf.py -p /dev/ttyUSB0 flash
```

`main/secrets.h` is gitignored and must never be committed — the built image
embeds those credentials. The bridge announces itself on the retained MQTT topic
`nimly/info`, which is how the integration discovers the prefix and firmware. Its
Wi-Fi can also be set with **Improv over Bluetooth** (the integration's discovery
card does it for you).

## Protocol

The MQTT topics and the UART line format are documented in
[docs/protocol.md](../../docs/protocol.md). The bridge is also the OTA ferry: it
downloads an image and pushes it to the C6 in chunks, with a rollback-safe
dual-OTA layout on both boards.
