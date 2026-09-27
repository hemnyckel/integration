# Privacy

This integration is built so that a home can run the lock **without any external
service**. It holds no vendor account and talks to no cloud API.

## What leaves the house

The integration itself sends nothing to the internet. Everything it knows comes
from the lock over Zigbee and from its own local store. There is no telemetry,
no analytics and no update check against any server of this project's own.

If you also run the emulator/bridge hardware, the emulator presents the lock to
the Nimly Connect Bridge over Zigbee — that is the vendor app's own path and is
outside this integration. The bridge connects only to your MQTT broker and
carries no vendor account or credentials.

## What never leaves

- **PIN codes and fingerprints**: credential material is write-only. The
  integration refuses to read attribute `0x0101`, never logs a code, and the
  guest-code services return a code once and store only what a schedule needs
  (see [guests.md](guests.md)).
- **Lock identity in the clear**: nothing is published anywhere; the IEEE
  addresses stay inside the boards, Home Assistant and its backups.

## What is stored where

| Where | What | Why |
|---|---|---|
| Config entry options | Slot names, the slot map, guest definitions (and, for recurring and permanent guests, their code), channel switches | Local behaviour and schedules. |
| `.hemnyckel/journal_<entry>.jsonl` | The access and admin timeline | The journal sensor and `fetch_journal`. |
| The firmware | Wi-Fi and MQTT credentials, on the bridge only | It has to connect; the emulator stores none. |

Everything is part of a normal Home Assistant backup and never leaves the
installation on its own.

## Running without the bridge

The mirror keeps the lock fully usable without any extra hardware; the emulator
only adds the app-side convenience.
