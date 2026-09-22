# Security Policy

## Reporting a vulnerability

Open a private security advisory on the repository, or contact the maintainer directly through
the address on the GitHub profile. Please do not open a public issue for anything that could be
exploited — in particular anything that could unlock a door, leak a credential, or let a third
party reach a lock.

## Scope

This project controls physical access to a home. Treat every part of it as security relevant.

In scope for this repository:

- The local mirror: the ZHA ownership, the lock control path, and the emulator's MQTT contract.
- The cloud layer: authentication, token storage, and anything that could expose account data.
- The firmware (ESP32-C6 emulator, ESP32-C3 bridge) in [`firmware/`](firmware/).

## Design commitments

- **Fail locked.** A loss of connectivity, a crash or a malformed message must never result in
  an open door.
- **Secrets never leave their store.** Tokens live in the config entry, are never logged, and
  are never written to the repository. Diagnostics redact them.
- **No PIN codes in Home Assistant.** ZCL attribute `0x0101` is write-only for us.
- **Convenience is opt-in.** Automatic unlocking, schedules and any other convenience feature
  must be explicitly enabled, auditable afterwards, and never a single factor.
- **The cloud cannot bypass the local layer.** The vendor cloud talks to an emulator we
  control, never directly to the module inside the door. Every cloud-originated command passes
  through the local mirror, which can be audited and can refuse.

## What is *not* a vulnerability

- The vendor API changing and an entity becoming unavailable. That is expected for an
  undocumented interface; report it as a bug.
- The cloud account having access to the vendor's own data. That is the account owner's own
  data, accessed with their own credentials.
