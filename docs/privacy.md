# Privacy

This integration is built so that a home can run the lock **without any external
service**. It holds no vendor account and talks to no cloud API.

## What leaves the house

The integration itself sends nothing to the internet. Everything it knows comes
from the lock over Zigbee and from its own local store. There is no telemetry,
no analytics and no update check against any server of this project's own.

## What never leaves

- **PIN codes and fingerprints**: credential material is write-only. The
  integration refuses to read attribute `0x0101`, never logs a code, and the
  code services return a code once and store only what a schedule needs
  (see [guests.md](guests.md)).
- **Lock identity in the clear**: nothing is published anywhere; the IEEE
  address stays inside Home Assistant and its backups.

## Person icons

A person's icon is the relay's, not the integration's: the relay publishes the
icon's descriptor (`kind`, `symbol`, `colour`, `version`) over MQTT, and — for a
photo — mirrors the JPEG to `/share/hemnyckel/avatars/<person_id>.jpg` (its
add-on maps `share:rw`). The integration serves that file at
`/api/hemnyckel/avatar/<id>` **behind Home Assistant's own authentication**, so a
photo is only ever fetched by a signed-in Home Assistant user, never from an open
URL and never over MQTT. A monogram or a symbol has no bytes to serve (the card
draws it from the descriptor), and a relay guest has no Home Assistant account
and still sees no family. Nothing about an icon leaves the house.

## What is stored where

| Where | What | Why |
|---|---|---|
| Config entry options | Slot names and person definitions (and, for recurring and permanent people, their code) | Local behaviour and schedules. |
| `.hemnyckel/journal_<entry>.jsonl` | The access and admin timeline | The journal sensor and `fetch_journal`. |

Everything is part of a normal Home Assistant backup and never leaves the
installation on its own.
