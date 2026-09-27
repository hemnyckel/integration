# Contributing

Thanks for considering a contribution.

## Language

Everything committed to this repository is written in **English**: code, comments, docstrings,
documentation, commit messages, pull request descriptions and release notes. Translation files
under `translations/` are the only exception.

## Ground rules

- **Never commit real device or account data.** That includes IEEE addresses, serial numbers,
  MAC addresses, account and location identifiers, tokens, PIN codes and firmware binaries
  built for a specific device. Use the placeholders already present in the code.
- **No PIN codes in Home Assistant.** ZCL attribute `0x0101` is write-only; never read,
  forward, log or store it.
- **Keep it local.** The mirror layer has to keep working with no internet access; nothing may
  require an external service to unlock the door.
- **Never make convenience a security regression.** New behaviour is opt-in, auditable and
  fails locked.
- **One change per pull request.** Small, reviewable, with a clear reason.

## Development

The integration is a plain Home Assistant custom component. To try a change against a live
installation:

```bash
tools/sync_to_ha.sh /path/to/homeassistant/config
```

That copies `custom_components/hemnyckel` into the target configuration directory. Restart Home
Assistant (or reload the integration) to pick up the change.

Checks that run in CI:

```bash
python3 -m compileall -q custom_components
python3 -m unittest discover -s tests -v
python3 tools/check_pii.py          # no real identifiers or secrets
```

Home Assistant's own `hassfest` and the HACS validation action run on every push once the
integration is published.

## Commit messages

Short imperative subject, then a body explaining *why* when it is not obvious. Prefix with the
layer when it helps: `mirror: ...`, `bridge: ...`, `firmware: ...`, `docs: ...`.
