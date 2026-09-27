# Multiple kits: 1, 2, 3 … N locks

The architecture that lets any number of C3+C6 kits share one broker, one
Home Assistant and one vendor account without configuration that collides —
and that works the same for anyone running this project.

## Principles

1. **Identity is the hardware MAC.** Never a position ("kit 2"), never a
   user-chosen name. Everything else derives from it.
2. **Every MQTT topic lives under a per-kit prefix.** One prefix per bridge;
   nothing is shared between kits, so commands cannot cross-talk.
3. **Credentials live in NVS, never in firmware images** (target state). One
   published image runs for everyone; provisioning is per device.
4. **Discovery, not configuration.** Home Assistant finds kits from their
   retained identity and binds them by MAC.

## The namespace

A bridge's prefix is `nimly/<mac12>` by default (twelve lowercase hex
characters of the WiFi MAC). All of its topics sit under that prefix:

| Topic | Direction | Retained | Payload |
| --- | --- | --- | --- |
| `nimly/<mac>/info` | bridge → HA | ✓ | `{"bridge": "<mac>", "fw": "…", "prefix": "…", "target": "esp32c3", "model": "…", "c6": {"fw": "…", "ieee": "…"}}` |
| `nimly/<mac>/ha_to_bridge` | HA → bridge | – | commands (`{"cmd": …}`); `set_prefix` and `ota` stay in the bridge, the rest is forwarded to the C6 |
| `nimly/<mac>/bridge_to_ha` | bridge → HA | – | raw C6 lines (events, hello, state) |
| `nimly/<mac>/state` / `battery` / `pin` | bridge → HA | state/battery ✓ | lock state, battery percent, PIN events |
| `nimly/<mac>/ota` | bridge → HA | – | OTA progress (`downloading`/`done`/`error`, percentage) |

**Prefix resolution** in the bridge firmware, in order:

1. NVS key `prefix` in namespace `nimly_cfg` (set by the `set_prefix` command;
   future provisioning will write it),
2. `NIMLY_TOPIC_PREFIX` in `secrets.h` (dev builds; kit #1 uses this to keep
   its historic `nimly/proxy`),
3. the MAC default `nimly/<mac>`.

`{"cmd":"set_prefix","value":"nimly/…"}` persists a new prefix and reboots;
prefix changes are therefore a live operation, never a reflash. The prefix is
validated as `[a-z0-9/_-]{3,47}`, no leading/trailing slash.

**Discovery** listens on the wildcard `nimly/+/info` — which also matches the
legacy `nimly/proxy/info` shape — plus the shared `nimly/info` topic that
firmware 0.5.x used. The legacy topic is accepted as a bootstrap source only:
once any identity is known, a stale retained copy cannot override the live
per-prefix one.

## Binding kit ↔ lock

* **Bridge entry** (one per C3): `unique_id` = the MAC. It subscribes the
  wildcard, matches the payload MAC, learns the prefix from `info`, persists it
  in the entry options, and subscribes its per-prefix `ota`/`state`/`battery`
  topics. The bridge's own OTA publishes to its learned prefix.
* **Mirror entry** (one per lock): stores the lock entity, its `topic_prefix`
  and — since this design — the `bridge` MAC it was added with.
* **The add-lock wizard** collects the discovered kits, marks the ones other
  mirror entries already bound as *in use*, and offers only free kits. It
  stores the MAC and the kit's prefix; a manual prefix entry remains for
  setups without discovery. `valid_prefix` is enforced, and a prefix already
  used by another mirror is rejected in the flow and raised as the
  `prefix_conflict` repair if it ever happens out of band.

## Onboarding a lock (the golden path)

1. Pair the lock's real module to **ZHA** and name it there.
2. Add the kit's **bridge entry** — over Bluetooth/Improv for a fresh board, or
   automatically from its `info` when it is already provisioned.
3. **Add integration → Nimly → Lock mirror**: pick the ZHA lock, pick the free
   kit (or accept the only one). The wizard prefills the prefix.
4. `nimly.set_ieee` provisions the emulator with the module's IEEE (the same
   value arrives from ZHA); open the vendor bridge's pairing window in the app;
   the emulator steers in and the app creates the record.
5. The record is **named automatically** (the ZHA name wins) on every later
   re-registration.

The emulator steers on its own once the app's pairing window is open.

## OTA at scale

* **Emulator (C6)**: one credential-free image for everyone; each kit's C3
  ferries it over UART. Driven per mirror entry (`update.nimly_emulator_firmware`).
* **Bridge (C3)**: the image is per-user today (`secrets.h` carries WiFi and
  MQTT credentials), so the update source is the user's own manifest — the
  integration option `ota_manifest_url` per entry. Provisioning now stores both
  WiFi and MQTT credentials in NVS: Wi-Fi over Improv, MQTT over an encrypted
  BLE config characteristic (bonding + LE Secure Connections). With a
  credential-free image the bridge becomes publishable like the emulator's and
  the default manifest can serve both. `secrets.h` remains the fallback for dev
  builds and never-provisioned boards.
* The manifest format is unchanged: `{"version", "builds": {"<target>": "<bin>"},
  "emulator": {"version", "file", "sha256"}}`; filenames are relative to the
  manifest URL. The bridge republishes its retained `info` every five minutes,
  so a missed retained delivery at HA startup heals itself.

## Failure modes and repairs

| Detector | Meaning | Action |
| --- | --- | --- |
| `emulator_not_joined` | C6 not on the bridge's network | guided re-pair in the app (remove the old lock, then add the device again) |
| `app_path_dead` (planned) | repeated 504s on device commands | re-pair the lock in the app |
| `prefix_conflict` | two mirrors share one prefix | give a kit its own prefix (`set_prefix`) |
| wrong emulator IEEE (warning + audit) | C6 wears another module's address | `nimly.set_ieee` |

## Migration in this installation

* **Kit #1** keeps its historic prefix `nimly/proxy` — baked into its
  (gitignored) `secrets.h` for the 0.6.0 build, so no HA-side change is
  needed. It can move to its MAC default later with `set_prefix` if desired.
* **Kit #2** (and every kit after it) is flashed with firmware 0.6.0 and gets
  `nimly/<mac>` automatically; the wizard binds it and names it.

## Status

| Piece | State |
| --- | --- |
| Per-kit topics, prefix resolution, `set_prefix`, `info` with C6 identity | built (firmware 0.6.0) |
| Wildcard discovery, free-kit picker, prefix uniqueness + repair | built |
| Bridge entry learns prefix by MAC; OTA over the learned prefix | built |
| Name-on-repair / reconcile rename, catalog replay on re-pair | built |
| BLE provisioning of WiFi **and** MQTT credentials; credential-free bridge image; webflash page for both boards | planned (world-scale flashing) |
| `nimly.add_lock` one-call onboarding | planned (composes `set_ieee` + the app's pairing window + replay) |
| Both C3s on 0.6.0 (kit A keeps `nimly/proxy`, kit B gets its MAC prefix) | done 2026-09-24 |
| Two-live-locks bring-up: module #2 to ZHA, the IEEE dance, join, name | next |
| Two-live-kits validation on the desk | next (needs both locks) |

## Open vendor questions

* Does the Nimly Connect Bridge accept a second paired lock? (The app's UI cap
  is two.) The two-kit test answers it.
* One vendor bridge per home is assumed; a second bridge is untested.
