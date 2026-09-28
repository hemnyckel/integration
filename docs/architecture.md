# Architecture

The integration keeps a Nimly lock on Home Assistant's **local** ZHA control. This
document explains how the pieces are put together and why. The vendor-cloud layer and
the vendor app path (the bridge and the emulator) have both been removed: the local
path never depended on either.

## Goals

1. **The lock is local.** Locking, unlocking, codes, settings and access history
   work without the internet. No outage or account problem can take the door
   away — and Home Assistant, not a vendor, is the source of truth for the
   schedule-like behaviour the lock itself does not have.
2. **No silent divergence.** When an operation cannot be carried out, the
   integration says so — a journal entry and, where a person can act, a repair —
   rather than pretending.

## Pieces

| Part | What it is | Why |
|---|---|---|
| `mirror` entry | The local layer: the ZHA link, the slot table, the journal and codes for people. | Everything that must keep working when nothing else does. |
| The real lock | On ZHA, exactly as any other Zigbee lock. | The truth. |

```
Real lock ◄── ZHA ──►  Home Assistant integration
```

## The local layer

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
manufacturer/model default.

Commands go back over the same cluster: `SetPINCode` (0x05), `ClearPINCode`
(0x07), the vendor's fingerprint commands (0x71/0x72), and standard attribute
reads/writes for capabilities and settings. Sleepy locks get one wake-and-retry,
and a read result is always confirmed by reading the attribute back.

Credential material is never read: attribute `0x0101` is refused by the reader
itself, and codes are write-only throughout the integration.

The lock's own device name is the source of the integration's device name, so the
entity ids read like the door — `sensor.ytterdorren_journal` — rather than
carrying a module prefix.

### The slot table and the finger policy (`mirror/slots.py`, `mirror/fingers.py`)

The lock has a slot space that Home Assistant writes to directly. The slot table
records, per slot, the person's name and which credential types it holds (PIN,
fingerprint, tag), plus the owner's **labels** for its fingers. A fingerprint is
one template per slot (the lock refuses a second enrolment with a red blink),
and it is only trusted once a finger in that slot has actually opened the door:
an enrollment proves nothing, because the lock reports nothing while it runs.
Clearing a slot clears **both** its PIN and its fingerprint template, so the
slot is really free for the next enrolment. The pure `mirror/fingers.py` holds
the one-template rule, the display state and the capacity arithmetic. When the
lock is used with an unnamed slot, the integration asks once for a name through
a repair instead of guessing.

### The journal (`mirror/journal.py`)

One timeline of access and admin events from the lock's own reports and the
integration's admin actions. Entries for the same physical event are merged by
time and action instead of stored twice. `hemnyckel_door_event` is fired for
automations; the file lives in `.hemnyckel/journal_<entry>.jsonl` with a retention of
5000 entries / 365 days.

### People, codes and schedules (`mirror/guests.py`, `mirror/schedule.py`)

The lock has **no schedules at all** — its own module specification says so, and
the schedule attributes answer UNSUPPORTED. Home Assistant is the schedule
keeper instead: a temporary person's code is written when it is created and
cleared when it expires; a recurring person keeps **one code forever** and the
credential is written when a window opens and cleared when it closes, with the
startup pass repairing whatever a restart missed. A permanent person is that
same kept code without any window: written once, never cleared.

Keeping one code for years means storing it, which is the deliberate cost of the
feature: the code lives in the config entry's options and never in an entity
state or a log line. See [guests.md](guests.md).

## Multi-lock

The integration is written for several locks: one mirror entry per lock, repair
issues and slots and people all per entry. A second lock is just a second entry.

## Known limits

- The lock does not know about schedules; the integration enforces them while it
  is running, and repairs a missed window edge at the next start.
