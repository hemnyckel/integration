# Agent instructions — nimly

This repository is the Nimly Home Assistant integration and its firmware. It controls
physical access to a home; treat everything here as security relevant.

## Rules

- Everything committed is **English**: code, comments, docstrings, docs, commit messages.
  Translation files under `translations/` are the only exception.
- **Never commit real device or account data**: no IEEE addresses, serial numbers, MACs,
  account or location identifiers, tokens, PIN codes or device-specific firmware. Run
  `python3 tools/check_pii.py` before you finish.
- **No PIN codes in Home Assistant**: never read, forward, log or store ZCL attribute
  `0x0101`. Codes are write-only.
- The local path must never depend on the cloud. Convenience is opt-in and fails locked.
- One change at a time; keep the diff reviewable and the reason in the commit message.

## Checks — all three must pass

```bash
python3 -m compileall -q custom_components
python3 -m unittest discover -s tests -v
python3 tools/check_pii.py
```

The unit tests deliberately load pure modules by path, so they run without a Home Assistant
installation. Anything that imports `homeassistant` is verified on hardware through
`tools/sync_to_ha.sh` instead.

## Layout

- `custom_components/nimly/` — the integration. `mirror/` hosts the local layer, the bridge
  provisioning and the ZHA link in `zha_link.py`. Platform files (`sensor.py`,
  `binary_sensor.py`, …) are thin routers that dispatch each config entry to its layer.
- `firmware/` — the two ESP-IDF projects (ESP32-C6 emulator, ESP32-C3 bridge) and the
  browser-flashing assets under `firmware/webflash/`.
- `www/nimly-guests-card.js` — the bundled Lovelace card (copy it to the Home Assistant
  configuration's `www/` to use it); the sources live here.
- `docs/` — architecture, hardware, flashing, protocol, guests, privacy, dashboard.
- `tools/` — the sync-to-HA helper and the PII check.
