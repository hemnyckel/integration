# Agent instructions — nimly

This repository is the Nimly Home Assistant integration (v2). It controls physical access
to a home; treat everything here as security relevant.

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

- `custom_components/nimly/` — the integration. `cloud/` and `mirror/` (which also hosts
  the bridge provisioning and the ZHA link in `zha_link.py`) are ported. Platform files
  (`sensor.py`, `binary_sensor.py`, …) are thin routers that dispatch each config entry to
  its layer.
- `docs/v2-arkitektur.md` — the ratified design; the phased plan is the last section.
- `docs/migration.md` — how the live v1 installation moves to v2.
- Firmware and the bridge protocol contract live in the `nimly-tools` repository.
