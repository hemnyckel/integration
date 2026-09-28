# The people card

The repository ships one Lovelace card for people and their codes:
[`www/hemnyckel-guests-card.js`](../www/hemnyckel-guests-card.js). It reads the
integration's guests sensors (the person records) **and** its slots sensors
(what each slot holds), and discovers the locks itself, so it works with no
configuration at all. Pin one door with `entity: sensor.<door>_guests`, for
example `sensor.ytterdorren_guests`, to make that door the card's own. When a
lock's sensor is missing, the card falls back to the slots table so a credential
is never hidden.

## Install

1. Copy the file to `<config>/www/hemnyckel-guests-card.js`.
2. **Settings → Dashboards → ⋮ → Resources → Add resource**:
   `/local/hemnyckel-guests-card.js` with **JavaScript module**.
3. Hard-refresh the browser, then add a card:

```yaml
type: custom:hemnyckel-guests-card
```

## What it does

- **Create in three taps**: name, then *Temporary* (duration chips and a
  one-time switch) or *Recurring* (day chips plus time rows you add and remove).
  The form says plainly whether the code is shown once or stored: a temporary
  code *visas bara en gång*, a permanent one is *sparad och kan återställas*.
- **The code is shown once** for a temporary or one-time person, big, with
  buttons to copy it, send it as an SMS (mobile only; the format adapts to iOS
  and Android) or share it over WhatsApp.
- **Live state per person**: a green *Active* pill inside a window, an amber
  *Outside* one, a grey *Paused* row that is dimmed at a glance.
- **Edit** (pencil): rename, change the schedule, set a new expiry, or give a
  new code — leave the code field empty to keep the current one.
- **Pause/resume and revoke**, the latter with a built-in confirmation.

The card is written in plain JavaScript with Home Assistant's own theme
variables, so it follows light and dark themes without configuration.

## Notes

- The UI text is Swedish; the card is self-contained, so changing the strings is
  a matter of editing the file.
- After replacing the file, bump the resource URL (for example
  `/local/hemnyckel-guests-card.js?v=4`) so browsers fetch the new version.
- The card is also mirrored in this repository, so improvements should go to
  `www/hemnyckel-guests-card.js` and be copied to Home Assistant, not the other way
  around.


## The person list

The card shows this integration's own people — create, edit, pause, revoke —
with their slot, what the slot holds (`Kod`, `Fingeravtryck`) and the validity
window. A row can also come straight from the slots table (a named slot with a
credential but no person record); it is marked *Bara i slot-tabellen* and can be
renamed or cleared like any other slot. RFID is never shown: the lock reports no
tag use ([known-issues.md](known-issues.md)).

