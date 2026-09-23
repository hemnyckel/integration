# Nimly

**Local control and the vendor app, at the same time.**

A Home Assistant integration for [Nimly](https://nimly.se) smart locks (Nimly
Touch, Code, Keypad, Pro and the Touch Pro families). The lock's module stays on
ZHA — Home Assistant keeps working when the internet, the vendor cloud or the
Nimly app does not — and two small ESP32 boards let the lock live on in the
vendor app as if nothing changed: same app, same notifications, same guest
codes, same history.

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
                          (local, always)                (vendor app + cloud)
```

- **The integration** ([`custom_components/nimly`](custom_components/nimly)) owns the
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
| **Guest codes with schedules** | Temporary codes with an expiry, one-time codes, and recurring guests (a cleaner, a nanny) whose code **never changes** but only works inside weekly windows. |
| **Cloud insight (optional)** | Sign in with the vendor account for the app's attributed history: *who* opened the door when Zigbee alone cannot say. |
| **Two-way cloud sync** | Guests created here get a vendor identity and a PIN access automatically — and app-created PINs, tags and fingerprints are paired back to the slot they live in. A **Cloud sync** switch pauses the automatic side. |
| **Several locks** | The guest card discovers every lock and, when there is more than one, offers a lock picker: create a guest once, choose the doors, and one code lands on each — with edit, pause and revoke following the person across locks. |
| **Restore after a loss** | `nimly.restore_cloud` replays the catalog onto the cloud: PINs with a stored value are re-created, fingerprints are re-recorded through the emulator (the lock holds the template), and anything only the guest knows is reported instead of guessed. `nimly.audit` shows the drift first. |
| **Slot virtualization** | App-created credentials never collide with local ones, and vice versa — the app keeps its own slot numbers while the lock keeps its own secrets. |
| **A journal** | One timeline of access and admin events, local and cloud merged, with a `nimly_journal_entry` event for your automations. |
| **OTA both ways** | The bridge and the emulator update over the air from Home Assistant. |
| **Diagnostics and repairs** | Stale bridge, unpaired emulator, cloud feedback and slot conflicts surface as repairs instead of silence. |

## Requirements

- Home Assistant **2025.1** or newer.
- A Nimly lock with its module on **ZHA** (the Zigbee integration).
- For the app bridge: an **ESP32-C6** board and an **ESP32-C3** board (or a
  classic ESP32), plus the lock's original **Nimly Connect Bridge**. The
  hardware list and wiring are in [docs/hardware.md](docs/hardware.md).
- The integration works **without the boards too**: the `cloud` entry alone
  gives you the vendor account's state and history.

## Installation

1. **HACS** → *Custom repositories* → add `https://github.com/c14ym0re/nimly` as
   an *Integration*, then install **Nimly** and restart Home Assistant.
   (Or copy `custom_components/nimly` into your configuration directory.)
2. **Settings → Devices & services → Add integration → Nimly**, and pick a path:
   - **Nimly account** — email and password of the vendor app. Gives history,
     attribution and the app's view of devices and settings.
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

Create a guest from the `nimly-guests-card` (included, see
[docs/dashboard.md](docs/dashboard.md)) or from the services:

```yaml
action: nimly.create_guest_code      # one-shot code with an expiry
data:
  name: "Anna"
  until: "2026-10-01T18:00:00+02:00"
```

```yaml
action: nimly.create_recurring_guest # a cleaner: same code, weekly windows
data:
  name: "Cleaner"
  schedule:
    - days: [mon, fri]
      start: "08:00"
      end: "12:00"
```

A recurring guest's code never changes: Home Assistant writes it when a window
opens and clears the credential when it closes, and repairs the state after a
restart. The vendor app shows the code as always valid — the schedule is
enforced locally. Details: [docs/guests.md](docs/guests.md).

## Services

| Service | Purpose |
|---|---|
| `nimly.set_lock` | Lock or unlock through the vendor cloud. |
| `nimly.fetch_history`, `nimly.refresh` | Cloud history and an on-demand poll. |
| `nimly.create_guest_code`, `nimly.create_recurring_guest`, `nimly.update_guest`, `nimly.revoke_guest_code`, `nimly.list_guests` | Guest codes and their schedules. |
| `nimly.fetch_journal` | The merged local+cloud journal. |
| `nimly.set_pin`, `nimly.clear_slot`, `nimly.set_slot_name` | Local slot management on the real lock. |
| `nimly.read_lock_attributes` | Standard DoorLock attributes (never credentials). |
| `nimly.set_auto_lock`, `nimly.set_sound_volume` | The lock's own settings, read back and mirrored to the app. |
| `nimly.ota_install`, `nimly.provision_wifi`, `nimly.set_ieee` | Firmware and provisioning. |
| `nimly.gateway_scan`, `nimly.probe` | Vendor-side discovery helpers. |
| `nimly.cloud_guests` | Read the account's guest users (the app's "Guest user list"), optionally per lock: where each guest's credentials really live (on this lock, on another, or nowhere). |
| `nimly.update_cloud_guest` | Edit an app guest: name, validity window, contact details. |
| `nimly.delete_cloud_guest` | Remove an app guest the way the app does: every access on every live lock first, then the identity. |
| `nimly.set_cloud_code` | Set or replace a PIN or tag of an app guest on one lock; a change writes the value into the lock first, so it lands in the right slot. |
| `nimly.enroll_fingerprint` | Light the lock's fingerprint reader for a slot (cloud path when the guest is synced, so the app records the access too); the touch — and only a real unlock — proves the template. |
| `nimly.link_cloud_guest` | Record which vendor identity one of our slots belongs to — the human answer to an adoption conflict. |
| `nimly.sync_cloud` | Reconcile local guests to the cloud: identity + PIN access (`dry_run` first, identity adoption by name, slot binding so a code is never written twice). |
| `nimly.restore_cloud` | Replay the catalog after a loss (identities, PINs, fingerprints; reports what only the guest can restore). |
| `nimly.audit` | Read-only drift report across lock, catalog and cloud. |
| `nimly.link_credential` | Tie a slot's credential to a vendor user (pin, tag or finger) when a link needs a human. |
| `nimly.cleanup_cloud` | Align the registry with the account: migrate, prune and rename (`dry_run` supported). Runs automatically at cloud setup. |

## The vendor cloud, honestly

The `cloud` entry talks to the same API as the official app, with the account
owner's own credentials. Nothing is sent anywhere else, no telemetry exists,
and **the local path never depends on the cloud**: if the vendor changes or
closes their API, your lock keeps working. What is sent and why:
[docs/privacy.md](docs/privacy.md).

The lock and this integration are the truth; the cloud is a view of it. Which
parts can be re-created after a crash, a module swap or a lost lock — and the
honest limits of each — is in [docs/cloud-sync.md](docs/cloud-sync.md).

## Repository layout

```
custom_components/nimly/   the integration
  cloud/                   the vendor account layer
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
- [docs/cloud-sync.md](docs/cloud-sync.md) — the lock/HA/cloud truth model, sync directions and the restore matrix.
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
