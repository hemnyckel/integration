# Building, flashing and pairing

Everything from an empty board to a working mirror. The walkthrough assumes
ESP-IDF is installed; the browser route needs no toolchain at all.

## 1. The emulator (ESP32-C6)

### Browser (no tools)

Open [`firmware/webflash/`](../firmware/webflash/) (in a checkout, or the GitHub
Pages copy), plug the C6 into USB and press **Install**. The image contains no
credentials and is safe to flash to any C6. Chrome, Edge or Opera on desktop
only — Web Serial needs a Chromium browser.

### Command line

```bash
cd firmware/esp32c6-nimly-ed
idf.py set-target esp32c6
idf.py build
idf.py -p /dev/ttyACM0 flash
```

The prebuilt parts and their offsets (for a blank chip) live in
`firmware/webflash/manifest-emulator.json`: bootloader at `0x0`, partition table
at `0x8000`, `ota_data_initial` at `0xf000`, application at `0x20000`.

`main/secrets.h` is optional (there are no network credentials in the emulator);
copy `secrets.h.example` only if you need to override something.

## 2. The bridge (ESP32-C3)

The bridge's image embeds **your** Wi-Fi and MQTT credentials, so it is built
locally:

```bash
cd firmware/esp32-uart-mqtt-bridge
cp main/secrets.h.example main/secrets.h   # fill in Wi-Fi + MQTT
idf.py set-target esp32c3                  # or esp32 for a classic board
idf.py build
idf.py -p /dev/ttyUSB0 flash
```

`main/secrets.h` is gitignored — never commit it, and never publish an image
built from it. The bridge announces itself on the retained topic `nimly/info`,
which is how Home Assistant finds its prefix and firmware version.

Alternative Wi-Fi setup: leave `NIMLY_WIFI_*` empty in secrets and let Home
Assistant provision the Wi-Fi over Bluetooth with **Improv**: the unprovisioned
bridge shows up as a discovered device and the card asks for your network.

## 3. Wire and power

Three wires (see [hardware.md](hardware.md)): C6 `GPIO6 →` C3 `GPIO5`, C6
`GPIO7 ←` C3 `GPIO4`, plus `GND ↔ GND`. Power each board from its own USB or
3V3 supply.

## 4. Home Assistant

1. Install the integration (HACS or a manual copy) and restart.
2. **Add integration → Hemnyckel → Nimly account** (the vendor app's email and
   password) and pick the location. This is optional but recommended: it gives
   attribution and the app's view.
3. **Add integration → Hemnyckel → Lock mirror**, pick the lock entity and accept
   the auto-detected MQTT prefix. The emulator's firmware version should show up
   in the device's entities.
4. Add the lock's real module to **ZHA** if it is not already there.

## 5. Pairing the emulator with the vendor bridge

The emulator must join the Nimly Connect Bridge's Zigbee network wearing the
lock module's own IEEE address: the app keys every device record on that
serial, so the app treats the emulator as *this* lock. (Measured
2026-09-23: there is no serial whitelist — a fabricated sibling
address joined and was accepted — but the identity is what makes the emulator
this lock and nothing else.) The sequence that works, learned on hardware:

1. **Give the emulator the right IEEE** — the mirror device exposes
   `hemnyckel.set_ieee`; called without a value it uses the real lock's address.
   The address is stored in NVS and survives a factory reset.
2. **Make sure the emulator is factory-new** — a module that has been paired
   before will not start a fresh join. The `nimly` firmware accepts a
   `factory_reset` command over the bridge (it wipes the Zigbee stack, not the
   IEEE).
3. **Open the bridge's pairing window from the app** — the Nimly app's
   add-device search opens it. The emulator steers continuously and joins within
   seconds when the window is open.
4. **A stale device registration does not block a rejoin** — the emulator
   rejoining an existing network is enough. If the app path is stranded
   entirely (commands time out, no reports reach the app), remove the old lock
   in the Nimly app and start the app's add-device flow; the emulator steers on
   its own. Factory-resetting the vendor bridge is never part of the flow.
5. When the lock appears in the app, its name is applied automatically (the
   ZHA device's name wins). The mirror re-applies the lock's settings to the
   app's record automatically.

> **Power-cycle, never factory-reset.** A bridge that is offline in the app
> only needs its power pulled for ten seconds. Factory-resetting the bridge
> (or the module) is a last resort that requires this whole sequence again.

## 6. Updates

Both boards update over the air from Home Assistant:

- **The emulator** downloads through the C3's UART ferry: the integration reads
  an OTA manifest (by default
  [`firmware/webflash/ota.json`](../firmware/webflash/ota.json), changeable in
  the integration options) and the update entity installs it, with a
  rollback-safe dual-OTA layout.
- **The bridge** updates itself from the manifest's `builds` entry — point the
  manifest at your own build if you host one, since a public manifest cannot
  contain your credentials.

The manifest format is `{"emulator": {"version", "file", "sha256"}, "builds":
{"<target>": "<app.bin>"}}`; filenames are relative to the manifest URL.

## Troubleshooting

| Symptom | What to do |
|---|---|
| The app says *gateway connected to power* / offline | The vendor bridge's Wi-Fi session dropped. Power-cycle the bridge; check its 2.4 GHz signal. Faulty mains (a shared power strip) can look the same. |
| The app cannot find the emulator when adding a device | Remove the old lock registration in the app first; then start *add device* again while the emulator steers. |
| The emulator never joins | Confirm it is factory-new (a previous pairing blocks a fresh join) and that the bridge's window is open. The emulator's console (`capture-remote.sh`, or the serial port at 115200) shows the join attempts. |
| A code edit in the vendor app makes the bridge restart | A known vendor firmware quirk: the edit still lands, and Home Assistant never uses the bridge for edits. Expect a short gateway blip. |
| `emulator_not_joined` repair | The C6 is not on the bridge's network. Check power and the pair sequence above; the repair clears itself on the next join. |
