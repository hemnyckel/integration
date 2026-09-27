# Security Policy

## Reporting a vulnerability

Open a private security advisory on the repository, or contact the maintainer directly through
the address on the GitHub profile. Please do not open a public issue for anything that could be
exploited — in particular anything that could unlock a door, leak a credential, or let a third
party reach a lock.

## Scope

This project controls physical access to a home. Treat every part of it as security relevant.

In scope for this repository:

- The local layer: the ZHA ownership, the lock control path, the slot table, the journal and
  guest codes.

## Design commitments

- **Fail locked.** A loss of connectivity, a crash or a malformed message must never result in
  an open door.
- **Secrets never leave their store.** A stored guest code lives only in the config entry's
  options, is never logged, and is never written to the repository. Diagnostics redact it.
- **No PIN codes in Home Assistant.** ZCL attribute `0x0101` is write-only for us.
- **Convenience is opt-in.** Automatic unlocking, schedules and any other convenience feature
  must be explicitly enabled, auditable afterwards, and never a single factor.
- **The lock is driven locally.** Everything runs over the Zigbee link Home Assistant already
  owns; no external account, service or extra hardware is in the path.

## What is *not* a vulnerability

- A vendor-side change making an entity unavailable. The integration relies only on the local
  Zigbee link, so report it as a bug.
- The owner's own data on their own hardware.
