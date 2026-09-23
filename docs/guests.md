# Guest codes

A guest code is a PIN written to the lock for someone who is not part of the
household. The lock can hold 50 PINs; guests do not need a vendor-app user.

Three kinds exist:

| Kind | Behaviour |
|---|---|
| **Temporary** | Valid until a timestamp; Home Assistant clears it at that moment and repairs the state after a restart. |
| **One-time** | Revoked by itself the first time its slot opens the door. |
| **Recurring** | Valid inside weekly windows. The code **never changes**; the credential is written when a window opens and cleared when it closes. |

## Creating one

From the bundled card ([dashboard.md](dashboard.md)) in three taps, or from the
services:

```yaml
# Temporary, two hours from now
action: nimly.create_guest_code
data:
  name: "Courier"
  until: "2026-10-01T18:00:00+02:00"

# One-time
action: nimly.create_guest_code
data:
  name: "Move-in help"
  one_time: true

# Recurring: same code, Monday and Friday mornings, forever
action: nimly.create_recurring_guest
data:
  name: "Cleaner"
  schedule:
    - days: [mon, fri]
      start: "08:00"
      end: "12:00"
```

The service **returns the code once** — hand it to the guest. For temporary and
one-time guests the code is never stored anywhere; for a recurring guest it is
stored in the config entry's options, because the same digits have to be
restored on every window. That storage is the deliberate cost of a fixed code
(see [privacy.md](privacy.md)).

## Schedules

A window is a list of days (`mon`…`sun`) with a start and end in local time. An
end at or before the start crosses midnight, so `fri 22:00-02:00` also covers
Saturday night. Several windows per guest are fine:

```yaml
schedule:
  - days: [fri]
    start: "08:00"
    end: "12:00"
  - days: [mon, wed]
    start: "13:00"
    end: "15:00"
```

Edit them with `nimly.update_guest` (or the card): the change takes effect
immediately — a window that just opened writes the code, one that just closed
clears it. `paused: true` clears the code and stops the schedule until resumed.

## What the lock and the app know

- The **lock** has no schedules at all (the vendor's own module specification:
  *"No schedules, no user status"*), so enforcement is Home Assistant's. While
  Home Assistant is up, a guest code exists in the lock exactly inside its
  window and offline use inside the window works.
- The **vendor app** shows a recurring guest's code as always valid. The app's
  record cannot express a schedule; the lock is the one that decides.
- The **emulator** reports the slot as occupied only while the credential is in
  the lock, so the app's own slot allocation stays consistent with reality.
- Trying a code outside its window counts as a failed attempt on the lock
  (that is what keeps the lockout counter honest) — tell the guest the hours.

## If Home Assistant was down

A window edge that was missed while Home Assistant was off is repaired at
startup: an open window writes the stored code again, a closed one clears it.
Only a window edge during downtime means the code can outlive its window until
the next start — the one bounded gap of the design.

## Managing guests

```yaml
action: nimly.list_guests          # live state per guest (never the codes)
action: nimly.update_guest         # name, code, schedule, pause, expiry
data:
  slot: 5
  paused: true

action: nimly.revoke_guest_code    # clears the code and forgets the guest
data:
  slot: 5
```

`nimly.update_guest` can also turn a temporary guest into a recurring one, but
only when it is given a code — a temporary guest never stored its own.

## Fingerprint enrollment from Home Assistant

`nimly.enroll_fingerprint` lights the lock's reader for one slot. With a synced
guest the enrollment goes through the vendor cloud (`scan-tag`), so the app
records the access too; otherwise it is sent straight to the lock. Either way
the lock reports nothing while the enrollment runs — a template exists only
once that finger has really opened the door, which is why `finger_used` (not
the enrollment) is the evidence the catalog trusts. The guest card's
fingerprint button calls the same service.

The guest card marks every row twice over. A **key**: solid (green) when the
catalog holds the PIN value itself — a recurring guest — so the code can be
replayed after a loss, and struck through when it does not (a temporary
guest's code is shown once and never stored; an app-created guest's value only
exists in the app). A **fingerprint**: solid when a finger in the slot has
really opened the door, so the slot can be reused when restoring, and struck
through when the finger was enrolled but never used — an enrollment proves
nothing about the template the lock holds, so it is never replayed on faith.
A guest with no linked finger shows no fingerprint at all.
