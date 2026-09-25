# Known issues and vendor quirks

Everything here is observed against the live vendor API and the lock on the
bench. Each entry says what happens, what we do about it, and what is still
open.

## The app's PIN edit frame is unanswered

`PATCH /devices/{id}/access` is the vendor's "change the value of this code".
Our emulator answers the create, delete and lock flows the app uses, but not
the frame the cloud sends for this edit, so the request times out (the cloud
waits for the module's acknowledgement).

- **We do** a change as delete-then-create, both flows the module answers.
  `nimly.set_cloud_code` writes the new value into the lock first, so the
  arriving write binds to the guest's own slot instead of adding a copy.
- **Open:** whether the app itself uses the PATCH path (try changing a code in
  the app). If it does, the emulator firmware needs to learn that frame —
  until then the app's own code edit cannot work end to end.

## The vendor's deletes are asynchronous

Deleting a guest does not immediately remove its accesses — the vendor queues
the removal and it lands about a minute later. Deleting an access and
immediately creating one with the same user and type answers
`409 already exists` while the old record is still there.

- **We do** delete every access before the identity, and retry the create
  until the vendor has caught up (bounded, with fresh reads).

## Ghost accesses for devices that no longer exist

The account keeps access rows that name a device which has been removed (the
original module, before the emulator). The app still shows the credential as
present — one guest's PIN exists only as such a ghost.

- **We do** mark them in `nimly.cloud_guests` (`ghost`) and in `nimly.audit`,
  and never count them as "on this lock".
- Cannot be re-pointed through the API; deleting the guest removes them.
- A ghost can also be invisible in the access list while still blocking a new
  access for the same user: the create answers `409 … already exists`
  (code 2801) with nothing to delete first. Deleting the guest identity clears
  it; re-create the guest after that (measured 2026-09-23).

## "Gateway is offline" from a stale check

A delete can answer `device's gateway … is offline` moments after the same
device reported access lists, and a retry seconds later passes.

- **We do** retry once after a short wait; the second attempt is normally
  enough.

## `PATCH /guest-users/{id}` wants both dates

A validity change must carry `validFrom` and `validTo` together; sending only
one answers `400` ("date references ref:validFrom").

- **We do** send the untouched date along; a date-only end becomes the end of
  that day.

## More than two locks: app policy, not (yet) an account limit

The app refuses a third lock (`doorLocksLimit: 2`). A second device claim
through the API is accepted and then waits for the module to answer — no
server-side rejection. The account has held two doorlock devices (one being
the retired module).

- **Open:** how the vendor gateway and the bridge behave with a real second
  module. The experiment needs a spare emulator and a healthy bridge.

## The gateway's clock is DST-unaware

The gateway stamps its own reports an hour ahead through the summer (CET
without DST): a lock event at 21:56 UTC arrives stamped 22:56. The cloud's own
timestamps — a gateway's `updatedAt`, the response to a gateway action — are
correct, and the public API has **no timezone or DST setting for a lock or a
gateway** (only cameras have one: `PUT /devices/{id}/settings/camera/timezone`
and `/dst`). Probing for hidden gateway/location settings paths answers 404.

- **We do** read the offset from the server's own `expires` arithmetic and
  correct every feed timestamp we show (`vendor_time`); the raw value stays as
  `vendor_time_raw` for diagnostics. If the vendor fixes the clock, the offset
  becomes zero and the correction does nothing.
- **The vendor app itself** (its history and push times) stays an hour ahead
  until the gateway's clock or timezone is fixed. The phone app has been seen
  offering a timezone setting; it is not reachable through the public API, so
  the fix there is the app, the gateway's provisioning, or vendor support.

## Fingerprints cannot be read back

The lock reports nothing while an enrollment runs; there is no way to ask
"does slot 5 have a template?".

- **We do** keep the fingerprint's presence as a hint and treat *use* as the
  proof: restore re-records a finger only when it has really opened the door,
  and a revocation sends an explicit clear.

## One device per config entry

Home Assistant 2026.9 keeps one device per config entry, so the cloud and ZHA
halves of a lock cannot be merged into one device in the registry.

- **We do** keep cloud entities on the cloud-owned device and the ZHA entities
  on the ZHA device, both named after the lock.

## In a tag flow the bridge never learns the UID

Measured 2026-09-23: enrolling a tag sends only the credential id over Zigbee
(`0x70`/`0x18` with a 16-bit id); the UID itself never leaves the lock. Scanning
the tag at the door reports nothing at all (see `protocol.md`), so the cloud's
`LastTagScanned` state stays empty.

- **We do** keep a tag's UID only when we read it ourselves, and restore by
  re-enrolling: a tag we never scanned cannot be re-created.

## The bridge evicts a device whose app record is gone

On 2026-09-23 the emulator rejoined with its stored keys and the bridge removed
it again after ~15 s with a Leave — the app's device record had been deleted.
The Leave is the expected cleanup, not a pairing failure; the fix is the normal
add-device flow in the app.

- **We do** treat `emulator_not_joined` as the repair for this state, and a
  reboot alone re-joins once the bridge has a record to accept.

## The vendor's access delete wants a module ack

Measured 2026-09-23 (the wipe): `DELETE /devices/{id}/access` is pushed to the
bridge over MQTT and the bridge waits for the module to answer. The emulator
does not speak that flow yet, so the cloud reports
`Mqtt timeout ... Gateway response timeout` (504, code 2040), and an identity
can be left behind — a retry with `nimly.delete_cloud_guest` went through.

- **We do** report the failed removal, retry it, and keep it visible in the
  audit until it is really gone. Answering the bridge's delete flow is a
  firmware item: capture what the bridge sends the emulator during a real
  delete and speak it (the C6 console logs unknown Door Lock commands).

## A request storm can reboot the bridge

Measured 2026-09-23: a burst of access creates — the 409 retry loop turning
six attempts, twice, plus the sync attempts around them — wedged the bridge's
request path and it **restarted itself**: the cloud view went offline for
~31 s and came back on its own. The emulator stayed joined throughout; only
the bridge's cloud link bounced.

- **We do** one retry at a time, then leave the bridge alone: every repair
  retry is a manual click, and a failed sync waits for the next attempt.
  Hammering a freshly recovered bridge is how it wedges again.
- Never factory-reset the bridge over this; quiet time heals it (and a longer
  power-off if needed).

## A bridge move can strand the app path — repair_join restores it

Measured 2026-09-24: after a physical move, the bridge's cloud link stayed
silent (its telemetry timestamps froze) until a power cycle — and even with
the link back, lock commands still answered `504 Gateway response timeout`
while management requests (scan) worked and the lock's own reports never
reached the cloud. Re-registering the lock record was the fix:
`nimly.repair_join` drops the record, resets the emulator, opens the join
window and heals what the fresh record lost — the remembered name goes back
(`manualName: true` again) and the catalog is replayed so guest accesses
return with the same codes. Tag UIDs still need a rescan; everything else
survives.

Why: the bridge has no graceful shutdown, so a move is an unclean power cut.
Its boot can restore the Zigbee network (same PAN, devices local) while
leaving the cloud session dead and the device bookkeeping half-restored — it
evicted a device whose account record still existed. It does not self-heal
that state: a second power cycle brought the cloud link back, but the
device's send/receive session was only rebuilt by a fresh registration. There
is no vendor-side fix to rely on; watch for the stale-link repair and re-pair
when commands keep timing out.

- **We do** treat a stranded app path as a re-pair, never a rebuild: one
  `repair_join` call, then `nimly.audit` for the drift report.
- A re-registration always yields a new device id and bumps
  `gatewayGeneratedId`; entity identity follows the serial, not the id.

## A fresh registration can carry a garbled settings value

Measured 2026-09-23: the device record created right after a join showed
`autorelocktime: 65537` although the emulator held 1 (the mirror had pushed it
and the console logged it). The lock's own attributes are the source of truth,
and nothing in the firmware re-reports a settings attribute after a pushed
change, so the record keeps whatever the interview read.

- **We do** treat the lock's attributes as the source and the app's settings as
  a view; reporting `0x0023`/`0x0024` after a pushed value is a firmware item
  (0.5.5), after which the record corrects itself on the next read.

## Changing the emulator's IEEE needs the full reset dance

Measured 2026-09-23: `nimly.set_ieee` alone stores the new address in the
firmware's NVS (the boot log confirms "IEEE satt till …a4") while the Zigbee
stack keeps its stored extended address ("own IEEE = …a3") — a plain set plus
reboot does not move it. What works: a local factory reset
(`{"cmd": "factory_reset"}` or the repair path), then `set_ieee`, then a
reboot — verified: the device announced Ext Addr `f4:...:a4`, joined, and the
cloud created a record for the fake serial. That record was accepted: the
vendor cloud does **not** whitelist exact module serials — a plausible sibling
in the same OUI family passed.

- **We do** use the dance for test builds (factory reset → set → reboot). A
  firmware item (0.5.5) folds the reset into `set_ieee` itself so one call
  suffices.

## A device that is gone stays "online" in the app

Measured 2026-09-23: after the emulator was reset and left steering (not on the
network), its record kept `online: true` in the cloud and the app's add-device
flow presented it immediately instead of searching — a "dead entry" that does
nothing when tapped and cannot be locked or unlocked.

- **We do** keep the record deliberately (a rejoin reuses it: same id, same
  name), and read `online` as "last known" rather than live — the cloud only
  flips it on the device's own reports.

## The scan endpoint that acks without opening

Measured 2026-09-23 (the API-only cycle): `POST /gateways/{id}/action` with
`{"feature": "scan", "action": "turnOn"}` is what actually opens the join
window — it carried the whole app-free cycle. The vendor app's dedicated
`POST /gateways/{id}/scan` (`locationId`, `enableScan`, `autoAdd`,
`deviceType: ZIGBEE`) answers 200 but did not open the window in two attempts,
so it is only kept as a fallback.

- **We do** treat the action payload as the primary scan and verify a window by
  the hub's Permit Join broadcast, never by the API answer alone.

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
