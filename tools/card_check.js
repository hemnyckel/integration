#!/usr/bin/env node
/**
 * Logic checks for the guests card, without a browser.
 *
 * The card is a class over the DOM and `hass`; this harness stubs exactly
 * those, captures the class from `customElements.define`, and drives the
 * multi-lock paths: lock discovery, sibling matching (shared group marker),
 * the lock-aware service data, the create flow across several
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
  path.join(__dirname, "..", "www", "hemnyckel-guests-card.js"),
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
      { slot: 3, name: "Isabelle", group: "grp-1" },
      { slot: 4, name: "Städfirma", group: null },
    ]),
    "sensor.nimly_guests_2": lockSensor(
      "sensor.nimly_guests_2",
      OTHER,
      "Källarlås",
      [
        { slot: 5, name: "Isabelle", group: "grp-1" },
        { slot: 6, name: "Syntest", group: null },
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
check("shared group finds the sibling", siblings.length === 1 && siblings[0].lock.entry_id === OTHER && siblings[0].slot === 5);
const astrid = { slot: 4, name: "Städfirma", group: null };
check("no sibling without a group", card._siblings(astrid).length === 0);
const grouped = { slot: 9, name: "Ny", group: "grp-1" };
check("the group marker finds the sibling", card._siblings(grouped).length === 1);
const unrelated = { slot: 9, name: "Ny", group: "grp-9" };
check("an unrelated group finds nothing", card._siblings(unrelated).length === 0);

// -- row rendering ------------------------------------------------------
const row = card._guestRow(isabelle);
check("row shows the lock count", row.innerHTML.includes("2 lås"), row.innerHTML.slice(0, 200));
check("row offers fingerprint enrollment", row.innerHTML.includes('data-act="finger"'));
check(
  "a stored code shows the restorable key",
  card._guestRow({ ...isabelle, restorable: true }).innerHTML.includes("mdi:key-variant")
);
check(
  "an unstored code shows the slashed key",
  card._guestRow({ ...astrid, restorable: false }).innerHTML.includes("mdi:key-remove")
);
check(
  "a used finger shows the solid fingerprint",
  card
    ._guestRow({ ...isabelle, has_finger: true, finger_restorable: true })
    .innerHTML.includes('title="Fingret i sloten')
);
check(
  "an unused finger shows the slashed fingerprint",
  card
    ._guestRow({ ...astrid, has_finger: true, finger_restorable: false })
    .innerHTML.includes('title="Fingret är registrerat')
);
check(
  "no finger mark without a linked finger",
  !card
    ._guestRow({ ...astrid, has_finger: false, finger_restorable: false })
    .innerHTML.includes('title="Fingret')
);
check("row tooltip lists the other lock", row.innerHTML.includes("Källarlås"));

// -- the list reads the person records and the slot table ----------------
// The dashboard card is added with no `entity` at all, which is where the
// bug lived: the old card read hass.states[""] and stayed empty. This is
// exactly that card.
const liveStates = {
  "sensor.door_a_guests": {
    entity_id: "sensor.door_a_guests",
    attributes: {
      entry_id: "entry-a",
      lock: "Ytterdörren",
      guests: [
        { slot: 3, name: "Claes", kind: "permanent", group: "grp-x", restorable: true, has_code: true },
      ],
    },
  },
  "sensor.door_a_slots": {
    entity_id: "sensor.door_a_slots",
    attributes: {
      entry_id: "entry-a",
      lock: "Ytterdörren",
      slots: [
        {
          slot: 3,
          name: "Claes",
          has_pin: true,
          has_fingerprint: true,
          credentials: ["pin", "fingerprint"],
          finger_used: false,
          finger_state: "claimed",
          fingers: [{ label: "left index", enrolled: "2026-09-28T10:00:00+00:00" }],
        },
        { slot: 4, name: "Städfirma", has_pin: true, has_fingerprint: false, credentials: ["pin"] },
        { slot: 5, name: "Alva", has_pin: false, has_fingerprint: true, credentials: ["fingerprint"] },
      ],
    },
  },
  "sensor.door_b_guests": {
    entity_id: "sensor.door_b_guests",
    attributes: {
      entry_id: "entry-b",
      lock: "Källardörren",
      guests: [
        { slot: 3, name: "Claes", kind: "permanent", group: "grp-x", restorable: true, has_code: true },
      ],
    },
  },
  "sensor.door_b_slots": {
    entity_id: "sensor.door_b_slots",
    attributes: {
      entry_id: "entry-b",
      lock: "Källardörren",
      slots: [
        { slot: 3, name: "Claes", has_pin: true, has_fingerprint: false, credentials: ["pin"] },
      ],
    },
  },
};
const listCard = new global.__Card();
listCard._config = {}; // exactly how the dashboard adds it
listCard._hass = { states: liveStates };
const people = listCard._buildGuests();
check(
  "an unconfigured card lists the person it can see",
  people.some((p) => p.name === "Claes"),
  JSON.stringify(people.map((p) => p.name))
);
check(
  "the same person across two locks is listed once",
  people.filter((p) => p.name === "Claes").length === 1,
  JSON.stringify(people.map((p) => [p.name, p.entry_id]))
);
const claes = people.find((p) => p.name === "Claes");
check("the person carries its slot", claes.slot === 3, String(claes && claes.slot));
check(
  "the person shows what the slot holds (PIN)",
  claes.has_pin === true && claes.credentials.includes("pin"),
  JSON.stringify(claes && claes.credentials)
);
check(
  "a slot-only credential is listed too",
  people.some((p) => p.name === "Städfirma" && p.kind === "slot"),
  JSON.stringify(people.map((p) => p.name))
);
check(
  "a finger is read from the slot table",
  people.find((p) => p.name === "Alva").has_finger === true
);
check(
  "a person's credentials are the union across their doors",
  people.find((p) => p.name === "Claes").has_finger === true,
  JSON.stringify(people.find((p) => p.name === "Claes").credentials)
);
const claesRow = listCard._guestRow(claes).innerHTML;
check("the row shows the Kod badge", claesRow.includes(">Kod<"), claesRow.slice(0, 260));
check("the row shows the slot number", claesRow.includes("Slot 3"));
check(
  "the Fingeravtryck badge comes from the slot",
  listCard._guestRow(people.find((p) => p.name === "Alva")).innerHTML.includes(">Fingeravtryck<")
);
check("no RFID/tag badge is ever rendered", !claesRow.includes("Bricka"));

// -- per-door finger truth and the mandatory picker ----------------------
// A person across two doors keeps one row, but the row tells the truth per
// door: here Ytterdörren has the left index and Källardörren has none. The
// finger is chosen (all ten) before the reader may be started, per door.
check(
  "a two-door person carries both doors",
  (claes.doors || []).length === 2,
  JSON.stringify((claes.doors || []).map((d) => d.lock))
);
check(
  "the row shows both doors",
  claesRow.includes("Ytterdörren ·") && claesRow.includes("Källardörren ·"),
  claesRow.slice(-400)
);
check(
  "the per-door finger state differs (has one / has none)",
  claesRow.includes("Vänster pekfinger") && claesRow.includes("inget finger"),
  claesRow.slice(-400)
);
check(
  "each door's chip can start that door's enrolment",
  claesRow.includes('data-open-finger="entry-a"') &&
    claesRow.includes('data-open-finger="entry-b"'),
  claesRow.slice(-400)
);
listCard._fingerKey = claes.key;
listCard._fingerPick = { door: claes.doors[1].entry_id, finger: "left index" };
const picker = listCard._fingerPickerHtml(claes);
check(
  "the picker offers the ten fingers",
  picker.includes("Vänster tumme") && picker.includes("Höger lillfinger") &&
    (picker.match(/data-finger=/g) || []).length === 10,
  String((picker.match(/data-finger=/g) || []).length)
);
check(
  "the picker offers each door",
  picker.includes("Ytterdörren") && picker.includes("Källardörren"),
  picker.slice(0, 300)
);
check("a chosen door and finger enable the reader", picker.includes("Starta läsaren") && !picker.includes("disabled"));
listCard._fingerPick = { door: claes.doors[1].entry_id, finger: "" };
const pickerEmpty = listCard._fingerPickerHtml(claes);
check(
  "the reader is disabled until a finger is chosen",
  pickerEmpty.includes("Välj finger först") && pickerEmpty.includes("disabled")
);
check(
  "a door with no finger has a per-door target slot",
  listCard._fingerTargetSlot(claes, "entry-b", 3) === 3,
  String(listCard._fingerTargetSlot(claes, "entry-b", 3))
);
check(
  "a door whose slot already holds a finger gets the next free slot",
  listCard._fingerTargetSlot(claes, "entry-a", 3) === 6,
  String(listCard._fingerTargetSlot(claes, "entry-a", 3))
);
listCard._fingerKey = null;

// -- person icons ---------------------------------------------------------
// The card draws the same icon the app does: the relay's descriptor (kind,
// symbol, colour, version) with the photo served from the integration's
// authenticated view, and the deterministic palette as the fallback. The
// colour and initials must match Shared/Avatar.swift exactly.
const avatarCard = new global.__Card();
avatarCard._config = {};
avatarCard._hass = {
  states: {
    "select.hemnyckel_elise": {
      entity_id: "select.hemnyckel_elise",
      state: "user",
      attributes: {
        person: "Elise Högberg",
        id: "e51ac3e70b8947c8983216408c1d634f",
        avatar_kind: "symbol",
        avatar_symbol: "star",
        avatar_color: "#FF9500",
        avatar_version: 3,
        entity_picture: null,
      },
    },
    "select.hemnyckel_bo": {
      entity_id: "select.hemnyckel_bo",
      state: "user",
      attributes: {
        person: "Bo",
        id: "2c518cea7a1b469081bcc4d8a423d2b9",
        avatar_kind: "photo",
        avatar_symbol: null,
        avatar_color: null,
        avatar_version: 5,
        entity_picture: "/api/hemnyckel/avatar/2c518cea7a1b469081bcc4d8a423d2b9?v=5",
      },
    },
  },
};
check(
  "the palette index matches the app's FNV-1a (Elise -> #5856D6)",
  avatarColor("e51ac3e70b8947c8983216408c1d634f") === "#5856D6",
  avatarColor("e51ac3e70b8947c8983216408c1d634f")
);
check(
  "the colour is stable for the same id",
  avatarColor("2c518cea7a1b469081bcc4d8a423d2b9") ===
    avatarColor("2c518cea7a1b469081bcc4d8a423d2b9")
);
check("initials take first and last word", avatarInitials("Elise Högberg") === "EH");
check("initials are unicode-aware", avatarInitials("Åsa Öberg") === "ÅÖ");
check("one word gives one letter", avatarInitials("Pappa") === "P");
check("an empty name is never blank", avatarInitials("") === "?");
const eliseEntity = avatarCard._personEntity("Elise Högberg");
check(
  "the person entity is found by its `person` attribute",
  eliseEntity && eliseEntity.attributes.id === "e51ac3e70b8947c8983216408c1d634f"
);
const eliseAvatar = avatarCard._avatarFor({ name: "Elise Högberg" });
check(
  "a symbol avatar carries the token and colour",
  eliseAvatar.kind === "symbol" && eliseAvatar.symbol === "star" && eliseAvatar.color === "#FF9500",
  JSON.stringify(eliseAvatar)
);
const eliseHtml = avatarCard._avatarHtml({ name: "Elise Högberg" });
check(
  "a symbol draws its mdi glyph on the chosen colour",
  eliseHtml.includes('icon="mdi:star"') && eliseHtml.includes("#FF9500"),
  eliseHtml
);
const boHtml = avatarCard._avatarHtml({ name: "Bo" });
check(
  "a photo points at the authenticated avatar view",
  boHtml.includes('class="avatar photo"') &&
    boHtml.includes("/api/hemnyckel/avatar/2c518cea7a1b469081bcc4d8a423d2b9?v=5"),
  boHtml
);
const boAbsolute = {
  entity_id: "select.hemnyckel_bo_abs",
  state: "user",
  attributes: {
    person: "Bo Absolut",
    id: "2c518cea7a1b469081bcc4d8a423d2b9",
    avatar_kind: "photo",
    avatar_version: 5,
    entity_picture:
      "https://ha.example/api/hemnyckel/avatar/2c518cea7a1b469081bcc4d8a423d2b9?v=5",
  },
};
avatarCard._hass.states["select.hemnyckel_bo_abs"] = boAbsolute;
const absHtml = avatarCard._avatarHtml({ name: "Bo Absolut" });
check(
  "an absolute picture is loaded from this origin, not another",
  absHtml.includes(
    'src="/api/hemnyckel/avatar/2c518cea7a1b469081bcc4d8a423d2b9?v=5"'
  ),
  absHtml
);
const boNoPicture = {
  entity_id: "select.hemnyckel_bo_nopic",
  state: "user",
  attributes: { ...boAbsolute.attributes, person: "Bo Utan", entity_picture: null },
};
avatarCard._hass.states["select.hemnyckel_bo_nopic"] = boNoPicture;
const noPictureHtml = avatarCard._avatarHtml({ name: "Bo Utan" });
check(
  "a photo with no published picture is rebuilt from the person's id",
  noPictureHtml.includes(
    'src="/api/hemnyckel/avatar/2c518cea7a1b469081bcc4d8a423d2b9?v=5"'
  ),
  noPictureHtml
);
const unknownHtml = avatarCard._avatarHtml({ name: "Okänd Person" });
check(
  "a person with no entity falls back to the deterministic monogram",
  unknownHtml.includes("OP") && unknownHtml.includes("background:#"),
  unknownHtml
);
const boRow = avatarCard._guestRow({ slot: 3, name: "Bo" });
check("the row shows the photo avatar", boRow.innerHTML.includes('class="avatar photo"'));
const slotRowHtml = avatarCard._guestRow({ slot: 9, name: "Städfirma" }).innerHTML;
check(
  "a slot-only row still gets a monogram avatar",
  slotRowHtml.includes('class="avatar"') && !slotRowHtml.includes('class="avatar photo"')
);

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

  // -- permanent guests ---------------------------------------------------
  check(
    "a permanent guest gets its own pill",
    card._pill({ kind: "permanent" }).includes("Permanent")
  );
  check(
    "permanent meta says the code is stored",
    card._meta({ kind: "permanent" }).includes("sparad")
  );
  check(
    "a permanent row shows the permanent pill",
    card
      ._guestRow({ slot: 8, kind: "permanent", name: "Alva", restorable: true })
      .innerHTML.includes('class="pill permanent"')
  );
  card._startEdit({ slot: 8, kind: "permanent", name: "Alva" });
  check(
    "editing a permanent guest keeps the permanent mode",
    card._form.editKind === "permanent" && card._form.mode === "permanent"
  );
  card._form = card._blankForm();
  card._form.mode = "permanent";
  const permanentHtml = card._formHtml();
  check(
    "the form offers the permanent mode",
    permanentHtml.includes('data-mode="permanent"')
  );
  check(
    "a permanent form has no duration chips",
    !permanentHtml.includes("data-duration")
  );
  check(
    "a permanent form has no one-time toggle",
    !permanentHtml.includes('id="onetime"')
  );
  const permanentCalls = [];
  card._callServiceWS = async (domain, service, payload) => {
    permanentCalls.push({ service, payload });
    return {
      [payload.entry_id]: {
        slot: 8,
        code: "445566",
        name: "Alva",
        permanent: true,
      },
    };
  };
  card._form = card._blankForm();
  card._form.name = "Alva";
  card._form.mode = "permanent";
  card._form.locks = new Set([OWN]);
  await card._submit();
  check(
    "permanent creation flags the service",
    permanentCalls.length === 1 &&
      permanentCalls[0].service === "create_guest_code" &&
      permanentCalls[0].payload.permanent === true &&
      permanentCalls[0].payload.until === undefined &&
      permanentCalls[0].payload.one_time === undefined,
    JSON.stringify(permanentCalls.map((c) => c.payload))
  );
  check(
    "the permanent result is marked permanent",
    Boolean(card._form.result && card._form.result.permanent === true)
  );

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

  // -- deleting a person and a row --------------------------------------
  // A delete revokes the person record (where there is one) and clears the
  // slot itself, so a fingerprint-only row disappears too, exactly as the
  // relay's DELETE /api/slots/{slot} does. clear_slot does not declare a
  // response, so it must be called without asking for one. The card here is
  // the unpinned one, where the old sibling check revisited its own door.
  const deleteCalls = [];
  listCard._renderList = () => {};
  listCard._callServiceWS = async (domain, service, payload, wantResponse) => {
    deleteCalls.push({ service, payload, wantResponse });
    return {};
  };
  await listCard._deleteGuest(claes);
  check(
    "a person delete revokes the record and clears the slot",
    deleteCalls.some((c) => c.service === "revoke_guest_code") &&
      deleteCalls.some((c) => c.service === "clear_slot"),
    JSON.stringify(deleteCalls)
  );
  const ownDoorServices = deleteCalls
    .filter((c) => c.payload.entry_id === "entry-a")
    .map((c) => c.service)
    .sort();
  check(
    "each door is acted on once, not twice",
    deleteCalls.length === 4 &&
      JSON.stringify(ownDoorServices) === JSON.stringify(["clear_slot", "revoke_guest_code"]),
    JSON.stringify(deleteCalls)
  );
  check(
    "clear_slot is called without asking for a response",
    deleteCalls
      .filter((c) => c.service === "clear_slot")
      .every((c) => c.wantResponse === false),
    JSON.stringify(deleteCalls)
  );

  deleteCalls.length = 0;
  const slotRow = people.find((p) => p.name === "Städfirma" && p.kind === "slot");
  await listCard._deleteGuest(slotRow);
  check(
    "a slot-only row is cleared, not just revoked",
    deleteCalls.length === 1 &&
      deleteCalls[0].service === "clear_slot" &&
      deleteCalls[0].payload.slot === 4,
    JSON.stringify(deleteCalls)
  );

  listCard._callServiceWS = async () => {
    throw new Error("låset svarar inte");
  };
  await listCard._deleteGuest(claes);
  check(
    "a failed delete is reported with the person's name",
    listCard._actionError.includes("Claes") &&
      listCard._actionError.includes("låset svarar inte"),
    listCard._actionError
  );

  if (failures) {
    console.error(`\n${failures} check(s) failed`);
    process.exit(1);
  }
  console.log("\nall card checks passed");
})();
