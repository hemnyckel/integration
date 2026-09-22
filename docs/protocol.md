# Protocol

Two contracts hold the product together: **MQTT** between Home Assistant and the
bridge, and the **UART line protocol** between the bridge and the emulator. The
emulator's Zigbee side speaks the vendor's own ZCL dialect.

Byte-level details live in the firmware sources; this page is the map.

## MQTT

The bridge announces itself on a retained topic outside its own prefix:

```json
nimly/info
{"bridge": "<mac>", "fw": "0.5.1", "prefix": "nimly/proxy",
 "target": "esp32c3", "model": "Nimly Shadow Bridge"}
```

Everything else lives under the announced prefix (default `nimly/proxy`):

| Topic | Direction | Content |
|---|---|---|
| `ha_to_bridge` | HA → bridge → emulator | Commands, see below. |
| `bridge_to_ha` | emulator → bridge → HA | Events, see below. |
| `state` | emulator → HA | `{"lock": "locked"}` on every change. |
| `battery` | emulator → HA | `{"battery": 100}`. |
| `pin` | emulator → HA | App-driven credential changes: `{"ev": "pin_set", "slot": 7, "code": "..."}` / `{"ev": "pin_clear", "slot": 7}`. The code is never logged. |
| `ota` | bridge → HA | OTA progress and result. |

### Commands (HA → emulator)

`{"cmd": "..."}` with an optional `value`:

| Command | Effect |
|---|---|
| `lock` / `unlock` | Actuates the *emulated* module (mirrored onto the real lock by the integration). |
| `get_state` | Health ping; the emulator answers on `state`. |
| `volume` / `autolock` / `battery` | Sets the emulated values so the app sees the real lock's settings. |
| `event` | Replays a lock operation to the bridge (`action`, `source`, `slot`) so the app's history stays truthful. |
| `pin_status` | Tells the emulator which virtual slots the app should consider occupied. |
| `set_ieee` | Provisions the module's IEEE address (the real module's); survives a factory reset. |
| `factory_reset` | Wipes the Zigbee stack (not the IEEE or the integration's NVS). |
| `ota` | The bridge downloads and installs its own firmware. |
| `ota_c6` | The bridge ferries an image to the emulator over UART: `url`, `sha256`, `version`. |
| `nack_next_pin` | Test hook: the next `SetPINCode` is answered FAILURE (used to measure vendor behaviour). |

### Events (emulator → HA)

`{"ev": "..."}`:

| Event | Fields | Meaning |
|---|---|---|
| `hello` | `fw`, `ieee` | Boot announcement; a long gap means the emulator rebooted, so the mirror re-asserts settings. |
| `net` | `joined` | Zigbee network membership. |
| `action` | `action`, `source`, `slot` | A command arrived from the bridge (the app pressed something). |
| `fp_enroll` / `fp_clear` | `slot` | The app enrolled or cleared a fingerprint. |
| `volume` / `autolock` | `value` | The app wrote one of the module's settings. |

## UART (bridge ↔ emulator)

115200 baud, one JSON object per line, both directions. The bridge relays
`ha_to_bridge` payloads down the wire and forwards emulator lines back up,
mapping the pin/state/battery/ota lines onto their topics. The emulator's own
log goes to its console (UART0/USB-JTAG), never onto the link, so diagnostics
stay readable.

## Zigbee (emulator ↔ Nimly Connect Bridge)

The emulator speaks the vendor's ZCL dialect on the Door Lock cluster (`0x0101`)
and reports operations profile-wide on attribute `0x0100` — a bitmap32:

| Bits | Meaning |
|---|---|
| 0–15 | user slot (0 = no user; with a human source, the master credential) |
| 16–23 | action: `0x01` lock, `0x02` unlock |
| 24–31 | source: `0x00` zigbee/remote command, `0x02` keypad, `0x03` fingerprint, `0x04` tag, `0x05` unattributed, `0x0A` auto |

Measured on a real lock: zigbee commands, remote unlocks and the lock's own
auto-relock all report **`0x05` unattributed**, while physical keypad/finger/tag
use is attributed. The vendor app maps `0x00` to *Key* in its history; the
emulator therefore never uses it for remote commands.

Commands the emulator answers: `SetPINCode` (0x05), `ClearPINCode` (0x07),
`GetPINCode` (0x06), `GetUserStatus` (0x0A, empty), `LockDoor`/`UnlockDoor`
(0x00/0x01) and the vendor's fingerprint commands `0x71`/`0x72` with the
measured response shapes.

Standard attributes are readable for diagnostics (lock state, capabilities,
`auto_relock_time`, `sound_volume`); attribute `0x0101` — the PIN material — is
**never read**, by rule and by code. The module has no schedules and no user
status: the vendor's own module specification states it, so validity windows and
disabling are Home Assistant's job (see [guests.md](guests.md)).

## OTA ferry

Both boards use a dual-OTA partition layout with rollback. The bridge updates
itself from an image URL (`ota`). For the emulator, the bridge streams the image
to the C6 over the same UART link with `ota_begin` / `ota_chunk` / `ota_end` /
mark-valid messages carrying size, SHA-256 and version; the C6 validates and
confirms, and a failed image rolls back on the next boot. The manifest the
integration reads is described in [flashing.md](flashing.md).
