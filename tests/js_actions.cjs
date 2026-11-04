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
  querySelector: (sel) => {
    if (sel === ".notice") {
      if (!els[".notice"]) els[".notice"] = makeEl("notice");
      return els[".notice"];
    }
    return null;
  },
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

// Project tasks approve on staged approval, never on sample evidence;
// revalidation re-snapshots the staged diff (no automated checks exist),
// so expired approvals and review_required tasks can recover.
run(`task = {id: "t1", status: "awaiting_approval", mode: "project", evidence: null, approval: {nonce: "n"}}; actions();`);
if (el("approve").disabled !== false) fail("approve disabled on staged project approval");
if (el("revalidate").disabled !== false) fail("revalidate disabled on project task");
run(`task.approval = null; actions();`);
if (el("approve").disabled !== true) fail("approve enabled without project approval");

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

// Key note names the funding key of the active mode, never the wrong one.
run(`selectedMode = {mode: "baseten"}; modelRouteConfigured = true; sessionKeyAttached = true; keyNote();`);
if (!String(el("key-note").textContent).includes("event Baseten key")) {
  fail("baseten note misnames funding: " + el("key-note").textContent);
}
run(`selectedMode = {mode: "meta"}; modelRouteConfigured = true; sessionKeyAttached = true; keyNote();`);
if (!String(el("key-note").textContent).includes("own Meta account")) {
  fail("meta note lost funding: " + el("key-note").textContent);
}
run(`selectedMode = {mode: "baseten"}; modelRouteConfigured = false; keyNote();`);
if (!String(el("key-note").textContent).includes("No Baseten model route")) {
  fail("baseten no-route note wrong: " + el("key-note").textContent);
}
run(`selectedMode = null; modelRouteConfigured = false; keyNote();`);
if (!String(el("key-note").textContent).includes("No Meta model route")) {
  fail("pre-session no-route note wrong: " + el("key-note").textContent);
}

// Run/supervision scopes are visible inline, not only inside tips.
if (!html.includes("Run mode (next plan)")) fail("run scope hint missing");
if (!html.includes("Supervision (all runs)")) fail("supervision scope hint missing");
if (html.includes("next task you create")) fail("stale run scope tip kept");

// Leaving without a judge account drops the session's tasks; say so visibly.
if (!html.includes('id="leave-note"')) fail("leave retention note missing");
if (!html.includes("keep tasks across visits")) fail("leave note names no remedy");

// Meta dictation follows the Meta route only: Baseten serves are live but
// their transcribe route always refuses, so they must not prefer Meta.
run(`displayMode({mode: "baseten", live_inference: true});`);
if (run(`metaSttPreferred`) !== false) fail("baseten prefers Meta STT");
run(`displayMode({mode: "meta", live_inference: true});`);
if (run(`metaSttPreferred`) !== true) fail("meta dropped Meta STT");
run(`displayMode({mode: "scripted", live_inference: false});`);
if (run(`metaSttPreferred`) !== false) fail("scripted prefers Meta STT");

// Idle sessions keep runs and inbox fresh on the slow loop; offline starts nothing.
run(`selectedMode = null; stopSlowPolling(); startSlowPolling();`);
if (run(`slowTimer`) !== null) fail("slow poll started while offline");
run(`selectedMode = {mode: "scripted"}; startSlowPolling();`);
if (run(`slowTimer`) === null) fail("slow poll did not start when connected");
run(`startSlowPolling(); stopSlowPolling();`);
if (run(`slowTimer`) !== null) fail("slow poll did not stop");

// Approved threads are closed server-side: chat and planning stay shut.
run(`selectedMode = {mode: "scripted"}; connecting = false; supervisorBusy = false;
     supervisorThreadId = "tid"; supervisorThreadStatus = "approved"; supervisorPlanNonce = "";
     supervisorActions();`);
if (el("supervisorSend").disabled !== true) fail("send enabled on approved thread");
if (el("supervisorWritePlan").disabled !== true) fail("write-plan enabled on approved thread");
if (el("supervisorInput").disabled !== true) fail("input enabled on approved thread");
run(`supervisorThreadStatus = "open"; supervisorActions();`);
if (el("supervisorSend").disabled !== false) fail("send disabled on open thread");
if (el("supervisorWritePlan").disabled !== false) fail("write-plan disabled on open thread");

// Project checklist reports staged files even before any approval exists.
run(`element("approvalChecklist").replaceChildren = function(){ this.kids = Array.from(arguments); };
     task = {id: "t1", status: "paused", mode: "project", approval: null, prompt: "p",
             calls: 1, project_root: "/repo", injected: false, evidence: null};
     taskSource = ""; reviewContext = null; lastProjectFiles = [{path: "a.txt"}, {path: "b.txt"}];
     renderApprovalSubject();`);
const checklistText = run(`element("approvalChecklist").kids.map((k) => k.textContent).join(" | ")`);
if (!checklistText.includes("2 file(s) staged")) fail("checklist missed staged files: " + checklistText);
if (checklistText.includes("No staged change")) fail("checklist contradicts staged files: " + checklistText);
if (!String(el("approvalSubject").textContent).includes("staged files 2")) {
  fail("subject missed staged count: " + el("approvalSubject").textContent);
}

// Concurrent inbox polls fetch once; the second call is dropped, not duplicated.
(async () => {
  run(`selectedMode = {mode: "scripted"}; inboxCursor = 0; inboxItems = []; ownerFloorSeq = 0;
       inboxBusy = false; var fetchCalls = [];
       fetch = (...args) => { fetchCalls.push(args);
         return Promise.resolve({ok: true, status: 200,
           text: async () => '{"cursor": 5, "items": []}'}); };
       pollInbox(); pollInbox();`);
  await new Promise((r) => setTimeout(r, 100));
  const calls = run(`fetchCalls.length`);
  if (calls !== 1) fail("concurrent pollInbox fetched " + calls + "x");
  console.log("ACTIONS-OK approve gating, submit reasons, hosted note, key funding, scope hints, leave note, stt route, slow poll, thread state, project files, inbox guard");
  process.exit(0);
})().catch((err) => fail("async harness: " + (err && err.message)));
