"use strict";
// Dictate state-machine simulation: drives the REAL app.js Meta dictation
// path (Firefox-like: no SpeechRecognition) with stubbed mic/recorder/fetch
// and asserts the record/stop/transcribe order across two sessions.
// Regression net for "audio after Stop is captured, before is lost".
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = process.env.SMOKE_ROOT
  || path.join(__dirname, "..", "src", "remote_operator");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const js = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");
const realIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));

const log = [];
let seq = 0;
const mark = (name) => log.push(++seq + ":" + name);

function makeClassList() {
  const set = new Set();
  return {
    add: (c) => set.add(c),
    remove: (c) => set.delete(c),
    toggle: (c, f) => {
      if (f === undefined) { if (set.has(c)) set.delete(c); else set.add(c); }
      else if (f) set.add(c); else set.delete(c);
    },
    contains: (c) => set.has(c),
  };
}
function makeEl(id) {
  return {
    id, hidden: false, disabled: false, value: "", textContent: "",
    checked: false, scrollTop: 0, scrollHeight: 0, offsetHeight: 0,
    style: {}, dataset: {}, options: [], children: [], parentNode: null,
    title: "", lang: "", rows: 0,
    classList: makeClassList(),
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    addEventListener() {}, removeEventListener() {},
    append() {}, appendChild() {}, replaceChildren() {}, remove() {},
    focus() {}, click() {}, scrollIntoView() {}, closest() { return null; },
    getClientRects() { return []; },
    getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, right: 0, width: 0, height: 0 }; },
    querySelector() { return null; }, querySelectorAll() { return []; },
  };
}
const els = {};
const rows = {};
const documentStub = {
  hidden: false,
  activeElement: null,
  getElementById: (id) => {
    if (!realIds.has(id)) return null;
    if (!els[id]) els[id] = makeEl(id);
    return els[id];
  },
  createElement: () => makeEl("anon"),
  querySelector: (sel) => {
    const m = /data-dictate-for="([^"]+)"/.exec(sel || "");
    if (m) {
      if (!rows[m[1]]) rows[m[1]] = makeEl("row-" + m[1]);
      return rows[m[1]];
    }
    return null;
  },
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  body: makeEl("body"),
};

let recorderCount = 0;
class FakeRecorder {
  static isTypeSupported() { return true; }
  constructor(stream, opts) {
    this.id = ++recorderCount;
    this.stream = stream;
    this.opts = opts;
    this.state = "inactive";
    this.ondataavailable = null;
    this.onstop = null;
    this.onerror = null;
  }
  start() {
    this.state = "recording";
    mark("rec" + this.id + ".start");
  }
  stop() {
    if (this.state === "inactive") return;
    this.state = "inactive";
    mark("rec" + this.id + ".stop");
    const chunk = new Blob([new Uint8Array(2048)]);
    setImmediate(() => {
      if (this.ondataavailable) this.ondataavailable({ data: chunk });
      if (this.onstop) this.onstop();
    });
  }
}
const fakeStream = { getTracks: () => [{ stop: () => mark("track.stop") }] };
let micDelayMs = 0;
let micGrants = 0;

let transcribeCount = 0;
const fetchStub = async (url) => {
  if (String(url).includes("/api/stt/transcribe")) {
    transcribeCount++;
    mark("fetch.transcribe#" + transcribeCount);
    await new Promise((r) => setTimeout(r, 20));
    return { ok: true, status: 200, json: async () => ({ text: "SIMULATED" + transcribeCount }), text: async () => "" };
  }
  return { ok: true, status: 200, json: async () => ({}), text: async () => "{}" };
};

const windowStub = {
  document: documentStub,
  location: { hash: "", href: "http://localhost/", pathname: "/" },
  navigator: {
    language: "en-US",
    onLine: true,
    mediaDevices: {
      getUserMedia: async () => {
        if (micDelayMs) await new Promise((r) => setTimeout(r, micDelayMs));
        micGrants++;
        mark("mic.granted#" + micGrants);
        return fakeStream;
      },
    },
  },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {},
  removeEventListener() {},
  confirm: () => false,
  alert() {},
  fetch: fetchStub,
  MediaRecorder: FakeRecorder,
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
};
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  Blob, FormData, AbortSignal,
  document: documentStub, window: windowStub, navigator: windowStub.navigator,
  location: windowStub.location, fetch: windowStub.fetch,
  MediaRecorder: FakeRecorder,
  matchMedia: windowStub.matchMedia, confirm: windowStub.confirm,
  alert: windowStub.alert, requestAnimationFrame: windowStub.requestAnimationFrame,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let asyncFailure = null;
process.on("unhandledRejection", (err) => {
  if (!asyncFailure) asyncFailure = err;
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function fail(msg) {
  console.log("DICTATE-FAIL " + msg);
  console.log("LOG: " + log.join(" "));
  process.exit(1);
}
async function settle(cond, ms, what) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (cond()) return;
    await sleep(25);
  }
  fail("timeout waiting for " + what);
}

(async () => {
  try {
    vm.runInContext(js, sandbox, { filename: "app.js" });
  } catch (err) {
    fail("load threw: " + (err && err.message));
  }
  await sleep(100);
  // Arm the Meta backend the way loadMode does on a live server.
  vm.runInContext("metaSttPreferred = true; refreshDictationBackends();", sandbox);
  const button = els["dictate-supervisor"];
  const field = els["supervisorInput"];
  const status = els["dictate-status-supervisor"];
  if (!button || !button.onclick) fail("dictate button not wired");
  if (button.textContent !== "🎤 Dictate (Meta)") fail("unexpected label: " + button.textContent);

  for (const round of [1, 2]) {
    mark("round" + round + ".clickDictate");
    button.onclick();
    await settle(() => button.textContent === "Stop dictation", 2000, "recording state round " + round);
    await sleep(120); // simulated speech time
    mark("round" + round + ".clickStop");
    button.onclick();
    await settle(() => button.textContent === "🎤 Dictate (Meta)", 5000, "restored label round " + round);
    if (!field.value.includes("SIMULATED")) fail("round " + round + ": field missing transcript: " + field.value);
  }

  // Order assertions on the event log.
  const at = (name) => log.findIndex((e) => e.endsWith(":" + name));
  for (const round of [1, 2]) {
    const clickD = at("round" + round + ".clickDictate");
    const start = log.findIndex((e, i) => i > clickD && /rec\d+\.start$/.test(e));
    const clickS = at("round" + round + ".clickStop");
    const stop = log.findIndex((e, i) => i > clickS && /rec\d+\.stop$/.test(e));
    const fetch = log.findIndex((e, i) => i > clickD && e.includes("fetch.transcribe"));
    if (!(clickD < start && start < clickS)) fail(`round ${round}: start must sit between clicks`);
    if (!(clickS < stop)) fail(`round ${round}: stop must follow stop-click`);
    if (!(stop < fetch)) fail(`round ${round}: transcribe must follow recorder stop`);
  }
  if (recorderCount !== 2) fail("expected exactly 2 recorders, got " + recorderCount);
  if (transcribeCount !== 2) fail("expected exactly 2 transcribes, got " + transcribeCount);

  // Hostile round 3: slow mic prompt + impatient second click must not fork a session.
  micDelayMs = 250;
  mark("round3.clickDictate");
  button.onclick();
  await sleep(50);
  mark("round3.clickDuringPrompt");
  button.onclick();
  await settle(() => button.textContent === "Stop dictation", 2000, "recording state round 3");
  await sleep(100);
  button.onclick();
  await settle(() => button.textContent === "🎤 Dictate (Meta)", 5000, "restored label round 3");
  await sleep(100);
  if (micGrants !== 3) fail("prompt click forked a session; mic grants: " + micGrants);
  if (recorderCount !== 3) fail("expected 3 recorders, got " + recorderCount);
  if (transcribeCount !== 3) fail("expected 3 transcribes, got " + transcribeCount);
  micDelayMs = 0;

  // Hostile round 4: double stop-click must transcribe exactly once.
  mark("round4.clickDictate");
  button.onclick();
  await settle(() => button.textContent === "Stop dictation", 2000, "recording state round 4");
  await sleep(100);
  mark("round4.doubleStop");
  button.onclick();
  button.onclick();
  await settle(() => button.textContent === "🎤 Dictate (Meta)", 5000, "restored label round 4");
  await sleep(100);
  if (recorderCount !== 4) fail("expected 4 recorders, got " + recorderCount);
  if (transcribeCount !== 4) fail("double stop transcribed twice; count: " + transcribeCount);
  if (asyncFailure) fail("async: " + (asyncFailure.stack || asyncFailure));
  console.log("DICTATE-OK two Meta sessions recorded in order; transcript landed in field");
  console.log("LOG: " + log.join(" "));
  process.exit(0);
})().catch((err) => fail("harness: " + (err && err.stack ? err.stack.split("\n")[0] : err)));
