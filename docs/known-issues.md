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
