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

## What is stored where

| Where | What | Why |
|---|---|---|
| Config entry options | Slot names and person definitions (and, for recurring and permanent people, their code) | Local behaviour and schedules. |
| `.hemnyckel/journal_<entry>.jsonl` | The access and admin timeline | The journal sensor and `fetch_journal`. |

Everything is part of a normal Home Assistant backup and never leaves the
installation on its own.
