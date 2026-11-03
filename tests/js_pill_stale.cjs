"use strict";
// Focused pill test: a stale agent heartbeat must NOT light the worker pill.
// Executes the REAL app.js against DOM stubs, drives refreshWindowStatus with
// a stale then a fresh /api/window payload, and asserts the pill text.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = process.env.SMOKE_ROOT
  || path.join(__dirname, "..", "src", "remote_operator");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const js = fs.readFileSync(path.join(ROOT, "app.js"), "utf8");
const realIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]));

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
    title: "",
    style: {}, dataset: {}, options: [], children: [], parentNode: null,
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
let windowPayload = { running: false };
const documentStub = {
  hidden: false,
  activeElement: null,
  getElementById: (id) => {
    if (!realIds.has(id)) return null;
    if (!els[id]) els[id] = makeEl(id);
    return els[id];
  },
  createElement: () => makeEl("anon"),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  body: makeEl("body"),
};
function stubFetch(url) {
  const data = String(url).includes("/api/window") ? windowPayload : {};
  return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(data) });
}
const windowStub = {
  document: documentStub,
  location: { hash: "", href: "http://localhost/", pathname: "/" },
  navigator: { language: "en-US", onLine: true },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {},
  removeEventListener() {},
  confirm: () => false,
  alert() {},
  fetch: stubFetch,
  ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
  IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
};
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  document: documentStub, window: windowStub, navigator: windowStub.navigator,
  location: windowStub.location, fetch: stubFetch,
  matchMedia: windowStub.matchMedia, confirm: windowStub.confirm,
  alert: windowStub.alert, requestAnimationFrame: windowStub.requestAnimationFrame,
  ResizeObserver: windowStub.ResizeObserver,
  IntersectionObserver: windowStub.IntersectionObserver,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

function fail(msg) {
  console.log("PILL-FAIL " + msg);
  process.exit(1);
}

let asyncFailure = null;
process.on("unhandledRejection", (err) => {
  if (!asyncFailure) asyncFailure = err;
});

try {
  vm.runInContext(js, sandbox, { filename: "app.js" });
} catch (err) {
  fail("load threw: " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join(" | ") : err));
}

function windowWith(heartbeatIso) {
  return {
    running: true, slice: "s", phase: "p",
    last_heartbeat_utc: heartbeatIso, note: "",
    agents: [{ name: "main", task: "P6/L3", status: "busy" }],
    drain: { alive: true, last_pass: Math.floor(Date.now() / 1000), passes: 1, errors: 0, restarts: 0 },
    serve_pid: 1,
  };
}

(async () => {
  vm.runInContext("selectedMode = {mode: 'scripted'}", sandbox);
  const pill = els["workerPill"] || documentStub.getElementById("workerPill");
  if (!pill) fail("no workerPill element");

  windowPayload = windowWith("2026-11-01T22:00:00Z"); // 2-day-old heartbeat
  await vm.runInContext("refreshWindowStatus()", sandbox);
  if (pill.textContent !== "○ Idle") {
    fail("stale heartbeat shows " + JSON.stringify(pill.textContent) + ", expected Idle");
  }

  windowPayload = windowWith(new Date().toISOString()); // fresh heartbeat
  await vm.runInContext("refreshWindowStatus()", sandbox);
  if (pill.textContent !== "● Working") {
    fail("fresh heartbeat shows " + JSON.stringify(pill.textContent) + ", expected Working");
  }
  if (!String(pill.title).includes("reported agent")) {
    fail("working title hides the reason: " + JSON.stringify(pill.title));
  }

  if (asyncFailure) fail("async: " + (asyncFailure.stack || asyncFailure));
  console.log("PILL-OK stale heartbeat idles the pill, fresh heartbeat works it");
  process.exit(0);
})().catch((err) => fail("harness: " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join(" | ") : err)));
