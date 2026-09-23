# Privacy

This integration is built so that a home can run the lock **without any external
service**. The cloud layer is optional and only talks to the vendor's own API,
with the account owner's own credentials.

## What leaves the house

Only the `cloud` entry talks to the internet, and only to the vendor API
(`api-neutralclone.iotiliti.cloud`) that the official Nimly app already uses:

- **Sign-in** with the email and password the owner types in the config flow
  (OAuth2 password grant, exchanged for a rotating refresh token).
- **Polls** of the account, its locations, devices, access lists and the
  device's feature history — the same requests the app makes.
- **Commands** when the user chooses a cloud action (`nimly.set_lock`,
  `nimly.gateway_scan`) or when a setting is mirrored back to the app's record
  (the lock's own auto-lock/volume values).
- The location's company identifier is sent as a header on history requests, as
  the app does.

Nothing else is transmitted. There is no telemetry, no analytics, no update
check against any server of this project's own.

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
| Config entry data | Vendor tokens, location id | Keeping the session alive across restarts. |
| Config entry options | Slot names, the slot map, guest definitions (and, for recurring and permanent guests, their code), channel switches | Local behaviour and schedules. |
| `.nimly/journal_<entry>.jsonl` | The access and admin timeline | The journal sensor and `fetch_journal`. |
| The firmware | Wi-Fi and MQTT credentials, on the bridge only | It has to connect; the emulator stores none. |

Everything is part of a normal Home Assistant backup and never leaves the
installation on its own.

## Running without the cloud

Skip the `cloud` entry entirely. The mirror keeps the lock fully usable and the
app keeps working through the emulator; the only losses are the vendor's
attribution (who opened the door when Zigbee alone cannot say) and the app-side
record keeping.
