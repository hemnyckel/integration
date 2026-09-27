# Multiple locks

The home can have several locks. This page describes how the integration treats
a lock as a self-contained entry and what that means when there is more than one.

## One entry per lock

A **mirror config entry per lock** owns everything local: slots, guests with
their schedules, the journal and events, all in that entry's options. There is no
shared account and no cross-lock state to keep in step, so a second lock is just
a second entry.

The guest card discovers every lock from the guests sensors and, when there is
more than one, offers a lock picker: create a guest once, choose the doors, and
one code lands on each with a shared group marker, reporting a lock that fails
without losing the rest. Edit, pause and revoke follow the group, so a person
keeps the same code across the locks you chose.

## Presentation

Presentation is per lock: each lock's guests sensor exposes only that lock's
records. A lock that is not reachable simply shows nothing new; the others are
unaffected.

## Hardware

The integration has no hardware limit of its own: add as many mirror entries as
there are locks on ZHA.

## Open questions

- Should a person's code be the same on every lock? Today each mirror creates
  its own code; reuse would need the value written per lock.
