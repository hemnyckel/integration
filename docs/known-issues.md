# Known issues and vendor quirks

Everything here is observed against the lock and its firmware on the bench.
Each entry says what happens, what we do about it, and what is still open.

## Fingerprints cannot be read back

The lock reports nothing while an enrollment runs; there is no way to ask
"does slot 5 have a template?".

- **We do** keep the fingerprint's presence as a hint and treat *use* as the
  proof: re-record a finger only when it has really opened the door, and a
  revocation sends an explicit clear.

## In a tag flow the bridge never learns the UID

Measured 2026-09-23: enrolling a tag sends only the credential id over Zigbee
(`0x70`/`0x18` with a 16-bit id); the UID itself never leaves the lock. Scanning
the tag at the door reports nothing at all (see `protocol.md`).

- **We do** keep a tag's UID only when we read it ourselves, and restore by
  re-enrolling: a tag we never scanned cannot be re-created.

## The bridge evicts a device whose app record is gone

On 2026-09-23 the emulator rejoined with its stored keys and the bridge removed
it again after ~15 s with a Leave — the app's device record had been deleted.
The Leave is the expected cleanup, not a pairing failure; the fix is the normal
add-device flow in the app.

- **We do** treat `emulator_not_joined` as the repair for this state, and a
  reboot alone re-joins once the bridge has a record to accept.

## A bridge move can strand the app path

Measured 2026-09-24: after a physical move, the bridge's vendor link stayed
silent (its telemetry timestamps froze) until a power cycle — and even with the
link back, lock commands still answered `504 Gateway response timeout` while
management requests (scan) worked. Re-registering the lock in the app was the
fix.

Why: the bridge has no graceful shutdown, so a move is an unclean power cut.
Its boot can restore the Zigbee network (same PAN, devices local) while leaving
the vendor session dead and the device bookkeeping half-restored — it evicted a
device whose app record still existed. It does not self-heal that state: a
second power cycle brought the link back, but the device's send/receive session
was only rebuilt by a fresh registration. Watch for a stranded app path and
re-pair from the app when commands keep timing out.

## Changing the emulator's IEEE needs the full reset dance

Measured 2026-09-23: `hemnyckel.set_ieee` alone stores the new address in the
firmware's NVS (the boot log confirms "IEEE satt till …a4") while the Zigbee
stack keeps its stored extended address ("own IEEE = …a3") — a plain set plus
reboot does not move it. What works: a local factory reset
(`{"cmd": "factory_reset"}` or the repair path), then `set_ieee`, then a
reboot — verified: the device announced Ext Addr `f4:...:a4` and joined. A
plausible sibling address in the same OUI family was accepted by the bridge.

- **We do** use the dance for test builds (factory reset → set → reboot). A
  firmware item (0.5.5) folds the reset into `set_ieee` itself so one call
  suffices.

## Flaky USB on some cheap boards: flash in small chunks

Measured 2026-09-24 on a fresh ESP32-C6: full `write_flash` runs died at
random points ("No more data to read from the serial port") while an
ESP32-C3 on the same cable, port and VM flashed flawlessly. That board's
USB-serial-JTAG link does not survive long sustained writes. What works:

- write the big app image in 32 KiB chunks, retrying each chunk (partial
  progress is kept and every chunk is verified on its own), and
- write the small parts (bootloader, partition table, ota_data) with
  `--no-stub` at 115200.

A partition table that was written and verified once can still read back as
0xFF later on such a board - re-check 0x8000 after a chunked flash. (Also:
the lab VM lost its Proxmox USB passthrough mid-session; when every USB
device vanishes at once, that is host-side, not the boards.)

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
