# Hemnyckel

**Local control for Nimly locks — no cloud, no vendor app.**

> **Disclaimer — unofficial, no affiliation, use at your own risk.**
> This is an independent hobby/research project. It is **not affiliated with,
> endorsed by, or supported by Nimly, EasyAccess or Onesti Products AS** in any
> way. "Nimly", "Nimly Connect" and "Nimly Connect Bridge" are their trademarks,
> used here only to describe compatibility. It drives a physical door lock —
> **you use it entirely at your own risk** and are solely responsible for access
> to your home.

Hemnyckel is a Home Assistant integration for [Nimly](https://nimly.se) smart locks (Nimly
Touch, Code, Keypad, Pro and the Touch Pro families). The lock's module stays on
ZHA — Home Assistant keeps working when the internet or the vendor app does not —
and this integration adds the parts the lock itself lacks: a slot table, guest
codes with schedules, a journal of who opened the door, and the local services to
manage it all. It is **local-only**; it holds no vendor account and talks to no
cloud service.

This integration is the engine. The household reads it through **Hemnyckel's own iOS app**
([`hemnyckel/ios`](https://github.com/hemnyckel/ios), private, heading for TestFlight): rich
notifications with *who opened the door, when and how*, the history, the guests and lock
control — with no Home Assistant app and no vendor app. The relay in
[`hemnyckel/addon`](https://github.com/hemnyckel/addon) turns this journal into those
notifications. The integration works fully on its own without either of them.

```
        Zigbee
Real lock ─────────►  Home Assistant (this integration)
  module              slot table · journal · guests · services
  (ZHA)
```

- **The integration** ([`custom_components/hemnyckel`](custom_components/hemnyckel)) owns the
  real lock through ZHA: state, PIN codes, fingerprints, settings, history.

The vendor app path — the ESP32 "bridge" and "emulator" boards and the MQTT layer
that kept the Nimly app in sync — has been **removed**. The boards are gone, the
vendor app is retired and the vendor cloud layer was dropped earlier; the real
slot number is the truth now.

## What you get

| | |
|---|---|
| **Local first** | Lock, unlock, codes, settings and history work with no cloud at all. No outage or account problem can take the door away. |
| **Guest codes with schedules** | Temporary codes with an expiry, one-time codes, recurring guests (a cleaner, a nanny) whose code **never changes** but only works inside weekly windows, and permanent codes for family members — stored and restorable, with no window at all. |
| **Several locks** | The guest card discovers every lock and, when there is more than one, offers a lock picker: create a guest once, choose the doors, and one code lands on each — with edit, pause and revoke following the person across locks. |
| **A journal** | One timeline of access and admin events, with a `hemnyckel_door_event` event for your automations. |
| **Slot management** | Name slots, set and clear PINs, enroll fingerprints and wipe credentials, all locally. |
| **Diagnostics and repairs** | An unnamed slot surfaces as a repair instead of silence. |

## Requirements

- Home Assistant **2025.1** or newer.
- A Nimly lock with its module on **ZHA** (the Zigbee integration).

## Installation

1. **HACS** → *Custom repositories* → add `https://github.com/hemnyckel/integration` as
   an *Integration*, then install **Hemnyckel** and restart Home Assistant.
   (Or copy `custom_components/hemnyckel` into your configuration directory.)
2. **Settings → Devices & services → Add integration → Hemnyckel**, pick the
   lock entity (paired in ZHA) and you are done.

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
is written once, stored, and replayed after a loss. Details:
[docs/guests.md](docs/guests.md).

## Services

| Service | Purpose |
|---|---|
| `hemnyckel.create_guest_code`, `hemnyckel.create_recurring_guest`, `hemnyckel.update_guest`, `hemnyckel.revoke_guest_code`, `hemnyckel.list_guests` | Guest codes and their schedules. |
| `hemnyckel.fetch_journal` | The lock's timeline of access and admin events. |
| `hemnyckel.set_pin`, `hemnyckel.clear_slot`, `hemnyckel.set_slot_name` | Local slot management on the real lock. |
| `hemnyckel.read_lock_attributes` | Standard DoorLock attributes (never credentials). |
| `hemnyckel.set_auto_lock`, `hemnyckel.set_sound_volume` | The lock's own settings, written and read back. |
| `hemnyckel.enroll_fingerprint` | Light the lock's fingerprint reader for a slot; the touch — and only a real unlock — proves the template. |
| `hemnyckel.wipe` | Empty every credential slot above the master slots and remove every guest (dry run first, then `confirm: WIPE`). |
| `hemnyckel.clear_repairs` | Delete every repair issue this integration raised. |

## Local by design

The integration never holds a vendor account and never talks to a cloud
service. Everything it knows comes from the lock itself over Zigbee and from the
integration's own store. What leaves the house, if anything, is in
[docs/privacy.md](docs/privacy.md).

## Repository layout

```
custom_components/hemnyckel/   the integration
  mirror/                  the local layer: ZHA link, slot table, journal, guests
tests/                     unit tests (no Home Assistant needed)
docs/                      architecture, guests, privacy, dashboard
www/hemnyckel-guests-card.js  the bundled Lovelace card
tools/                     sync-to-HA helper and the PII check
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — how the local layer fits together and why.
- [docs/guests.md](docs/guests.md) — guest codes, expiry and schedules.
- [docs/matter.md](docs/matter.md) — bridging the lock to Apple Home, Google Home and friends.
- [docs/multi-lock.md](docs/multi-lock.md) — one entry per lock and what that means.
- [docs/privacy.md](docs/privacy.md) — what leaves the house.
- [docs/known-issues.md](docs/known-issues.md) — observed lock quirks.
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
bundled Lovelace card is under the same MIT terms as the project. See [NOTICE](NOTICE).
