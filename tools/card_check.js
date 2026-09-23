#!/usr/bin/env node
/**
 * Logic checks for the guests card, without a browser.
 *
 * The card is a class over the DOM and `hass`; this harness stubs exactly
 * those, captures the class from `customElements.define`, and drives the
 * multi-lock paths: lock discovery, sibling matching (group marker or shared
 * cloud identity), the lock-aware service data, the create flow across several
 * locks (same code, one group, partial failures reported) and the fan-out of
 * an edit. Run from the repository root:
 *
 *     node tools/card_check.js
 */

const fs = require("fs");
const path = require("path");

let failures = 0;
function check(name, condition, detail) {
  if (condition) {
    console.log("ok  -", name);
  } else {
    failures += 1;
    console.log("FAIL-", name, detail ? `(${detail})` : "");
  }
}

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
    this._innerHTML = "";
    this.hidden = false;
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
  }
  get innerHTML() {
    return this._innerHTML;
  }
  set textContent(value) {
    this._textContent = String(value);
  }
  get textContent() {
    return this._textContent || "";
  }
  attachShadow() {
    this.shadowRoot = new Element("shadow");
    return this.shadowRoot;
  }
  addEventListener(name, fn) {
    this.listeners[name] = fn;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  querySelector(selector) {
    const match =
      typeof selector === "string" && selector.match(/\[data-act="([^"]+)"\]/);
    if (match && !this._innerHTML.includes(`data-act="${match[1]}"`)) {
      return null; // a missing button must not be silently stubbed away
    }
    return new Element("stub");
  }
  querySelectorAll() {
    return [];
  }
  getElementById() {
    return new Element("stub");
  }
}

global.HTMLElement = Element;
global.customElements = {
  define(name, cls) {
    global.__Card = cls;
  },
};
global.document = {
  createElement: (tag) => new Element(tag),
};
global.window = { crypto: undefined };

const source = fs.readFileSync(
  path.join(__dirname, "..", "www", "nimly-guests-card.js"),
  "utf8"
);
eval(source);

const card = new global.__Card();
const OWN = "entry-own";
const OTHER = "entry-other";

function lockSensor(entity, entry, name, guests) {
  return {
    entity_id: entity,
    state: String(guests.length),
    attributes: { entry_id: entry, lock: name, guests },
  };
}

card._config = { entity: "sensor.nimly_guests" };
card._hass = {
  states: {
    "sensor.nimly_guests": lockSensor("sensor.nimly_guests", OWN, "Ytterdörren", [
      { slot: 3, name: "Isabelle", cloud_users: ["uuid-1"], group: "grp-1" },
      { slot: 4, name: "Städfirma", cloud_users: [], group: null },
    ]),
    "sensor.nimly_guests_2": lockSensor(
      "sensor.nimly_guests_2",
      OTHER,
      "Källarlås",
      [
        { slot: 5, name: "Isabelle", cloud_users: ["uuid-1"], group: "grp-1" },
        { slot: 6, name: "Syntest", cloud_users: ["uuid-2"], group: null },
      ]
    ),
  },
};

// -- lock discovery -----------------------------------------------------
const locks = card._locks();
check("both locks discovered, own lock first", locks.length === 2 && locks[0].entry_id === OWN, JSON.stringify(locks.map((l) => l.name)));
check("lock names come from the sensor", locks[0].name === "Ytterdörren" && locks[1].name === "Källarlås");

// -- entry-aware data ---------------------------------------------------
const data = card._lockData({ slot: 3 });
check("service data carries the card's lock", data.entry_id === OWN);

// -- sibling matching ---------------------------------------------------
const isabelle = card._guests
  ? card._guests.find((g) => g.name === "Isabelle")
  : card._hass.states["sensor.nimly_guests"].attributes.guests[0];
const siblings = card._siblings(isabelle);
check("shared identity finds the sibling", siblings.length === 1 && siblings[0].lock.entry_id === OTHER && siblings[0].slot === 5);
const astrid = { slot: 4, name: "Städfirma", cloud_users: [], group: null };
check("no sibling without group or identity", card._siblings(astrid).length === 0);
const grouped = { slot: 9, name: "Ny", cloud_users: [], group: "grp-1" };
check("group marker alone finds the sibling", card._siblings(grouped).length === 1);
const unrelated = { slot: 9, name: "Ny", cloud_users: ["uuid-9"], group: null };
check("unrelated identity finds nothing", card._siblings(unrelated).length === 0);

// -- row rendering ------------------------------------------------------
const row = card._guestRow(isabelle);
check("row shows the lock count", row.innerHTML.includes("2 lås"), row.innerHTML.slice(0, 200));
check("row offers fingerprint enrollment", row.innerHTML.includes('data-act="finger"'));
check("row tooltip lists the other lock", row.innerHTML.includes("Källarlås"));

// -- create across locks ------------------------------------------------
(async () => {
  const calls = [];
  card._callServiceWS = async (domain, service, payload) => {
    calls.push({ service, payload });
    if (payload.entry_id === OTHER) {
      throw new Error("låset svarar inte");
    }
    return { [payload.entry_id]: { slot: 7, code: "123456", name: "Isabelle" } };
  };
  card._form = card._blankForm();
  card._form.name = "Isabelle";
  card._form.mode = "simple";
  card._form.locks = new Set([OWN, OTHER]);
  card._renderForm = () => {};
  await card._submit();
  check("one creation per selected lock", calls.length === 2, JSON.stringify(calls.map((c) => c.payload.entry_id)));
  check("same code on every lock", calls[1].payload.code === "123456");
  check("one group for the batch", Boolean(calls[0].payload.group) && calls[0].payload.group === calls[1].payload.group);
  check("partial failure is reported", card._form.result && card._form.result.failed.length === 1 && card._form.result.failed[0].name === "Källarlås", JSON.stringify(card._form.result && card._form.result.failed));
  check("the code survives a partial failure", card._form.result.code === "123456");

  // -- fan-out of an edit ----------------------------------------------
  const fanCalls = [];
  card._callServiceWS = async (domain, service, payload) => {
    fanCalls.push({ service, payload });
    return {};
  };
  const failed = await card._fanOut("update_guest", isabelle, (target) => ({
    slot: target.slot,
    entry_id: target.entry_id,
    paused: true,
  }));
  check("fan-out hits the sibling's own slot", fanCalls.length === 1 && fanCalls[0].payload.entry_id === OTHER && fanCalls[0].payload.slot === 5);
  check("fan-out reports no failure", failed.length === 0);
  check("fan-out never touches the card's own lock", !fanCalls.some((c) => c.payload.entry_id === OWN));

  if (failures) {
    console.error(`\n${failures} check(s) failed`);
    process.exit(1);
  }
  console.log("\nall card checks passed");
})();
