# People and codes

A code is a PIN written to the lock for one person. The lock can hold 50 PINs;
a person is an ordinary user-slot credential.

Four kinds exist:

| Kind | Behaviour |
|---|---|
| **Temporary** | Valid until a timestamp; Home Assistant clears it at that moment and repairs the state after a restart. The code is shown once and never stored. |
| **One-time** | Revoked by itself the first time its slot opens the door. |
| **Recurring** | Valid inside weekly windows. The code **never changes**; the credential is written when a window opens and cleared when it closes. |
| **Permanent** | Always valid — no expiry, no window. The code is **stored** so it can be replayed; the family-member case: a PIN (and a finger) without the vendor app. |

## Creating one

From the bundled card ([dashboard.md](dashboard.md)) in three taps, or from the
services:

```yaml
# Temporary, two hours from now
action: hemnyckel.create_guest_code
data:
  name: "Courier"
  until: "2026-10-01T18:00:00+02:00"

# One-time
action: hemnyckel.create_guest_code
data:
  name: "Move-in help"
  one_time: true

# Recurring: same code, Monday and Friday mornings, forever
action: hemnyckel.create_recurring_guest
data:
  name: "Cleaner"
  schedule:
    - days: [mon, fri]
      start: "08:00"
      end: "12:00"
```

```yaml
# Permanent: a family member; the code is stored and never cleared
action: hemnyckel.create_guest_code
data:
  name: "Alva"
  permanent: true
```

The service **returns the code once** — hand it to the person. For temporary and
one-time people the code is never stored anywhere; for a recurring or permanent
person it is stored in the config entry's options, because the same digits have
to come back — on every window, or after a loss. That storage is the deliberate
cost of a fixed code (see [privacy.md](privacy.md)).

The card makes that choice visible before anything is written: a temporary code
**visas bara en gång** and is not stored, a permanent one is **sparad och kan
återställas** — choose Permanent when a cleaning firm's code must survive a lost
or cleared lock.

## Schedules

A window is a list of days (`mon`…`sun`) with a start and end in local time. An
end at or before the start crosses midnight, so `fri 22:00-02:00` also covers
Saturday night. Several windows per person are fine:

```yaml
schedule:
  - days: [fri]
    start: "08:00"
    end: "12:00"
  - days: [mon, wed]
    start: "13:00"
    end: "15:00"
```

Edit them with `hemnyckel.update_guest` (or the card): the change takes effect
immediately — a window that just opened writes the code, one that just closed
clears it. `paused: true` clears the code and stops the schedule until resumed.

## What the lock knows

- The **lock** has no schedules at all (the vendor's own module specification:
  *"No schedules, no user status"*), so enforcement is Home Assistant's. While
  Home Assistant is up, a code exists in the lock exactly inside its window and
  offline use inside the window works.
- Trying a code outside its window counts as a failed attempt on the lock
  (that is what keeps the lockout counter honest) — tell the person the hours.

## If Home Assistant was down

A window edge that was missed while Home Assistant was off is repaired at
startup: an open window writes the stored code again, a closed one clears it.
Only a window edge during downtime means the code can outlive its window until
the next start — the one bounded gap of the design.

## Managing people

```yaml
action: hemnyckel.list_guests          # live state per person (never the codes)
action: hemnyckel.update_guest         # name, code, schedule, pause, expiry
data:
  slot: 5
  paused: true

action: hemnyckel.revoke_guest_code    # clears the code and forgets the person
data:
  slot: 5
```

`hemnyckel.update_guest` can also turn a temporary person into a recurring one,
but only when it is given a code — a temporary person never stored its own. A
permanent person keeps its code: rename it or change the code the same way, and
it never expires on its own.

## Fingerprint enrollment from Home Assistant

`hemnyckel.enroll_fingerprint` lights the lock's reader for one slot. It is sent
straight to the lock, and the lock reports nothing while the enrollment runs — a
template exists only once that finger has really opened the door, which is why
`finger_used` (not
the enrollment) is the evidence the catalog trusts. The card's fingerprint
button calls the same service.

The card marks every row twice over. A **key**: solid (green) when the
catalog holds the PIN value itself — a recurring or permanent person — so the
code can be replayed after a loss, and struck through when it does not (a
temporary person's code is shown once and never stored). A **fingerprint**:
solid when a finger in the slot has
really opened the door, so the slot can be reused when restoring, and struck
through when the finger was enrolled but never used — an enrollment proves
nothing about the template the lock holds, so it is never replayed on faith.
A person with no linked finger shows no fingerprint at all. RFID is deliberately
never marked: the lock reports no tag use (see [known-issues.md](known-issues.md)).
