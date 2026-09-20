# Migration — from v1 (`nimly_cloud` + `nimly_shadow`) to `nimly`

Status: the plan. Nothing here happens until the new integration is at feature parity and
verified on hardware. The v1 integrations and this repository coexist during the transition.

## Order of operations

1. **Build and verify** — the mirror layer is ported, then tested on hardware: mirror in both
   directions, attribution, settings, OTA.
2. **Install the new integration alongside** — `tools/sync_to_ha.sh /path/to/config` and
   restart Home Assistant. The old entries keep working.
3. **Create the new entries**: `cloud` (sign in, pick the location), `mirror` (pick the lock
   and channels), `bridge` (only when re-provisioning). Verify entities and services.
4. **Switch over** — automations and dashboards that call `nimly_cloud.*` or `nimly_shadow.*`
   services move to `nimly.*`. Service names are unchanged; only the domain is.
5. **Remove the old** — delete the `nimly_cloud` and `nimly_shadow` config entries, then the
   custom components (or uninstall them in HACS). Clear any V1-only repairs.
6. **Remove `onesti_lock`** — the mirror owns the ZHA link now (raw `0x0100` listener, ZCL
   commands and the slot table), and slot names and occupancy are imported automatically on
   mirror setup. Removal is safe after hardware verification; the lock entity itself is
   created by ZHA and stays.

## What changes for the user

- Services move domain: `nimly_cloud.refresh` → `nimly.refresh`, `nimly_shadow.set_ieee` →
  `nimly.set_ieee`, and so on.
- Entities are recreated when the new entries are set up. Remove the old entries first, then
  the entity IDs follow the usual naming and most stay identical.
- History is preserved: the recorder is keyed on entity IDs, not on the integration.

## The vendor app during a reflash

The emulator carries the real module's IEEE, so the app's lock object stays valid. The
one-time dual-OTA reflash uses a partition table that keeps `nvs` at its current offset and
does not erase it — the Zigbee pairing state can survive, and the emulator should rejoin the
bridge on its own.

Only if the bridge rejects the rejoined device:

1. Verify the lock still has a **master PIN and a master fingerprint**. They are required
   before the bridge accepts a module, and a lock factory reset wipes them.
2. Remove the lock in the app, put the bridge in pairing mode, and let the emulator join
   again.
3. Re-apply app-side settings (auto-lock, volume); the integration re-syncs the rest.

Do **not** remove the lock in the app preemptively.

## Rollback

The v1 integrations stay installed and configured until step 5. If anything fails, delete
`custom_components/nimly` from the live configuration and restart: the old entries are
untouched and take over again.
