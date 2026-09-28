# Known issues and vendor quirks

Everything here is observed against the lock itself on the bench. Each entry
says what happens and what we do about it.

## The capacity attributes are hints, not limits (measured)

The lock reports `num_of_total_users_supported = 100`,
`num_of_pin_users_supported = 50` and `num_of_rfid_users_supported = 50`
(attribute ids `0x0011`, `0x0012`, `0x0013`), and nothing about fingerprints.
On 2026-09-28 the lock was asked directly whether those numbers are enforced:
raw `SetPINCode` (ZCL `0x05`, no Home Assistant guard in the way) to slots
**60, 150, 500 and 999** each answered `Status.SUCCESS`, while slots **1000 and
65535** answered `Status.FAILURE`. The enforced user range is therefore exactly
**003–999** (the Touch Pro manual's range), and the reported `50` does not
describe PIN slots at all.

- **We do** treat the standard capacity attributes as hints: `pin_rules.pin_capacity`
  never lets a reported value below the manual range cap the household, and
  honours one above it. The master slots 000–002 stay reserved. The test slots
  above were cleared again after the measurement.

## Occupancy cannot be read back either

`GetUserStatus` (`0x0A`) is not answered by the lock (it times out), so the
integration cannot ask whether a slot is occupied, so the slot table is still
only a map; `wipe` still sweeps the whole user range because of it. With the range
now 003–999 that sweep is up to 997 slots, which is a slow but deliberate
operation; the alternative (a lock-wide clear-all command) is not used because it
may take the master codes with it.

## The fingerprint slot limit is unverified

No attribute anywhere reports fingerprints, and the manufacturer-specific cluster
(`0xFEA2`) answers nothing readable. The manuals put user fingers in slots
**003–199** (199 unique), but the only way to confirm the bound is a finger at
the reader. Until the owner tests it, the slot checks use the PIN range and the
manual's 199 is a claim, not a measurement.

## Fingerprints cannot be read back

The lock reports nothing while an enrollment runs; there is no way to ask
"does slot 5 have a template?".

- **We do** keep the fingerprint's presence as a hint and treat *use* as the
  proof: re-record a finger only when it has really opened the door, and a
  revocation sends an explicit clear.

## Re-enrolment into a "cleared" slot was refused (fixed)

The lock blinks red and refuses a second enrolment into a slot that already
holds a template, and `hemnyckel.clear_slot` cleared only the PIN — the
fingerprint clear (`0x72`) was never sent. A slot the household considered free
therefore still held a template and the next enrolment failed at the reader: the
refusal was our own bug, not the lock's.

- **We do** clear both credentials now: `clear_slot` sends the PIN clear and the
  fingerprint clear, and `hemnyckel.clear_fingerprint` sends `0x72` alone. The
  master slots 0–2 are still never touched. (Steps 1–3 of
  [fingerprints.md](fingerprints.md).)

## The slots sensor never carried `finger_used` (fixed)

`finger_used` — the only proof that a finger in a slot has really opened the
door — lived in the slot table but was never emitted on the slots sensor, so the
app's `LockSlot.fingerUsed` was always false and a confirmed finger could not be
shown.

- **We do** emit `finger_used` and the per-slot `fingers` labels on both the
  slots sensor and the per-slot sensors; the slot table records the owner's
  label for each finger as a claim, confirmed only by a real use.

## RFID is deliberately unsupported

Measured 2026-09-23: enrolling a tag sends only the credential id over Zigbee
(`0x70`/`0x18` with a 16-bit id); the UID itself never leaves the lock. Scanning
the tag at the door reports nothing at all — the lock sends no notification at
all when it is opened with an RFID tag.

- **We do** leave RFID out of the family's workflow on purpose. With no report
  when a tag is used, a tag could never be attributed, shown in the journal or
  revoked on first use, so the home would never use one. The card has no tag
  enrolment and no tag mark, and there is no "add tag" path. The lock's own
  capability report ("50 PIN · 50 RFID · 100 total") is the hardware's fact and
  stays; so does the fact that a tag we never scanned could not be re-created.

## ZHA's reconfigure can leave the operation event on a long reporting interval

Measured 2026-09-25 on the bench: the only unlocks that reached Home Assistant
were 13-19 minutes apart, and an air capture on the lock's channel during the
silent attempts showed polls and beacons but not one data frame from the lock -
the operation event (0x0100) was suppressed on the device, not lost on air. The
long minimum interval comes from ZHA's `reconfigure`, which had just run.

- **We do** write our own reporting configuration for the operation event
  (0x0100) and the lock state (0x0000) - minimum interval 0, maximum 3600,
  reportable change 1 - from the mirror at the first moment the device proves
  awake (any report, any successful command, or the health tick). The lock
  keeps the configuration, so it is a one-time fix per lock; a later ZHA
  reconfigure overwrites it and the health tick puts ours back.
