# Matter, HomeKit and other ecosystems

The lock is a local device first: the real module sits on ZHA, so lock/unlock,
codes and history work with no vendor involvement. This page is about handing a
few of those abilities to the ecosystems people already have in their phones.

## What to bridge

| Ability | How | Status |
| --- | --- | --- |
| Lock / unlock | A `lock` entity is a standard Matter Door Lock device type; bridge it with the official Matter Bridge. | Works |
| Locked state, battery, connectivity | Standard sensor and binary_sensor entities; the bridge maps them. | Works |
| Codes, fingerprints, tags, guest profiles | Not available over Matter today — see below. | Use this integration's services, the guest card, or the vendor app |

Bridging is **outsourced on purpose**: a custom integration cannot publish its
own Matter devices, because Home Assistant's Matter support lives in the Matter
Server add-on and its bridge mode. Our job is to keep the entities standard and
the local path solid, which is exactly what the bridge needs.

### Matter Bridge (Apple Home, Google Home, Alexa, …)

1. Install the **Matter Server** app (add-on) and check that its version offers
   **Bridge** mode; bridging ships there, not in the integration.
2. In the app's bridge configuration, expose this integration's `lock` entity
   (and optionally battery and the connectivity binary sensors). Leave the
   diagnostic and journal entities out — they add noise, not control.
3. Pair the bridge with the ecosystem (a QR/matter code from the app), then the
   lock appears as a normal door lock in the other app.

The command path is local: ecosystem → Matter Server → Home Assistant → ZHA →
lock. The vendor cloud is not involved, so unlock keeps working during a vendor
outage.

### HomeKit Bridge (HomeKit-only homes)

The official **HomeKit Bridge** integration exposes the same entities to Apple
Home without Matter. It is the simpler route on an iPhone-only household; the
trade-offs are the same, and credentials still stay in this integration.

## Why credentials are not bridged

Matter 1.2 added Users, Credentials and Schedules to the Door Lock cluster —
including PIN, RFID and fingerprint credential types — so the protocol can in
principle carry "add a code" or "enroll a finger". Today it cannot be used
end-to-end:

- Home Assistant's `lock` entity has no concept of credentials, so its bridge
  has nothing to map the cluster to.
- The big ecosystems' user interfaces barely surface those clusters yet.
- Codes and fingerprints are deeply tied to *our* slot model, the emulator and
  the vendor cloud (see [cloud-sync.md](cloud-sync.md)); a partial Matter
  mapping would diverge from the catalog we treat as the truth.

The plan is to revisit this when Home Assistant's bridge grows credential
support: the catalog already knows every credential, its slot and its owner, so
a future mapping would be a presentation layer, not a new source of truth.

## Recommendation

- Bridge the lock (and battery/connectivity) for everyday control from Apple
  Home and friends.
- Keep guest management in Home Assistant: the guest card and
  [dashboard.md](dashboard.md) mirror the vendor app's list, including the
  guests that only exist in the cloud.
