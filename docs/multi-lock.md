# Multiple locks

The home will have two locks, possibly three. This page is the plan for making
every layer — firmware, integration, cloud sync, card, dashboard — work the same
with N locks as it does with one.

## The shape we have today

One **mirror config entry per lock**. Everything local is already per entry:
slots, guests with schedules, the journal, events, channels (the cloud-sync
switch included) and the credential links, which live in that entry's options.

The **cloud entry is shared**: it holds the account, the session and one cloud
device per emulator serial (`cloud_device_for(mirror)` picks the right one), so
several locks already talk to the same vendor account without mixing devices.

## What breaks with a second lock

1. **Adoption across locks.** `match_guest` adopts a cloud identity only when a
   unique name has *no access at all*. Synced once, the identity has an access —
   so lock #2 would create a **duplicate identity** for the same person. The
   catalog must be able to answer "this person already exists, on another lock"
   and reuse the uuid.
2. **The card shows the account, not the lock.** "I appen" filters the shared
   guest list by owned uuid and name, but not by *this lock's* accesses, so the
   same cloud guest would render on every lock's card.
3. **The dashboard assumes one lock.** Views are built around "Ytterdörren".

## The person model

A **person** is one cloud identity (`uuid`) with access on one or more locks.
The per-lock records stay where they are (slot values, links in the mirror
entry options); the cloud entry keeps a derived, rebuildable index:

```
persons: {
  "<uuid>": {"name": "…", "locks": {"<entry_id>": {"slots": [6], "types": ["pin"]}}}
}
```

It is written by sync/link operations and rebuilt by maintenance from the
mirror entries on load, so it never becomes a second source of truth — the
mirror options remain the catalog of record. With the index:

- `sync_cloud` on lock #2 finds the person by name across locks and adds the
  access there with the existing uuid instead of creating a new identity.
- The audit can report a person that exists on one lock but not the other.
- The card can show the lock-local subset and, later, a per-person view.

## Phases

1. **Identity layer** — persons index, cross-lock adoption, maintenance rebuild.
   Tests: two mirrors, same person, one uuid, an access per lock.
2. **Per-lock presentation** — card filters cloud guests by this lock's device
   access list; dashboard gets per-lock sections. **Done:** the card discovers
   every lock from the guests sensors and shows, per guest, where the person
   exists; with one lock nothing changes. **Done:** the create form has a lock
   picker (only shown when there is more than one lock) that creates the guest
   on every selected lock with one code and one group marker, reporting a lock
   that fails without losing the rest. Edit, pause and revoke follow the
   person: a group marker, or one shared cloud identity, ties the records
   together, and the row shows how many locks the person is on.
3. **Third lock experiment** — the app refuses more than two locks
   (`doorLocksLimit: 2`). Claiming a third through `POST /devices` may well
   work, since the limit looks like app UI policy; the bridge and the cloud
   server are the unknown parts. Test with the spare emulator and the fabricated
   IEEE only when the bridge is healthy, then decide. (Note: the bridge radio
   recovery was painful the last time — do this in daylight, with a plan B.)
4. **App guests, editable** — `nimly.update_cloud_guest` (`PATCH
   /guest-users/{id}` for name, validity, contact) plus the edit sheet in the
   card. Deleting a guest stays app-only: the API has no DELETE for guest
   users, only for their accesses (`DELETE /devices/{id}/access`).

## What the account already shows

A read of the account (the same reads the integration makes) found **two
doorlock devices in one location**: the retired module and the current
emulator-backed lock. The app's "two locks" refusal therefore looks like an UI
policy, not an account limit — the server happily holds two.

The same read shows that accesses are **per device**: a guest's access record
on the old device stays there (one person's PIN still sits on the retired
device, while her fingerprint is on the current one). `GET /devices/{id}/access`
is the per-lock truth — `type` and `userId`, but **no slot**, because slots are
the lock's own numbering and live only in this integration.

That settles the card's filter for phase 2: "I appen" for a lock shows the
guest users with an access on *that* device, and a linked person can appear
with different credentials on different locks.

## Open questions

- Does the bridge firmware accept a second/third paired lock, or is its own
  limit lower than the app's? Only the experiment answers it.
- Does the cloud enforce one gateway per account, or one gateway per location?
- Should a person's code be the same on every lock? Today each mirror creates
  its own code; reuse would need the value written per lock.
