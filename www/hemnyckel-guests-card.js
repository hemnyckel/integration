/**
 * hemnyckel-guests-card — people and keys with three-tap simplicity.
 *
 * A Lovelace card for the Hemnyckel integration's people and their keys:
 * create a temporary code (shown once), a recurring person (weekly windows,
 * same code every time) or a permanent one (family; the code is stored and can
 * be restored), see what every slot holds right now, pause it, change it or
 * revoke it. The card reads both the guests sensors (the person records) and
 * the slots sensors (what each slot holds) and calls the hemnyckel services, so
 * a person it creates appears in its list at once even when no entity is
 * pinned. A freshly created temporary code is shown once, in the card only, and
 * never stored - a recurring or permanent code lives in the config entry's
 * options.
 *
 * Config: { entity: "sensor.<door>_guests" } — optional: the card finds the locks itself.
 */

const DAYS = [
  ["mon", "Mån"],
  ["tue", "Tis"],
  ["wed", "Ons"],
  ["thu", "Tor"],
  ["fri", "Fre"],
  ["sat", "Lör"],
  ["sun", "Sön"],
];

const DURATIONS = [
  ["1h", "1 timme", 3600e3],
  ["1d", "1 dag", 864e5],
  ["1w", "1 vecka", 6048e5],
  ["1m", "1 månad", 2592e6],
  ["none", "Tills vidare", 0],
];

/* The ten fingers, fixed: the stored label is the integration's canonical
   English word (so the same finger reads the same on every lock), and what the
   household sees is the Swedish name. The lock never reports the finger, so
   this is a claim the owner makes at enrolment. */
const FINGERS = [
  ["left thumb", "Vänster tumme"],
  ["left index", "Vänster pekfinger"],
  ["left middle", "Vänster långfinger"],
  ["left ring", "Vänster ringfinger"],
  ["left little", "Vänster lillfinger"],
  ["right thumb", "Höger tumme"],
  ["right index", "Höger pekfinger"],
  ["right middle", "Höger långfinger"],
  ["right ring", "Höger ringfinger"],
  ["right little", "Höger lillfinger"],
];
const FINGER_NAMES = Object.fromEntries(FINGERS);

/* Empty on purpose: the card discovers every lock from the guests sensors.
   Pin one entity here to make that door this card's own. */
const DEFAULT_ENTITY = "";

const STYLE = `
  :host { display: block; }
  ha-card { padding: 0; overflow: hidden; }
  .wrap { padding: 16px; }
  .head { display: flex; align-items: center; gap: 10px; padding: 16px 16px 8px; }
  .head .title { font-size: 18px; font-weight: 600; color: var(--primary-text-color); flex: 1; }
  .head .count {
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    border-radius: 999px; font-size: 12px; font-weight: 600;
    min-width: 22px; height: 22px; display: flex; align-items: center; justify-content: center;
    padding: 0 7px;
  }
  .head ha-icon { color: var(--primary-color); --mdc-icon-size: 24px; }
  .list { display: flex; flex-direction: column; }
  .row {
    display: flex; align-items: center; gap: 12px; padding: 12px 16px;
    border-top: 1px solid var(--divider-color);
  }
  .row.paused { opacity: .5; }
  .row.paused .avatar { filter: grayscale(1); }
  .row.paused .name { font-weight: 500; }
  .avatar {
    width: 40px; height: 40px; border-radius: 50%; flex: 0 0 40px;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    display: flex; align-items: center; justify-content: center;
    font-weight: 600; font-size: 16px; text-transform: uppercase;
  }
  .info { flex: 1; min-width: 0; }
  .name { font-weight: 600; color: var(--primary-text-color); display: flex;
          align-items: center; gap: 8px; }
  .pill {
    font-size: 11px; font-weight: 700; padding: 3px 9px; border-radius: 999px;
    letter-spacing: .2px; text-transform: uppercase;
  }
  .pill.active { background: rgba(67, 160, 71, .18); color: #43a047; }
  .pill.outside { background: rgba(255, 166, 0, .18); color: #ffa600; }
  .pill.paused { background: rgba(158, 158, 158, .22); color: var(--secondary-text-color); }
  .pill.temp { background: rgba(33, 150, 243, .16); color: #42a5f5; }
  .pill.permanent { background: rgba(0, 150, 136, .18); color: #009688; }
  .meta { font-size: 13px; color: var(--secondary-text-color); margin-top: 2px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; }
  .dot.active { background: var(--success-color, #43a047); }
  .dot.outside { background: var(--warning-color, #ffa600); }
  .dot.paused { background: var(--disabled-text-color, #9e9e9e); }
  .actions { display: flex; gap: 2px; }
  button.icon {
    background: none; border: none; cursor: pointer; padding: 8px; border-radius: 50%;
    color: var(--secondary-text-color); display: flex; align-items: center;
    transition: background .15s ease, color .15s ease;
  }
  button.icon:hover { background: var(--secondary-background-color); color: var(--primary-text-color); }
  button.icon.danger:hover { color: var(--error-color, #db4437); }
  button.icon.confirm { color: var(--error-color, #db4437); width: auto; border-radius: 12px;
                        font-size: 13px; font-weight: 600; padding: 6px 10px; }
  .empty { padding: 4px 16px 16px; color: var(--secondary-text-color); font-size: 14px; }
  .newbtn {
    margin: 12px 16px 16px; width: calc(100% - 32px); border: none; cursor: pointer;
    background: var(--primary-color); color: var(--text-primary-color, #fff);
    border-radius: 12px; padding: 13px; font-size: 15px; font-weight: 600;
    display: flex; align-items: center; justify-content: center; gap: 8px;
    transition: filter .15s ease;
  }
  .newbtn:hover { filter: brightness(1.08); }
  .form { padding: 4px 16px 16px; display: flex; flex-direction: column; gap: 14px; }
  .seg { display: flex; background: var(--secondary-background-color); border-radius: 10px; padding: 3px; }
  .seg button {
    flex: 1; border: none; background: none; cursor: pointer; padding: 8px;
    border-radius: 8px; font-size: 14px; font-weight: 600; color: var(--secondary-text-color);
    transition: background .15s ease, color .15s ease;
  }
  .seg button.on { background: var(--card-background-color); color: var(--primary-text-color);
                   box-shadow: 0 1px 3px rgba(0,0,0,.18); }
  .label { font-size: 13px; font-weight: 600; color: var(--secondary-text-color); margin-bottom: -6px; }
  input[type=text], input[type=time] {
    width: 100%; box-sizing: border-box; border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
    border-radius: 10px; padding: 11px 12px; font-size: 15px; outline: none;
  }
  input:focus { border-color: var(--primary-color); }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; }
  .chip {
    border: 1px solid var(--divider-color); background: none; cursor: pointer;
    border-radius: 999px; padding: 8px 14px; font-size: 14px; color: var(--primary-text-color);
    transition: all .15s ease;
  }
  .chip.on { background: var(--primary-color); border-color: var(--primary-color);
             color: var(--text-primary-color, #fff); font-weight: 600; }
  .chip.day { min-width: 44px; text-align: center; }
  .timerow { display: flex; align-items: center; gap: 8px; }
  .timerow span { color: var(--secondary-text-color); }
  .addrow { background: none; border: none; color: var(--primary-color); cursor: pointer;
            font-size: 14px; font-weight: 600; padding: 4px 0; text-align: left; }
  .switch { display: flex; align-items: center; justify-content: space-between; }
  .switch span { font-size: 14px; color: var(--primary-text-color); }
  .toggle { position: relative; width: 46px; height: 28px; border-radius: 999px; border: none;
            background: var(--divider-color); cursor: pointer; transition: background .2s ease; }
  .toggle.on { background: var(--primary-color); }
  .toggle::after { content: ""; position: absolute; top: 3px; left: 3px; width: 22px; height: 22px;
                   border-radius: 50%; background: #fff; transition: transform .2s ease; }
  .toggle.on::after { transform: translateX(18px); }
  .submit {
    border: none; cursor: pointer; background: var(--primary-color); color: var(--text-primary-color, #fff);
    border-radius: 12px; padding: 13px; font-size: 15px; font-weight: 600;
  }
  .submit:disabled { opacity: .6; cursor: default; }
  .ghost { border: none; background: none; color: var(--secondary-text-color); cursor: pointer;
           font-size: 14px; padding: 6px; }
  .error { color: var(--error-color, #db4437); font-size: 14px; }
  .result { padding: 8px 16px 20px; text-align: center; }
  .result .lead { font-size: 15px; color: var(--secondary-text-color); }
  .result .who { font-size: 17px; font-weight: 600; color: var(--primary-text-color); margin-top: 2px; }
  .code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 40px; letter-spacing: 10px; font-weight: 700;
    color: var(--primary-text-color); margin: 18px 0 6px; text-indent: 10px;
  }
  .validity { font-size: 14px; color: var(--secondary-text-color); }
  .share { display: flex; gap: 8px; justify-content: center; margin: 20px 0 4px; flex-wrap: wrap; }
  .share a, .share button {
    border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color);
    border-radius: 999px; padding: 9px 16px; font-size: 14px; font-weight: 600;
    text-decoration: none; cursor: pointer;
  }
  .share a:hover, .share button:hover { border-color: var(--primary-color); color: var(--primary-color); }
  .done { margin-top: 18px; }
  .section { border-top: 1px solid var(--divider-color); }
  .section .shead { display: flex; align-items: center; gap: 8px; padding: 14px 16px 4px; }
  .section .shead .stitle { font-size: 15px; font-weight: 600; color: var(--primary-text-color); flex: 1; }
  .section .shead .count { background: var(--secondary-background-color); color: var(--secondary-text-color); }
  .badges { display: flex; gap: 6px; align-items: center; margin-top: 5px; flex-wrap: wrap; }
  .badge { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 600;
           color: var(--secondary-text-color); background: var(--secondary-background-color);
           border-radius: 8px; padding: 3px 8px; }
  .badge ha-icon { --mdc-icon-size: 15px; }
  .badge.warn { color: #ffa600; background: rgba(255, 166, 0, .15); }
  .badge.dim { opacity: .5; }
  .badge.locknote { background: none; border: 1px dashed var(--divider-color); }
  .mark { display: inline-flex; color: var(--secondary-text-color); }
  .mark ha-icon { --mdc-icon-size: 16px; }
  .mark.ok { color: var(--success-color, #43a047); }
  .mark.off { opacity: .55; }
  .doors { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .doorchip {
    display: inline-flex; align-items: center; gap: 4px; font-size: 12px;
    color: var(--secondary-text-color); background: var(--secondary-background-color);
    border-radius: 8px; padding: 3px 8px; border: none; cursor: pointer;
    font-family: inherit;
  }
  .doorchip:hover { color: var(--primary-text-color); }
  .doorchip.ok { color: var(--success-color, #43a047); }
  .doorchip.off { opacity: .6; }
  .fingerbox {
    margin-top: 10px; display: flex; flex-direction: column; gap: 8px;
    padding: 10px; border: 1px solid var(--divider-color); border-radius: 10px;
    background: var(--secondary-background-color);
  }
  .fingerbox .hint { font-size: 12px; color: var(--secondary-text-color); }
  .fingerbox button.submit { padding: 9px; font-size: 14px; }
`;

class HemnyckelGuestsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._fingerprint = null;
    this._form = this._blankForm();
    this._confirmSlot = null;
    this._confirmTimer = null;
    this._actionError = "";
    this._actionNotice = "";
    this._actionTimer = null;
    /* Which person's per-door finger picker is open, and what is chosen in it.
       The finger is a required choice: the reader is only started once one of
       the ten is picked. */
    this._fingerKey = null;
    this._fingerPick = { door: "", finger: "" };
  }

  setConfig(config) {
    this._config = config || {};
    this._renderShell();
  }

  set hass(hass) {
    this._hass = hass;
    const guests = this._buildGuests();
    const fingerprint = JSON.stringify(
      guests.map((g) => [
        g.entry_id,
        g.slot,
        g.name,
        g.state,
        g.paused,
        g.until,
        g.summary,
        g.credentials,
        /* The per-door finger truth has to be part of the fingerprint, or a
           label that changes without a credential changing would not repaint. */
        (g.doors || []).map((door) => [
          door.entry_id,
          door.slot,
          door.has_finger,
          door.finger_used,
          door.fingers,
        ]),
      ])
    );
    if (fingerprint !== this._fingerprint) {
      this._fingerprint = fingerprint;
      this._guests = guests;
      this._renderList();
      this._renderForm();
    }
  }

  _configuredEntity() {
    return this._config.entity || DEFAULT_ENTITY;
  }

  _slotsFor(entryId) {
    /* What each slot on one lock holds, as that lock's slots sensor reports it.
       The card reads the slot table as well as the person records, so a
       credential that exists is never hidden just because no person record was
       written for it. */
    const bySlot = {};
    for (const state of Object.values(this._hass.states)) {
      const attrs = state.attributes || {};
      if (!Array.isArray(attrs.slots)) continue;
      if (entryId && attrs.entry_id !== entryId) continue;
      for (const row of attrs.slots) bySlot[String(row.slot)] = row;
    }
    return bySlot;
  }

  _enrich(row, lock) {
    /* Fold the slot table into a person row: the slot number and what the slot
       actually holds (PIN, fingerprint). RFID is deliberately not shown - the
       lock never reports a tag use, so the family never uses one. */
    const data = this._slotsFor(lock.entry_id)[String(row.slot)] || {};
    const credentials = Array.isArray(data.credentials)
      ? data.credentials
      : data.has_pin
      ? ["pin"]
      : [];
    const hasPin = credentials.includes("pin") || Boolean(row.has_code);
    const hasFinger = credentials.includes("fingerprint");
    const fingers = Array.isArray(data.fingers) ? data.fingers : [];
    return {
      ...row,
      entry_id: lock.entry_id,
      lock: lock.name,
      credentials,
      has_pin: hasPin,
      has_finger: hasFinger,
      finger_restorable: hasFinger,
      finger_used: Boolean(data.finger_used),
      finger_state: data.finger_state || "",
      fingers,
    };
  }

  _doorRef(row) {
    /* One door's own truth about a person: its entry id, its slot, and what
       that slot holds. Kept per door so a person across two locks is shown,
       and can be acted on, door by door. */
    return {
      entry_id: row.entry_id,
      lock: row.lock,
      slot: row.slot,
      has_pin: row.has_pin,
      has_finger: row.has_finger,
      finger_used: row.finger_used,
      finger_state: row.finger_state,
      fingers: row.fingers || [],
      credentials: row.credentials,
    };
  }

  _key(guest) {
    if (!guest) return "";
    if (guest.group) return `g:${guest.group}`;
    return `s:${guest.entry_id || ""}:${guest.slot}`;
  }

  _buildGuests() {
    /* Every person on every lock the card can see, keyed by lock and slot, with
       the slot table folded in. Without a pinned entity this is the whole
       house; a person whose group puts them on several doors is listed once. */
    const pinned = this._configuredEntity();
    let locks = this._locks();
    if (pinned) {
      locks = locks.filter((lock) => lock.entity === pinned);
      if (!locks.length) {
        const state = this._hass.states[pinned];
        const attrs = (state && state.attributes) || {};
        locks = [
          {
            entity: pinned,
            entry_id: attrs.entry_id || null,
            name: attrs.lock || pinned,
            guests: Array.isArray(attrs.guests) ? attrs.guests : [],
          },
        ];
      }
    }
    const out = [];
    for (const lock of locks) {
      const guestRows = new Map(
        (lock.guests || []).map((row) => [String(row.slot), row])
      );
      const slots = this._slotsFor(lock.entry_id);
      const keys = new Set([...Object.keys(slots), ...guestRows.keys()]);
      for (const key of [...keys].sort((a, b) => Number(a) - Number(b))) {
        const guest = guestRows.get(key);
        const data = slots[key] || {};
        const occupied = Boolean(
          data.name || (Array.isArray(data.credentials) && data.credentials.length)
        );
        if (!guest && !occupied) continue;
        const row = this._enrich(
          guest || { slot: Number(key), name: data.name || "", kind: "slot" },
          lock
        );
        if (row.group) {
          const existing = out.find((item) => item.group === row.group);
          if (existing) {
            /* The same person on another door: still one row, but each door
               keeps its own slot, its own credentials and its own fingers, so
               the row can tell the truth per door instead of pretending the
               person can be enrolled on both at once. */
            existing.doors.push(this._doorRef(row));
            const union = new Set([
              ...(existing.credentials || []),
              ...(row.credentials || []),
            ]);
            existing.credentials = [...union];
            existing.has_pin = existing.has_pin || row.has_pin;
            existing.has_finger = existing.has_finger || row.has_finger;
            existing.finger_restorable =
              existing.finger_restorable || row.finger_restorable;
            continue;
          }
        }
        row.doors = [this._doorRef(row)];
        row.key = this._key(row);
        out.push(row);
      }
    }
    return out;
  }

  _errorText(err) {
    const text = (err && (err.message || err.error || String(err))) || "okänt fel";
    return text.replace(/^.*?Error:\s*/, "");
  }

  getCardSize() {
    return 3 + (this._guests ? this._guests.length : 0);
  }

  _blankForm() {
    return {
      open: false,
      editSlot: null,
      editEntry: null,
      editKind: "simple",
      mode: "simple",
      name: "",
      code: "",
      duration: "1d",
      oneTime: false,
      paused: false,
      forever: false,
      until: "",
      days: new Set(),
      windows: [{ start: "08:00", end: "12:00" }],
      locks: new Set(this._entryId() ? [this._entryId()] : []),
      busy: false,
      error: "",
      result: null,
    };
  }

  _fingerName(label) {
    const key = String(label || "").trim().toLowerCase();
    return FINGER_NAMES[key] || label || "";
  }

  _toggleFingerPicker(guest, door) {
    /* Step one of any enrolment is choosing the finger, so the reader is never
       lit before that choice is made. Tapping a door's chip opens the picker
       with that door chosen; the row button opens it on the first door. */
    const key = this._key(guest);
    const chosen =
      door ||
      (guest.doors && guest.doors[0] && guest.doors[0].entry_id) ||
      guest.entry_id ||
      "";
    if (this._fingerKey === key && this._fingerPick.door === chosen) {
      this._fingerKey = null;
      this._renderList();
      return;
    }
    this._fingerKey = key;
    this._fingerPick = { door: chosen, finger: "" };
    this._actionError = "";
    this._actionNotice = "";
    this._renderList();
  }

  _fingerTargetSlot(guest, doorEntryId, doorSlot) {
    /* The slot stays automatic: the person's own slot on that door when it has
       no fingerprint yet, otherwise the first free user slot, named for them.
       Templates never go below slot 3 (the masters 0-2 are the lock's own). */
    const slots = this._slotsFor(doorEntryId);
    const own =
      doorSlot === null || doorSlot === undefined ? null : slots[String(doorSlot)];
    if (own && !own.has_fingerprint && !(own.fingers || []).length) {
      return doorSlot;
    }
    const occupied = new Set(
      Object.values(slots)
        .filter(
          (row) =>
            row &&
            (row.name ||
              (Array.isArray(row.credentials) && row.credentials.length))
        )
        .map((row) => Number(row.slot))
    );
    let slot = 3;
    while (occupied.has(slot)) slot += 1;
    return slot;
  }

  async _startFingerEnroll(guest, doorEntryId, finger) {
    /* Light the chosen door's reader for the chosen finger. One door at a
       time: nobody stands at two doors at once, and a template lives in one
       lock. The label is stored as a claim; the lock reports nothing while
       enrolling and only a real use confirms it. */
    const door = (guest.doors || []).find((item) => item.entry_id === doorEntryId);
    if (!door || !finger) return;
    this._actionError = "";
    this._actionNotice = "";
    try {
      const target = this._fingerTargetSlot(guest, doorEntryId, door.slot);
      if (target !== door.slot) {
        await this._callServiceWS("hemnyckel", "set_slot_name", {
          slot: target,
          name: guest.name,
          entry_id: doorEntryId,
        }, false);
      }
      await this._callServiceWS("hemnyckel", "enroll_fingerprint", {
        slot: target,
        entry_id: doorEntryId,
        finger,
      });
      this._fingerKey = null;
      this._actionNotice = `Läsaren är öppen på ${door.lock} — lägg fingret på låset nu. "${this._fingerName(
        finger
      )}" sparas som ett påstående; låset bekräftar först när ett finger i platsen öppnat dörren.`;
      clearTimeout(this._actionTimer);
      this._actionTimer = setTimeout(() => {
        this._actionNotice = "";
        this._renderList();
      }, 20000);
      this._renderList();
    } catch (err) {
      this._actionError = this._errorText(err);
      this._renderList();
    }
  }

  _startEdit(guest) {
    const windows = (guest.schedule || []).map((window) => ({
      start: window.start,
      end: window.end,
    }));
    const days = new Set();
    for (const window of guest.schedule || []) {
      for (const day of window.days || []) days.add(day);
    }
    this._form = {
      ...this._blankForm(),
      open: true,
      editSlot: guest.slot,
      editEntry: guest.entry_id || null,
      editKind: ["recurring", "permanent"].includes(guest.kind)
        ? guest.kind
        : "simple",
      mode: ["recurring", "permanent"].includes(guest.kind)
        ? guest.kind
        : "simple",
      name: guest.name || "",
      paused: Boolean(guest.paused),
      forever: !guest.until,
      until: guest.until || "",
      days,
      windows: windows.length ? windows : [{ start: "08:00", end: "12:00" }],
    };
    this._renderForm();
  }

  _untilLocal(value) {
    if (!value) return "";
    try {
      const date = new Date(value);
      const pad = (number) => String(number).padStart(2, "0");
      return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(
        date.getDate()
      )}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
    } catch (err) {
      return "";
    }
  }

  _entityId() {
    return this._config.entity || DEFAULT_ENTITY;
  }

  _entryId() {
    const state = this._hass && this._hass.states[this._entityId()];
    return (state && state.attributes && state.attributes.entry_id) || null;
  }

  _lockData(data, entryId) {
    /* With several locks the services need to know which one: without the
       entry id they fan out to every mirror. A row that came from a specific
       lock passes its own entry id, so an action lands on that door first. */
    const id = entryId || this._entryId();
    return id ? { ...data, entry_id: id } : { ...data };
  }

  _locks() {
    /* Every lock in the house, as its guests sensor presents it: the same
       attributes (entry_id, lock, guests) mark a mirror. Sorted by name, with
       this card's own lock first, so a household with one door never sees a
       picker at all. */
    const locks = [];
    for (const [entityId, state] of Object.entries(this._hass.states)) {
      const attrs = state.attributes || {};
      if (!attrs.entry_id || !Array.isArray(attrs.guests)) continue;
      locks.push({
        entity: entityId,
        entry_id: attrs.entry_id,
        name: attrs.lock || entityId,
        guests: attrs.guests,
      });
    }
    const own = this._entryId();
    locks.sort((a, b) => {
      if (a.entry_id === own) return -1;
      if (b.entry_id === own) return 1;
      return String(a.name).localeCompare(String(b.name), "sv");
    });
    return locks;
  }

  _lockName() {
    const lock = this._locks().find((item) => item.entry_id === this._entryId());
    return lock ? lock.name : "detta lås";
  }

  _newGroup() {
    if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
    return "g-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  _editingGuest() {
    if (this._form.editSlot === null) return null;
    return (
      (this._guests || []).find(
        (row) =>
          row.slot === this._form.editSlot &&
          (!this._form.editEntry || row.entry_id === this._form.editEntry)
      ) || null
    );
  }

  _siblings(guest) {
    /* The same person on other locks: one group marker, when the guests were
       created together through this card. "Mine" is the row's own lock, from
       the row itself (the card is usually unpinned, so _entryId() is empty and
       the old check never skipped a lock — every fan-out then hit the row's own
       door a second time). */
    if (!guest) return [];
    const mine = guest.entry_id || this._entryId();
    const group = guest.group || null;
    const out = [];
    for (const lock of this._locks()) {
      if (lock.entry_id === mine) continue;
      const row = (lock.guests || []).find(
        (item) => group && item.group && item.group === group
      );
      if (row) out.push({ lock, slot: row.slot, name: row.name });
    }
    return out;
  }

  async _fanOut(service, guest, build) {
    /* Apply one action to every lock the person exists on; a lock that
       refuses is reported, never silently skipped. */
    const siblings = this._siblings(guest);
    const failed = [];
    for (const sibling of siblings) {
      try {
        await this._callServiceWS(
          "hemnyckel",
          service,
          build({ ...sibling, entry_id: sibling.lock.entry_id })
        );
      } catch (err) {
        failed.push(`${sibling.lock.name}: ${this._errorText(err)}`);
      }
    }
    return failed;
  }

  async _deleteGuest(guest) {
    /* Remove a person, or a slot-only row, on every lock it exists on. The
       guest record is revoked when there is one; the slot itself is always
       cleared, because revoke_guest_code only clears the PIN and a slot can
       also hold a fingerprint template — the relay's own DELETE
       /api/slots/{slot} clears both, and so must "ta bort". Targets are each
       lock once, so an unpinned card no longer acts on its own door twice. */
    const hasRecord = guest.kind !== "slot";
    const targets = [{ entry_id: guest.entry_id, slot: guest.slot }];
    for (const sibling of this._siblings(guest)) {
      targets.push({ entry_id: sibling.lock.entry_id, slot: sibling.slot });
    }
    const results = await Promise.allSettled(
      targets.map((target) => this._deleteOn(target.entry_id, target.slot, hasRecord))
    );
    const failed = results.find((result) => result.status === "rejected");
    this._actionError = failed
      ? `Kunde inte ta bort ${guest.name || "personen"} — ${this._errorText(failed.reason)}`
      : "";
    this._renderList();
  }

  async _deleteOn(entryId, slot, hasRecord) {
    /* Both calls are attempted even if the first is refused, so a slot that
       dropped its code but kept its finger is reported rather than silently
       left behind. */
    const errors = [];
    if (hasRecord) {
      try {
        await this._callServiceWS(
          "hemnyckel",
          "revoke_guest_code",
          this._lockData({ slot }, entryId)
        );
      } catch (err) {
        errors.push(err);
      }
    }
    try {
      await this._callServiceWS(
        "hemnyckel",
        "clear_slot",
        this._lockData({ slot }, entryId),
        false
      );
    } catch (err) {
      errors.push(err);
    }
    if (errors.length) throw errors[0];
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <ha-icon icon="mdi:account-key"></ha-icon>
          <div class="title">Personer</div>
          <div class="count" id="count">0</div>
        </div>
        <div class="list" id="list"></div>
        <div id="form"></div>
      </ha-card>
    `;
    this._renderList();
    this._renderForm();
  }

  _esc(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _renderList() {
    const list = this.shadowRoot.getElementById("list");
    const count = this.shadowRoot.getElementById("count");
    if (!list) return;
    const guests = this._guests || [];
    if (count) count.textContent = String(guests.length);
    if (!guests.length) {
      list.innerHTML = `<div class="empty">Inga personer just nu.</div>`;
      return;
    }
    list.innerHTML = this._actionError
      ? `<div class="empty error">${this._esc(this._actionError)}</div>`
      : this._actionNotice
      ? `<div class="empty">${this._esc(this._actionNotice)}</div>`
      : "";
    for (const guest of guests) {
      list.appendChild(this._guestRow(guest));
    }
  }

  _guestRow(guest) {
    const row = document.createElement("div");
    row.className = guest.paused ? "row paused" : "row";
    const initial = (guest.name || "?").trim().charAt(0);
    const meta = this._meta(guest);
    const slotText =
      guest.slot === null || guest.slot === undefined ? "" : `Slot ${guest.slot} · `;
    const recurring = guest.kind === "recurring";
    const slotOnly = guest.kind === "slot";
    const confirming = this._confirmSlot === guest.slot;
    const siblings = this._siblings(guest);
    const sibBadge = siblings.length
      ? `<span class="mark" title="Även på ${siblings
          .map((sibling) => this._esc(sibling.lock.name))
          .join(", ")}"><ha-icon icon="mdi:door"></ha-icon>${siblings.length + 1} lås</span>`
      : "";
    row.innerHTML = `
      <div class="avatar">${initial}</div>
      <div class="info">
        <div class="name"><span>${this._esc(guest.name) || "Namnlös"}</span>${
        guest.restorable
          ? '<span class="mark ok" title="PIN-koden sparas här — kan återställas efter en förlust"><ha-icon icon="mdi:key-variant"></ha-icon></span>'
          : slotOnly
          ? ""
          : '<span class="mark off" title="PIN-koden sparas inte — kan inte återställas efter en förlust"><ha-icon icon="mdi:key-remove"></ha-icon></span>'
      }${
        guest.has_finger
          ? guest.finger_restorable
            ? '<span class="mark ok" title="Fingret i sloten har öppnat dörren — kan återställas så länge låset inte nollställts"><ha-icon icon="mdi:fingerprint"></ha-icon></span>'
            : '<span class="mark off" title="Fingret är registrerat men aldrig använt — kan inte verifieras och återställs inte automatiskt"><ha-icon icon="mdi:fingerprint-off"></ha-icon></span>'
          : ""
      }${sibBadge}${this._pill(guest)}</div>
        <div class="meta">${this._esc(slotText + meta)}</div>
        ${this._badges(guest)}
        ${this._fingerDoors(guest)}
        ${this._fingerKey === guest.key ? this._fingerPickerHtml(guest) : ""}
      </div>
      <div class="actions">
        ${
          slotOnly
            ? ""
            : `<button class="icon" data-act="edit" title="Redigera">
          <ha-icon icon="mdi:pencil"></ha-icon></button>`
        }
        <button class="icon" data-act="finger" title="Starta finger-enroll">
          <ha-icon icon="mdi:fingerprint"></ha-icon></button>
        ${
          recurring
            ? `<button class="icon" data-act="pause" title="${
                guest.paused ? "Återuppta" : "Pausa"
              }"><ha-icon icon="${
                guest.paused ? "mdi:play" : "mdi:pause"
              }"></ha-icon></button>`
            : ""
        }
        <button class="icon ${confirming ? "confirm" : "danger"}" data-act="revoke" title="${
          slotOnly ? "Rensa" : "Återkalla"
        }">
          ${
            confirming
              ? slotOnly
                ? "Rensa?"
                : "Återkalla?"
              : `<ha-icon icon="mdi:trash-can-outline"></ha-icon>`
          }
        </button>
      </div>
    `;
    row.querySelector('[data-act="finger"]').addEventListener("click", () =>
      this._toggleFingerPicker(guest)
    );
    row.querySelectorAll("[data-open-finger]").forEach((button) =>
      button.addEventListener("click", () => {
        this._toggleFingerPicker(guest, button.dataset.openFinger);
      })
    );
    row.querySelectorAll("[data-finger-door]").forEach((button) =>
      button.addEventListener("click", () => {
        this._fingerPick.door = button.dataset.fingerDoor;
        this._renderList();
      })
    );
    row.querySelectorAll("[data-finger]").forEach((button) =>
      button.addEventListener("click", () => {
        this._fingerPick.finger = button.dataset.finger;
        this._renderList();
      })
    );
    row.querySelector("#finger-start")?.addEventListener("click", () => {
      this._startFingerEnroll(guest, this._fingerPick.door, this._fingerPick.finger);
    });
    row.querySelector('[data-act="edit"]')?.addEventListener("click", () =>
      this._startEdit(guest)
    );
    row.querySelector('[data-act="pause"]')?.addEventListener("click", async () => {
      const failed = await this._fanOut("update_guest", guest, (target) => ({
        slot: target.slot,
        entry_id: target.entry_id,
        paused: !guest.paused,
      }));
      this._callService(
        "update_guest",
        this._lockData({ slot: guest.slot, paused: !guest.paused }, guest.entry_id)
      );
      if (failed.length) {
        this._actionError = `Misslyckades — ${failed.join("; ")}`;
        this._renderList();
      }
    });
    row.querySelector('[data-act="revoke"]').addEventListener("click", () => {
      if (this._confirmSlot !== guest.slot) {
        this._confirmSlot = guest.slot;
        this._renderList();
        clearTimeout(this._confirmTimer);
        this._confirmTimer = setTimeout(() => {
          this._confirmSlot = null;
          this._renderList();
        }, 3000);
        return;
      }
      clearTimeout(this._confirmTimer);
      this._confirmSlot = null;
      this._deleteGuest(guest);
    });
    return row;
  }

  _badges(guest) {
    /* What the slot holds, in the app's method words: Kod and Fingeravtryck.
       RFID is deliberately absent - the lock never reports a tag use. */
    const marks = [];
    if (guest.has_pin || (guest.credentials || []).includes("pin")) {
      marks.push(["mdi:key-variant", "Kod"]);
    }
    if (guest.has_finger || (guest.credentials || []).includes("fingerprint")) {
      marks.push(["mdi:fingerprint", "Fingeravtryck"]);
    }
    if (!marks.length) return "";
    return `<div class="badges">${marks
      .map(
        ([icon, label]) =>
          `<span class="badge"><ha-icon icon="${icon}"></ha-icon>${label}</span>`
      )
      .join("")}</div>`;
  }

  _fingerDoors(guest) {
    /* Per-door truth for a person who spans more than one lock: each door's
       own slot and its own finger. The badge above is the union; this is what
       is actually true at each door. A door with no finger says so. */
    const doors = guest.doors || [];
    if (doors.length < 2) return "";
    const chips = doors.map((door) => {
      const labels = (door.fingers || [])
        .map((item) => this._fingerName(item.label))
        .filter(Boolean);
      const shown = labels.length
        ? labels.join(", ")
        : door.has_finger
        ? "finger utan etikett"
        : "inget finger";
      return `<button class="doorchip ${
        door.has_finger ? "ok" : "off"
      }" data-open-finger="${this._esc(door.entry_id)}" title="Välj finger för ${
        this._esc(door.lock)
      }">${this._esc(door.lock)} · ${this._esc(shown)}</button>`;
    });
    return `<div class="doors">${chips.join("")}</div>`;
  }

  _fingerPickerHtml(guest) {
    /* The reader is only enabled once a finger is chosen, and the finger is
       enrolled one door at a time: a template lives in one lock, and nobody
       stands at two doors at once. */
    const doors = guest.doors || [];
    const doorChips = doors
      .map(
        (door) =>
          `<button class="chip ${
            this._fingerPick.door === door.entry_id ? "on" : ""
          }" data-finger-door="${this._esc(door.entry_id)}">${this._esc(
            door.lock
          )}</button>`
      )
      .join("");
    const fingerChips = FINGERS.map(
      ([key, label]) =>
        `<button class="chip ${
          this._fingerPick.finger === key ? "on" : ""
        }" data-finger="${key}">${label}</button>`
    ).join("");
    const ready = Boolean(this._fingerPick.door && this._fingerPick.finger);
    return `
      <div class="fingerbox">
        <div class="label">Dörr</div>
        <div class="chips">${doorChips}</div>
        <div class="label">Finger</div>
        <div class="chips">${fingerChips}</div>
        <div class="hint">Fingret sparas som ett påstående — låset berättar aldrig vilket finger som öppnade.</div>
        <button class="submit" id="finger-start" ${
          ready ? "" : "disabled"
        }>${ready ? "Starta läsaren" : "Välj finger först"}</button>
      </div>
    `;
  }

  _pill(guest) {
    if (guest.kind === "slot") {
      return `<span class="pill active">Upptagen</span>`;
    }
    if (guest.kind === "recurring") {
      if (guest.paused) return `<span class="pill paused">Pausad</span>`;
      if (guest.in_window) return `<span class="pill active">Aktiv</span>`;
      return `<span class="pill outside">Utanför</span>`;
    }
    if (guest.kind === "permanent") {
      return `<span class="pill permanent">Permanent</span>`;
    }
    if (guest.one_time) return `<span class="pill temp">Engång</span>`;
    return `<span class="pill temp">Tillfällig</span>`;
  }

  _meta(guest) {
    if (guest.kind === "slot") {
      return "Bara i Nycklar";
    }
    if (guest.kind === "recurring") {
      const summary = (guest.summary || "").replace(/mon|tue|wed|thu|fri|sat|sun/g, (day) => {
        const found = DAYS.find(([key]) => key === day);
        return found ? found[1] : day;
      });
      if (guest.paused) return `Återupptas manuellt · ${summary}`;
      if (guest.in_window) return summary;
      return `Öppnar nästa gång enligt ${summary}`;
    }
    if (guest.kind === "permanent") {
      return "Alltid · koden är sparad och kan återställas";
    }
    if (guest.one_time) return "Återkallas efter första upplåsningen";
    if (guest.until) return `Giltig till ${this._niceTime(guest.until)}`;
    return "Tills vidare";
  }

  _niceTime(value) {
    try {
      const date = new Date(value);
      return date.toLocaleString("sv-SE", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (err) {
      return String(value);
    }
  }

  _renderForm() {
    const host = this.shadowRoot.getElementById("form");
    if (!host) return;
    const form = this._form;
    if (form.result) {
      host.innerHTML = this._resultHtml(form.result);
      this._bindResult();
      return;
    }
    if (!form.open) {
      host.innerHTML = `<button class="newbtn" id="new"><ha-icon icon="mdi:plus"></ha-icon> Ny person</button>`;
      host.querySelector("#new").addEventListener("click", () => {
        this._form = this._blankForm();
        this._form.open = true;
        this._renderForm();
      });
      return;
    }
    host.innerHTML = this._formHtml();
    this._bindForm();
  }

  _formHtml() {
    const form = this._form;
    const dayChips = DAYS.map(
      ([key, label]) =>
        `<button class="chip day ${form.days.has(key) ? "on" : ""}" data-day="${key}">${label}</button>`
    ).join("");
    const durationChips = DURATIONS.map(
      ([key, label]) =>
        `<button class="chip ${form.duration === key ? "on" : ""}" data-duration="${key}">${label}</button>`
    ).join("");
    const windows = form.windows
      .map(
        (window, index) => `
        <div class="timerow">
          <input type="time" value="${window.start}" data-start="${index}" />
          <span>–</span>
          <input type="time" value="${window.end}" data-end="${index}" />
          ${
            form.windows.length > 1
              ? `<button class="icon danger" data-remove="${index}" title="Ta bort">
                   <ha-icon icon="mdi:close"></ha-icon></button>`
              : ""
          }
        </div>`
      )
      .join("");
    const recurring = form.mode === "recurring";
    const permanent = form.mode === "permanent";
    const editing = form.editSlot !== null;
    const keepRecurring = editing && form.editKind === "recurring";
    const keepPermanent = editing && form.editKind === "permanent";
    const segmented =
      keepRecurring || keepPermanent
        ? ""
        : `<div class="seg">
          <button class="${!recurring && !permanent ? "on" : ""}" data-mode="simple">Tillfällig</button>
          <button class="${recurring ? "on" : ""}" data-mode="recurring">Återkommande</button>
          <button class="${permanent ? "on" : ""}" data-mode="permanent">Permanent</button>
        </div>`;
    const simpleFields = editing
      ? `
        <div class="label">Giltig till</div>
        <input type="datetime-local" id="until" value="${this._untilLocal(form.until)}" ${
          form.forever ? "disabled" : ""
        } />
        <div class="switch"><span>Tills vidare</span>
          <button class="toggle ${form.forever ? "on" : ""}" id="forever"></button></div>
      `
      : `
        <div class="label">Giltig i</div>
        <div class="chips">${durationChips}</div>
        <div class="switch"><span>Engångskod</span>
          <button class="toggle ${form.oneTime ? "on" : ""}" id="onetime"></button></div>
        <div class="empty">Koden visas bara en gång och sparas inte — den går inte att återställa om låset tappar den. Välj Permanent om koden ska kunna återställas.</div>
      `;
    return `
      <div class="form">
        ${segmented}
        <div class="label">Namn</div>
        <input type="text" id="name" placeholder="t.ex. Städfirma" value="${this._esc(form.name)}" />
        ${(() => {
          const locks = this._locks();
          if (editing) {
            const siblings = this._siblings(this._editingGuest());
            return siblings.length
              ? `<div class="empty">Ändringen gäller även ${siblings
                  .map((sibling) => this._esc(sibling.lock.name))
                  .join(", ")}.</div>`
              : "";
          }
          if (locks.length < 2) return "";
          return `
            <div class="label">Lås</div>
            <div class="chips">${locks
              .map(
                (lock) =>
                  `<button class="chip ${form.locks.has(lock.entry_id) ? "on" : ""}" data-lock="${this._esc(lock.entry_id)}">${this._esc(lock.name)}</button>`
              )
              .join("")}</div>`;
        })()}
        ${
          recurring
            ? `
          <div class="label">Dagar</div>
          <div class="chips">${dayChips}</div>
          <div class="label">Tider</div>
          <div class="chips" style="flex-direction:column;gap:10px">${windows}</div>
          <button class="addrow" id="addwindow">+ Lägg till tid</button>
          <div class="switch"><span>Pausad</span>
            <button class="toggle ${form.paused ? "on" : ""}" id="paused"></button></div>
        `
            : permanent
            ? `<div class="empty">Alltid giltig — koden sparas och kan återställas.</div>`
            : simpleFields
        }
        <div class="label">Kod${editing ? "" : " (valfritt)"}</div>
        <input type="text" id="code" inputmode="numeric" placeholder="${
          editing ? "Lämna tomt för att behålla nuvarande" : "Lämna tomt för slumpad"
        }" value="${form.code}" />
        ${form.error ? `<div class="error">${this._esc(form.error)}</div>` : ""}
        <button class="submit" id="submit" ${form.busy ? "disabled" : ""}>
          ${form.busy ? "Sparar…" : editing ? "Spara ändringar" : "Skapa person"}
        </button>
        <button class="ghost" id="cancel">Avbryt</button>
      </div>
    `;
  }

  _bindForm() {
    const form = this._form;
    const root = this.shadowRoot;
    root.getElementById("cancel").addEventListener("click", () => {
      this._form = this._blankForm();
      this._renderForm();
    });
    root.querySelectorAll("[data-lock]").forEach((button) =>
      button.addEventListener("click", () => {
        const entry = button.dataset.lock;
        if (form.locks.has(entry)) form.locks.delete(entry);
        else form.locks.add(entry);
        this._renderForm();
      })
    );
    root.querySelectorAll("[data-mode]").forEach((button) =>
      button.addEventListener("click", () => {
        form.mode = button.dataset.mode;
        this._renderForm();
      })
    );
    root.getElementById("name").addEventListener("input", (event) => {
      form.name = event.target.value;
    });
    root.getElementById("code").addEventListener("input", (event) => {
      form.code = event.target.value;
    });
    root.querySelectorAll("[data-day]").forEach((button) =>
      button.addEventListener("click", () => {
        const day = button.dataset.day;
        if (form.days.has(day)) form.days.delete(day);
        else form.days.add(day);
        this._renderForm();
      })
    );
    root.querySelectorAll("[data-duration]").forEach((button) =>
      button.addEventListener("click", () => {
        form.duration = button.dataset.duration;
        this._renderForm();
      })
    );
    root.querySelectorAll("[data-start]").forEach((input) =>
      input.addEventListener("change", (event) => {
        form.windows[Number(event.target.dataset.start)].start = event.target.value;
      })
    );
    root.querySelectorAll("[data-end]").forEach((input) =>
      input.addEventListener("change", (event) => {
        form.windows[Number(event.target.dataset.end)].end = event.target.value;
      })
    );
    root.querySelectorAll("[data-remove]").forEach((button) =>
      button.addEventListener("click", () => {
        form.windows.splice(Number(button.dataset.remove), 1);
        this._renderForm();
      })
    );
    root.getElementById("addwindow")?.addEventListener("click", () => {
      form.windows.push({ start: "08:00", end: "12:00" });
      this._renderForm();
    });
    root.getElementById("onetime")?.addEventListener("click", () => {
      form.oneTime = !form.oneTime;
      this._renderForm();
    });
    root.getElementById("paused")?.addEventListener("click", () => {
      form.paused = !form.paused;
      this._renderForm();
    });
    root.getElementById("forever")?.addEventListener("click", () => {
      form.forever = !form.forever;
      this._renderForm();
    });
    root.getElementById("until")?.addEventListener("change", (event) => {
      form.until = event.target.value;
    });
    root.getElementById("submit").addEventListener("click", () => this._submit());
  }

  async _submit() {
    const form = this._form;
    const name = form.name.trim();
    if (!name) {
      form.error = "Ge personen ett namn.";
      this._renderForm();
      return;
    }
    const editing = form.editSlot !== null;
    const recurring = form.mode === "recurring";
    const permanent = form.mode === "permanent";
    if (recurring && !form.days.size) {
      form.error = "Välj minst en dag.";
      this._renderForm();
      return;
    }
    if (editing && recurring && form.editKind === "simple" && !form.code.trim()) {
      form.error = "En återkommande person behöver en kod — fyll i kodfältet.";
      this._renderForm();
      return;
    }
    form.busy = true;
    form.error = "";
    this._renderForm();
    try {
      if (editing) {
        const editingGuest = this._editingGuest();
        const changes = { slot: form.editSlot, name };
        if (form.code.trim()) changes.code = form.code.trim();
        if (recurring) {
          changes.schedule = form.windows.map((window) => ({
            days: Array.from(form.days),
            start: window.start,
            end: window.end,
          }));
          changes.paused = form.paused;
        } else if (form.editKind === "simple") {
          changes.until =
            form.forever || !form.until ? "" : new Date(form.until).toISOString();
        }
        await this._callServiceWS(
          "hemnyckel",
          "update_guest",
          this._lockData(changes, form.editEntry)
        );
        const failed = editingGuest
          ? await this._fanOut("update_guest", editingGuest, (target) => ({
              ...changes,
              slot: target.slot,
              entry_id: target.entry_id,
            }))
          : [];
        this._form = this._blankForm();
        this._renderForm();
        if (failed.length) {
          this._actionError = `Misslyckades på andra lås — ${failed.join("; ")}`;
          this._renderList();
        }
        return;
      }
      let data;
      let service;
      if (recurring) {
        service = "create_recurring_guest";
        data = {
          name,
          schedule: form.windows.map((window) => ({
            days: Array.from(form.days),
            start: window.start,
            end: window.end,
          })),
        };
        if (form.code.trim()) data.code = form.code.trim();
        if (form.paused) data.paused = true;
      } else if (permanent) {
        service = "create_guest_code";
        data = { name, permanent: true };
        if (form.code.trim()) data.code = form.code.trim();
      } else {
        service = "create_guest_code";
        data = { name };
        const choice = DURATIONS.find(([key]) => key === form.duration);
        if (choice && choice[2] > 0) {
          data.until = new Date(Date.now() + choice[2]).toISOString();
        }
        if (form.code.trim()) data.code = form.code.trim();
        if (form.oneTime) data.one_time = true;
      }
      const locks = this._locks();
      const targets =
        locks.length > 1
          ? locks.filter((lock) => form.locks.has(lock.entry_id))
          : [{ entry_id: this._entryId(), name: this._lockName() }];
      if (!targets.length) {
        throw new Error("Välj minst ett lås.");
      }
      const group = targets.length > 1 ? this._newGroup() : null;
      let created = null;
      let createdCode = form.code.trim() || null;
      const failed = [];
      for (const target of targets) {
        const payload = { ...data, entry_id: target.entry_id };
        if (group) payload.group = group;
        if (createdCode) payload.code = createdCode;
        try {
          const response = await this._callServiceWS("hemnyckel", service, payload);
          const result =
            (target.entry_id && response && response[target.entry_id]) ||
            Object.values(response || {})[0] ||
            {};
          if (!created) created = result;
          if (!createdCode && result.code) createdCode = result.code;
        } catch (err) {
          failed.push({ name: target.name || "lås", error: this._errorText(err) });
        }
      }
      if (!created) {
        throw new Error(failed.length ? failed[0].error : "Inget lås svarade.");
      }
      this._form = this._blankForm();
      this._form.result = {
        code: created.code || createdCode,
        name,
        until: created.until,
        schedule: created.schedule,
        permanent: Boolean(created.permanent),
        failed,
      };
      this._renderForm();
    } catch (err) {
      form.busy = false;
      form.error = editing
        ? "Kunde inte spara ändringen."
        : "Kunde inte skapa personen. Försök igen.";
      this._renderForm();
    }
  }

  _resultHtml(result) {
    const valid = result.schedule
      ? "Återkommande · samma kod varje gång"
      : result.permanent
      ? "Permanent · koden är sparad och kan återställas"
      : result.until
      ? `Visas bara en gång · giltig till ${this._niceTime(result.until)}`
      : "Visas bara en gång · tills vidare";
    const text = encodeURIComponent(
      `Din kod till ytterdörren: ${result.code} (${valid})`
    );
    /* sms: differs per platform: iOS wants &body=, Android ?body=, and a
       desktop browser has no SMS app at all - hide the button there. */
    const ua = navigator.userAgent || "";
    const isIOS = /iPad|iPhone|iPod/.test(ua);
    const isAndroid = /Android/.test(ua);
    const smsHref = isIOS ? `sms:&body=${text}` : `sms:?body=${text}`;
    const smsButton =
      isIOS || isAndroid ? `<a href="${smsHref}">SMS</a>` : "";
    const failedNote =
      result.failed && result.failed.length
        ? `<div class="error">Misslyckades på ${result.failed
            .map((item) => this._esc(item.name))
            .join(", ")}: ${this._esc(result.failed[0].error)}</div>`
        : "";
    return `
      <div class="result">
        <div class="lead">Koden är klar</div>
        <div class="who">${this._esc(result.name)}</div>
        <div class="code">${this._esc(result.code)}</div>
        <div class="validity">${valid}</div>
        ${failedNote}
        <div class="share">
          <button id="copy">Kopiera</button>
          ${smsButton}
          <a href="https://wa.me/?text=${text}" target="_blank" rel="noreferrer">WhatsApp</a>
        </div>
        <button class="ghost done" id="done">Klar</button>
      </div>
    `;
  }

  _bindResult() {
    const root = this.shadowRoot;
    const form = this._form;
    root.getElementById("copy").addEventListener("click", async (event) => {
      const code = String(form.result.code || "");
      try {
        await navigator.clipboard.writeText(code);
      } catch (err) {
        const area = document.createElement("textarea");
        area.value = code;
        this.shadowRoot.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
      }
      event.target.textContent = "Kopierat";
      setTimeout(() => {
        if (event.target) event.target.textContent = "Kopiera";
      }, 1500);
    });
    root.getElementById("done").addEventListener("click", () => {
      this._form = this._blankForm();
      this._renderForm();
    });
  }

  async _callServiceWS(domain, service, data, wantResponse = true) {
    /* Call the service over the WebSocket API: its return_response flag is
       explicit and stable, unlike the positional argument on hass.callService
       that some frontend versions drop. Only services that declare
       supports_response may be asked for one — clear_slot and set_slot_name do
       not, and Home Assistant refuses the call outright ("An action which does
       not return responses can't be called with return_response=True"), so the
       caller passes false for those. */
    const result = await this._hass.callWS({
      type: "call_service",
      domain,
      service,
      service_data: data,
      return_response: wantResponse,
    });
    return (result && result.response) || {};
  }

  async _callService(service, data) {
    try {
      await this._callServiceWS("hemnyckel", service, data);
      this._actionError = "";
    } catch (err) {
      this._actionError = this._errorText(err);
      clearTimeout(this._actionTimer);
      this._actionTimer = setTimeout(() => {
        this._actionError = "";
        this._renderList();
      }, 6000);
      this._renderList();
    }
  }
}

customElements.define("hemnyckel-guests-card", HemnyckelGuestsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "hemnyckel-guests-card",
  name: "Hemnyckel Personer",
  description: "Skapa och hantera personer och deras nycklar — tillfälliga, återkommande och permanenta.",
});
