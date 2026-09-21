# v2 architecture — Nimly integrations

Status: **ratified** 2026-09-20 (D1–D3, see `beslut.md`). Implementation order: the merge
comes first (Phase 1, behaviour unchanged), then the absorbed ZHA layer into the merged
structure (Phase 2). `current-state.md` remains the verified handoff for what exists today.

This redesign exists because the September lab sessions answered the questions v1 was built
to test. Some of v1's most careful work (the `onesti_lock` listener patch, the mirror source
codes, the vendor-app settings mirror) settled facts that v2 can now build on directly, and
one structural weakness — a local-only patch in a third-party integration — should not
survive into the product.

## 1. Goal

A lock that is **locally controlled by Home Assistant, always**, while the household's
vendor app keeps working as if the lock still sat on the vendor bridge: state, history and
notifications with who/how. One product, installable in stages (cloud only, or cloud + kit).

## 2. Principles

1. **The local path never depends on the cloud.** The lock is controllable from HA with the
   account or the bridge offline.
2. **One job per chip.** Zigbee stays on the lock's module, WiFi on the bridge. Never WiFi
   and Zigbee on the same ESP (v1 lesson, kept).
3. **No PIN codes in Home Assistant.** Attribute `0x0101` is never read, forwarded or stored.
4. **Fail locked.**
5. **Every self-healing action is observable** — sensor, log or repair. Silent recovery is
   not recovery.
6. **Convenience never becomes a security regression:** opt-in, auditable.
7. **Reads never actuate the lock.** The module is a sleepy end device: waking it means
   driving the bolt. The local facts layer reads opportunistically - right after the lock
   was awake for another reason, at startup without a wake-up, or on explicit request.
   Background code never wakes a lock.
8. English code, docs, commits; no real identifiers, IEEEs or secrets in the repo
   (`check_pii.py` in CI).

## 3. Components (v2)

```
                    ┌─────────────────────────┐
   vendor cloud ───►│ Nimly Connect Bridge     │  Zigbee 3.0 (ep 11, DoorLock 0x0101
   (app, push)      │ (vendor hardware)        │  + Onesti 0x0100/0x0101)
                    └──────────┬──────────────┘
                               │ coordinator
                    ┌──────────▼──────────────┐
                    │ ESP32-C6 emulator (fw)  │  looks exactly like the module (IEEE!)
                    └──────────┬──────────────┘
                         UART  │ settings + mirror frames + OTA ferry
                    ┌──────────▼──────────────┐   WiFi/MQTT   ┌──────────────────────┐
                    │ ESP32-C3 bridge (fw)    ├──────────────►│ Home Assistant        │
                    └─────────────────────────┘               │  integration `nimly` │
                                                              └──────────┬───────────┘
   real lock ◄──────── Zigbee (ZHA) ─────────────────────────────────────┘
```

- **Real lock + original module on ZHA** — the local truth and the only lock HA talks to.
- **C6 emulator** on the vendor bridge — the vendor cloud's view of the lock. Mirrors state,
  follows settings, carries the real module's IEEE.
- **C3 bridge** — UART ⇄ WiFi/MQTT translation, and the OTA ferry for the C6.
- **HA integration** — owns the ZHA link, the slot table, the mirror engine and the cloud
  client. One integration `nimly` with three entry types (`cloud`, `mirror`, `bridge`) —
  decided, see §5.

## 4. Facts v2 must respect (measured, see `current-state.md`)

- **The vendor cloud's source bytes** (measured by writing frames through the emulator):

  | byte | meaning in the cloud feed |
  |---|---|
  | `0x00` | "Key" — a mirrored HA lock/unlock, creates an activity entry |
  | `0x01` | "Button" |
  | `0x02` | `DOORLOCK_UNLOCKED_BY_PIN` |
  | `0x03` | `DOORLOCK_UNLOCKED_BY_FINGER` |
  | `0x0A` | `DOORLOCK_*_FROM_APP` — state updates silently, no activity entry |

  The mirror keeps `0x00` for HA-originated operation events and `0x0A` for auto-relock.
  No source in `0x00–0x12` produced `DOORLOCK_AUTOLOCKED` for this model.
- **`0x0100` is profile-wide** (manufacturer code 0), sent as
  `[slot u16][action u8][source u8]`. The ZHA quirk (and `onesti_lock`) only listens for the
  manufacturer-specific form, which is why v1 saw nothing: the information *is* on the wire.
  A raw cluster listener sees it and attribution works end to end (verified: PIN unlock →
  slot + `_BY_PIN` + `userName` → app push with the right name).
- **Report-on-change:** the module sends `0x0100` only when the 32-bit value changes.
  Repeated unlocks with the same credential produce no Zigbee report; history for those comes
  from the cloud feed alone.
- **Master credentials are a precondition.** A master PIN and a master fingerprint must exist
  on the lock before it is paired to the bridge; without them local reporting stops. A lock
  factory reset wipes them — the integration must warn and verify before pairing.
- **The vendor bridge can wedge:** app commands reach the lock while state feedback to the
  cloud stops. Today's recovery is a power cycle. v2 must detect it and say so (repair).
- **Firmware flashes wipe RAM-backed settings** on the emulator (auto-lock, volume, battery).
  The reboot detector on the HA side re-syncs after reboot — keep that behaviour.
- **The vendor app may attempt firmware OTA against "the module".** The emulator must refuse
  gracefully and never accept a vendor image.
- The app's settings display (`settings.autolock`/`settings.volume`) is **cloud-stored**; no
  module attribute report updates it. Driving it from HA needs the vendor write endpoint
  (open item, §9).
- **Attribute reads work; read commands do not.** The module answers standard DoorLock
  attribute reads (through the ZHA cluster object) but silently ignores the read commands
  (`GetUserStatus`, `GetUserType`, `GetPINCode`, `GetRFIDCode`, `GetLogRecord`), even while
  awake and answering `LockDoor`. Attribute `0x0101` (last used PIN) is therefore readable -
  and deliberately never read (principle 3).
- **Capabilities (measured 2026-09-21):** 100 users total - 50 PIN + 50 RFID; PIN length
  4-8, RFID length 4-8. Settings: `auto_relock_time` (`0x0023`, 1 = auto-lock on),
  `sound_volume` (`0x0024`, 0-2). `operating_mode`/`supported_operating_modes` are
  unsupported (ZCL status `0x86`).
- **There is no door sensor.** `door_state` (`0x0003`) is readable but always reports `4`
  (unspecified), before and after lock/unlock; neither the cloud feature list nor the action
  vocabulary has a door event. Door status, if wanted, comes from a separate contact sensor.
- **Slot rules (from the vendor manuals, see the onesti-lock project):** slots 0-2 are
  reserved for master credentials on every model but the Code Pro (which reserves only slot
  0 and documents 1-999 as user slots); user slots start at 3. Fingerprints have their own
  numbering (000-199 on the Touch Pro/EasyFingerTouch), so a fingerprint 5 and a code 5 can
  be two different credentials - the source byte of `0x0100` tells them apart. Codes are 4-8
  digits. `set_pin`, `clear_pin` and `clear_slot` refuse slots below the per-lock reserved
  count and slots at or above the lock's reported PIN capacity (`pin_rules.py`); naming
  stays allowed on every slot.
- **The cloud knows users, not slots.** `GET /devices/{id}/access` lists a user's credential
  types (pin/tag/finger/otp/digitalKey) with the user id, never the slot number - the gateway
  translates internally. Slot names therefore stay local; the cloud only supplies name
  suggestions for freshly learned slots when exactly one user fits (`facts.suggest_user_name`).

## 5. Decisions (ratified 2026-09-20)

### D1 — Absorb the ZHA access layer; `onesti_lock` becomes optional

Today the attribution path depends on a **local-only patch inside `onesti_lock`**, which
would silently disappear on the next HACS update. That is the strongest single reason for v2.

Take over, inside our integration:

- ZHA device lookup by IEEE, and cluster access for endpoint 11 / `0x0101`.
- A **controller-level packet listener** (not attached to a cluster object) that decodes
  `LockState` and `0x0100` in both profile-wide and manufacturer-specific form, and survives
  ZHA device rebuilds.
- The ZCL surface the product uses: `SetPINCode` `0x05`, `ClearPINCode` `0x07`, read-only
  `GetUserStatus`, and the raw fingerprint commands `0x71`/`0x72`. Codes are write-only here
  too — never read back.
- A slot table with names and occupancy, exposed as our own entities.

Compatibility: if `onesti_lock` is installed, offer a one-time import of its slot names
(consented), then recommend removing it. We upstream the raw-listener fix to the `onesti_lock`
project regardless — good citizenship and a fallback for people running it standalone.

Risks: zigpy/ZHA internals move (pin a tested range, unit-test the decode); multi-model
scope (start with the models we own).

### D2 — C6 firmware updates: UART ferry from the C3, after a one-time dual-OTA reflash

The C6 partition table today is a single `factory` app partition — no `otadata`, no second
slot — so self-update is impossible until it is reflashed once over USB with a dual-OTA
layout (8 MB flash is roomy).

Update flow, all inside the product:

1. HA shows an `update` entity for the emulator (installed vs latest from a signed,
   checksummed manifest).
2. The C3 downloads the image over HTTPS (SHA-256 verified) and streams it to the C6 over
   UART in chunks with CRC.
3. The C6 writes the **inactive** OTA partition, verifies the digest, switches boot slot and
   reboots.
4. The new firmware calls `mark_app_valid` only after it has rejoined the vendor bridge and
   said hello to HA. Otherwise the bootloader rolls back automatically.
5. The integration verifies the running version via `hello` and re-syncs settings (the
   existing reboot detector).

Why not WiFi on the C6 (considered): it shares the radio (forbidden by principle 2), needs
credentials on the C6, and requires a Zigbee stack stop/restart against the vendor bridge.
The C3 already has dual OTA and rollback for itself; it is the natural ferry. BLE maintenance
channel: possible, not chosen.

### D3 — One integration `nimly` (decided 2026-09-20)

| | two integrations | one `nimly` (chosen) |
|---|---|---|
| install | stage by stage | one install; entries added as needed |
| sharing code | duplication or vendoring | direct |
| distribution | one HACS repo per integration | one repo |
| migration | — | entries are recreated (pre-release: cheap); services move to the `nimly` domain |

Three entry types: `cloud` (vendor account), `mirror` (lock mirror), `bridge` (C3
provisioning) — mirror and bridge keep the split they have today. The layers stay logically
separate inside the package; only the packaging changes. The old packages keep running live
until feature parity is verified, then entries are recreated and the old packages removed.

## 6. Runtime model

- **Mirror:** real lock state ⇒ HA lock state ⇒ emulator frame with source `0x00` (activity
  entry) / `0x0A` (auto-relock, silent). Settings the app writes to the module
  (`0x0023`/`0x0024`) are applied to the real lock, verified both directions. A setting
  write from HA goes the other way too: the local write is confirmed first, then the app's
  cloud record is updated best-effort (`PATCH /devices/{id}/settings`) so the app display
  follows and the drift check stays empty.
- **Attribution:** raw `0x0100` from the real lock ⇒ decode slot/action/source ⇒ slot name ⇒
  cloud activity plus app notification. When the report is absent (report-on-change), the
  cloud feed's own history is the record.
- **Reconciliation:** beyond reboot resync, a periodic drift check compares real lock vs
  emulator state/settings and corrects, with an observable log entry.
- **Health:** bridge heartbeat (`hello`, vendor time), state-feedback freshness; a stale
  feedback reading becomes a repair telling the user to power cycle the bridge.
- **Provisioning:** master-credential precondition check, install code + IEEE pairing,
  zero-touch via Improv (kept from the v1 roadmap).

### Local facts layer (added 2026-09-21)

The mirror reads the lock's own standard DoorLock attributes - capabilities and settings,
never credentials - through the ZHA cluster object (`mirror/facts.py` plus
`read_attributes`/`write_attributes` in `zha_link.py`). Reads happen at startup (best
effort, no wake-up), opportunistically right after a lock-originated activity (at most once
per five minutes) and on explicit request; a failed read is not an error, the lock is
simply asleep and gets read the next time it is awake anyway. Setting writes
(`nimly.set_auto_lock`, `nimly.set_sound_volume`) are confirmed by reading the attribute
back. A drift check compares the lock's own values with the app's record
(`app_autolock`/`app_volume`), logs any difference and exposes it on the `lock_facts`
diagnostic sensor (state: the capability summary; attributes: own settings, app values,
drift, timestamp).

## 7. Observability

- Sensors: bridge reachable, emulator firmware version, settings-sync age, last local event,
  last cloud event, lock facts (capabilities, own settings, drift vs the app).
- Repairs: master credentials missing, bridge wedged (stale feedback), firmware mismatch or
  rollback happened, ZHA device rebuilt.
- Diagnostics with redaction; no PIN codes in any log.

## 8. Testing and release gates

- CI: unit tests, decode fixtures for `0x0100`/`LockState`, PII check, lint.
- Hardware-in-the-loop: MQTT injection, capture scripts, mirror + attribution end-to-end.
- Release gate: attribution and mirror verified on real hardware after a full
  reflash → rejoin → resync cycle.

## 9. Open items carried forward

1. **Vendor settings-write endpoint** — found in the onesti-lock OpenAPI spec
   (`PATCH /devices/{id}/settings`), see item 6. No phone-side capture needed.
2. **Repeated same-credential unlocks** produce no Zigbee report; attribution for those is
   history-only by nature.
3. **Multi-model scope** for the absorbed ZHA layer (start with our own models).
4. **Push CI** blocked: the stored GitHub token lacks the `workflow` scope.
5. **Device claiming (cloud):** `POST /devices` and `GET /home/{id}/new-devices/{type}` can
   add a new module to the account (needed for locks 2-3). Reverse-engineered and unverified;
   do it with the second lock at hand.
6. **Vendor settings write — done:** `PATCH /devices/{id}/settings` is verified on
   hardware (`autolock` bool, `volume` silent/low/high). `set_auto_lock` and
   `set_sound_volume` push the app's record best-effort after the local write confirms, so
   the app display follows HA and the drift check stays empty.

## 10. Phases

0. Ratified 2026-09-20 (D1–D3 in `beslut.md`).
1. **The merge:** one `nimly` package, three entry types, behaviour unchanged. Old
   integrations keep running live until a feature-parity check, then entries are recreated
   and the old packages removed. Migration note for the service domains.
2. **Absorb the ZHA layer** inside `nimly`; onesti import path; upstream the listener fix.
3. One-time C6 dual-OTA reflash + C3 UART ferry + `update` entity + rollback drill.
4. Robustness: bridge health/repair, drift reconciliation, vendor-OTA refusal.
5. Provisioning polish, release gates, HACS distribution.
