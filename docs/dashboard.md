# The guest-code card

The repository ships one Lovelace card for guest codes:
[`www/nimly-guests-card.js`](../www/nimly-guests-card.js). It needs the
integration's `sensor.nimly_guests` entity and nothing else.

## Install

1. Copy the file to `<config>/www/nimly-guests-card.js`.
2. **Settings → Dashboards → ⋮ → Resources → Add resource**:
   `/local/nimly-guests-card.js` with **JavaScript module**.
3. Hard-refresh the browser, then add a card:

```yaml
type: custom:nimly-guests-card
```

## What it does

- **Create in three taps**: name, then *Temporary* (duration chips and a
  one-time switch) or *Recurring* (day chips plus time rows you add and remove).
- **The code is shown once**, big, with buttons to copy it, send it as an SMS
  (mobile only; the format adapts to iOS and Android) or share it over WhatsApp.
- **Live state per guest**: a green *Active* pill inside a window, an amber
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
  `/local/nimly-guests-card.js?v=2`) so browsers fetch the new version.
- The card is also mirrored in this repository, so improvements should go to
  `www/nimly-guests-card.js` and be copied to Home Assistant, not the other way
  around.


## The app's guests, in the same card

The guest card shows two lists. The top one is this integration's own guests —
create, edit, pause, revoke. The **"I appen"** section below it is the vendor
account's guest users (from `nimly.cloud_guests` / `sensor.nimly_cloud_guests`):
read-only identities the app created, with their credential badges (PIN, finger,
tag), their validity window, and a warning badge when the cloud reports a sync
error (`ERROR_UPDATING_DOORLOCK_ACCESS` — the app's "(!)").

Names already managed locally are left out of the cloud list, so a guest never
appears twice. The refresh button re-reads the account.
