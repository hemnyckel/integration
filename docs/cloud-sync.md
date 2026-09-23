# Cloud sync: keeping the lock, Home Assistant and the vendor cloud in step

Status: **built and live** (2026-09-23). The services below exist and the push
triggers are wired into the mirror; the API behaviour they rely on is verified
(see "Verified on hardware").

## Principles

- **The lock and this integration are the truth; the vendor cloud is a view.**
  The local path never depends on the cloud, and a cloud loss must never cost a
  credential.
- **The lock holds what exists, never the values.** Its slot table carries the
  names, the user types and which credential types sit in a slot; the PIN code
  itself is write-only, a fingerprint template never leaves the lock, and a tag's
  UID is readable only while it is scanned.
- **The integration keeps the catalog:** who owns which slot with which credential
  types, every value we created ourselves, the cloud identity (uuid) and the state
  of the cloud's records. The catalog is what makes a restore a replay instead of
  a rebuild.
- **Nothing is invented.** A credential is only ever synced or simulated for a
  person and a slot where the lock really holds it.

## The stores

| Store | Holds | Never holds |
| --- | --- | --- |
| Lock (physical truth) | Slot table, credential codes/templates, settings | — (it is the source) |
| Catalog (HA, entry options + journal) | Per-credential record `{slot, type, owner, value?, origin, cloud_user_id?, cloud_state}`, guests, `slot_map` | Values it was never given |
| Cloud (view) | Guest users (`GUEST_USER`, one identity per person), per-device accesses `{userId, type}` | Any value; the cloud never returns one |

A cloud access carries **no slot** — the gateway tracks the slot it assigned and
attributes events through it. Locally the mirror translates the lock's real slots
to the app's virtual numbers (`mirror/slot_virtual.py`) and keeps the owner in
the catalog.

## Verified on hardware (2026-09-22)

- `POST /guest-users {name, locationId, validFrom, validTo}` creates a guest
  identity (email/phone optional); `GET /guest-users?locationId=` lists them;
  `PATCH /guest-users/{id}` updates one.
- `POST /devices/{id}/access {userId, type, value}` accepts **pin** (value = the
  code) and **tag** (value = the UID); `DELETE /devices/{id}/access {userId, type}`
  also accepts **finger**.
- The app's fingerprint enrollment is
  **`POST /devices/{id}/access/scan-tag {userId, sendEmailNotification: false, type: "finger"}`**
  — the endpoint name is historic, the `type` chooses finger or tag. The cloud
  then pushes the ZigBee enroll (0x71/0x72) to the module; our emulator answers it,
  the cloud records the finger access, and **no physical touch is required**.
  Verified: the access appeared, the user's `hasDoorlockFingerprint` turned true.
- The cloud attributes an event to a person through the access record the gateway
  created; a PIN unlock from an access we created was pushed to the app with the
  person's name. Fingers the cloud never enrolled cannot be attributed there.

## Sync directions

### HA → cloud

For every catalog credential with a known value: ensure the person has a guest
identity (uuid stored in the guest record) and an access on the device.

| Type | Call | Value |
| --- | --- | --- |
| PIN | `POST /devices/{id}/access` | the code we hold |
| RFID | `POST /devices/{id}/access` | the UID we scanned or created |
| Finger | `POST /devices/{id}/access/scan-tag` `type: finger` | none — the lock already holds the template |

A simulated finger enrollment is only allowed when the lock's slot table shows a
fingerprint in that slot, and it must not mirror the enroll to the real lock
(the reader would light up for a finger that is already enrolled). The mirror
gains a "simulated" marker for this.

### Cloud → HA

Read the guest list and the device accesses, and bind each new access to the slot
the lock just gained:

- A new `{userId, type}` in the access list plus a new credential event from the
  lock (the enroll or SetPINCode the gateway pushed) is the same person; record
  `{cloud_user_id, slot, type}` in the catalog. The journal's name suggestion
  already covers the UI side of this.
- Values are never learned this way; only the mapping is.

## Restore matrix

| Loss | Lock | Cloud | Catalog | Restore |
| --- | --- | --- | --- | --- |
| Emulator evicted / module swapped | intact | device record orphaned or gone | intact | Re-pair the emulator (IEEE provisioning); replay identities + accesses. For app-created PINs whose value we do not hold, let the emulator accept the cloud's push **without mirroring it to the lock**, so the code in the lock stays the one the guest knows. Fingers by simulation. |
| Cloud account / device record removed | intact | empty | intact | Same replay as above; the lock is untouched. |
| Bridge replaced | intact | device record gone | intact | Re-pair module + bridge (join recovery: remove the device in the app, search again), then the same replay. |
| Lock factory reset (hardware intact) | credentials gone | intact | intact | Write back every PIN/tag we hold the value for; users re-enroll fingers and re-enter codes we do not know; re-sync the cloud (finger records by simulation). Every value re-entered through us is **promoted** into the catalog. |
| Lock destroyed | gone | intact | intact | A new lock is provisioned; the catalog is the blueprint. Everything we hold a value for is restored; everything else is re-created by the users (finger, codes, tags). Then promoted. |
| Home Assistant lost | intact | intact | backup | Restore the catalog from backup and audit against the lock and the cloud. |

**Promotion** is the point of the recovery round: whatever a user re-enters or
re-enrolls through the integration becomes a known value in the catalog, so the
next loss is a pure replay.

## Guardrails

- Simulate a finger enrollment only for a slot that really holds a fingerprint.
- Never overwrite an app-created code with a value we made up: the lock keeps
  what the guest knows, and the cloud record is restored around it.
- Journal every sync, restore and simulated action with its origin (`ORIGIN_HA`),
  so the audit can always tell what happened and when.
- Every service runs `dry_run` first and reports exactly what it would change.
- `nimly.audit` compares the three stores (lock slots ↔ catalog ↔ cloud accesses)
  and reports drift instead of fixing it silently.

## Services (built)

| Service | Purpose | Status |
| --- | --- | --- |
| `nimly.cloud_guests` | Read the cloud's guest users into a response (and a sensor). | built |
| `nimly.sync_cloud` | Reconcile HA → cloud (identities, pin/tag accesses, finger records), dry-run first. | built |
| `nimly.restore_cloud` | Replay the catalog onto a fresh cloud/device without touching the lock's codes. | built |
| `nimly.simulate_enroll` | The guarded finger-enrollment replay (also used by sync/restore). | inside restore, no separate service yet |
| `nimly.audit` | The drift report across lock, catalog and cloud. | built |

Every local guest edit follows the same direction — not only creation: renaming
a guest, changing its validity or changing its code pushes the change
(`PATCH /guest-users/{id}`, and an access replace for a code) through
`nimly.update_guest`, the plan card included. A rejected push journals
`cloud_update_failed` and raises the `cloud_push_failed` repair, whose fix
retries with the values Home Assistant already holds; a later successful push
clears the repair. The `Cloud sync` channel switch gates all of it.

## Auto vs guided

A wrong cloud access hands someone a door. The policy is therefore split by how
certain the information is, not by convenience:

| Tier | When | Behaviour |
| --- | --- | --- |
| **1 — automatic, silent, idempotent** | Everything is known: a PIN/tag this integration created with a stored value and an owner, or a fingerprint the lock's table shows in a slot with a known owner | Sync immediately: ensure the guest identity (uuid) and the access (pin/tag carry the value; finger is a simulated enrollment). Journaled. The code or finger already opens the lock; the cloud record adds attribution and app notifications. |
| **2 — proposal + repair** | Something is uncertain: a slot without a name or owner, a credential that cannot be tied to a person, a cloud access that cannot be mapped | A repair issue with a suggestion (the existing `new_slot` flow). Once confirmed, the record becomes tier 1 and syncs. |
| **3 — guided manual** | After a loss where the value is unknown (an app-created PIN, a fingerprint template) | Guided flows: the guest re-enters the code or re-enrolls the finger. The value is **promoted** into the catalog, so the next time it is tier 1. |

The cloud → HA direction is read-only and always safe: a new access plus the new
credential event from the lock is the same person; the mapping is recorded
automatically.

Controls that make tier 1 trustworthy:

- **Only known values and verified templates.** Never invent a value; never
  simulate a finger for a slot that does not hold one.
- **Idempotent**: compare before every write, so a re-run and a periodic
  reconcile change nothing.
- **`switch.nimly_cloud_sync`** pauses the automatic tier (like the other mirror
  channels), and every service supports `dry_run` first.
- **`nimly.audit`** reports drift between lock, catalog and cloud instead of
  fixing it silently; every action is journaled with its origin.

Triggers: immediately after a credential is created here, on the periodic
reconcile, and on demand through the services.

## Lifecycle of a synced guest

A guest that HA owns has a full lifecycle, and each step keeps the cloud in
step with the lock:

- **Created** (with the sync channel on): the identity and the access are made
  in the cloud right away; the vendor's push of the code binds to the guest's
  own slot — the code is never written twice.
- **Code changed** (`nimly.set_cloud_code`): the new value goes into the lock
  and the catalog *first*, then the cloud access is replaced (delete, then
  create; the vendor's delete is asynchronous and the create retries until it
  lands). The arriving push binds by the new value, so nothing is duplicated,
  and a failure restores the old value in both places.
- **Revoked / expired**: the lock is cleared first and the record is dropped
  only when that succeeded; a linked fingerprint is cleared in the lock, the
  identity links are removed, and the cloud's accesses for the guest go away.
  The identity itself is deleted when no other lock and no other guest still
  needs it — a revocation must not leave a working access behind for the
  vendor to push back.

## Open questions

- **Slot alignment for finger attribution:** the gateway picks the slot during an
  enrollment; whether our emulator's user-table reports can steer that choice to
  the slot the template really lives in is untested. Verify with a real finger
  unlock after a simulated enrollment.
- **Tag UID read-back:** standard ZCL exposes no way to read a stored tag's UID;
  a tag we did not scan ourselves cannot be re-created without a new scan.
- **App-created PINs in the emulator's RAM:** the emulator stores the codes it is
  asked for vendor `GetPINCode` responses, so they are technically recoverable
  while it runs. Reading them would break this project's PIN-handling rule
  ([privacy.md](privacy.md)); the design restores around the code instead.
