# Known issues and vendor quirks

Everything here is observed against the lock itself on the bench. Each entry
says what happens and what we do about it.

## Fingerprints cannot be read back

The lock reports nothing while an enrollment runs; there is no way to ask
"does slot 5 have a template?".

- **We do** keep the fingerprint's presence as a hint and treat *use* as the
  proof: re-record a finger only when it has really opened the door, and a
  revocation sends an explicit clear.

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
