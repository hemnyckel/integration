# Fingerprints: several per person, re-enrolment, and across several locks

This document designs how the household gets **more than one finger per person**,
knows **which finger is which**, can **re-enrol** a finger without losing the
others by accident, and sees the whole thing **across several locks**.

It is a design, not a description of what exists. Two things must be said up
front, because everything below depends on them:

1. **The physical test in [§2](#2-the-one-physical-test-that-decides-the-model)
   has not been run.** It decides whether a slot can hold one template or
   several, and that single answer decides how fingers are stored. The design is
   written so both outcomes implement the same household model; only the
   storage rule and the capacity arithmetic change.
2. **The lock never reports which finger was used — only which slot.** No
   amount of software changes that. "Which finger" is a **label we keep**, and
   the document treats it as a claim, never as a measurement.

---

## 1. The facts we measured

All of this was read from the live, connected locks on the bench, read-only:
`hemnyckel.read_lock_attributes` over Home Assistant's REST API, the decoded
operation event, and the integration's own code. The two locks are
`Ytterdörren` and `Källardörren`; both are the same model and report the same
capabilities.

### 1.1 What the lock exposes about users and credentials

`hemnyckel.read_lock_attributes` (default set) returned, for both locks:

| Attribute | Ytterdörren | Källardörren |
|---|---|---|
| `lock_state` | 1 | 1 |
| `door_state` | 4 | 4 |
| `num_of_total_users_supported` | 100 | 100 |
| `num_of_pin_users_supported` | 50 | 50 |
| `num_of_rfid_users_supported` | 50 | 50 |
| `max_pin_len` / `min_pin_len` | 8 / 4 | 8 / 4 |
| `max_rfid_len` / `min_rfid_len` | 8 / 4 | 8 / 4 |
| `auto_relock_time` | 1 | 1 |
| `sound_volume` | 2 | 1 |

`operating_mode` and `supported_operating_modes` came back as status **134
(`0x86`, UNSUPPORTED_ATTRIBUTE)** — the lock does not implement them.

A wider probe of the Door Lock cluster (ids `0x0000`–`0x00B0` and
`0x0100`–`0x015F`, covering every standard credential attribute the zigpy build
knows) found:

- the same user-capacity attributes above;
- **no fingerprint attribute at all** — no "number of fingerprints", no
  "supported fingers", no per-user template count;
- `credential_rules_support` (`0x001B`) and
  `num_of_credentials_supported_per_user` (`0x001C`) are **not even defined in
  this zigpy build** (the probe skipped those ids), so "max templates per user"
  cannot be read here even in principle;
- attribute `0x0100` is exposed under the vendor name
  **`nimly_last_lock_unlock_source`**, and it read `83951616` = `0x05010000`:
  slot 0, action `lock` (`0x01`), source `unattributed` (`0x05`) — the last
  operation event, with no fingerprint information in it.

The integration's own `FACTS_ATTRIBUTES` (the background refresh) is the same
user-capacity set; there is nothing fingerprint-specific to refresh.

**Conclusion for 1.1:** the lock tells us how many *users* it supports (100
total, 50 PIN, 50 RFID) and nothing about fingerprints. Any fingerprint count
must be measured, not queried.

### 1.2 What a fingerprint unlock actually reports

The lock reports one attribute, `0x0100`, a 32-bit bitmap decoded by
`const.decode_operation_event`:

```
bits  0–15  slot   (0 when none)
bits 16–23  action (lock / unlock)
bits 24–31  source (keypad / fingerprint / rfid / unattributed / auto)
```

The integration attaches its own raw listener to the zigpy cluster and turns a
fingerprint report into `last_event` and a `hemnyckel_door_event` with
`source: "fingerprint"` and the **slot number**. ZHA's own quirk sensors
(`sensor.<lock>_last_action_user`, `_last_action_source`) carry the same three
fields and no more.

There is **no per-template identifier anywhere** in the report. A lock that
cannot distinguish two templates in one slot reports the identical value for
both; even a lock that keeps one template per slot reports only the slot, which
is enough only because the slot *is* the finger under that model.

**Conclusion for 1.2:** "which finger" can only ever be a label we keep.
The door event says "a fingerprint in slot N opened the door", never "the left
index opened the door". The UI must word it accordingly ([§4](#4-which-finger-is-which)).

### 1.3 How an enrolment works today

- `hemnyckel.enroll_fingerprint` takes `{slot, mode?}` and sends vendor command
  **`0x71`** to the slot (`ZCL_CMD_FP_ENROLL`). It lights the reader and
  returns; `mode` is `auto` or `local` and both enrol locally.
- The lock reports **nothing** while an enrolment runs. Nothing is proven by an
  enrolment.
- The slot table keeps `has_fingerprint` as a **hint** that an enrolment
  happened, and `finger_used` as the **only proof** — set when a fingerprint in
  that slot has actually opened the door (`slots.mark_credential`). At startup
  `correct_fingerprints()` drops any `has_fingerprint` mark that no use ever
  confirmed.
- Vendor command **`0x72`** (`ZCL_CMD_FP_CLEAR`) clears a slot's fingerprint.
  Today it is sent **only by the whole-lock `wipe`** (`coordinator.async_wipe_credentials`),
  which sweeps every user slot above the master floor.
- `hemnyckel.clear_slot` clears the **PIN** (`ClearPINCode`, `0x07`) and forgets
  the slot locally — it does **not** send `0x72`, so a fingerprint template
  stays on the lock while Home Assistant forgets it. That is a real gap for
  re-enrolment ([§5](#5-re-enrolment)) and must be fixed.
- The slot table is per lock, stored in that entry's options under `slots`:
  `name`, `has_pin`, `has_fingerprint`, `has_rfid`, `finger_used`. There is no
  finger label anywhere today.

### 1.4 What already exists that we can lean on

- **Slots are the unit the lock works in.** Everything is keyed by slot number;
  the master floor is `FIRST_USER_SLOT = 3` (`mirror/pin_rules.py`) and slots
  0–2 are never written.
- **Guest records already span locks.** A person created across doors carries a
  shared `group` field, and the bundled card folds the per-lock records together
  (`docs/multi-lock.md`). A live example exists on the bench: the same person
  occupies slot 3 on **both** locks with the same group, and `has_fingerprint`
  is `true` on one lock and `false` on the other — exactly the
  per-finger-per-door gap this design has to show.
- **The relay already attributes slots to people.** It resolves each door's
  `sensor.*_slots` from Home Assistant and exposes `GET /slots`, and a named slot
  is what turns a journal entry into "Elise" (`relay/README.md`,
  `docs/api.md`).
- **The role model is settled.** Owner / User / Guest live on paired devices in
  the relay, enforcement is in the relay, the first device is the owner and the
  last owner can never be demoted; Home Assistant owns the lock's side — people
  and codes, **fingerprints** and settings (see Hindsight: *Hemnyckel: roles
  (Owner / User / Guest)* and *Hemnyckel: MQTT-bryggan*). This design keeps that
  split.

---

## 2. The one physical test that decides the model

**Status: not run.** It is one action by the owner at a lock, and it decides
everything about how fingers are stored.

### The test

1. Pick a spare user slot (say slot 8) on one lock and name it for the test, or
   use the existing person's slot.
2. Enrol a first finger: `hemnyckel.enroll_fingerprint` with that slot, then
   touch finger A at the reader.
3. **Enrol a second, different finger into the same slot** (touch finger B at
   the same reader, same slot) — do not clear the slot in between.
4. At the door, try finger A, and separately finger B.

### What each outcome means

| Outcome | Meaning | Consequence |
|---|---|---|
| **Both A and B open the door, and the report still names the same slot** | The slot holds **several templates** (branch B) | One slot is a person's *finger set*; the lock can never say which one was used; removing one finger means clearing the slot and re-enrolling the others. |
| **A opens, B does not (or the second enrolment replaces A)** | The slot holds **one template** (branch A) | One slot is exactly one finger; "which finger" is the slot's label; a person with several fingers needs several slots. |

### Why it decides the design

A template lives in a **slot**, and the lock only ever reports the **slot**. So
the question "does a slot hold one finger or many?" is the same as "does the
slot identify the finger or only the person?". Under branch A the label is
redundant with the slot (one finger each); under branch B the label is the only
thing distinguishing a person's fingers, and it is unverifiable. The capacity
arithmetic and the meaning of "clear a finger" also differ completely.

**Everything else below is written to be identical for both branches.** Only one
policy value and the wording of two UI states change; the physical test chooses
it.

---

## 3. The model

### 3.1 Entities

```
person ──< finger (a label we keep)
   │
   └── has a slot on each lock where they have access
            │
            └── enrolment = (person, finger, lock, slot)
```

- **Person** — a member of the household. On the lock side this is the slot's
  `name`; where a person spans locks, the guest record's `group` is the join
  (`docs/multi-lock.md`). The relay's own person identity (from paired devices)
  is the app-side view of the same name.
- **Finger** — a **word**, chosen by the owner: "left index", "right thumb", …
  It is not data from the lock; it is a label we store ([§4](#4-which-finger-is-which)).
- **Lock** — one config entry / one mirror (one lock on ZHA).
- **Slot** — the lock's unit. A person has at least one slot per lock they can
  open; a slot belongs to exactly one person (a shared slot is not supported —
  see [§8](#8-what-must-not-change)).
- **Enrolment** — the act and the record of putting one finger into one slot on
  one lock. It is the thing that carries the label.

### 3.2 Storage: one record per slot, a list of labels

Add to the slot table a list under a new key:

```
slot 5 → { name: "Elise", has_pin: true, has_fingerprint: true,
           finger_used: false,
           fingers: [ { label: "left index", enrolled: "<iso>" } ] }
```

The **same shape covers both branches**:

- **Branch A (one template per slot):** `fingers` has at most one entry; the
  slot's label is that finger. A person with five fingers has five slots on the
  lock, each holding one finger.
- **Branch B (several per slot):** `fingers` has one or more entries; the slot
  is the person's finger set on that lock, and the label list is our belief about
  what is in it.

The list is the only new storage. It lives with the slot — i.e. **inside the
integration**, which is where the household's fingerprints belong, and the relay
reads it from the slots sensor. There is **one source of truth for labels**:
storing a copy in the relay as well would create a second thing to keep in step,
which is exactly what the existing design avoids.

### 3.3 The branch policy, in one place

Put the decision in one pure module (`mirror/fingers.py`, no Home Assistant
imports, loadable by the unit tests like `guests.py` / `pin_rules.py`):

```python
# Set from the physical test (§2). One slot holds at most this many templates.
TEMPLATES_PER_SLOT = None   # None = unbounded (branch B); 1 = branch A
```

- `can_add(slot_data, label) -> str | None` returns a refusal reason when the
  policy forbids another template in this slot.
- `finger_state(slot_data) -> "none" | "claimed" | "confirmed"` derives the
  display state: `none` = no label, `claimed` = labelled but no use ever
  confirmed, `confirmed` = `finger_used`.
- `plan_slots(person, fingers, locks, branch)` computes the slots a person
  needs — used by the UI for the capacity budget.

Everything that reads or writes fingers goes through this module, so flipping
the branch after the test is a one-line change plus tests.

### 3.4 Enrolment today vs. the model

The enrolment action itself barely changes: `enroll_fingerprint` still lights
`0x71` on a slot. What changes is that the caller now **names the finger**, and
the result is a **record** rather than a bare `has_fingerprint` hint. Because
the lock still reports nothing during enrolment, the record starts as
`claimed`; only a real finger unlock in that slot (`finger_used`) moves it to
`confirmed`.

---

## 4. Which finger is which

**The label is a claim, never a measurement.** The lock reports a slot, and at
most "a fingerprint in slot N opened the door". It never reports a template id,
and it never will.

Consequences the implementation must honour:

- **The label is chosen when the enrolment happens.** The enrolment flow asks
  for it (a picker of canonical names, plus a free-text option) and stores it in
  the slot's `fingers` entry with the enrolment time.
- **The UI always shows the label** wherever a finger is shown — the app's
  Koder view, the Personer card, the slots sensor. A slot with a fingerprint but
  no label is shown as *unlabelled finger*, and the owner is offered a way to
  label it (for slots that predate this feature).
- **A mismatch between the label and reality cannot be detected.** If the owner
  labels a finger "left index" but really enrolled the left thumb, the door
  event will look identical and nothing in the system can tell. There is no
  self-test that can catch it.
- **So the wording must never overclaim.** The UI says *"Elise unlocked with a
  finger"* and shows the label as the label we kept — not *"Elise unlocked with
  her left index"*. A confirmed slot means *some* finger in that slot has opened
  the door; under branch B it does **not** mean the finger you think is in
  there. Suggested wording:
  - label: `Left index` (a chip/noun, not a sentence);
  - state: `Enrolled (not yet used)` → `Used to open` (slot-level);
  - the finger label is never put in the sentence that describes the event.
- **Verification is per slot, not per finger.** The only verifiable statement is
  "a fingerprint in this slot opened the door on \<time\>". That is what the
  journal records and what `finger_used` means.
- **Use a fixed vocabulary for the label** (`left thumb`, `left index`,
  `left middle`, `left ring`, `left little`, `right thumb`, … plus `other`), so
  that the *same* finger on two locks is recognisably the same word. Free text
  is allowed but the join across locks is by exact label, so the picker makes
  the common case reliable.

---

## 5. Re-enrolment

"Re-enrol" is really two operations, and the branch decides their cost.

### 5.1 Changing a finger (a thumb for an index)

Under **branch A** the finger *is* the slot, so:

1. clear the slot's template (`0x72`) and, if wanted, its PIN;
2. enrol the new finger (`0x71`) and write its label;
3. journal both, and re-confirm only by a real use.

Under **branch B** the finger is one entry in the slot's list, but the lock has
**no per-template clear command we know of** — `0x72` clears the slot. So:

1. clearing to change one finger **wipes every template in the slot**;
2. the other fingers in that slot are now gone and **must all be re-enrolled**;
3. the label list is replaced, not edited.

This asymmetry is the strongest practical reason the physical test matters: in
branch B "change my index finger" is a household event ("come and re-enrol all
your fingers"), not a one-finger edit.

### 5.2 What "clear then enrol" means, precisely

- **The slot.** Sending `0x72` on a user slot clears that slot's fingerprint
  template(s). It must never touch slots 0–2 ([§8](#8-what-must-not-change)).
  Today only `wipe` sends `0x72`; a **fingerprint-only clear must be added**, and
  `clear_slot` must stop leaving a template behind.
- **The label.** Clearing the fingerprint removes the slot's `fingers` entries
  and `has_fingerprint` / `finger_used`; if the slot keeps a PIN, the slot and
  its name stay. A **rename of just the label** (a finger's name was wrong) is a
  separate, cheap operation that writes no ZCL at all.
- **The journal history.** History is **never rewritten**. Each enrolment and
  each clear gets its own entry — `finger_enroll_started` (exists),
  `finger_enrolled` (new, with the label), `finger_cleared` (new),
  `finger_relabelled` (new). The old label survives in the timeline even after
  the slot's current label changes. The `50 %` overlap rule of
  `journal.make_entry` applies as for every other action.
- **The other locks where that finger may also be enrolled.** A template lives
  in **one** lock; the same physical finger enrolled on a second door is a
  **separate enrolment**. Clearing a slot on Ytterdörren does not touch
  Källardörren. The app must therefore ask "this finger is also enrolled on
  N other door(s) — update them too?" and drive each lock's clear/enrol
  separately. A partial failure is reported per door, never as one all-or-nothing
  result.

---

## 6. Across several locks

### 6.1 The rule

**A template lives in one lock. A finger that should open two doors is enrolled
twice.** There is no sync, no copy, no shared template — only the shared
*labels* (and the person) let the household see one finger across doors.

### 6.2 The per-finger-per-door view

For each `(person, finger, door)` show one of:

| State | Meaning | How we know |
|---|---|---|
| **Present** | An enrolment record exists on that door's slot for that finger, and (if shipped) it may be `confirmed` by a real use | The slot's `fingers` list contains the label; `finger_used` is the confirmation |
| **Missing** | The person has this finger enrolled on at least one door and has a slot on this door, but this door's slot does not list it | Derived: union of the person's labels across doors minus this door's labels |
| **Unknown** | We have no record for this door (the person has no slot here, the lock is unreachable, or the template predates this feature) | Absence of data — the honest default |

`Missing` is a **claim about our records**, not about the lock: the lock cannot
be asked whether a template is there (see [§1.1](#11-what-the-lock-exposes-about-users-and-credentials)).
The UI must say *"not enrolled here"*, never *"cannot open this door"*.

### 6.3 How the view gets established

- **The person at the door** is the normal case: the owner picks the person, the
  finger and the doors, then the app lights each chosen door's reader in turn
  and the person touches it. Each touch writes an enrolment and a label.
- **The owner driving it for someone else** is the same flow; there is no
  difference in the relay's eyes, because the relay already owns "owner drives an
  enrolment" (`POST /slots/{slot}/finger`).
- Because labels only exist after an enrolment, **unknown is the starting state
  everywhere**. There is no bulk import; the existing slot that already has an
  unlabelled fingerprint stays *unknown/unlabelled* until the owner labels it.

### 6.4 The capacity budget

The lock reports **100 total user slots, 50 PIN, 50 RFID**, and **no fingerprint
count** ([§1.1](#11-what-the-lock-exposes-about-users-and-credentials)). The
integration today caps *all* user slots at `num_of_pin_users_supported` — i.e.
slots `3…49`, **47 usable slots** (`pin_rules.pin_capacity`), the masters 0–2
excluded. Whether fingerprints count against the 50 PIN, the 100 total, or a
separate space is **unknown** and is one of the things to confirm.

So the budget is shown as arithmetic with the assumption made explicit:

- **Branch A:** a person with **5 fingers on 3 locks** costs up to
  **5 slots per lock = 15 enrolments**, i.e. 5 of this lock's usable slots. A
  family of 5 doing the same costs 25 of 47 — visible and finite.
- **Branch B:** **one slot per person per lock** regardless of finger count; 5
  fingers cost 3 slots (one per door) total.

The UI shows, per lock, `used / usable` and, per person, "this person takes N of
this door's slots"; and it refuses to start an enrolment when
`can_add` says the slot is full, pointing at the lock's own capacity instead of
failing at the ZCL layer.

---

## 7. The surfaces

### 7.1 The app (Koder / Personer)

- **Koder** (the brief: `Hemnyckel/Views/CodesView.swift`) becomes **grouped by
  person**, not a flat list of slots. Under each person: their fingers, each
  with its label, its per-door state (present / missing / unknown / unlabelled),
  and the slot it sits in.
- **The enrolment flow** asks, in order: *which person → which finger (the
  picker) → which doors*. Then it lights each chosen reader and waits for the
  person; each door resolves independently into present/confirmed.
- **`has_fingerprint` vs `finger_used`** are two different row treatments, as
  the card already intends: *enrolled, not yet used* vs *used to open*. The app's
  `LockSlot.fingerUsed` is currently **always false** because the integration's
  slots sensor never emits `finger_used` — the relay reads it from the sensor, so
  this must be fixed before the app can show confirmation (step 1 below).
- **An owner driving it for someone else** sees the same sheet with the person
  pre-chosen; nothing special.
- **Re-enrolment**: a "change finger" action that explains the branch's cost
  ("this clears the slot; you will need to re-enrol all of this person's
  fingers") and offers "also update the other door(s)".
- **Relabelling** a finger is a one-field edit and needs no trip to the door.

### 7.2 Home Assistant

- **The slots sensor** (`sensor.<lock>_slots`) gains `finger_used` (missing
  today) and a `fingers` list per row, so automations and the card can see the
  labels. The per-slot sensors (`sensor.<lock>_slot_N`) get the same.
- **The Personer card** (`www/hemnyckel-guests-card.js`) folds the per-lock
  slots and guest records together already; it gains a per-person finger list
  with the per-door state, using the same present/missing/unknown words, and an
  "enrol a finger" action that calls `hemnyckel.enroll_fingerprint` with the
  label.
- **MQTT bridge:** **no change.** The bridge deliberately carries roles and the
  relay's health and nothing else (`docs/mqtt-bridge.md`: "the bridge carries
  roles and health, and nothing else"). Fingers are lock-side data and belong to
  the integration/HA, not to the bridge. If the family ever wants a finger
  *entity*, that is a new deliberate topic and a new row in the bridge document,
  not a quiet addition here.
- **A new `hemnyckel.clear_fingerprint` service** (and a `finger` argument on
  `enroll_fingerprint`) is required; `services.yaml` and both translation files
  gain the keys.

### 7.3 The relay

The relay needs only to **carry** the labels, not store them:

- `GET /slots` rows gain `fingers` (passed through from the slots sensor, plus
  `finger_used`), so the app gets the labels from the same place it already gets
  slots. The join across doors is done in the app from the per-door `/slots`
  calls it already makes; no new per-person store and no duplicate truth.
- `POST /slots/{slot}/finger` gains an optional `finger` label and forwards it;
  a matching `DELETE /slots/{slot}/finger` forwards the fingerprint clear.
- The relay still owns **who may manage** (owner-only, as `/slots` is today)
  and still enforces the role rules; it does not decide finger policy.
- The **last-owner rule** is untouched: it is about roles, and fingers never
  change a role.

---

## 8. What must not change

- **The master slots 0–2 are never written.** `FIRST_USER_SLOT = 3` and
  `check_credential_slot` already refuse them for PIN and enrolment; the new
  fingerprint clear must go through the same check. `wipe` still leaves the
  masters alone.
- **Codes stay write-only.** Attribute `0x0101` is never read, and no code is
  logged or stored except where an existing feature deliberately does
  (`docs/privacy.md`). Fingers are labels, not biometric data, and no template
  material is ever read or stored.
- **The last owner can never be demoted.** The relay rule is unchanged; fingers
  and roles are separate.
- **The relay remains the enforcement point.** The app's UI is never the guard;
  owners drive enrolments, and the relay accepts or refuses.
- **The local path never depends on the cloud.** Finger labels live in the
  integration's entry options and the journal; the relay can be down and the
  lock still works.
- **One slot, one person.** The model does not support a slot shared by two
  people; the household's answer for a shared door is two slots.

---

## 9. Implementation plan

Ordered; each step is independently reviewable and reversible. The checks are
the repository's own: integration `python3 -m compileall -q custom_components`,
`python3 -m unittest discover -s tests -v`, `python3 tools/check_pii.py`; relay
`ruff check app tests` and `python -m pytest -q`; app `xcodegen` + the iOS CI
build/test.

| # | Step | Repo | Checks | Risk | Physical |
|---|---|---|---|---|---|
| 0 | **Run the physical test (§2)** and set `TEMPLATES_PER_SLOT` | bench | — | none — it is a measurement | **owner: enrol finger A then finger B in one slot, try both** |
| 1 | **Expose the truth that already exists**: emit `finger_used` on the slots sensor and the per-slot sensors; add a `fingers` list to the slot table and show it | integration | compileall, unittest, check_pii | **cheap**, pure additive; the `fingers` list has no writers yet | none |
| 2 | **Pure finger policy** (`mirror/fingers.py`): `can_add`, `finger_state`, `plan_slots`, with the branch constant | integration | unittest (new `test_fingers.py`, loaded by path like `test_guests.py`) | **cheap**, no HA imports, no device I/O | none |
| 3 | **Services**: `enroll_fingerprint` takes `finger` and records it; new `clear_fingerprint` sends `0x72`; `clear_slot` also clears the template; journal entries `finger_enrolled` / `finger_cleared` / `finger_relabelled` | integration | compileall, unittest, check_pii; hardware via `tools/sync_to_ha.sh` | **genuine change to the model** — a new write path to the lock; must be verified on hardware before trusting | owner: one clear + one enrol on a spare slot |
| 4 | **HA surfaces**: slots sensor rows, the Personer card finger section, and the translations | integration | compileall, unittest, check_pii | low; UI only, but the card is shipped so it must be copied to HA | none |
| 5 | **Relay pass-through**: `/slots` carries `fingers` + `finger_used`; `POST /slots/{slot}/finger` takes `finger`; `DELETE /slots/{slot}/finger` | addon | `ruff`, `pytest` (`test_slots.py` patterns) | low; the relay stores nothing new, so no migration | none |
| 6 | **App**: group Koder by person, the which-finger picker, present/missing/unknown states, the "also update other doors" re-enrolment sheet | app | iOS CI (`xcodegen`, build, test) | medium; this is where the branch's wording must be honest | owner: an end-to-end enrolment at a door |
| 7 | **Docs**: update `docs/guests.md`, `docs/architecture.md`, `docs/known-issues.md` (the current `clear_slot` template gap), and the card's on-screen words | integration | check_pii | cheap | none |

The cheap steps are **1, 2, 7**; the genuine model changes are **3** (a new
write path to the lock) and, if the answer is branch B, the framing in **6**.
Until step 0 is done, step 2's constant is `None` (unbounded) and the UI says
"we do not yet know whether one slot can hold several fingers" — the code must
not pretend either way.

---

## 10. What we could not determine

These are open because they need the lock, the owner's hands, or vendor
documentation — not because the software is unfinished:

- **One template per slot or several** — the physical test; decides the model
  and the capacity arithmetic.
- **The fingerprint capacity** — the lock reports no fingerprint attribute. Is
  it the 100 total, the 50 PIN, or separate? The arithmetic in
  [§6.4](#64-the-capacity-budget) states its assumption rather than hiding it.
- **What `0x72` clears** — the template in one slot, or something wider — and
  whether re-enrolling into an occupied slot adds or replaces. Branch A vs B
  hinges on the second half of this.
- **Whether the same finger always reports the same slot** — assumed (the event
  carries only the slot), but only a real use confirms it, which is why
  `finger_used` remains the sole proof.
- **Which finger was used, ever** — never determinable. Not a gap to close; a
  boundary of the design.

Everything above is enough to implement the feature the moment the physical
answer is in.
