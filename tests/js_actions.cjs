"use strict";
// Focused UI-behavior test: approve gating, submit block reasons, hosted plan note.
// Executes the REAL app.js against DOM stubs and drives actions() states.
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
function stubFetch() {
  return Promise.resolve({ ok: true, status: 200, text: async () => "{}" });
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
  console.log("ACTIONS-FAIL " + msg);
  process.exit(1);
}
function el(id) {
  const found = els[id] || documentStub.getElementById(id);
  if (!found) fail("missing element " + id);
  return found;
}

try {
  vm.runInContext(js, sandbox, { filename: "app.js" });
} catch (err) {
  fail("load threw: " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join(" | ") : err));
}
const run = (code) => vm.runInContext(code, sandbox);

// Approve requires passing evidence.
run(`element("taskId").value = "t1"; task = {id: "t1", status: "awaiting_approval", evidence: {passed: false}, approval: {}};
     selectedMode = {mode: "scripted"}; actions();`);
if (el("approve").disabled !== true) fail("approve enabled on failed evidence");
run(`task.evidence.passed = true; actions();`);
if (el("approve").disabled !== false) fail("approve disabled on passing evidence");

// Disabled Create names the allowance block.
run(`selectedMode = {mode: "meta", live_inference: true};
     allowance = {enabled: true, expired: true, remaining_usd_estimate: 0, requests_used: 0, requests_limit: 5};
     task = null; element("taskId").value = ""; actions();`);
if (el("submit").disabled !== true) fail("submit enabled on expired allowance");
if (!String(el("submit").title).includes("expired")) fail("submit hides the reason: " + el("submit").title);

// Hosted plan note omits the hidden supervision control.
run(`hosted = true; supervisorPlanNonce = "n";
     element("runMode").value = "implement"; element("supervisionMode").value = "attended";
     updatePlanSettingsNote();`);
if (/attended/i.test(String(el("planSettingsNote").textContent))) {
  fail("hosted note names hidden supervision: " + el("planSettingsNote").textContent);
}
run(`hosted = false; updatePlanSettingsNote();`);
if (!/attended/i.test(String(el("planSettingsNote").textContent))) {
  fail("owner note lost supervision: " + el("planSettingsNote").textContent);
}

console.log("ACTIONS-OK approve gating, submit reasons, hosted note");
process.exit(0);
