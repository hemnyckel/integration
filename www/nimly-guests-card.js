/**
 * nimly-guests-card — guest codes with three-tap simplicity.
 *
 * A Lovelace card for the Nimly integration's guest codes: create a temporary
 * code or a recurring guest (weekly windows, same code every time), see what
 * is active right now, pause it, change it or revoke it. The card reads
 * sensor.nimly_guests (attributes.guests) and calls the nimly guest services;
 * a freshly created code is shown once, in the card only, and never stored.
 *
 * Config: { entity: "sensor.nimly_guests" } — the entity is optional.
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

const DEFAULT_ENTITY = "sensor.nimly_guests";

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
  .cloudform { padding: 12px 16px 14px; display: flex; flex-direction: column; gap: 12px;
               background: var(--secondary-background-color); }
  .cloudform .row2 { display: flex; gap: 10px; }
  .cloudform .row2 > div { flex: 1; min-width: 0; }
  .cloudform .actions2 { display: flex; gap: 8px; }
  .cloudform .actions2 button { flex: 1; }
  .cloudform input[type=date] {
    width: 100%; box-sizing: border-box; border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color);
    border-radius: 10px; padding: 10px 12px; font-size: 15px; outline: none;
  }
  .cloudrow .avatar { background: var(--secondary-text-color); }
  .cloudmark { display: inline-flex; color: var(--secondary-text-color); }
  .cloudmark ha-icon { --mdc-icon-size: 16px; }
  .cloudmark.ok { color: var(--success-color, #43a047); }
  .cloudmark.off { opacity: .55; }
`;

class NimlyGuestsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._fingerprint = null;
    this._form = this._blankForm();
    this._confirmSlot = null;
    this._confirmTimer = null;
    this._cloudGuests = null;
    this._cloudLoaded = false;
    this._cloudLoading = false;
    this._cloudEdit = null;
    this._cloudConfirm = null;
    this._cloudConfirmTimer = null;
    this._cloudError = "";
    this._cloudNotice = "";
    this._cloudFailedAt = 0;
    this._actionError = "";
    this._actionNotice = "";
    this._actionTimer = null;
  }

  setConfig(config) {
    this._config = config || {};
    this._renderShell();
  }

  set hass(hass) {
    this._hass = hass;
    const entity = this._config.entity || DEFAULT_ENTITY;
    const state = hass.states[entity];
    const guests = (state && state.attributes && state.attributes.guests) || [];
    const fingerprint = JSON.stringify(
      guests.map((g) => [g.slot, g.name, g.state, g.paused, g.until, g.summary])
    );
    if (fingerprint !== this._fingerprint) {
      this._fingerprint = fingerprint;
      this._guests = guests;
      this._renderList();
      this._renderForm();
    }
    if (!this._cloudLoaded && !this._cloudLoading && hass) {
      const retryAt = (this._cloudFailedAt || 0) + 60000;
      if (Date.now() >= retryAt) this._fetchCloud();
    }
  }

  async _fetchCloud() {
    /* The app's guest users: identities created in the vendor app. The
       integration answers with each guest's accesses per lock, so the card can
       tell what actually lives on this door, on another door, or nowhere. */
    this._cloudLoading = true;
    this._cloudError = "";
    try {
      const state = this._hass.states[this._entityId()];
      const entryId = state && state.attributes && state.attributes.entry_id;
      const response = await this._callServiceWS(
        "nimly",
        "cloud_guests",
        entryId ? { entry_id: entryId } : {}
      );
      this._cloudGuests = Array.isArray(response.guests) ? response.guests : [];
      this._cloudLoaded = true;
    } catch (err) {
      this._cloudGuests = null;
      this._cloudError = this._errorText(err);
      this._cloudFailedAt = Date.now();
    } finally {
      this._cloudLoading = false;
      this._renderCloud();
      this._renderShellHooks();
    }
  }

  _errorText(err) {
    const text = (err && (err.message || err.error || String(err))) || "okänt fel";
    return text.replace(/^.*?Error:\s*/, "");
  }

  _renderShellHooks() {
    const refresh = this.shadowRoot.getElementById("cloudrefresh");
    if (refresh && !refresh._bound) {
      refresh._bound = true;
      refresh.addEventListener("click", () => {
        this._cloudLoaded = false;
        this._fetchCloud();
      });
    }
  }

  getCardSize() {
    return 3 + (this._guests ? this._guests.length : 0);
  }

  _blankForm() {
    return {
      open: false,
      editSlot: null,
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

  async _startFingerEnroll(guest) {
    /* The reader lights up for the person at the door; a template only exists
       once that finger has really opened the door (the lock reports nothing
       while enrolling). */
    this._actionError = "";
    this._actionNotice = "";
    try {
      await this._callServiceWS(
        "nimly",
        "enroll_fingerprint",
        this._lockData({ slot: guest.slot })
      );
      this._actionNotice = `Läsaren är öppen — lägg ${guest.name || "gästens"} finger på låset nu.`;
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
      editKind: guest.kind === "recurring" ? "recurring" : "simple",
      mode: guest.kind === "recurring" ? "recurring" : "simple",
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

  _lockData(data) {
    /* With several locks the services need to know which one: without the
       entry id they fan out to every mirror. */
    const entryId = this._entryId();
    return entryId ? { ...data, entry_id: entryId } : { ...data };
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
    return (this._guests || []).find((row) => row.slot === this._form.editSlot) || null;
  }

  _siblings(guest) {
    /* The same person on other locks: one group marker when the guests were
       created together, or one shared cloud identity once they are synced. */
    if (!guest) return [];
    const mine = this._entryId();
    const users = new Set(guest.cloud_users || []);
    const group = guest.group || null;
    const out = [];
    for (const lock of this._locks()) {
      if (lock.entry_id === mine) continue;
      const row = (lock.guests || []).find((item) => {
        if (group && item.group && item.group === group) return true;
        return (
          users.size > 0 &&
          (item.cloud_users || []).some((user) => users.has(user))
        );
      });
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
          "nimly",
          service,
          build({ ...sibling, entry_id: sibling.lock.entry_id })
        );
      } catch (err) {
        failed.push(`${sibling.lock.name}: ${this._errorText(err)}`);
      }
    }
    return failed;
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <ha-icon icon="mdi:account-key"></ha-icon>
          <div class="title">Gästkoder</div>
          <div class="count" id="count">0</div>
        </div>
        <div class="list" id="list"></div>
        <div id="form"></div>
        <div class="section" id="cloud" hidden>
          <div class="shead">
            <ha-icon icon="mdi:cloud-outline"></ha-icon>
            <div class="stitle">I appen</div>
            <div class="count" id="cloudcount">0</div>
            <button class="icon" id="cloudrefresh" title="Uppdatera">
              <ha-icon icon="mdi:refresh"></ha-icon></button>
          </div>
          <div class="list" id="cloudlist"></div>
        </div>
      </ha-card>
    `;
    this._renderList();
    this._renderForm();
    this._renderShellHooks();
  }

  _renderCloud() {
    const section = this.shadowRoot.getElementById("cloud");
    const list = this.shadowRoot.getElementById("cloudlist");
    const count = this.shadowRoot.getElementById("cloudcount");
    if (!section || !list) return;
    const localNames = new Set(
      (this._guests || []).map((g) => (g.name || "").trim().toLowerCase())
    );
    const owned = new Set();
    for (const g of this._guests || []) {
      for (const user of g.cloud_users || []) owned.add(user);
    }
    const guests = (this._cloudGuests || []).filter(
      (g) =>
        !(g.id && owned.has(g.id)) &&
        !localNames.has((g.name || "").trim().toLowerCase())
    );
    if (count) count.textContent = String(guests.length);
    if (!guests.length && !this._cloudEdit && !this._cloudError && !this._cloudNotice) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    list.innerHTML = "";
    if (this._cloudError) {
      const line = document.createElement("div");
      line.className = "empty error";
      line.textContent = this._cloudError;
      list.appendChild(line);
    }
    if (this._cloudNotice) {
      const line = document.createElement("div");
      line.className = "empty";
      line.textContent = this._cloudNotice;
      list.appendChild(line);
    }
    if (this._cloudEdit) list.appendChild(this._cloudForm());
    for (const guest of guests) list.appendChild(this._cloudRow(guest));
    if (!guests.length && !this._cloudEdit && !this._cloudError) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Inga gäster i appen.";
      list.appendChild(empty);
    }
  }

  _cloudRow(guest) {
    const row = document.createElement("div");
    row.className = "row cloudrow";
    const initial = (guest.name || "?").trim().charAt(0);
    const onLock = Array.isArray(guest.on_lock) ? guest.on_lock : null;
    const elsewhere = Array.isArray(guest.elsewhere) ? guest.elsewhere : [];
    const ghosts = Array.isArray(guest.ghost) ? guest.ghost : [];
    const badges = [];
    const types = [
      ["pin", "mdi:dialpad", "PIN"],
      ["finger", "mdi:fingerprint", "Finger"],
      ["tag", "mdi:tag-outline", "Tag"],
    ];
    for (const [type, icon, label] of types) {
      const claimed =
        type === "pin"
          ? guest.has_pin
          : type === "finger"
          ? guest.has_fingerprint
          : guest.has_tag;
      if (onLock) {
        if (onLock.includes(type)) badges.push(this._cloudBadge(icon, label));
        else if (elsewhere.includes(type)) {
          badges.push(
            this._cloudBadge(icon, label, "dim", "på ett annat lås")
          );
        } else if (ghosts.includes(type)) {
          badges.push(
            this._cloudBadge(
              icon,
              label,
              "warn",
              "finns i appen men koden ligger inte på något aktivt lås"
            )
          );
        }
      } else if (claimed) {
        badges.push(this._cloudBadge(icon, label));
      }
    }
    if (guest.status) {
      badges.push(
        `<span class="badge warn" title="${this._esc(guest.status)}">` +
          `<ha-icon icon="mdi:alert-circle-outline"></ha-icon>(!)</span>`
      );
    }
    const pinFromApp = guest.has_pin
      ? '<span class="cloudmark off" title="PIN-koden finns bara i appen — värdet kan inte återställas härifrån"><ha-icon icon="mdi:key-off"></ha-icon></span>'
      : "";
    const confirming = this._cloudConfirm === guest.id;
    row.innerHTML = `
      <div class="avatar">${initial}</div>
      <div class="info">
        <div class="name"><span>${this._esc(guest.name) || "Namnlös"}</span>${pinFromApp}</div>
        <div class="meta">${this._cloudValidity(guest)}</div>
        <div class="badges">${badges.join("")}</div>
      </div>
      <div class="actions">
        <button class="icon" data-act="edit" title="Redigera">
          <ha-icon icon="mdi:pencil"></ha-icon></button>
        <button class="icon ${confirming ? "confirm" : "danger"}" data-act="delete" title="Ta bort gäst">
          ${
            confirming
              ? "Ta bort?"
              : `<ha-icon icon="mdi:trash-can-outline"></ha-icon>`
          }
        </button>
      </div>
    `;
    row.querySelector('[data-act="edit"]').addEventListener("click", () =>
      this._startCloudEdit(guest)
    );
    row.querySelector('[data-act="delete"]').addEventListener("click", () => {
      if (this._cloudConfirm !== guest.id) {
        this._cloudConfirm = guest.id;
        this._renderCloud();
        clearTimeout(this._cloudConfirmTimer);
        this._cloudConfirmTimer = setTimeout(() => {
          this._cloudConfirm = null;
          this._renderCloud();
        }, 3000);
        return;
      }
      clearTimeout(this._cloudConfirmTimer);
      this._cloudConfirm = null;
      this._deleteCloudGuest(guest);
    });
    return row;
  }

  _startCloudEdit(guest) {
    this._cloudError = "";
    this._cloudNotice = "";
    this._cloudEdit = {
      id: guest.id,
      original: guest,
      name: guest.name || "",
      validFrom: (guest.valid_from || "").slice(0, 10),
      validTo: (guest.valid_to || "").slice(0, 10),
      code: "",
      busy: false,
      error: "",
    };
    this._renderCloud();
  }

  _cloudForm() {
    const edit = this._cloudEdit;
    const box = document.createElement("div");
    box.className = "cloudform";
    box.innerHTML = `
      <div class="label">Namn</div>
      <input type="text" id="cename" value="${this._esc(edit.name)}" autocomplete="off">
      <div class="row2">
        <div><div class="label">Giltig från</div>
          <input type="date" id="cefrom" value="${edit.validFrom}"></div>
        <div><div class="label">Giltig till</div>
          <input type="date" id="ceto" value="${edit.validTo}"></div>
      </div>
      <div class="label">Ny PIN (lämna tom för att behålla)</div>
      <input type="text" id="cecode" inputmode="numeric" pattern="[0-9]{4,10}"
             placeholder="4–10 siffror" autocomplete="off">
      ${edit.error ? `<div class="error">${this._esc(edit.error)}</div>` : ""}
      <div class="actions2">
        <button class="ghost" id="cecancel">Avbryt</button>
        <button class="submit" id="cesave" ${edit.busy ? "disabled" : ""}>
          ${edit.busy ? "Sparar…" : "Spara"}</button>
      </div>
    `;
    box.querySelector("#cecancel").addEventListener("click", () => {
      this._cloudEdit = null;
      this._renderCloud();
    });
    box.querySelector("#cesave").addEventListener("click", () => {
      this._saveCloudEdit(
        box.querySelector("#cename").value.trim(),
        box.querySelector("#cefrom").value,
        box.querySelector("#ceto").value,
        box.querySelector("#cecode").value.trim()
      );
    });
    return box;
  }

  async _saveCloudEdit(name, validFrom, validTo, code) {
    const edit = this._cloudEdit;
    if (!edit || edit.busy) return;
    edit.busy = true;
    edit.error = "";
    this._renderCloud();
    const state = this._hass.states[this._entityId()];
    const entryId = state && state.attributes && state.attributes.entry_id;
    try {
      const original = edit.original;
      const windowChanged =
        validFrom !== (original.valid_from || "").slice(0, 10) ||
        validTo !== (original.valid_to || "").slice(0, 10);
      const nameChanged = name && name !== original.name;
      if (nameChanged || windowChanged) {
        const data = { user_id: edit.id };
        if (nameChanged) data.new_name = name;
        if (windowChanged) {
          if (validFrom) data.valid_from = validFrom;
          if (validTo) data.valid_to = validTo;
        }
        await this._callServiceWS("nimly", "update_cloud_guest", data);
      }
      if (code) {
        if (!/^\d{4,10}$/.test(code)) {
          throw new Error("koden måste vara 4–10 siffror");
        }
        const data = { user_id: edit.id, type: "pin", value: code };
        if (entryId) data.entry_id = entryId;
        await this._callServiceWS("nimly", "set_cloud_code", data);
      }
      this._cloudEdit = null;
      this._cloudNotice = "Sparat.";
      setTimeout(() => {
        if (this._cloudNotice === "Sparat.") {
          this._cloudNotice = "";
          this._renderCloud();
        }
      }, 2500);
      this._cloudLoaded = false;
      await this._fetchCloud();
    } catch (err) {
      edit.busy = false;
      edit.error = this._errorText(err);
      this._renderCloud();
    }
  }

  async _deleteCloudGuest(guest) {
    this._cloudError = "";
    this._cloudNotice = "";
    try {
      await this._callServiceWS("nimly", "delete_cloud_guest", {
        user_id: guest.id,
      });
      this._cloudNotice = `${guest.name || "Gästen"} borttagen.`;
      setTimeout(() => {
        this._cloudNotice = "";
        this._renderCloud();
      }, 2500);
      this._cloudLoaded = false;
      await this._fetchCloud();
    } catch (err) {
      this._cloudError = this._errorText(err);
      this._renderCloud();
    }
  }

  _cloudBadge(icon, label, extraClass, title) {
    const cls = extraClass ? `badge ${extraClass}` : "badge";
    const tip = title ? ` title="${title}"` : "";
    return `<span class="${cls}"${tip}><ha-icon icon="${icon}"></ha-icon>${label}</span>`;
  }

  _esc(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  _cloudValidity(guest) {
    const from = this._cloudDate(guest.valid_from);
    const to = this._cloudDate(guest.valid_to);
    if (!from && !to) return "Giltighet saknas";
    return `${from || "?"} – ${to || "?"}`;
  }

  _cloudDate(value) {
    if (!value) return "";
    try {
      return new Date(value).toLocaleDateString("sv-SE");
    } catch (err) {
      return "";
    }
  }

  _renderList() {
    const list = this.shadowRoot.getElementById("list");
    const count = this.shadowRoot.getElementById("count");
    if (!list) return;
    const guests = this._guests || [];
    if (count) count.textContent = String(guests.length);
    if (!guests.length) {
      list.innerHTML = `<div class="empty">Inga gäster just nu.</div>`;
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
    const recurring = guest.kind === "recurring";
    const confirming = this._confirmSlot === guest.slot;
    const siblings = this._siblings(guest);
    const sibBadge = siblings.length
      ? `<span class="cloudmark" title="Även på ${siblings
          .map((sibling) => this._esc(sibling.lock.name))
          .join(", ")}"><ha-icon icon="mdi:door"></ha-icon>${siblings.length + 1} lås</span>`
      : "";
    row.innerHTML = `
      <div class="avatar">${initial}</div>
      <div class="info">
        <div class="name"><span>${this._esc(guest.name) || "Namnlös"}</span>${
        (guest.cloud_users || []).length
          ? '<span class="cloudmark" title="Synkad med appen"><ha-icon icon="mdi:cloud-check-outline"></ha-icon></span>'
          : ""
      }${
        guest.restorable
          ? '<span class="cloudmark ok" title="PIN-koden sparas här — kan återställas efter en förlust"><ha-icon icon="mdi:key-variant"></ha-icon></span>'
          : '<span class="cloudmark off" title="PIN-koden sparas inte — kan inte återställas efter en förlust"><ha-icon icon="mdi:key-off"></ha-icon></span>'
      }${sibBadge}${this._pill(guest)}</div>
        <div class="meta">${this._esc(meta)}</div>
      </div>
      <div class="actions">
        <button class="icon" data-act="edit" title="Redigera">
          <ha-icon icon="mdi:pencil"></ha-icon></button>
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
        <button class="icon ${confirming ? "confirm" : "danger"}" data-act="revoke" title="Återkalla">
          ${
            confirming
              ? "Återkalla?"
              : `<ha-icon icon="mdi:trash-can-outline"></ha-icon>`
          }
        </button>
      </div>
    `;
    row.querySelector('[data-act="finger"]').addEventListener("click", () =>
      this._startFingerEnroll(guest)
    );
    row.querySelector('[data-act="edit"]').addEventListener("click", () =>
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
        this._lockData({ slot: guest.slot, paused: !guest.paused })
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
      this._fanOut("revoke_guest_code", guest, (target) => ({
        slot: target.slot,
        entry_id: target.entry_id,
      })).then((failed) => {
        this._callService(
          "revoke_guest_code",
          this._lockData({ slot: guest.slot })
        );
        if (failed.length) {
          this._actionError = `Misslyckades — ${failed.join("; ")}`;
          this._renderList();
        }
      });
    });
    return row;
  }

  _pill(guest) {
    if (guest.kind === "recurring") {
      if (guest.paused) return `<span class="pill paused">Pausad</span>`;
      if (guest.in_window) return `<span class="pill active">Aktiv</span>`;
      return `<span class="pill outside">Utanför</span>`;
    }
    if (guest.one_time) return `<span class="pill temp">Engång</span>`;
    return `<span class="pill temp">Tillfällig</span>`;
  }

  _meta(guest) {
    if (guest.kind === "recurring") {
      const summary = (guest.summary || "").replace(/mon|tue|wed|thu|fri|sat|sun/g, (day) => {
        const found = DAYS.find(([key]) => key === day);
        return found ? found[1] : day;
      });
      if (guest.paused) return `Återupptas manuellt · ${summary}`;
      if (guest.in_window) return summary;
      return `Öppnar nästa gång enligt ${summary}`;
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
      host.innerHTML = `<button class="newbtn" id="new"><ha-icon icon="mdi:plus"></ha-icon> Ny gästkod</button>`;
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
    const editing = form.editSlot !== null;
    const keepRecurring = editing && form.editKind === "recurring";
    const segmented = keepRecurring
      ? ""
      : `<div class="seg">
          <button class="${recurring ? "" : "on"}" data-mode="simple">Tillfällig</button>
          <button class="${recurring ? "on" : ""}" data-mode="recurring">Återkommande</button>
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
            : simpleFields
        }
        <div class="label">Kod${editing ? "" : " (valfritt)"}</div>
        <input type="text" id="code" inputmode="numeric" placeholder="${
          editing ? "Lämna tomt för att behålla nuvarande" : "Lämna tomt för slumpad"
        }" value="${form.code}" />
        ${form.error ? `<div class="error">${this._esc(form.error)}</div>` : ""}
        <button class="submit" id="submit" ${form.busy ? "disabled" : ""}>
          ${form.busy ? "Sparar…" : editing ? "Spara ändringar" : "Skapa gästkod"}
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
      form.error = "Ge gästen ett namn.";
      this._renderForm();
      return;
    }
    const editing = form.editSlot !== null;
    const recurring = form.mode === "recurring";
    if (recurring && !form.days.size) {
      form.error = "Välj minst en dag.";
      this._renderForm();
      return;
    }
    if (editing && recurring && form.editKind === "simple" && !form.code.trim()) {
      form.error = "En återkommande gäst behöver en kod — fyll i kodfältet.";
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
          "nimly",
          "update_guest",
          this._lockData(changes)
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
          const response = await this._callServiceWS("nimly", service, payload);
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
        failed,
      };
      this._renderForm();
    } catch (err) {
      form.busy = false;
      form.error = editing
        ? "Kunde inte spara ändringen."
        : "Kunde inte skapa koden. Försök igen.";
      this._renderForm();
    }
  }

  _resultHtml(result) {
    const valid = result.schedule
      ? "Återkommande · samma kod varje gång"
      : result.until
      ? `Giltig till ${this._niceTime(result.until)}`
      : "Tills vidare";
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
        <div class="lead">Gästkoden är klar</div>
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

  async _callServiceWS(domain, service, data) {
    /* Call the service over the WebSocket API: its return_response flag is
       explicit and stable, unlike the positional argument on hass.callService
       that some frontend versions drop. */
    const result = await this._hass.callWS({
      type: "call_service",
      domain,
      service,
      service_data: data,
      return_response: true,
    });
    return (result && result.response) || {};
  }

  async _callService(service, data) {
    try {
      await this._callServiceWS("nimly", service, data);
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

customElements.define("nimly-guests-card", NimlyGuestsCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "nimly-guests-card",
  name: "Nimly Gästkoder",
  description: "Skapa och hantera gästkoder — tillfälliga och återkommande scheman.",
});
