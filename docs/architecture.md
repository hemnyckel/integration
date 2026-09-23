# Architecture

The product keeps a Nimly lock on **both** worlds at once: Home Assistant's local
ZHA control and the vendor app. This document explains how the pieces are put
together and why.

## Goals

1. **The lock is local.** Locking, unlocking, codes, settings and access history
   work without the internet. No cloud outage, vendor change or account problem
   can take the door away — and Home Assistant, not the vendor, is the source of
   truth for the schedule-like behaviour the lock itself does not have.
2. **The app keeps working.** The household's app, notifications, guest codes and
   history keep behaving as before. The mirror is invisible when it works.
3. **No silent divergence.** When a vendor-side operation cannot be carried out
   the way the app believes, the integration says so — a journal entry and a
   repair — rather than pretending.

## Pieces

| Part | What it is | Why |
|---|---|---|
| `cloud` entry | The vendor account and its activity feed. | Attribution (*who* opened the door), the app's own view, and set-up convenience. Optional: the local path works without it. |
| `mirror` entry | The local layer: the ZHA link, the slot table, the journal, guest codes and the MQTT link to the emulator. | Everything that must keep working when nothing else does. |
| `bridge` entry | The ESP32-C3 board that carries MQTT and performs firmware updates. | Transport and provisioning; discovered over Bluetooth. |
| Emulator (ESP32-C6) | A Zigbee module wearing the lock's own IEEE address, paired to the Nimly Connect Bridge. | Makes the vendor bridge — and therefore the app and the cloud — believe the original module is still there. |
| The real lock | On ZHA, exactly as any other Zigbee lock. | The truth. |

```
App / cloud  ◄──►  Nimly Connect Bridge  ◄── Zigbee ──►  ESP32-C6 emulator
                                                              │ UART
                                                              ▼
                                                ESP32-C3 bridge ◄──► MQTT
                                                              ▲
                                                              │
                          Real lock ◄── ZHA ──►  Home Assistant integration
```

## The mirror layer

### The ZHA link (`mirror/zha_link.py`)

The lock reports every operation as attribute `0x0100` on the Door Lock cluster:
a bitmap with the slot, the action and the source (keypad, fingerprint, tag, …).
The module sends it *profile-wide*, which the stock ZHA quirk does not match, so
the mirror attaches its own raw listener straight to the zigpy cluster and
re-attaches it on every health tick — a ZHA device rebuild cannot silently kill
attribution.

**Name on join.** The mirror watches the device registry for a device that
carries the module's serial and re-applies the name (and area) the user gave it
in an earlier life — nothing invented, only a remembered identity — so a
factory-reset or re-paired module comes back as "Ytterdörren" instead of the
manufacturer/model default. The cloud side gets the same treatment right after
a registration (`PATCH /devices/{id}`), and the pairing flows do it too.

The relay itself (app events to the real lock and back) is **serial-independent**:
it rides the bridge's UART/MQTT link and ZHA, so app control keeps working even
when the emulator wears a different extended address than the lock's module.
Only the *cloud* mapping (`cloud_device_for`, the audit, the guest sync) keys on
the serial — an emulator whose IEEE does not match the module's is unmapped
there until the address is put back.

Commands go back over the same cluster: `SetPINCode` (0x05), `ClearPINCode`
(0x07), the vendor's fingerprint commands (0x71/0x72), and standard attribute
reads/writes for capabilities and settings. Sleepy locks get one wake-and-retry,
and a read result is always confirmed by reading the attribute back.

Credential material is never read: attribute `0x0101` is refused by the reader
itself, and codes are write-only throughout the integration.

### The slot table (`mirror/slots.py`) and virtualization (`mirror/slot_virtual.py`)

The lock has a slot space that Home Assistant writes to directly, and the vendor
app has its own idea of slot numbers (assigned by the bridge, invisible to
everyone else). Since the app reports success before the bridge even answers, a
refusal only diverges silently — so the mirror never refuses:

- A free slot passes through unchanged.
- A collision with a local credential is **relocated** to a free real slot and
  remembered as `virtual -> real` (identity mappings record the app's own
  slots).
- The lock's own `0x0100` events are translated back through the mapping, so the
  vendor cloud attributes the person it provisioned.
- A clear only touches the app's own credential; a local write can never touch
  an app-owned slot; when nothing can be placed safely, the journal and a repair
  say so.

### The journal (`mirror/journal.py`)

One timeline of access and admin events. A physical unlock appears twice — once
from the lock's own report, once from the cloud's attributed feed — so entries
are merged by time and action instead of stored twice. `nimly_journal_entry` is
fired for automations; the file lives in `.nimly/journal_<entry>.jsonl` with a
retention of 5000 entries / 365 days.

### Guest codes and schedules (`mirror/guests.py`, `mirror/schedule.py`)

The lock has **no schedules at all** — its own module specification says so, and
the schedule attributes answer UNSUPPORTED. Home Assistant is the schedule
keeper instead: a temporary guest code is written when it is created and cleared
when it expires; a recurring guest keeps **one code forever** and the credential
is written when a window opens and cleared when it closes, with the startup pass
repairing whatever a restart missed.

Keeping one code for years means storing it, which is the deliberate cost of the
feature: the code lives in the config entry's options and never in an entity
state or a log line. See [guests.md](guests.md).

## The cloud layer (`cloud/`)

The account layer is a thin client for the same API the official app uses.

- The **activity feed** (`/devices/{id}/features-history`) records a
  `report_event` entry for an action and, next to it, a `lock_state` entry
  carrying the user the cloud attributed it to. The coordinator pairs them and
  fires `nimly_cloud_activity`.
- The feed's clock is **DST-unaware** (an hour ahead through the summer) while
  its server-computed `expires` fields stay true; the difference is derived and
  applied to every stamp, with the raw value kept for diagnostics.
- Settings the app writes (auto-lock, volume) are mirrored onto the lock, and
  the lock's own values are pushed back to the app's record.
- **Entity identity is the module serial** — the account's `serialNumber`, the
  same IEEE address ZHA knows — never the vendor's device id, which changes
  every time the app registers the lock again. A re-registration therefore
  updates the existing entities instead of creating `_2` twins, and older,
  vendor-id keyed entries are migrated onto the serial at cloud setup.
- Upkeep at setup (also on demand through **`nimly.cleanup_cloud`**, with
  `dry_run`): entities left behind by a device the account no longer lists are
  removed, together with the device entry of a vendor id that is gone. Each
  config entry owns its own device (Home Assistant 2026 keeps one config entry
  per device), so the cloud entities stay on the cloud entry's device; its name
  is resolved *before* entities are created — the user's name for the device
  that owns the serial (the ZHA device, typically) wins over the vendor's
  default and is cached in the entry options.
- Auth is OAuth2 password grant with a rotating refresh token; refresh is
  single-flight because the provider invalidates the old token. A rejected
  credential raises `ConfigEntryAuthFailed`, which opens the reauthentication
  flow instead of retrying forever.

## The firmware

Two ESP-IDF images (see [../firmware/](../firmware/)):

- The **emulator** answers the vendor bridge's Zigbee requests as the lock's
  module, reports the lock's state and events, and mirrors app-side changes back
  to Home Assistant.
- The **bridge** carries MQTT ↔ UART, offers Improv Wi-Fi setup, and performs
  dual-OTA on both boards (the C6 is updated through the C3's UART ferry).

The MQTT topics, the UART line format and the ZCL contract are in
[protocol.md](protocol.md).

## Multi-lock

One Nimly Connect Bridge accepts **two** modules. Locks one and two can run the
full stack (ZHA + emulator); a third lock is local-only — it keeps every Home
Assistant feature and loses only the app-side conveniences. The integration is
written for several mirrors: repair issues are per entry, channels and slots are
per lock, and the journal and guests are per entry.

## Known limits

- The vendor app does not know about schedules: it shows a recurring guest's
  code as always valid. The lock does not.
- A code *edit* in the vendor app reaches the module with a quirk: the bridge
  sometimes restarts during it (vendor firmware behaviour). The edit still
  lands; HA edits never involve the bridge.
- The cloud API is unofficial and can change without notice. The local path is
  unaffected if it does.
