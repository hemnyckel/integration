# Hardware

The mirror needs two small boards next to the lock's original Nimly Connect
Bridge. The integration itself needs none of this: the lock mirror alone works
with just a Home Assistant instance and the lock on ZHA.

## Bill of materials

| Part | Notes |
|---|---|
| **ESP32-C6** dev board | The emulator. A DevKitC-1 (or any board with the C6's radio) works; it needs USB for flashing only. |
| **ESP32-C3** board | The bridge. A SuperMini is plenty; a classic ESP32 (e.g. KinCony KC868-A6) works too. |
| **ESP32-C6 + C3 carrier** | Optional: a small perfboard, or the project's 3D-printed case, plus a 3.3 V supply for both boards. |
| **The lock's Nimly Connect Bridge** | The vendor's own wall plug, already paired with the lock. It must be factory-reset and re-added once, so the emulator can join it (see [flashing.md](flashing.md)). |
| **The lock, its module on ZHA** | Any Nimly lock supported by the vendor app: Touch, Code, Keypad, Pro, Touch Pro. |
| Wires | Three: two signal, one ground. 3.3 V logic; keep them short. |

The emulator has **no network credentials of its own** — it is a Zigbee device.
Only the bridge stores Wi-Fi and MQTT credentials (in its own `secrets.h`, which
is never committed).

## Wiring

UART, 115200 baud, crossed:

| Emulator (ESP32-C6) | | Bridge (ESP32-C3) |
|---|---|---|
| `GPIO6` (TX) | ──► | `GPIO5` (RX) |
| `GPIO7` (RX) | ◄── | `GPIO4` (TX) |
| `GND` | ──── | `GND` |

On a classic ESP32 bridge the pins are `GPIO12` (TX) and `GPIO13` (RX); the
firmware selects them from the build target automatically (`main/uart_link.c`).
Each board is powered over its own USB or 3V3 pin — never from the other board's
regulator.

```
        ┌───────────────┐                      ┌───────────────┐
        │  ESP32-C6     │  TX GPIO6 ────► RX   │  ESP32-C3     │
        │  emulator     │  RX GPIO7 ◄──── TX   │  bridge       │
        │  (Zigbee)     │  GND ───────── GND   │  (Wi-Fi/MQTT) │
        └───────┬───────┘                      └───────┬───────┘
                │ Zigbee                               │ Wi-Fi/MQTT
                ▼                                      ▼
        Nimly Connect Bridge                   Home Assistant
        (the vendor app)                       (Mosquitto)
```

## Placement

- The **emulator** does not need range beyond the vendor bridge; both usually sit
  in the same room.
- The **bridge** needs its Wi-Fi link to be stable. Give it a good spot: a weak
  2.4 GHz signal is the most common cause of the vendor app briefly showing the
  gateway as offline. A smart plug on the bridge's outlet makes a remote power
  cycle possible, and a Zigbee/Wi-Fi plug that Home Assistant can switch is
  enough.
- The **vendor bridge** itself must stay reachable by the lock's real module? No
  — the real module talks to ZHA, and the emulator talks to the bridge. They do
  not need to be near each other, but both radios should have a sane path to
  their coordinators.

## Panel-mounting

The project's dev setup used a 3D-printed enclosure for the C6 + C3 pair, with
the C6's antenna kept clear and the boards powered from a single USB supply.
Anything that keeps the three wires short and the radios unblocked works.
