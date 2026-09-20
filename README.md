# nimly

A Home Assistant integration for Nimly locks that keeps **local control** and the **vendor
app** at the same time: the lock's module stays on ZHA, and a small ESP32 emulator keeps the
vendor bridge and app working. Home Assistant never stops being able to open the door —
internet or not.

> **Status: v2, under construction.** The package is being rebuilt from the lessons of the
> v1 integrations (`nimly_cloud` + `nimly_shadow`). The cloud, mirror and bridge layers are
> ported, and the mirror owns its ZHA link — the raw `0x0100` listener, the ZCL commands and
> the slot table — so `onesti_lock` is no longer needed. Next: the C6 OTA reflash. Nothing
> here is installable yet — the design and the phased plan live in
> [docs/v2-arkitektur.md](docs/v2-arkitektur.md).

## What this repository is

One integration, `custom_components/nimly`, with three entry types:

| Entry type | What it is |
|---|---|
| `cloud` | The vendor account: history, who/when/how, and an optional second control path. |
| `mirror` | The local mirror: HA ↔ the lock ↔ the emulator the vendor app talks to. |
| `bridge` | Provisioning and updates of the ESP32 bridge (Wi-Fi/Improv, OTA). |

## What lives where

- **This repository** — the Home Assistant integration, its tests and CI.
- **[nimly-tools](https://github.com/c14ym0re/nimly-tools)** — firmware (ESP32-C6 emulator,
  ESP32-C3 bridge), the bridge protocol contract, the flasher and web-flash tooling, and the
  lab documentation.

## Development

```bash
python3 -m compileall -q custom_components   # syntax
python3 -m unittest discover -s tests -v     # unit tests (no Home Assistant needed)
python3 tools/check_pii.py                   # no real identifiers or secrets
tools/sync_to_ha.sh /path/to/homeassistant/config
```

Home Assistant's own `hassfest` runs on every push; the HACS validation action joins at
publication.

## Principles

The local path never depends on the cloud. No PIN codes in Home Assistant or its logs. Fail
locked. Every self-healing action is observable. English code, docs and commits — only
`translations/*.json` are exempt. See [docs/v2-arkitektur.md](docs/v2-arkitektur.md).

## Migration

Coming from `nimly_cloud` + `nimly_shadow`? The step-by-step plan is in
[docs/migration.md](docs/migration.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
