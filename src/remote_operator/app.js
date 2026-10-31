"use strict";
const element = (id) => document.getElementById(id);
let task = null;
let taskEvents = [];
let showAllEvents = false;
let busy = false;
let connecting = false;
let selectedMode = null;
let allowance = null;
let allowanceCursor = "";
let hosted = false;
let modelRouteConfigured = true;
let authenticated = false;
let sessionKeyAttached = false;
let judgeLoginOffered = false;
let judgeSession = false;
let sessionRevision = 0;
let selectionRevision = 0;
let progressTimer = null;
let pollBusy = false;
const defaultPrompt = element("prompt").value;
if (/^[a-f0-9]{32}$/.test(location.hash.slice(1))) element("taskId").value = location.hash.slice(1);
const eventNames = {
  submitted:"Task submitted", started:"Actor started", inference_requested:"Actor turn requested",
  tool_started:"Tool started", tool_completed:"Tool completed", transport_failed:"Transport interrupted",
  recovered:"Continued without replaying completed edits", approval_requested:"Change ready for review",
  apply_started:"Applying approved change", applied:"Approved change applied", model_summary:"Actor update",
  inference_receipt:"Provider usage receipt", cancellation_requested:"Cancellation requested",
  cancelled:"Task cancelled; earlier effects preserved", sample_executed:"Sample feature exercised",
  revalidation_started:"Fresh validation started", reconciled:"Interrupted effects reconciled"
};
async function api(path, method = "GET", body) {
  const headers = {};
  if (hosted) { if (method !== "GET") headers["X-Operator-Request"] = "1"; }
  else headers["Authorization"] = "Bearer " + element("token").value;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {method, headers, credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body)});
  const raw = await response.text();
  let data;
  try { data = JSON.parse(raw); }
  catch { throw new Error("Server response (HTTP " + response.status + "): " + raw.slice(0, 300)); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function message(text) { element("message").textContent = text; }
async function guarded(action) {
  try { await action(); } catch (error) { message(error.message); }
}
function stopPolling() {
  if (progressTimer !== null) clearInterval(progressTimer);
  progressTimer = null;
}
function startPolling() {
  if (progressTimer !== null || !selectedMode) return;
  progressTimer = setInterval(async () => {
    if (pollBusy) return;
    pollBusy = true;
    try { await refresh(); await refreshExternalActions(); }
    catch (error) { stopPolling(); message(error.message); }
    finally { pollBusy = false; }
  }, 1000);
}
function stillSelected(id, selection, session) {
  return selectedMode !== null && session === sessionRevision && selection === selectionRevision && element("taskId").value.trim() === id;
}
function inferenceAllowed() {
  return selectedMode !== null && (!selectedMode.live_inference || (allowance && allowance.enabled && !allowance.expired &&
    Number(allowance.remaining_usd_estimate) > 0 && allowance.requests_used < allowance.requests_limit));
}
async function refreshAllowance() {
  const session = sessionRevision;
  let snapshot;
  try { snapshot = await api("/api/allowance"); }
  catch (error) {
    if (session === sessionRevision) {
      allowance = null;
      element("allowance-summary").textContent = "Spending status unavailable. Refresh before starting model work.";
      actions();
    }
    throw error;
  }
  if (session !== sessionRevision || selectedMode === null) return;
  allowance = snapshot;
  if (!snapshot.enabled) {
    element("allowance-summary").textContent = "Offline reference: no model requests or spending.";
    element("allowance-note").textContent = "This mode uses a fixed reference implementation.";
  } else {
    const money = value => "$" + Number(value).toFixed(4);
    element("allowance-summary").textContent = money(snapshot.remaining_usd_estimate) + " remaining of " +
      money(snapshot.limit_usd) + "; " + money(snapshot.reserved_usd_estimate) + " reserved. Requests: " +
      snapshot.requests_used + "/" + snapshot.requests_limit + ".";
    element("allowance-note").textContent = (snapshot.expired ? "Allowance expired. " : "") +
      "This server's local estimates include failed calls; they are not provider credit balance or final billing. " +
      "Expires " + new Date(snapshot.expires_at * 1000).toLocaleString() + ".";
  }
  actions();
}
function actions() {
  const active = selectedMode !== null && !connecting && task && task.id === element("taskId").value.trim();
  element("connect").disabled = connecting || hosted;
  element("enterSession").disabled = connecting || !hosted;
  element("judgeSignup").disabled = connecting || busy;
  element("judgeLogin").disabled = connecting || busy;
  element("grantSave").disabled = connecting || busy || !judgeSession;
  element("attachKey").disabled = busy || connecting || !authenticated || sessionKeyAttached;
  element("detachKey").disabled = busy || connecting || !sessionKeyAttached;
  element("newTask").disabled = busy || connecting || selectedMode === null;
  element("submit").disabled = busy || connecting || !inferenceAllowed();
  element("refreshAllowance").disabled = selectedMode === null || connecting;
  element("refreshExternal").disabled = selectedMode === null || connecting;
  for (const id of ["recentTasks", "refreshTasks", "load", "taskId"]) element(id).disabled = busy || connecting || selectedMode === null;
  element("approve").disabled = !active || busy || task.status !== "awaiting_approval";
  element("reject").disabled = !active || busy || !["queued", "paused", "awaiting_approval", "review_required"].includes(task.status);
  element("resume").disabled = !active || busy || task.status !== "paused" || !inferenceAllowed();
  const recoverable = active && !busy && ["running", "reconciliation_required", "applying"].includes(task.status);
  element("reconcile").disabled = !recoverable;
  element("recovery-note").hidden = !recoverable;
  element("recovery-note").textContent = recoverable && task.status === "running" ?
    "If progress stopped after a disconnect, inspect the saved state. Active execution refuses this check without interruption." :
    "An action may have been interrupted. Inspect its saved result before resuming. This does not repeat the action or approve a change.";
  element("revalidate").disabled = !active || busy || !["awaiting_approval", "review_required", "paused"].includes(task.status);
  element("preview").disabled = !active || busy || task.status !== "completed";
  element("cancel").disabled = !active || ["completed", "applying", "rejected", "cancelled"].includes(task.status);
  if (typeof supervisorActions === "function") supervisorActions();
}
async function recentTasks() {
  const session = sessionRevision;
  const summaries = await api("/api/tasks?limit=20");
  if (session !== sessionRevision || selectedMode === null) return [];
  const chooser = element("recentTasks");
  const placeholder = document.createElement("option");
  placeholder.value = ""; placeholder.textContent = summaries.length ? "New task (no saved task selected)" : "No saved tasks yet";
  chooser.replaceChildren(placeholder, ...summaries.map((item) => {
    const option = document.createElement("option");
    option.value = item.id; option.textContent = item.status + " · " + item.prompt;
    return option;
  }));
  chooser.value = element("taskId").value.trim();
  return summaries;
}
async function refreshExternalActions() {
  const session = sessionRevision;
  if (selectedMode === null) return;
  const actions = await api("/api/external-actions?limit=20");
  if (session !== sessionRevision || selectedMode === null) return;
  const list = element("externalActions");
  if (!actions.length) {
    const empty = document.createElement("li");
    empty.textContent = "No pending external actions.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...actions.map((action) => {
    const item = document.createElement("li");
    item.className = "external-action";
    const summary = document.createElement("div");
    summary.textContent = action.status + " · " + action.description.replace(/\n/g, " · ");
    item.append(summary);
    if (action.status === "submitted") {
      const approve = document.createElement("button");
      approve.textContent = "Approve"; approve.className = "secondary";
      approve.onclick = () => guarded(async () => {
        await api("/api/external-actions/" + action.id + "/approve", "POST", {nonce: action.nonce});
        await refreshExternalActions(); message("External action approved.");
      });
      const reject = document.createElement("button");
      reject.textContent = "Reject"; reject.className = "secondary";
      reject.onclick = () => guarded(async () => {
        await api("/api/external-actions/" + action.id + "/reject", "POST");
        await refreshExternalActions(); message("External action rejected.");
      });
      item.append(approve, reject);
    }
    return item;
  }));
}
element("refreshExternal").onclick = () => guarded(refreshExternalActions);
let supervisorThreadId = "";
let supervisorBusy = false;
let supervisorPlanNonce = "";
function supervisorNote(text) { element("supervisorNote").textContent = text; }
function supervisorActions() {
  const ready = selectedMode !== null && !connecting && !supervisorBusy;
  for (const id of ["supervisorThreads", "refreshSupervisor", "supervisorNewTopic", "supervisorOpen"]) element(id).disabled = !ready;
  const open = ready && !!supervisorThreadId;
  element("supervisorInput").disabled = !open;
  element("supervisorSend").disabled = !open;
  element("supervisorWritePlan").disabled = !open;
  element("supervisorApprovePlan").disabled = !open || !supervisorPlanNonce;
}
function renderSupervisorTranscript(history) {
  element("supervisorTranscript").replaceChildren(...history.map((turn) => {
    const item = document.createElement("li");
    item.textContent = turn.role + ": " + turn.text;
    return item;
  }));
}
async function loadSupervisorThreads() {
  const session = sessionRevision;
  const threads = await api("/api/supervisor/threads");
  if (session !== sessionRevision || selectedMode === null) return;
  const chooser = element("supervisorThreads");
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = threads.length ? "Choose a thread" : "No threads yet";
  chooser.replaceChildren(placeholder, ...threads.map((item) => {
    const option = document.createElement("option");
    option.value = item.id; option.textContent = item.status + " · " + item.topic;
    return option;
  }));
  chooser.value = supervisorThreadId;
}
async function openSupervisorThread(id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Choose a thread first.");
  const detail = await api("/api/supervisor/threads/" + encodeURIComponent(id));
  supervisorThreadId = id;
  const approval = detail.thread.status === "planned" ? detail.thread.approval : null;
  supervisorPlanNonce = approval && approval.nonce ? approval.nonce : "";
  element("supervisorThreads").value = id;
  renderSupervisorTranscript(detail.thread.history || []);
  element("supervisorPlan").textContent = detail.thread.plan ? detail.thread.plan.markdown : "No plan written.";
  supervisorNote("Thread open. Chat to refine requirements, then write the plan.");
  supervisorActions();
}
async function loadSupervisorRuns() {
  const session = sessionRevision;
  const runs = await api("/api/supervisor/runs");
  if (session !== sessionRevision || selectedMode === null) return;
  const list = element("supervisorRuns");
  if (!runs.length) {
    const empty = document.createElement("li");
    empty.textContent = "No runs yet.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...runs.map((run) => {
    const item = document.createElement("li");
    item.textContent = run.status + " · " + run.run_id.slice(0, 12);
    return item;
  }));
}
async function supervisorStep(work) {
  if (supervisorBusy) return;
  supervisorBusy = true; supervisorActions();
  try { await work(); }
  finally { supervisorBusy = false; supervisorActions(); }
}
element("refreshSupervisor").onclick = () => guarded(() => supervisorStep(async () => {
  await loadSupervisorThreads(); await loadSupervisorRuns();
}));
element("supervisorThreads").onchange = () => guarded(() => supervisorStep(async () => {
  if (element("supervisorThreads").value) await openSupervisorThread(element("supervisorThreads").value);
}));
element("supervisorOpen").onclick = () => guarded(() => supervisorStep(async () => {
  const topic = element("supervisorNewTopic").value.trim();
  if (!topic) { supervisorNote("Enter a topic first."); return; }
  const opened = await api("/api/supervisor/threads", "POST", {topic});
  element("supervisorNewTopic").value = "";
  await loadSupervisorThreads();
  await openSupervisorThread(opened.id);
}));
element("supervisorSend").onclick = () => guarded(() => supervisorStep(async () => {
  if (!supervisorThreadId) return;
  const text = element("supervisorInput").value.trim();
  if (!text) return;
  await api("/api/supervisor/threads/" + supervisorThreadId + "/messages", "POST", {text});
  element("supervisorInput").value = "";
  await openSupervisorThread(supervisorThreadId);
}));
element("supervisorWritePlan").onclick = () => guarded(() => supervisorStep(async () => {
  if (!supervisorThreadId) return;
  const plan = await api("/api/supervisor/threads/" + supervisorThreadId + "/plan", "POST");
  supervisorPlanNonce = plan.nonce;
  element("supervisorPlan").textContent = plan.markdown;
  supervisorNote("Plan written. Review it above, then approve to queue a run.");
  supervisorActions();
}));
element("supervisorApprovePlan").onclick = () => guarded(() => supervisorStep(async () => {
  if (!supervisorThreadId || !supervisorPlanNonce) return;
  const queued = await api("/api/supervisor/threads/" + supervisorThreadId + "/approve", "POST", {nonce: supervisorPlanNonce});
  supervisorPlanNonce = "";
  await loadSupervisorRuns();
  await openSupervisorThread(supervisorThreadId);
  supervisorNote("Plan approved. Run queued: " + queued.run_id.slice(0, 12) + ".");
}));
function renderEvents() {
  const start = showAllEvents ? 0 : Math.max(0, taskEvents.length - 20);
  element("events").replaceChildren(...taskEvents.slice(start).map((event, offset) => {
    const item = document.createElement("li");
    item.textContent = (start + offset + 1) + " · " + (eventNames[event.kind] || event.kind) + (event.payload.injected ? " (injected)" : "") +
      (event.payload.name ? " · " + event.payload.name : "") +
      (event.kind === "transport_failed" && typeof event.payload.detail === "string" ? " · " + event.payload.detail : "");
    return item;
  }));
  element("event-summary").textContent = !task ? "Create a new task or select a saved task." :
    taskEvents.length ? (start ? "Latest 20 of " : "Showing ") + taskEvents.length + " activity entries for this task." : "No activity for this task yet.";
  element("eventHistory").hidden = taskEvents.length <= 20;
  element("eventHistory").textContent = showAllEvents ? "Show latest activity" : "Show all activity (" + taskEvents.length + ")";
}
function clearTaskView() {
  task = null; taskEvents = []; showAllEvents = false;
  element("status").textContent = "No task selected";
  element("diff").textContent = "No candidate yet.";
  element("evidence").textContent = "Not validated.";
  element("validation-summary").textContent = "Not validated.";
  element("issues").replaceChildren(); element("import-errors").replaceChildren();
  renderEvents(); actions();
}
function newTask() {
  if (busy || connecting || selectedMode === null) return;
  stopPolling(); selectionRevision++;
  element("taskId").value = ""; element("recentTasks").value = "";
  history.replaceState(null, "", location.pathname + location.search);
  element("prompt").value = defaultPrompt;
  clearTaskView();
  message("New task draft. Review the request, then choose Create and run new task. Saved work is unchanged.");
  element("prompt").focus();
}
element("newTask").onclick = newTask;
element("eventHistory").onclick = () => { showAllEvents = !showAllEvents; renderEvents(); };
async function chooseTask(id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Choose a saved task or provide a valid task ID.");
  stopPolling(); selectionRevision++; task = null;
  element("taskId").value = id;
  element("issues").replaceChildren(); element("import-errors").replaceChildren();
  clearTaskView();
  await refresh();
  if (task && typeof task.prompt === "string") element("prompt").value = task.prompt;
}
async function refresh() {
  const id = element("taskId").value.trim();
  if (!id || selectedMode === null) return;
  const session = sessionRevision, selection = selectionRevision;
  const review = await api("/api/tasks/" + encodeURIComponent(id));
  if (!stillSelected(id, selection, session)) return;
  const previousStatus = task && task.id === id ? task.status : null;
  task = review.task;
  if (location.hash.slice(1) !== task.id) location.hash = task.id;
  element("recentTasks").value = task.id;
  element("status").textContent = task.status;
  element("diff").textContent = review.diff || "No candidate change.";
  element("evidence").textContent = task.evidence ? JSON.stringify(task.evidence, null, 2) : "Not validated.";
  element("validation-summary").textContent = task.evidence ?
    (task.evidence.passed ? "Passed" : "Failed") + " · " + task.evidence.runner + " · source " + task.evidence.candidate_digest.slice(0,12) : "Not validated.";
  actions();
  const events = await api("/api/tasks/" + encodeURIComponent(id) + "/events");
  if (!stillSelected(id, selection, session)) return;
  taskEvents = events; renderEvents();
  element("events").scrollTop = element("events").scrollHeight;
  if (["running", "applying"].includes(task.status)) startPolling();
  else stopPolling();
  const status = task.status;
  if (previousStatus !== status && !["running", "applying"].includes(status)) await recentTasks();
  if (status === "awaiting_approval" && previousStatus !== status)
    message("Change ready for review: check the diff below, then press Approve this verified change.");
  const receipts = events.filter(event => event.kind === "inference_receipt");
  const cursor = id + ":" + (receipts.length ? receipts[receipts.length - 1].sequence : 0) + ":" + status;
  if (stillSelected(id, selection, session) && cursor !== allowanceCursor) {
    allowanceCursor = cursor;
    await refreshAllowance();
  }
}
async function taskAction(suffix, body, success) {
  if (busy || !task || task.id !== element("taskId").value.trim()) return;
  const id = task.id, session = sessionRevision, selection = selectionRevision;
  busy = true; actions();
  try {
    const result = await api("/api/tasks/" + id + suffix, "POST", body);
    if (!stillSelected(id, selection, session)) return null;
    await refresh(); message(success);
    return result;
  } catch (error) {
    if (stillSelected(id, selection, session)) {
      try { await refresh(); } catch { /* Keep the original action failure visible. */ }
    }
    throw error;
  } finally { busy = false; actions(); }
}
element("submit").onclick = () => guarded(async () => {
  if (busy || !inferenceAllowed()) return;
  busy = true; actions();
  const session = sessionRevision;
  try {
    const configuration = await api("/api/config");
    if (session !== sessionRevision) return;
    if (JSON.stringify(configuration) !== JSON.stringify(selectedMode)) {
      selectedMode = configuration; displayMode(configuration);
      throw new Error("Execution mode changed. Review the displayed mode before submitting again.");
    }
    const created = await api("/api/tasks", "POST", {prompt: element("prompt").value});
    if (session !== sessionRevision) return;
    await chooseTask(created.id); await recentTasks();
    message(configuration.live_inference ? "Running the configured Meta actor within its allowance." : "Running a fixed offline reference; no model request is being made.");
    startPolling();
    try { await api("/api/tasks/" + created.id + "/run", "POST", {inject_failure: element("inject").checked}); }
    finally { if (session === sessionRevision) await refresh(); }
    if (session === sessionRevision) message("Review the task status, candidate and Docker evidence.");
  } finally { busy = false; actions(); }
});
element("recentTasks").onchange = () => guarded(async () => {
  if (element("recentTasks").value) await chooseTask(element("recentTasks").value);
  else newTask();
});
element("refreshTasks").onclick = () => guarded(async () => {
  await recentTasks();
});
element("refreshAllowance").onclick = () => guarded(refreshAllowance);
element("reconcile").onclick = () => guarded(() => taskAction("/reconcile", {}, "Saved result inspected. Review the new status before resuming."));
element("load").onclick = () => guarded(() => chooseTask(element("taskId").value.trim()));
element("approve").onclick = () => guarded(async () => {
  if (!task) return;
  if (!task.approval) { message("No approval nonce for this task. Run fresh validation first."); return; }
  await taskAction("/approve", {nonce: task.approval.nonce}, "Verified change applied to the local demo target.");
});
element("reject").onclick = () => guarded(() => taskAction("/reject", {}, "Change rejected; target not updated."));
element("revalidate").onclick = () => guarded(() => taskAction("/revalidate", {}, "Fresh validation completed; previous approval nonce is invalid."));
element("cancel").onclick = () => guarded(async () => {
  const id = element("taskId").value.trim(), session = sessionRevision, selection = selectionRevision;
  await api("/api/tasks/" + encodeURIComponent(id) + "/cancel", "POST", {});
  if (!stillSelected(id, selection, session)) return;
  await refresh(); message("Cancellation requested. A started atomic application cannot be revoked.");
});
element("resume").onclick = () => guarded(async () => {
  if (busy || !task || !inferenceAllowed()) return;
  const id = task.id, session = sessionRevision, selection = selectionRevision;
  busy = true; actions(); startPolling();
  try { await api("/api/tasks/" + id + "/run", "POST", {inject_failure: false}); }
  finally {
    busy = false;
    if (stillSelected(id, selection, session)) await refresh();
    actions();
  }
});
element("preview").onclick = () => guarded(async () => {
  const result = await taskAction("/sample", {csv_text: element("csv").value}, "Applied importer executed.");
  if (!result) return;
  element("issues").replaceChildren(...result.issues.map((issue) => {
    const row = document.createElement("tr");
    for (const key of ["id", "title", "status"]) {
      const cell = document.createElement("td"); cell.textContent = issue[key]; row.append(cell);
    }
    return row;
  }));
  element("import-errors").replaceChildren(...result.errors.map((error) => {
    const item = document.createElement("li"); item.textContent = "Row " + error.row + ": " + error.message; return item;
  }));
  message(result.issues.length + " issues accepted; " + result.errors.length + " row errors. Applied source: " + result.target_digest.slice(0,12));
});
function displayMode(configuration) {
  document.querySelector(".notice").textContent = configuration.live_inference ?
    "META MODE · Real AI coding" : "SCRIPTED OFFLINE REFERENCE · No Meta inference";
  element("access-note").textContent = hosted ?
    "Hosted demo. Your task list, spending and key belong to this private session only." :
    (configuration.https_origin ? "HTTPS origin configured. Owner credential stays in page memory." :
     "Owner credential stays in page memory. Remote access is not configured.");
}
function keyNote() {
  if (!modelRouteConfigured) {
    element("key-note").textContent = "No Meta model route is configured on this host. Every task runs the fixed offline reference.";
    return;
  }
  element("key-note").textContent = sessionKeyAttached ?
    "Your key is attached for this session. Model calls are charged to your own Meta account, not the host's." :
    "No key attached. This demo runs the fixed offline reference and makes no model requests.";
}
async function loadMode(session) {
  const configuration = await api("/api/config");
  if (session !== sessionRevision) return;
  if (configuration.hosted && !configuration.authenticated) {
    selectedMode = null; authenticated = false; sessionKeyAttached = false;
    actions();
    message("Enter the demo access code to open your own private session.");
    return;
  }
  authenticated = true;
  selectedMode = configuration; displayMode(configuration);
  sessionKeyAttached = !!configuration.session_key_attached;
  judgeSession = !!configuration.judge;
  if (hosted) {
    modelRouteConfigured = configuration.model_route_configured !== false;
    element("key-controls").hidden = !modelRouteConfigured;
    element("judge-grant").hidden = !judgeSession;
    keyNote();
    if (judgeSession) await refreshJudgeGrant(session);
  }
  await refreshAllowance();
  if (session !== sessionRevision) return;
  const summaries = await recentTasks();
  if (session !== sessionRevision) return;
  await refreshExternalActions();
  if (session !== sessionRevision) return;
  await loadSupervisorThreads();
  if (session !== sessionRevision) return;
  await loadSupervisorRuns();
  if (session !== sessionRevision) return;
  const saved = element("taskId").value.trim();
  if (saved || summaries.length) {
    await chooseTask(saved || summaries[0].id);
    if (session === sessionRevision) message("Connected. Your saved task is loaded; review its current status below.");
  } else message("Connected. Review execution mode before submitting your task.");
}
element("connect").onclick = () => guarded(async () => {
  stopPolling(); const session = ++sessionRevision;
  selectedMode = null; allowance = null; allowanceCursor = ""; connecting = true; actions();
  try { await loadMode(session); }
  finally {
    if (session === sessionRevision) { connecting = false; actions(); }
  }
});
element("enterSession").onclick = () => guarded(async () => {
  stopPolling(); const session = ++sessionRevision;
  selectedMode = null; allowance = null; allowanceCursor = ""; connecting = true; actions();
  try {
    await api("/api/session", "POST", {code: element("accessCode").value});
    element("accessCode").value = "";
    await loadMode(session);
    if (session === sessionRevision) message("Private session started. Review execution mode before submitting your task.");
  } finally {
    if (session === sessionRevision) { connecting = false; actions(); }
  }
});
element("attachKey").onclick = () => guarded(async () => {
  if (busy || !authenticated) return;
  busy = true; actions();
  try {
    await api("/api/session/key", "POST", {api_key: element("modelKey").value});
    element("modelKey").value = "";
    const session = ++sessionRevision;
    await loadMode(session);
    if (session === sessionRevision) message("Key attached to this session. Real Meta calls are charged to your own account.");
  } finally { busy = false; actions(); }
});
element("detachKey").onclick = () => guarded(async () => {
  if (busy) return;
  busy = true; actions();
  try {
    await api("/api/session/key", "DELETE");
    sessionKeyAttached = false; keyNote();
    const session = ++sessionRevision;
    await loadMode(session);
    if (session === sessionRevision) message("Key removed from the server session. Tasks now use the offline reference.");
  } finally { busy = false; actions(); }
});
element("leaveSession").onclick = () => guarded(async () => {
  await api("/api/session", "DELETE");
  location.reload();
});
async function refreshJudgeGrant(session) {
  const stored = await api("/api/judges/grant");
  if (session !== sessionRevision) return;
  if (stored.grant) {
    element("grantRequests").value = stored.grant.max_requests;
    element("grantUsd").value = stored.grant.approved_usd;
    element("grant-note").textContent = "Limits saved. Attach your key below to run real inference on your account.";
  } else {
    element("grant-note").textContent = "No spending limits yet. Save limits before attaching a key.";
  }
}
element("judgeSignup").onclick = () => guarded(async () => {
  if (busy) return;
  busy = true; actions();
  try {
    await api("/api/judges/signup", "POST", {code: element("accessCode").value,
      username: element("judgeName").value.trim(), password: element("judgePassword").value});
    element("judgePassword").value = "";
    message("Judge account registered. Log in to open your private workspace.");
  } finally { busy = false; actions(); }
});
element("judgeLogin").onclick = () => guarded(async () => {
  stopPolling(); const session = ++sessionRevision;
  selectedMode = null; allowance = null; allowanceCursor = ""; connecting = true; actions();
  try {
    await api("/api/judges/login", "POST", {username: element("judgeName").value.trim(),
      password: element("judgePassword").value});
    element("judgePassword").value = "";
    await loadMode(session);
    if (session === sessionRevision) message("Judge session started. Set spending limits, then attach your key.");
  } finally {
    if (session === sessionRevision) { connecting = false; actions(); }
  }
});
element("grantSave").onclick = () => guarded(async () => {
  if (busy || !judgeSession) return;
  busy = true; actions();
  try {
    await api("/api/judges/grant", "PUT", {max_requests: Number(element("grantRequests").value),
      approved_usd: element("grantUsd").value.trim()});
    const session = ++sessionRevision;
    await loadMode(session);
    if (session === sessionRevision) message("Spending limits saved. Attach your key to run real inference.");
  } finally { busy = false; actions(); }
});
element("token").addEventListener("input", () => {
  sessionRevision++; selectionRevision++; stopPolling(); selectedMode = null; task = null; connecting = false;
  allowance = null; allowanceCursor = "";
  element("allowance-summary").textContent = "Connect to view this server's allowance.";
  element("allowance-note").textContent = "Local reserved estimates are separate from provider credits and final billing.";
  element("recentTasks").replaceChildren(); clearTaskView();
  document.querySelector(".notice").textContent = "Load execution mode before submitting";
});
element("taskId").addEventListener("input", () => {
  selectionRevision++; stopPolling(); clearTaskView();
});
(async function detect() {
  try {
    const configuration = await api("/api/config");
    if (!configuration.hosted) return;
    hosted = true;
    modelRouteConfigured = configuration.model_route_configured !== false;
    judgeLoginOffered = configuration.judge_login === true;
    element("owner-access").hidden = true;
    element("visitor-access").hidden = false;
    element("judge-access").hidden = !judgeLoginOffered;
    element("key-section").hidden = false;
    element("key-controls").hidden = !modelRouteConfigured;
    element("access-note").textContent = "Private demo: enter the access code to open your own session.";
    keyNote();
    if (configuration.authenticated) {
      const session = ++sessionRevision;
      connecting = true; actions();
      try { await loadMode(session); }
      finally { connecting = false; actions(); }
    } else {
      document.querySelector(".notice").textContent = "Enter the demo access code to start your private session";
      actions();
    }
  } catch { /* Local deployment: wait for the owner token. */ }
})();
actions();
