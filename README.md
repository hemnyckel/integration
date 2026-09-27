# Hemnyckel

**Local control for Nimly locks — with the vendor app kept alive.**

> **Disclaimer — unofficial, no affiliation, use at your own risk.**
> This is an independent hobby/research project. It is **not affiliated with,
> endorsed by, or supported by Nimly, EasyAccess or Onesti Products AS** in any
> way. "Nimly", "Nimly Connect" and "Nimly Connect Bridge" are their trademarks,
> used here only to describe compatibility. It drives a physical door lock and
> involves reflashing ESP32 boards — **you use it entirely at your own risk**
> and are solely responsible for access to your home.

Hemnyckel is a Home Assistant integration for [Nimly](https://nimly.se) smart locks (Nimly
Touch, Code, Keypad, Pro and the Touch Pro families). The lock's module stays on
ZHA — Home Assistant keeps working when the internet or the Nimly app does not —
and two small ESP32 boards let the lock live on in the vendor app as if nothing
changed: same app, same notifications, same guest codes, same history. The
integration itself is **local-only**; it holds no vendor account and talks to no
cloud service.

The product is three pieces that behave as one:

```
                 Zigbee                 UART                 MQTT
Real lock  ───────────────►  Home Assistant  ◄──────────►  ESP32-C3 bridge
  module                     (this integration)                 │
  (ZHA)                           │       ▲                     │ UART
                                  │       │                     ▼
                                  │       │            ESP32-C6 emulator
                                  │       │                     │
                                  │       └──── Zigbee ─────────┘
                                  │                             │
                                  ▼                             ▼
                          the real lock                 Nimly Connect Bridge
                          (local, always)                (the vendor app)
```

- **The integration** ([`custom_components/hemnyckel`](custom_components/hemnyckel)) owns the
  real lock through ZHA: state, PIN codes, fingerprints, settings, history.
- **The emulator** ([`firmware/esp32c6-nimly-ed`](firmware/esp32c6-nimly-ed)) is a
  Zigbee module with the lock's own IEEE address, paired to the Nimly Connect
  Bridge. Everything Home Assistant does on the real lock is mirrored to it, so
  the app still sees the lock, its users and its events.
- **The bridge** ([`firmware/esp32-uart-mqtt-bridge`](firmware/esp32-uart-mqtt-bridge))
  carries MQTT between Home Assistant and the emulator, and ferries firmware
  updates over the air.

## What you get

| | |
|---|---|
| **Local first** | Lock, unlock, codes, settings and history work with no cloud at all. The vendor app is a convenience, never a dependency. |
| **The app keeps working** | Notifications, who-unlocked history, guest codes and settings stay in sync through the emulator. |
| **Guest codes with schedules** | Temporary codes with an expiry, one-time codes, recurring guests (a cleaner, a nanny) whose code **never changes** but only works inside weekly windows, and permanent codes for family members — stored and restorable, with no window at all. |
| **Several locks** | The guest card discovers every lock and, when there is more than one, offers a lock picker: create a guest once, choose the doors, and one code lands on each — with edit, pause and revoke following the person across locks. |
| **Slot virtualization** | App-created credentials never collide with local ones, and vice versa — the app keeps its own slot numbers while the lock keeps its own secrets. |
| **A journal** | One timeline of access and admin events, with a `hemnyckel_door_event` event for your automations. |
| **OTA both ways** | The bridge and the emulator update over the air from Home Assistant. |
| **Diagnostics and repairs** | Stale bridge, unpaired emulator and slot conflicts surface as repairs instead of silence. |

## Requirements

- Home Assistant **2025.1** or newer.
- A Nimly lock with its module on **ZHA** (the Zigbee integration).
- For the app bridge: an **ESP32-C6** board and an **ESP32-C3** board (or a
  classic ESP32), plus the lock's original **Nimly Connect Bridge**. The
  hardware list and wiring are in [docs/hardware.md](docs/hardware.md).

## Installation

1. **HACS** → *Custom repositories* → add `https://github.com/hemnyckel/integration` as
   an *Integration*, then install **Hemnyckel** and restart Home Assistant.
   (Or copy `custom_components/hemnyckel` into your configuration directory.)
2. **Settings → Devices & services → Add integration → Hemnyckel**, and pick a path:
   - **Lock mirror** — the local lock. Pick the lock entity (ZHA) and the MQTT
     prefix (auto-detected from the bridge when one is online), then choose how
     much to mirror.
   - **Bridge** — appears by itself over Bluetooth when a bridge board is
     unprovisioned; the card sets up its Wi-Fi with Improv.
3. **Flash the firmware** if you want the app bridge: see
   [firmware/](firmware/) — the emulator can be flashed straight from a
   Chromium browser, the bridge is built with your own Wi-Fi/MQTT credentials.

The full walkthrough, including pairing the emulator with the vendor bridge,
is in [docs/flashing.md](docs/flashing.md).

## Guest codes

Create a guest from the `hemnyckel-guests-card` (included, see
[docs/dashboard.md](docs/dashboard.md)) or from the services:

```yaml
action: hemnyckel.create_guest_code      # one-shot code with an expiry
data:
  name: "Anna"
  until: "2026-10-01T18:00:00+02:00"
```

```yaml
action: hemnyckel.create_recurring_guest # a cleaner: same code, weekly windows
data:
  name: "Cleaner"
  schedule:
    - days: [mon, fri]
      start: "08:00"
      end: "12:00"
```

A recurring guest's code never changes: Home Assistant writes it when a window
opens and clears the credential when it closes, and repairs the state after a
restart. A permanent guest (`permanent: true`) has no window at all — the code
is written once, stored, and replayed after a loss. The vendor app shows both
as always valid; the schedule is enforced locally. Details:
[docs/guests.md](docs/guests.md).

## Services

| Service | Purpose |
|---|---|
| `hemnyckel.create_guest_code`, `hemnyckel.create_recurring_guest`, `hemnyckel.update_guest`, `hemnyckel.revoke_guest_code`, `hemnyckel.list_guests` | Guest codes and their schedules. |
| `hemnyckel.fetch_journal` | The lock's timeline of access and admin events. |
| `hemnyckel.set_pin`, `hemnyckel.clear_slot`, `hemnyckel.set_slot_name` | Local slot management on the real lock. |
| `hemnyckel.read_lock_attributes` | Standard DoorLock attributes (never credentials). |
| `hemnyckel.set_auto_lock`, `hemnyckel.set_sound_volume` | The lock's own settings, read back and mirrored to the app. |
| `hemnyckel.enroll_fingerprint` | Light the lock's fingerprint reader for a slot; the touch — and only a real unlock — proves the template. |
| `hemnyckel.wipe` | Empty every credential slot above the master slots and remove every guest (dry run first, then `confirm: WIPE`). |
| `hemnyckel.clear_repairs` | Delete every repair issue this integration raised. |
| `hemnyckel.ota_install`, `hemnyckel.provision_wifi`, `hemnyckel.set_ieee` | Firmware and provisioning. |

## Local by design

The integration never holds a vendor account and never talks to a cloud
service. Everything it knows comes from the lock itself over Zigbee and from the
integration's own store. What leaves the house, if anything, is in
[docs/privacy.md](docs/privacy.md).

## Repository layout

```
custom_components/hemnyckel/   the integration (local mirror + bridge)
  mirror/                  the local layer: ZHA link, slot table, journal, guests
firmware/                  the two ESP-IDF projects and browser flashing
tests/                     unit tests (no Home Assistant needed)
docs/                      architecture, hardware, flashing, protocol, guests
tools/                     sync-to-HA helper and the PII check
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — how the layers fit together and why.
- [docs/hardware.md](docs/hardware.md) — bill of materials and wiring.
- [docs/flashing.md](docs/flashing.md) — build, flash, pair, update.
- [docs/protocol.md](docs/protocol.md) — the MQTT topics and UART line format.
- [docs/guests.md](docs/guests.md) — guest codes, expiry and schedules.
- [docs/matter.md](docs/matter.md) — bridging the lock to Apple Home, Google Home and friends.
- [docs/privacy.md](docs/privacy.md) — what leaves the house.
- [docs/dashboard.md](docs/dashboard.md) — the bundled guest-code card.

## Development

```bash
python3 -m compileall -q custom_components   # syntax
python3 -m unittest discover -s tests        # unit tests (no Home Assistant)
python3 tools/check_pii.py                   # no real identifiers or secrets
tools/sync_to_ha.sh /path/to/homeassistant/config
```

Contributions are welcome — read [CONTRIBUTING.md](CONTRIBUTING.md) first.
Security reports: [SECURITY.md](SECURITY.md).

## License

The integration is licensed under **Apache-2.0** ([LICENSE](LICENSE)); the
firmware under **MIT** (SPDX headers in each file). See [NOTICE](NOTICE).
