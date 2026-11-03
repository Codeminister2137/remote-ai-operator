"use strict";
// Minimal load smoke: executes the REAL app.js top level against DOM stubs
// and fails on any load-time throw (e.g. TDZ ReferenceError). Load-only;
// interaction paths are covered by browser/manual passes.
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
const windowStub = {
  document: documentStub,
  location: { hash: "", href: "http://localhost/", pathname: "/" },
  navigator: { language: "en-US", onLine: true },
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  addEventListener() {},
  removeEventListener() {},
  confirm: () => false,
  alert() {},
  fetch: async () => ({ ok: true, json: async () => ({}), text: async () => "" }),
  ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
  IntersectionObserver: class { observe() {} unobserve() {} disconnect() {} },
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
};
// NOTE: localStorage/sessionStorage deliberately absent: app.js must not use them.
const sandbox = {
  console, setTimeout, clearTimeout, setInterval, clearInterval,
  document: documentStub, window: windowStub, navigator: windowStub.navigator,
  location: windowStub.location, fetch: windowStub.fetch,
  matchMedia: windowStub.matchMedia, confirm: windowStub.confirm,
  alert: windowStub.alert, requestAnimationFrame: windowStub.requestAnimationFrame,
  ResizeObserver: windowStub.ResizeObserver,
  IntersectionObserver: windowStub.IntersectionObserver,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

let asyncFailure = null;
process.on("unhandledRejection", (err) => {
  if (!asyncFailure) asyncFailure = err;
});

try {
  vm.runInContext(js, sandbox, { filename: "app.js" });
} catch (err) {
  console.log("SMOKE-FAIL load threw: " + (err && err.stack ? err.stack.split("\n").slice(0, 4).join(" | ") : err));
  process.exit(1);
}
for (const name of ["element", "applyDiffMode"]) {
  let type = "missing";
  try { type = vm.runInContext("typeof " + name, sandbox); } catch { type = "threw"; }
  if (type !== "function") {
    console.log("SMOKE-FAIL expected function " + name + ", got " + type);
    process.exit(1);
  }
}
setTimeout(() => {
  if (asyncFailure) {
    console.log("SMOKE-FAIL async: " + (asyncFailure.stack || asyncFailure));
    process.exit(1);
  }
  console.log("SMOKE-OK app.js top level executed without throwing");
  process.exit(0);
}, 300);
