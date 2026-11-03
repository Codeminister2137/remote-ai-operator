"use strict";
const element = (id) => document.getElementById(id);
let task = null;
let taskSource = ""; // Where the selected task came from (link, list, inbox, ...).
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
let grantSaved = false;
let scriptedChoice = false;
let sessionRevision = 0;
let selectionRevision = 0;
let progressTimer = null;
let pollBusy = false;
let hiddenPollTicks = 0;
let triedApproved = false;
let currentView = "demo";
let reviewContext = null; // {run_id, thread_id, thread_topic} when opened from a run.
const WORKBENCH_ACTIONS_VISIBLE = false; // Hackathon: hide Workbench section. Post-hackathon: set true to restore.
const WINDOW_STALE_MINUTES = 30; // Heartbeats older than this are shown as outdated and never light the pill.
let lastDrain = null; // Latest /api/window drain health (owner serve only).
let lastRunsActive = false; // Any supervised run executing.
let lastAgentsActive = false; // Any reported agent on a task.
function updateWorkerPill() {
  const pill = element("workerPill");
  if (!pill) return;
  if (selectedMode === null) {
    pill.textContent = "○ Offline";
    pill.className = "secondary nav-action pill-off";
    pill.title = "Not connected to the server.";
    return;
  }
  if (lastDrain && lastDrain.alive === false) {
    pill.textContent = "● Worker stalled";
    pill.className = "secondary nav-action pill-alert";
    pill.title = "The worker is not running. Queued work is stalled — see Activity.";
    return;
  }
  const reasons = [];
  if (task && ["running", "applying"].includes(task.status)) reasons.push("selected task " + task.status);
  if (lastRunsActive) reasons.push("supervised run");
  if (lastAgentsActive) reasons.push("reported agent");
  if (reasons.length) {
    pill.textContent = "● Working";
    pill.className = "secondary nav-action pill-work";
    pill.title = "Working: " + reasons.join(", ") + ". See Activity for details.";
    return;
  }
  pill.textContent = "○ Idle";
  pill.className = "secondary nav-action pill-idle";
  pill.title = "Connected. Nothing is running.";
}
function switchView(name) {
  currentView = name;
  for (const view of ["demo", "supervise", "review", "activity"]) {
    element("view-" + view).hidden = view !== name;
  }
  for (const view of ["Demo", "Supervise", "Review", "Activity"]) {
    element("nav" + view).classList.toggle("active", view.toLowerCase() === name);
  }
}
element("navDemo").onclick = () => switchView("demo");
element("navSupervise").onclick = () => switchView("supervise");
element("navReview").onclick = () => switchView("review");
element("navActivity").onclick = () => switchView("activity");
element("workerPill").onclick = () => switchView("activity");
element("demoReviewCta").onclick = () => switchView("review");
switchView("demo");
const defaultPrompt = element("prompt").value;
if (/^[a-f0-9]{32}$/.test(location.hash.slice(1))) element("taskId").value = location.hash.slice(1);
const eventNames = {
  submitted:"Task submitted", started:"Actor started", inference_requested:"Actor turn requested",
  tool_started:"Tool started", tool_completed:"Tool completed", transport_failed:"Transport interrupted",
  recovered:"Continued without replaying completed edits", approval_requested:"AI change ready for your review",
  apply_started:"Applying approved change", applied:"Approved by you and applied", model_summary:"Actor update",
  inference_receipt:"Provider usage receipt", cancellation_requested:"Cancellation requested",
  cancelled:"Cancelled by you; earlier effects preserved", rejected:"Rejected by you", sample_executed:"Sample feature exercised",
  revalidation_started:"Fresh validation started", reconciled:"Interrupted effects reconciled"
};
function humanizeStatus(raw) {
  const words = String(raw || "").replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Unknown";
}
function parseChecksReport(output) {
  const match = /OPERATOR_CHECKS (\{[^}]*\})/.exec(String(output || ""));
  if (!match) return null;
  try {
    const report = JSON.parse(match[1]);
    return typeof report.tests === "number" ? report : null;
  } catch { return null; }
}
function parseChecksDuration(output) {
  const match = /Ran \d+ tests? in ([\d.]+)s/.exec(String(output || ""));
  return match ? match[1] + "s" : null;
}
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
  catch { throw new Error("The server returned a response this page could not read (HTTP " + response.status + "). Check your connection and try again. Details: " + raw.slice(0, 300)); }
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail));
  return data;
}
function message(text) { element("message").textContent = text; }
function accessStatus(text) { element("accessStatus").textContent = text; }
async function guarded(action) {
  try { await action(); } catch (error) { message(error.message); }
}
function stopPolling() {
  if (progressTimer !== null) clearInterval(progressTimer);
  progressTimer = null;
}
let windowTimer = null;
function stopWindowPolling() {
  if (windowTimer !== null) clearInterval(windowTimer);
  windowTimer = null;
}
function startWindowPolling() {
  if (windowTimer !== null || !selectedMode) return;
  windowTimer = setInterval(() => {
    if (selectedMode === null) { stopWindowPolling(); return; }
    refreshWindowStatus().catch(() => {});
  }, 10000);
}
function startPolling() {
  if (progressTimer !== null || !selectedMode) return;
  progressTimer = setInterval(async () => {
    if (pollBusy) return;
    if (typeof document !== "undefined" && document.hidden) {
      hiddenPollTicks++;
      if (hiddenPollTicks % 15 !== 0) return; // Hidden tab: poll about every 15s.
    } else hiddenPollTicks = 0;
    pollBusy = true;
    try { await refresh(); await refreshExternalActions(); try { await loadSupervisorRuns(); } catch { /* Runs refresh is best-effort inside the task poll. */ } try { await pollInbox(); } catch { /* Inbox refresh is best-effort inside the task poll. */ } }
    catch (error) { stopPolling(); message(error.message); }
    finally { pollBusy = false; }
  }, 1000);
}
function stillSelected(id, selection, session) {
  return selectedMode !== null && session === sessionRevision && selection === selectionRevision && element("taskId").value.trim() === id;
}
function allowanceBlockReason() {
  if (selectedMode === null) return "Create is unavailable: connect first.";
  if (!allowance || !allowance.enabled) return "Create is unavailable: spending allowance not loaded.";
  if (allowance.expired) return "Create is unavailable: spending allowance expired.";
  if (allowance.requests_used >= allowance.requests_limit) return "Create is unavailable: request limit reached.";
  return "Create is unavailable: spending limit reached. See Activity for details.";
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
    if (snapshot.operations && snapshot.operations.chat && snapshot.operations.audio) {
      const chat = snapshot.operations.chat, audio = snapshot.operations.audio;
      element("allowance-summary").textContent += " Chat: " + money(chat.reserved_usd_estimate) +
        " reserved (" + chat.requests_used + " req). Audio: " + money(audio.reserved_usd_estimate) +
        " reserved (" + audio.requests_used + " req).";
    }
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
  element("sampleReview").disabled = busy || connecting || !inferenceAllowed();
  const submitBlocked = !busy && !connecting && !inferenceAllowed();
  element("submit").title = submitBlocked ? allowanceBlockReason() : "Create a task from the request above and run it.";
  element("sampleReview").title = submitBlocked ? allowanceBlockReason() : "Load a ready-made scripted review. Reuses one of your validated tasks, or creates and runs one for you.";
  element("refreshAllowance").disabled = selectedMode === null || connecting;
  element("refreshExternal").disabled = selectedMode === null || connecting;
  for (const id of ["runMode", "supervisionMode", "powerDelay", "powerArm"]) element(id).disabled = selectedMode === null || connecting;
  for (const id of ["recentTasks", "refreshTasks", "load", "taskId"]) element(id).disabled = busy || connecting || selectedMode === null;
  element("approve").disabled = !active || busy || task.status !== "awaiting_approval" ||
    !(task.evidence && task.evidence.passed);
  element("reject").disabled = !active || busy || !["queued", "paused", "awaiting_approval", "review_required"].includes(task.status);
  element("resume").disabled = !active || busy || task.status !== "paused" || !inferenceAllowed();
  const recoverable = active && !busy && ["running", "reconciliation_required", "applying"].includes(task.status);
  element("reconcile").disabled = !recoverable;
  element("recovery-note").hidden = !recoverable;
  element("recovery-note").textContent = recoverable && task.status === "running" ?
    "If progress stopped after a disconnect, use Inspect interrupted action below. If a run is still active, wait for it to finish first." :
    "An action may have been interrupted. Inspect its saved result before resuming. This does not repeat the action or approve a change.";
  element("revalidate").disabled = !active || busy || !["awaiting_approval", "review_required", "paused"].includes(task.status);
  element("preview").disabled = !active || busy || task.status !== "completed";
  element("tryApproved").disabled = !active || busy || task.status !== "completed";
  const hasId = selectedMode !== null && !busy && !connecting && !!element("taskId").value.trim();
  element("copyTaskId").disabled = !hasId;
  element("copyTaskLink").disabled = !hasId;
  element("cancel").disabled = !active || ["completed", "applying", "rejected", "cancelled"].includes(task.status);
  const parked = active && task.status === "waiting_on_owner";
  element("question-block").hidden = !parked;
  if (parked) element("pending-question").textContent = task.pending_question || "(no question recorded)";
  element("send-answer").disabled = !parked || busy;
  if (typeof supervisorActions === "function") supervisorActions();
  renderWizard();
  if (typeof updateStepper === "function") updateStepper();
  updateWorkerPill();
}
async function recentTasks() {
  const session = sessionRevision;
  const summaries = await api("/api/tasks?limit=20");
  if (session !== sessionRevision || selectedMode === null) return [];
  const chooser = element("recentTasks");
  const tasksOnly = summaries.filter((item) => !item.thread);
  const placeholder = document.createElement("option");
  placeholder.value = ""; placeholder.textContent = tasksOnly.length ? "New task (no saved task selected)" : "No saved tasks yet";
  chooser.replaceChildren(placeholder, ...tasksOnly.map((item) => {
    const option = document.createElement("option");
    option.value = item.id; option.textContent = humanizeStatus(item.status) + " · " + item.prompt;
    option.title = "Raw status: " + item.status;
    return option;
  }));
  chooser.value = element("taskId").value.trim();
  return tasksOnly;
}
async function loadSettings() {
  const session = sessionRevision;
  if (hosted) {
    element("supervision-row").hidden = true;
    element("power-block").hidden = true;
    element("settingsNote").textContent = "Run mode applies to your plans. Machine settings are owner-only.";
    return;
  }
  const settings = await api("/api/settings");
  if (session !== sessionRevision || selectedMode === null) return;
  element("supervisionMode").value = settings.supervision;
  element("mailState").textContent = settings.mail_configured ?
    "Mail: configured. Unattended mode sends run updates to the owner address." :
    "Mail: NOT configured. Unattended mode cannot send updates until SMTP is set up.";
  element("approvalWindow").textContent = "Review windows: tasks ~" + settings.task_approval_minutes + " min, plans ~" + settings.approval_window_minutes + " min. Expired approvals need a fresh validation or plan.";
  element("settingsNote").textContent = "";
}
async function refreshPowerStatus() {
  if (hosted || selectedMode === null) return;
  const session = sessionRevision;
  const status = await api("/api/power/sleep");
  if (session !== sessionRevision || selectedMode === null) return;
  element("powerStatus").textContent = status.status === "sleep_scheduled" ?
    "Sleep scheduled. Cancel here to stop it." : "No sleep scheduled.";
  element("powerCancel").disabled = status.status !== "sleep_scheduled";
}
async function refreshWindowStatus() {
  if (hosted || selectedMode === null) return;
  const session = sessionRevision;
  const status = await api("/api/window");
  if (session !== sessionRevision || selectedMode === null) return;
  const beat = status.last_heartbeat_utc ? Date.parse(status.last_heartbeat_utc) : NaN;
  const ageMin = Number.isNaN(beat) ? NaN : Math.max(0, Math.round((Date.now() - beat) / 60000));
  const heartbeatFresh = !Number.isNaN(ageMin) && ageMin <= WINDOW_STALE_MINUTES;
  if (!status.running) {
    element("windowStatus").textContent = "Window: no agent activity reported.";
    element("windowStatus").title = "";
  } else {
    let line = "Window: " + (status.slice || "unscheduled slice");
    if (status.phase) line += " · " + humanizeStatus(status.phase);
    if (!Number.isNaN(ageMin)) {
      const age = ageMin < 60 ? ageMin + "m ago" : Math.round(ageMin / 60) + "h ago";
      line += " (updated " + age + (!heartbeatFresh ? " — may be outdated" : "") + ")";
    } else if (status.last_heartbeat_utc) line += " (last heartbeat " + status.last_heartbeat_utc + ")";
    else line += " (no recent update)";
    if (status.note) line += " " + status.note;
    element("windowStatus").textContent = line;
    element("windowStatus").title = status.phase ? "Raw phase: " + status.phase : "";
  }
  const agents = Array.isArray(status.agents) ? status.agents : [];
  element("agentsStatus").textContent = agents.length ?
    "Agents: " + agents.map((a) => a.name + " (" + (a.task || "idle") + ", " + humanizeStatus(a.status || "") + ")").join("; ") + "." :
    "Agents: none active.";
  element("agentsStatus").title = agents.length ?
    "Raw: " + agents.map((a) => a.name + "=" + (a.status || "?")).join("; ") : "";
  lastAgentsActive = heartbeatFresh && agents.some((a) => (a.task || "idle") !== "idle");
  const health = status.drain;
  lastDrain = health || null;
  if (!health || !health.alive) {
    element("drainStatus").textContent = "Worker: NOT draining. Queued runs are stalled; restart serve on the PC.";
    updateWorkerPill();
    return;
  }
  const ago = health.last_pass === null ? "never" : Math.max(0, Math.round(Date.now() / 1000 - health.last_pass)) + "s ago";
  element("drainStatus").textContent = "Worker: draining, last pass " + ago +
    " (passes " + health.passes + ", errors " + health.errors + ", restarts " + health.restarts + ", serve PID " + status.serve_pid + ").";
  updateWorkerPill();
}
async function refreshExternalActions() {
  const session = sessionRevision;
  if (selectedMode === null) return;
  const actions = await api("/api/external-actions?limit=20");
  if (session !== sessionRevision || selectedMode === null) return;
  const list = element("externalActions");
  if (!actions.length) {
    const empty = document.createElement("li");
    empty.textContent = "No pending Workbench actions. Approvals requested from AI Workbench will appear here.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...actions.map((action) => {
    const item = document.createElement("li");
    item.className = "external-action";
    const summary = document.createElement("div");
    summary.textContent = humanizeStatus(action.status) + " · " + action.description.replace(/\n/g, " · ");
    summary.title = "Raw status: " + action.status;
    item.append(summary);
    if (action.status === "submitted") {
      const approve = document.createElement("button");
      approve.textContent = "Approve"; approve.className = "secondary";
      approve.title = "Approve this pending external action.";
      approve.onclick = () => guarded(async () => {
        await api("/api/external-actions/" + action.id + "/approve", "POST", {nonce: action.nonce});
        await refreshExternalActions(); message("External action approved.");
      });
      const reject = document.createElement("button");
      reject.textContent = "Reject"; reject.className = "secondary";
      reject.title = "Reject this pending external action.";
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
let diffMode = "split"; // Declared before first use: applyDiffMode() runs at load below.
element("diffModeSplit").onclick = () => { diffMode = "split"; applyDiffMode(); };
element("diffModeUnified").onclick = () => { diffMode = "unified"; applyDiffMode(); };
applyDiffMode();
element("supervisionMode").onchange = () => guarded(async () => {
  const saved = await api("/api/settings", "PUT", {supervision: element("supervisionMode").value});
  element("settingsNote").textContent = saved.supervision === "unattended" ?
    (saved.mail_configured ? "Unattended: run updates will be mailed to the owner." : "Unattended selected but mail is NOT configured: no updates will arrive.") :
    (saved.mail_configured ? "Attended: mail only when a run needs you (waiting, failed, done after your answers)." : "Attended selected but mail is NOT configured: no updates will arrive.");
  updatePlanSettingsNote();
});
element("runMode").onchange = () => updatePlanSettingsNote();
let powerArmed = false;
element("powerArm").onclick = () => guarded(async () => {
  if (!powerArmed) {
    powerArmed = true;
    element("powerArm").textContent = "Confirm sleep";
    element("powerStatus").textContent = "Press Confirm sleep to schedule, or Cancel.";
    return;
  }
  powerArmed = false;
  element("powerArm").textContent = "Sleep this PC…";
  await api("/api/power/sleep", "POST", {delay_seconds: Number(element("powerDelay").value)});
  await refreshPowerStatus();
  message("Sleep scheduled. Cancel here to stop it.");
});
element("powerCancel").onclick = () => guarded(async () => {
  powerArmed = false;
  element("powerArm").textContent = "Sleep this PC…";
  await api("/api/power/sleep/cancel", "POST");
  await refreshPowerStatus();
  message("Scheduled sleep cancelled.");
});
element("send-answer").onclick = () => guarded(async () => {
  const text = element("answer-text").value;
  const language = dictateLangTag(element("dictate-lang-answer").value);
  const out = await api("/api/tasks/" + element("taskId").value.trim() + "/answer", "POST", {text, language});
  element("answer-text").value = "";
  await refresh();
  message("Answer recorded. " + (out.resumed_runs.length ? out.resumed_runs.length + " run(s) resuming." : "Task paused; run it to resume."));
});
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
  updatePlanSettingsNote();
}
function updatePlanSettingsNote() {
  const mode = element("runMode").value === "planning" ?
    "Planning only: approving queues nothing." :
    "Plan + implement: approving queues a run.";
  const supervision = element("supervisionMode").value === "unattended" ?
    "Unattended supervision (mail on run events)." :
    "Attended supervision (mail only when a run needs you).";
  const hostedNote = supervisorPlanNonce ? "Approving now: " + mode : "Approval settings will appear here once a plan is written.";
  element("planSettingsNote").textContent = hosted ? hostedNote :
    (supervisorPlanNonce ? "Approving now: " + mode + " " + supervision :
    "Approval settings will appear here once a plan is written.");
}
// ---- Inbox bell (GET /api/supervisor/inbox, same tick as the task poll) ----
let inboxCursor = 0;
let inboxItems = []; // oldest first, capped
let inboxAckSeq = 0;
let ownerFloorSeq = -1; // owner messages at/below the connect-time tip are history, not unread
const threadAckSeq = new Map(); // thread_id -> seq acked by opening the thread
const taskAckSeq = new Map(); // task_id -> seq acked by opening the task
function inboxUnread() {
  return inboxItems.filter((item) =>
    (item.type === "owner_message" || item.type === "parked_question") &&
    item.seq > inboxAckSeq &&
    (item.type !== "owner_message" || (item.seq > ownerFloorSeq &&
      item.thread_id !== supervisorThreadId && (threadAckSeq.get(item.thread_id) || 0) < item.seq)) &&
    (item.type !== "parked_question" || (!item.answered && (taskAckSeq.get(item.task_id) || 0) < item.seq)));
}
async function pollInbox() {
  const session = sessionRevision;
  if (selectedMode === null) return;
  const data = await api("/api/supervisor/inbox?since=" + inboxCursor);
  if (session !== sessionRevision || selectedMode === null) return;
  if (ownerFloorSeq < 0 && typeof data.cursor === "number") ownerFloorSeq = data.cursor;
  if (Array.isArray(data.items) && data.items.length) {
    inboxItems.push(...data.items);
    if (inboxItems.length > 50) inboxItems.splice(0, inboxItems.length - 50);
  }
  if (typeof data.cursor === "number") inboxCursor = data.cursor;
  renderBell();
}
function renderBell() {
  const count = inboxUnread().length;
  const badge = element("inboxCount");
  badge.hidden = count === 0;
  badge.textContent = count > 9 ? "9+" : String(count);
  element("inboxBell").setAttribute("aria-label",
    count ? "Inbox: " + count + " unread. Activate to jump to the newest unread item." : "Inbox: clear.");
}
element("inboxBell").onclick = () => guarded(async () => {
  if (selectedMode === null) { message("Connect first: the inbox needs an established session."); return; }
  try { await pollInbox(); } catch { /* Jump with the items already held. */ }
  const unread = inboxUnread();
  const target = unread[unread.length - 1] || inboxItems[inboxItems.length - 1];
  if (!target) { message("Inbox is clear. Nothing needs you."); return; }
  inboxAckSeq = Math.max(inboxAckSeq, target.seq);
  if (target.type === "owner_message") {
    await loadSupervisorThreads();
    await openSupervisorThread(target.thread_id);
    switchView("supervise");
    message("Jumped to thread '" + (target.topic || target.thread_id.slice(0, 12)) + "'.");
  } else if (target.type === "parked_question") {
    await chooseTask(target.task_id, "inbox");
    switchView("review");
    message("Jumped to the parked task. Answer the owner question in the Review view to resume it.");
  } else if (target.run_id) {
    const detail = await api("/api/supervisor/runs/" + target.run_id);
    if (detail.task_id) {
      await chooseTask(detail.task_id, "inbox");
      switchView("review");
      message("Jumped to run " + target.run_id.slice(0, 12) + " (" + target.status + ").");
    } else if (target.thread_id) {
      await loadSupervisorThreads();
      await openSupervisorThread(target.thread_id);
      switchView("supervise");
      message("Run " + target.run_id.slice(0, 12) + " has no linked task yet; opened its thread.");
    }
  }
  renderBell();
});
let typingRow = null;
function showTyping() {
  hideTyping();
  const list = element("supervisorTranscript");
  typingRow = document.createElement("li");
  typingRow.className = "chat-msg chat-assistant chat-typing";
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble typing-dots";
  bubble.setAttribute("aria-label", "Assistant is typing");
  for (let i = 0; i < 3; i++) bubble.append(document.createElement("span"));
  typingRow.append(bubble);
  list.append(typingRow);
  list.scrollTop = list.scrollHeight;
}
function hideTyping() {
  if (typingRow) { typingRow.remove(); typingRow = null; }
}
function renderSupervisorTranscript(history) {
  typingRow = null; // replaceChildren below drops any typing row; clear the stale ref.
  element("supervisorTranscript").replaceChildren(...history.map((turn, index) => {
    const mine = turn.role === "user";
    const item = document.createElement("li");
    item.className = "chat-msg " + (mine ? "chat-user" : "chat-assistant");
    const who = document.createElement("span");
    who.className = "chat-who";
    who.textContent = mine ? "You" : "Assistant" + (turn.route ? " · " + turn.route : "");
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = turn.text;
    item.append(who, bubble);
    if (turn.role === "user" && turn.translated && typeof turn.source_text === "string") {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "Show original (" + String(turn.source_lang || "?").toUpperCase() + ")";
      const source = document.createElement("p");
      source.textContent = turn.source_text;
      const meta = document.createElement("p");
      meta.textContent = "Language: " + (turn.source_lang || "?") + " · Translator: " + translatorName(turn.translator);
      meta.title = "Translator: " + (turn.translator || "?");
      details.append(summary, source, meta);
      item.append(details);
    }
    return item;
  }));
  element("supervisorTranscript").scrollTop = element("supervisorTranscript").scrollHeight;
}
function translatorName(id) {
  if (typeof id === "string" && id.startsWith("meta:")) return "Meta AI";
  return id || "?";
}
function renderTranslationNote(thread) {
  const note = element("supervisorTranslationNote");
  const history = thread.history || [];
  const untranslated = history.some((turn) => turn.role === "user" && !turn.translated &&
    typeof turn.source_lang === "string" && turn.source_lang !== "en");
  if (untranslated) {
    note.textContent = "Contains untranslated non-English text — planning ran on source text.";
    return;
  }
  const langs = new Set();
  const engines = new Set();
  if (typeof thread.prompt_lang === "string" && thread.prompt_lang !== "en" &&
      thread.prompt_translator && thread.prompt_translator !== "none") {
    langs.add(thread.prompt_lang.toUpperCase());
    engines.add(translatorName(thread.prompt_translator));
  }
  for (const turn of history) {
    if (turn.role === "user" && turn.translated) {
      langs.add(String(turn.source_lang || "?").toUpperCase());
      engines.add(translatorName(turn.translator));
    }
  }
  note.textContent = langs.size ?
    "Includes text translated from " + [...langs].join(", ") + " via " + [...engines].join(", ") +
    " — source preserved below; verify meaning before approving." : "";
}
async function loadSupervisorThreads() {
  const session = sessionRevision;
  const threads = await api("/api/supervisor/threads");
  if (session !== sessionRevision || selectedMode === null) return;
  const chooser = element("supervisorThreads");
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = threads.length ? "Choose a thread" : "No threads yet";
  const starter = document.createElement("option");
  starter.value = "__new__"; starter.textContent = "＋ New thread…";
  chooser.replaceChildren(placeholder, starter, ...threads.map((item) => {
    const option = document.createElement("option");
    option.value = item.id; option.textContent = humanizeStatus(item.status) + " · " + item.topic;
    option.title = "Raw status: " + item.status;
    return option;
  }));
  chooser.value = supervisorThreadId;
  element("newThreadRow").hidden = chooser.value !== "__new__";
}
async function openSupervisorThread(id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Choose a thread first.");
  const detail = await api("/api/supervisor/threads/" + encodeURIComponent(id));
  supervisorThreadId = id;
  const approval = detail.thread.status === "planned" ? detail.thread.approval : null;
  supervisorPlanNonce = approval && approval.nonce ? approval.nonce : "";
  element("supervisorThreads").value = id;
  element("newThreadRow").hidden = true;
  renderSupervisorTranscript(detail.thread.history || []);
  renderTranslationNote(detail.thread);
  element("supervisorPlan").textContent = detail.thread.plan ? detail.thread.plan.markdown : "No plan written.";
  if (detail.thread.status === "planned" && approval && typeof approval.expires === "number") {
    const remainingMs = approval.expires * 1000 - Date.now();
    if (remainingMs <= 0) {
      supervisorPlanNonce = "";
      supervisorNote("Plan approval expired. Write the plan again for a fresh approval window.");
    } else {
      supervisorNote("Thread open. Approve the plan within ~" + Math.max(1, Math.round(remainingMs / 60000)) + " min. If it expires, write the plan again.");
    }
  } else {
    supervisorNote("Thread open. Chat to refine requirements, then write the plan.");
  }
  threadAckSeq.set(id, inboxCursor); // Opening a thread reads its owner-message items.
  renderBell();
  supervisorActions();
}
function threadTopic(thread_id) {
  const options = element("supervisorThreads").options;
  for (const option of options) {
    if (option.value === thread_id) {
      const sep = option.textContent.indexOf(" \u00b7 ");
      return sep >= 0 ? option.textContent.slice(sep + 3) : option.textContent;
    }
  }
  return "";
}
// ==split-parser-start== (pure; unit-tested by tests/split-parser.test.mjs via marker slice)
function parseUnifiedDiff(text) {
  // Split concatenated unified diffs (difflib format, one section per file)
  // into per-file old/new line columns. Left holds context + deletions,
  // right holds context + additions. Unknown lines go to both sides so no
  // content is ever hidden by the split view.
  const files = [];
  let cur = null;
  let lastSide = "both"; // attaches "\ No newline" markers to the prior side
  const pushLine = (side, line) => {
    if (!cur) { cur = { file: "change", left: [], right: [] }; files.push(cur); }
    if (side === "left" || side === "both") cur.left.push(line);
    if (side === "right" || side === "both") cur.right.push(line);
  };
  for (const raw of String(text || "").split("\n")) {
    if (raw.startsWith("--- ")) {
      cur = { file: raw.slice(4).trim().replace(/^base\//, ""), left: [], right: [] };
      files.push(cur); lastSide = "both";
    } else if (raw.startsWith("+++ ")) {
      if (cur) {
        const name = raw.slice(4).trim().replace(/^candidate\//, "");
        if (cur.file !== name) cur.file += " \u2192 " + name;
      }
      lastSide = "both";
    } else if (raw.startsWith("@@")) {
      pushLine("both", raw); lastSide = "both";
    } else if (raw.startsWith("\\ ")) {
      pushLine(lastSide, raw);
    } else if (raw.startsWith("-")) {
      pushLine("left", raw); lastSide = "left";
    } else if (raw.startsWith("+")) {
      pushLine("right", raw); lastSide = "right";
    } else if (raw === "") {
      continue; // trailing-newline artifact; difflib never emits bare mid-diff lines
    } else {
      pushLine("both", raw); lastSide = "both";
    }
  }
  return files.filter((f) => f.left.length || f.right.length);
}
// ==split-parser-end==
function renderDiffViews(text, emptyText) {
  element("diff").textContent = text || emptyText;
  const box = element("diffSplit");
  box.replaceChildren();
  const files = parseUnifiedDiff(text || "");
  if (!files.length) {
    const empty = document.createElement("p");
    empty.textContent = text ? "No line changes." : emptyText;
    box.append(empty);
  }
  for (const f of files) {
    const section = document.createElement("div");
    section.className = "diff-split-file";
    const head = document.createElement("div");
    head.className = "diff-split-head";
    head.textContent = f.file + " \u2014 old (base) vs new (candidate)";
    const cols = document.createElement("div");
    cols.className = "diff-cols";
    const left = document.createElement("pre");
    left.className = "diff-col diff-old";
    left.setAttribute("aria-label", "Old (base)");
    left.textContent = f.left.join("\n");
    const right = document.createElement("pre");
    right.className = "diff-col diff-new";
    right.setAttribute("aria-label", "New (candidate)");
    right.textContent = f.right.join("\n");
    cols.append(left, right);
    section.append(head, cols);
    box.append(section);
  }
  applyDiffMode();
}
function applyDiffMode() {
  const split = diffMode === "split";
  element("diff").hidden = split;
  element("diffSplit").hidden = !split;
  element("diffModeSplit").classList.toggle("active", split);
  element("diffModeUnified").classList.toggle("active", !split);
}
function renderApprovalSubject() {
  const subject = element("approvalSubject");
  const checklist = element("approvalChecklist");
  if (!task) {
    subject.textContent = "No task selected.";
    checklist.replaceChildren();
    return;
  }
  const short = (hex) => typeof hex === "string" ? hex.slice(0, 12) : "?";
  const kind = reviewContext ?
    "Supervised run " + reviewContext.run_id.slice(0, 12) + (reviewContext.thread_topic ? " \u00b7 thread '" + reviewContext.thread_topic + "'" : "") :
    "Demo task";
  const promptLine = typeof task.prompt === "string" ? task.prompt.split("\n")[0].slice(0, 90) : "";
  const injected = task.injected ? " \u00b7 injected failure" : "";
  const runner = task.evidence && task.evidence.runner ? task.evidence.runner : "not run yet";
  subject.textContent = kind + " \u00b7 task " + task.id.slice(0, 12) + " \u00b7 attempt " + (task.calls || 0) + injected +
    " \u00b7 actor " + runner + " \u00b7 candidate " + short(task.evidence && task.evidence.candidate_digest) +
    " \u00b7 base " + short(task.base_digest) + (promptLine ? ' \u00b7 "' + promptLine + '"' : "") +
    (taskSource ? " \u00b7 opened from " + taskSource : "");
  const rows = [];
  const push = (ok, text) => rows.push({ok, text: (ok ? "PASS " : "FAIL ") + text});
  if (!task.evidence) {
    push(false, "No candidate produced yet.");
  } else {
    push(true, "Candidate fingerprinted " + short(task.evidence.candidate_digest) + ".");
    push(!!task.evidence.passed, task.evidence.passed ? "Validation passed (" + runner + ")." : "Validation FAILED \u2014 do not approve.");
  }
  if (task.status === "awaiting_approval" && task.approval && typeof task.approval.expires === "number") {
    const fresh = Date.now() < task.approval.expires * 1000;
    push(fresh, fresh ? "Approval window fresh." : "Approval EXPIRED \u2014 revalidate.");
  } else if (task.status === "awaiting_approval") {
    push(false, "No valid approval \u2014 run fresh validation first.");
  }
  checklist.replaceChildren(...rows.map((row) => {
    const item = document.createElement("li");
    item.textContent = row.text;
    item.className = row.ok ? "pass" : "fail";
    return item;
  }));
}
async function loadSupervisorRuns() {
  const session = sessionRevision;
  const payload = await api("/api/supervisor/runs");
  if (session !== sessionRevision || selectedMode === null) return;
  const all = Array.isArray(payload) ? payload : (payload.runs || []);
  const runs = supervisorThreadId ? all.filter((run) => run.thread_id === supervisorThreadId) : all;
  lastRunsActive = all.some((run) => ["running", "applying"].includes(run.status));
  updateWorkerPill();
  const list = element("supervisorRuns");
  if (!runs.length) {
    const empty = document.createElement("li");
    empty.textContent = supervisorThreadId ? "No runs for this thread yet." : "No runs yet.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...runs.map((run) => {
    const item = document.createElement("li");
    item.textContent = humanizeStatus(run.status) + " · " + run.run_id.slice(0, 12) + " ";
    item.title = "Raw status: " + run.status;
    const manifest = run.translation_manifest;
    if (manifest && Array.isArray(manifest.langs) && manifest.langs.length) {
      item.textContent += "(translated from " + manifest.langs.join(", ").toUpperCase() + ") ";
    }
    const review = document.createElement("button");
    review.textContent = "Review"; review.className = "secondary";
    review.title = "Open this run's task in the Review view.";
    review.onclick = () => guarded(() => supervisorStep(async () => {
      const detail = await api("/api/supervisor/runs/" + run.run_id);
      if (!detail.task_id) { supervisorNote("Run has no linked task yet; wait for the worker."); return; }
      await chooseTask(detail.task_id, "run");
      reviewContext = {run_id: run.run_id, thread_id: run.thread_id, thread_topic: threadTopic(run.thread_id)};
      renderApprovalSubject();
      switchView("review");
    }));
    item.append(review);
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
  const value = element("supervisorThreads").value;
  if (value === "__new__") {
    element("newThreadRow").hidden = false;
    element("supervisorNewTopic").focus();
    return;
  }
  element("newThreadRow").hidden = true;
  if (value) await openSupervisorThread(value);
}));
element("supervisorOpen").onclick = () => guarded(() => supervisorStep(async () => {
  const topic = element("supervisorNewTopic").value.trim();
  if (!topic) { supervisorNote("Enter a thread topic first."); return; }
  const opened = await api("/api/supervisor/threads", "POST", {topic, language: element("supervisorLang").value});
  element("supervisorNewTopic").value = "";
  await loadSupervisorThreads();
  await openSupervisorThread(opened.id);
  element("newThreadRow").hidden = true;
}));
element("supervisorSend").onclick = () => guarded(() => supervisorStep(async () => {
  if (!supervisorThreadId) return;
  const text = element("supervisorInput").value.trim();
  if (!text) return;
  showTyping();
  try {
    await api("/api/supervisor/threads/" + supervisorThreadId + "/messages", "POST", {text, language: element("supervisorLang").value});
    element("supervisorInput").value = "";
    await openSupervisorThread(supervisorThreadId);
  } finally {
    hideTyping();
  }
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
  const execute = element("runMode").value !== "planning";
  const queued = await api("/api/supervisor/threads/" + supervisorThreadId + "/approve", "POST", {nonce: supervisorPlanNonce, execute});
  supervisorPlanNonce = "";
  await loadSupervisorRuns();
  await openSupervisorThread(supervisorThreadId);
  supervisorNote(execute ? "Plan approved. Run queued: " + queued.run_id.slice(0, 12) + "." :
    "Plan approved (planning only). Nothing was queued; switch run mode and approve a fresh plan to implement.");
}));
function renderEvents() {
  const start = showAllEvents ? 0 : Math.max(0, taskEvents.length - 20);
  element("events").replaceChildren(...taskEvents.slice(start).map((event, offset) => {
    const item = document.createElement("li");
    item.className = "event-row";
    const label = document.createElement("span");
    label.textContent = (start + offset + 1) + " · " + (eventNames[event.kind] || humanizeStatus(event.kind)) + (event.payload.injected ? " (injected)" : "") +
      (event.payload.name ? " · " + event.payload.name : "") +
      (event.kind === "transport_failed" && typeof event.payload.detail === "string" ? " · " + event.payload.detail : "");
    item.title = "Raw event: " + event.kind;
    item.append(label);
    return item;
  }));
  element("event-summary").textContent = !task ? "Create a new task or select a saved task." :
    taskEvents.length ? (start ? "Latest 20 of " : "Showing ") + taskEvents.length + " activity entries for this task." : "No activity for this task yet.";
  element("eventHistory").hidden = taskEvents.length <= 20;
  element("eventHistory").textContent = showAllEvents ? "Show latest activity" : "Show all activity (" + taskEvents.length + ")";
}
function renderEvidenceCard() {
  const card = element("evidenceCard");
  if (!task || !task.evidence) { card.hidden = true; card.replaceChildren(); card.title = ""; return; }
  const evidence = task.evidence;
  const report = parseChecksReport(evidence.output);
  const duration = parseChecksDuration(evidence.output);
  const bits = [];
  if (report) {
    const passedCount = Math.max(0, report.tests - (report.failures || 0) - (report.errors || 0));
    bits.push(passedCount + "/" + report.tests + " checks passed");
    if (report.skipped) bits.push(report.skipped + " skipped");
  }
  bits.push("runner " + evidence.runner);
  if (duration) bits.push(duration);
  bits.push("exit " + String(evidence.exit_code));
  const verdict = document.createElement("strong");
  verdict.className = evidence.passed ? "pass" : "fail";
  verdict.textContent = evidence.passed ? "PASS" : "FAIL";
  card.replaceChildren(verdict, document.createTextNode(" · " + bits.join(" · ")));
  card.title = "candidate " + evidence.candidate_digest + " · raw: " +
    JSON.stringify({passed: evidence.passed, runner: evidence.runner, exit_code: evidence.exit_code});
  card.hidden = false;
}
function updateWindowChip() {
  const chip = element("windowChip");
  chip.classList.remove("fresh", "stale");
  if (!task || task.status !== "awaiting_approval" || !task.approval || typeof task.approval.expires !== "number") {
    chip.textContent = task && task.status === "awaiting_approval" ? "Review window: revalidate needed" : "No review window";
    chip.title = "Reviewer approval window for the current task.";
    return;
  }
  const remainingMs = task.approval.expires * 1000 - Date.now();
  const when = new Date(task.approval.expires * 1000).toLocaleString();
  if (remainingMs <= 0) {
    chip.textContent = "Review window: expired";
    chip.classList.add("stale");
    chip.title = "Approval expired at " + when + ". Run fresh validation.";
    return;
  }
  const minutes = Math.floor(remainingMs / 60000);
  chip.textContent = minutes >= 1 ? "Review window: ~" + minutes + " min left" : "Review window: <1 min left";
  chip.classList.add("fresh");
  chip.title = "Approval expires at " + when + ".";
}
setInterval(() => { if (selectedMode !== null && task) updateWindowChip(); }, 10000);
function updateBuildChip() {
  const chip = element("buildChip");
  const commit = selectedMode && selectedMode.build_commit ? String(selectedMode.build_commit) : "";
  chip.textContent = commit ? "build " + commit.slice(0, 12) : "build ?";
  chip.title = commit ? "Server build commit " + commit + "." : "Server build commit unknown.";
}
function applyJudgeVisibility() {
  const judge = judgeSession === true;
  element("machineSection").hidden = judge;
  element("workbenchSection").hidden = judge || !WORKBENCH_ACTIONS_VISIBLE;
}
function clearTaskView() {
  task = null; taskEvents = []; showAllEvents = false; reviewContext = null; triedApproved = false; taskSource = "";
  element("status").textContent = "No task selected";
  element("status").title = "";
  renderDiffViews("", "No candidate yet.");
  element("evidence").textContent = "Not validated.";
  element("validation-summary").textContent = "Not validated.";
  element("verdict").textContent = "No candidate yet.";
  element("verdict").className = "";
  renderEvidenceCard();
  updateWindowChip();
  renderApprovalSubject();
  element("demoReviewStatus").textContent = "No task yet. Create and run one above; you will be brought to the Review view when it is ready.";
  element("demoReviewCta").disabled = true;
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
async function chooseTask(id, source) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Choose a saved task or provide a valid task ID.");
  reviewContext = null;
  stopPolling(); selectionRevision++; task = null; taskSource = source || "list";
  element("taskId").value = id;
  element("issues").replaceChildren(); element("import-errors").replaceChildren();
  clearTaskView();
  await refresh();
  if (task && task.id === id) {
    taskAckSeq.set(id, inboxCursor); // Opening a task reads its parked-question items.
    renderBell();
  }
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
  element("status").textContent = humanizeStatus(task.status);
  element("status").title = "Raw status: " + task.status;
  renderDiffViews(review.diff, "No candidate change.");
  element("evidence").textContent = task.evidence ? JSON.stringify(task.evidence, null, 2) : "Not validated.";
  element("validation-summary").textContent = task.evidence ?
    (task.evidence.passed ? "Passed" : "Failed") + " · " + task.evidence.runner + " · source " + task.evidence.candidate_digest.slice(0,12) : "Not validated.";
  if (task.status === "awaiting_approval" && task.approval && typeof task.approval.expires === "number") {
    const remainingMs = task.approval.expires * 1000 - Date.now();
    if (remainingMs <= 0) {
      message("Approval expired. Run fresh validation, then approve within its window.");
    } else {
      const minutes = Math.max(1, Math.round(remainingMs / 60000));
      element("validation-summary").textContent += " · approve within ~" + minutes + " min";
    }
  }
  element("verdict").textContent = !task.evidence ? "No candidate yet. Run the task to produce one." :
    !task.evidence.passed ? "Validation FAILED. Do not approve; inspect the receipt or revalidate." :
    task.status === "awaiting_approval" ? "Ready: validation passed. Review the diff, then approve to apply." :
    task.status === "completed" ? "Completed: this change was applied." :
    "Status: " + humanizeStatus(task.status) + ".";
  element("verdict").className = !task.evidence ? "" : !task.evidence.passed ? "verdict-fail" :
    ["awaiting_approval", "completed"].includes(task.status) ? "verdict-ready" : "";
  element("demoReviewStatus").textContent = "Task " + humanizeStatus(task.status) + ": " + element("verdict").textContent +
    (taskSource ? " (opened from " + taskSource + ")" : "");
  element("demoReviewStatus").title = "Raw status: " + task.status;
  element("demoReviewCta").disabled = false;
  renderApprovalSubject();
  renderEvidenceCard();
  updateWindowChip();
  actions();
  const events = await api("/api/tasks/" + encodeURIComponent(id) + "/events");
  if (!stillSelected(id, selection, session)) return;
  taskEvents = events; renderEvents();
  element("events").scrollTop = element("events").scrollHeight;
  if (["running", "applying"].includes(task.status)) startPolling();
  else stopPolling();
  const status = task.status;
  if (previousStatus !== status && !["running", "applying"].includes(status)) await recentTasks();
  if (status === "awaiting_approval" && previousStatus !== status && !task.supervisor_thread) {
    message("Change ready for review: check the diff in the Review view, then press Approve this verified change.");
    switchView("review");
  }
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
    if (created.reply) {
      await loadSupervisorThreads();
      await openSupervisorThread(created.thread_id);
      message("Reply recorded in the Owner replies thread. No demo task was started.");
      return;
    }
    await chooseTask(created.id, "creation"); await recentTasks();
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
element("load").onclick = () => guarded(() => chooseTask(element("taskId").value.trim(), "ID"));
element("approve").onclick = () => guarded(async () => {
  if (!task) return;
  if (!task.approval) { message("This task has no valid approval yet. Run fresh validation first, then approve."); return; }
  const short = (hex) => typeof hex === "string" ? hex.slice(0, 12) : "?";
  const runner = task.evidence && task.evidence.runner ? task.evidence.runner : "not run yet";
  const summary = "Approve this change?\n\nCandidate: " + short(task.evidence && task.evidence.candidate_digest) +
    "\nBase: " + short(task.base_digest) + "\nValidated by: " + runner +
    "\nTarget: separate LOCAL practice file only — no push, no deploy." +
    "\n\nPress OK to approve, Cancel to keep reviewing.";
  if (!window.confirm(summary)) return;
  await taskAction("/approve", {nonce: task.approval.nonce}, "Verified change applied to the local practice file.");
});
element("reject").onclick = () => guarded(() => taskAction("/reject", {}, "Change rejected; target not updated."));
element("revalidate").onclick = () => guarded(() => taskAction("/revalidate", {}, "Fresh validation completed. The previous approval is no longer valid — review and approve again."));
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
async function runSample() {
  const result = await taskAction("/sample", {csv_text: element("csv").value}, "Applied importer executed.");
  if (!result) return null;
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
  return result;
}
element("preview").onclick = () => guarded(runSample);
element("tryApproved").onclick = () => guarded(async () => {
  const result = await runSample();
  if (!result) return;
  switchView("demo");
  element("demoStep3").scrollIntoView();
});
async function copyText(text, success) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  message(success);
  triedApproved = true;
  if (typeof updateStepper === "function") updateStepper();
}
element("copyTaskId").onclick = () => guarded(async () => {
  const id = element("taskId").value.trim();
  if (!id) { message("No task selected to copy."); return; }
  await copyText(id, "Task ID copied.");
});
element("copyTaskLink").onclick = () => guarded(async () => {
  const id = element("taskId").value.trim();
  if (!id) { message("No task selected to copy."); return; }
  await copyText(location.origin + location.pathname + location.search + "#" + id, "Task link copied.");
});
function displayMode(configuration) {
  updateBuildChip();
  const liveBanner = configuration.mode === "baseten" ?
    "Muse Spark Open · live AI answers (Baseten)" : "Meta AI · live coding with your key";
  metaSttPreferred = configuration.live_inference === true;
  refreshDictationBackends();
  document.querySelector(".notice").textContent = configuration.live_inference ?
    liveBanner : "Offline reference · no AI calls, no cost";
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
    selectedMode = null; authenticated = false; sessionKeyAttached = false; grantSaved = false;
    element("access-details").open = true; element("access-state").textContent = "";
    actions();
    message("Enter the demo access code to open your own private session.");
    return;
  }
  authenticated = true;
  grantSaved = false;
  element("access-details").open = false;
  element("access-state").textContent = "connected — click to expand";
  selectedMode = configuration; displayMode(configuration);
  sessionKeyAttached = !!configuration.session_key_attached;
  judgeSession = !!configuration.judge;
  applyJudgeVisibility();
  if (hosted) {
    modelRouteConfigured = configuration.model_route_configured !== false;
    element("key-controls").hidden = !modelRouteConfigured;
    element("judge-grant").hidden = !judgeSession;
    keyNote();
    if (judgeSession) await refreshJudgeGrant(session);
  }
  await refreshAllowance();
  if (session !== sessionRevision) return;
  const liveMode = selectedMode.mode === "baseten" ? "live Baseten inference (Muse Spark Open)" : "live Meta inference";
  element("execMode").textContent = "Execution mode: " + (selectedMode.live_inference ? liveMode : "scripted offline reference") + ".";
  await loadSettings();
  if (session !== sessionRevision) return;
  await refreshPowerStatus();
  if (session !== sessionRevision) return;
  await refreshWindowStatus();
  if (session !== sessionRevision) return;
  startWindowPolling();
  const summaries = await recentTasks();
  if (session !== sessionRevision) return;
  await refreshExternalActions();
  if (session !== sessionRevision) return;
  try {
    await loadSupervisorThreads();
    if (session !== sessionRevision) return;
    if (!supervisorThreadId) {
      const chooser = element("supervisorThreads");
      const replies = [...chooser.options].find((option) => option.textContent.endsWith("Owner replies"));
      if (replies && replies.value) await openSupervisorThread(replies.value);
    }
    await loadSupervisorRuns();
    if (session !== sessionRevision) return;
    try { await pollInbox(); } catch { /* Bell seeds on the next task tick. */ }
    if (session !== sessionRevision) return;
  } catch (error) {
    supervisorNote("Supervisor data unavailable: " + error.message);
  }
  const saved = element("taskId").value.trim();
  if (saved) {
    await chooseTask(saved, "link");
    if (session === sessionRevision) {
      if (task && !task.supervisor_thread) {
        switchView("review");
        message("Connected. Your saved task is loaded; review its current status in the Review view.");
      } else {
        message("Connected. Your saved task is loaded.");
      }
    }
  } else if (summaries.length) {
    message("Connected. Select a saved task to review it, or create a new task.");
  } else message("Connected. Review execution mode before submitting your task.");
}
element("connect").onclick = () => guarded(async () => {
  stopPolling(); const session = ++sessionRevision;
  selectedMode = null; allowance = null; allowanceCursor = ""; connecting = true; actions();
  accessStatus("Connecting…");
  try {
    await loadMode(session);
    if (session === sessionRevision)
      accessStatus(selectedMode ? "Connected. Execution mode loaded." : "Connected, but no execution mode was loaded. Check the server is running, then press Connect again.");
  } catch (error) {
    if (session === sessionRevision) accessStatus("Connect failed: " + error.message);
    throw error;
  } finally {
    if (session === sessionRevision) { connecting = false; actions(); }
  }
});
element("enterSession").onclick = () => guarded(async () => {
  stopPolling(); const session = ++sessionRevision;
  selectedMode = null; allowance = null; allowanceCursor = ""; connecting = true; actions();
  accessStatus("Connecting…");
  try {
    await api("/api/session", "POST", {code: element("accessCode").value});
    element("accessCode").value = "";
    await loadMode(session);
    if (session === sessionRevision) {
      message("Private session started. Review execution mode before submitting your task.");
      accessStatus("Connected. Private session started.");
    }
  } catch (error) {
    if (session === sessionRevision) accessStatus("Connect failed: " + error.message);
    throw error;
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
    grantSaved = true;
  } else {
    element("grant-note").textContent = "No spending limits yet. Save limits before attaching a key.";
    grantSaved = false;
  }
}
element("judgeSignup").onclick = () => guarded(async () => {
  if (busy) return;
  if (!judgeSignupValid()) { message("Check the account hints above before registering."); return; }
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
  if (!grantLimitsValid()) { message("Check the spending limit hints above before saving."); return; }
  busy = true; actions();
  try {
    await api("/api/judges/grant", "PUT", {max_requests: Number(element("grantRequests").value),
      approved_usd: element("grantUsd").value.trim()});
    const session = ++sessionRevision;
    await loadMode(session);
    if (session === sessionRevision) message("Spending limits saved. Attach your key to run real inference.");
  } finally { busy = false; actions(); }
});
/* ---- Onboarding: wizard, inline validation, sample review, sample inputs ---- */
function judgeSignupValid() {
  const name = element("judgeName").value.trim();
  const password = element("judgePassword").value;
  const nameOk = name.length >= 3 && name.length <= 32;
  const passOk = password.length >= 8;
  if (nameOk && passOk) element("judge-validation").textContent = "Account details look valid. Register, then log in.";
  else {
    const problems = [];
    if (!nameOk) problems.push("username needs 3–32 characters");
    if (!passOk) problems.push("password needs at least 8 characters");
    element("judge-validation").textContent = "To register: " + problems.join("; ") + ".";
  }
  return nameOk && passOk;
}
element("judgeName").addEventListener("input", judgeSignupValid);
element("judgePassword").addEventListener("input", judgeSignupValid);
judgeSignupValid();
function grantLimitsValid() {
  const requests = Number(element("grantRequests").value);
  const usd = element("grantUsd").value.trim();
  const usdNumber = usd === "" ? NaN : Number(usd);
  const requestsOk = Number.isInteger(requests) && requests >= 1 && requests <= 100;
  const usdOk = Number.isFinite(usdNumber) && usdNumber > 0 && usdNumber <= 100;
  if (!requestsOk || !usdOk) {
    const problems = [];
    if (!requestsOk) problems.push("Max requests must be a whole number from 1 to 100");
    if (!usdOk) problems.push("Approved USD must be a number between 0 and 100, e.g. 2.50");
    element("grant-note").textContent = "Check your limits: " + problems.join("; ") + ".";
    return false;
  }
  return true;
}
function wizardStep() {
  if (!authenticated) return 1;
  if (!judgeSession) return 2;
  if (!grantSaved) return 3;
  if (!sessionKeyAttached && !scriptedChoice) return 4;
  return 0;
}
function renderWizard() {
  if (element("onboard-wizard").hidden) return;
  const current = wizardStep();
  const labels = ["", "demo access code", "judge account", "spending limits", "key or scripted"];
  element("wizard-progress").textContent = current === 0 ?
    "Setup complete — " + (sessionKeyAttached ? "Meta key attached." : "offline reference selected.") + " Load a sample review below." :
    "Step " + current + " of 4: " + labels[current] + ".";
  for (let step = 1; step <= 4; step++) {
    const done = current === 0 || step < current;
    let text = done ? "Done." : step === current ? "Current step." : "To do.";
    if (step === 4 && done) text = sessionKeyAttached ? "Done (key attached)." : "Done (offline reference).";
    element("wizard-state-" + step).textContent = text;
  }
}
function wizardFocus(id) {
  element("access-details").open = true;
  const target = element(id);
  target.scrollIntoView({block: "center"});
  target.focus({preventScroll: true});
}
element("wizardGo1").onclick = () => wizardFocus(hosted ? "accessCode" : "token");
element("wizardGo2").onclick = () => wizardFocus("judgeName");
element("wizardGo3").onclick = () => wizardFocus("grantRequests");
element("wizardGo4").onclick = () => wizardFocus("modelKey");
element("wizardScripted").onclick = () => {
  scriptedChoice = true; renderWizard();
  message("Continuing with the scripted offline reference: no key, no cost, no model requests.");
};
element("sampleReview").onclick = () => guarded(async () => {
  if (busy || !inferenceAllowed()) return;
  busy = true; actions();
  const session = sessionRevision;
  const note = element("sampleReviewNote");
  try {
    note.textContent = "Looking for a ready-made review…";
    const summaries = await api("/api/tasks?limit=20");
    if (session !== sessionRevision) return;
    const candidates = summaries.filter((item) => !item.thread && ["awaiting_approval", "completed"].includes(item.status));
    for (const candidate of candidates.slice(0, 3)) {
      try {
        const review = await api("/api/tasks/" + encodeURIComponent(candidate.id));
        if (session !== sessionRevision) return;
        if (review.task && review.task.evidence && review.task.evidence.passed) {
          await chooseTask(candidate.id, "sample"); await recentTasks();
          message("Sample review loaded from your saved tasks.");
          switchView("review");
          return;
        }
      } catch { /* Try the next candidate. */ }
    }
    if (candidates.length && session === sessionRevision) {
      await chooseTask(candidates[0].id, "sample"); await recentTasks();
      message("Sample review loaded from your saved tasks.");
      switchView("review");
      return;
    }
    note.textContent = "Cold start: the first run builds the reference and validates it, which can take a minute or two. Creating a sample task…";
    const created = await api("/api/tasks", "POST", {prompt: defaultPrompt});
    if (session !== sessionRevision) return;
    if (created.reply) { message("Reply recorded; no demo task was started."); return; }
    await chooseTask(created.id, "sample"); await recentTasks();
    startPolling();
    try { await api("/api/tasks/" + created.id + "/run", "POST", {inject_failure: false}); }
    finally { if (session === sessionRevision) await refresh(); }
    if (session !== sessionRevision) return;
    message("Sample task ran. Review the diff in the Review view, then approve to apply.");
  } finally {
    busy = false;
    if (session === sessionRevision) note.textContent = "";
    actions();
  }
});
const csvSamples = {
  happy: {text: "id,title,status\nalpha,First issue,open\nbeta,Second issue,closed\ngamma,Third issue,OPEN",
    expect: "All valid: expect 3 accepted, 0 row errors."},
  edges: {text: "id,title,status\nexisting,Clashes with seeded issue,open\nedge-1,,closed\nedge-2,Bad status row,pending\nedge-3,Valid after failures,closed\nedge-3,Duplicate within file,open",
    expect: "Tricky rows: expect 1 accepted, 4 row errors (rows 2–4 and 6: duplicate, missing title, bad status, duplicate)."},
  invalid: {text: "id,title,status\n,Missing id,open\nx,,closed\ny,Bad status,waiting\nexisting,Seeded duplicate,open",
    expect: "All broken: expect 0 accepted, 4 row errors."}
};
function loadCsvSample(kind) {
  element("csv").value = csvSamples[kind].text;
  element("csvExpect").textContent = csvSamples[kind].expect + " Counts describe the offline reference importer.";
}
element("csvHappy").onclick = () => loadCsvSample("happy");
element("csvEdges").onclick = () => loadCsvSample("edges");
element("csvInvalid").onclick = () => loadCsvSample("invalid");
element("token").addEventListener("input", () => {
  sessionRevision++; selectionRevision++; stopPolling(); selectedMode = null; task = null; connecting = false;
  allowance = null; allowanceCursor = ""; judgeSession = false;
  supervisorThreadId = ""; supervisorPlanNonce = "";
  inboxItems = []; inboxCursor = 0; inboxAckSeq = 0; ownerFloorSeq = -1;
  powerArmed = false; element("powerArm").textContent = "Sleep this PC…";
  element("supervisorTranscript").replaceChildren();
  element("supervisorPlan").textContent = "No plan written.";
  updatePlanSettingsNote(); renderBell();
  element("access-details").open = true; element("access-state").textContent = "";
  element("allowance-summary").textContent = "Connect to view this server's allowance.";
  element("allowance-note").textContent = "Local reserved estimates are separate from provider credits and final billing.";
  element("recentTasks").replaceChildren(); clearTaskView();
  applyJudgeVisibility(); updateBuildChip();
  document.querySelector(".notice").textContent = "Load execution mode before submitting";
});
element("taskId").addEventListener("input", () => {
  selectionRevision++; stopPolling(); clearTaskView();
  history.replaceState(null, "", location.pathname + location.search);
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
    element("onboard-wizard").hidden = !judgeLoginOffered;
    renderWizard();
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
/* ---- Dictation: Meta transcription first, Web Speech fallback ----
   Fills the paired textarea only; never sends. When a key route is live
   (metaSttPreferred) and the browser can record, clips are transcribed by
   Meta over the shared allowance in ~55s slices; otherwise, or when Meta
   fails before any text arrives, the Web Speech path is used. The button
   always names the backend actually used. Rows stay hidden unless at
   least one backend is available. */
let metaSttPreferred = false;
const dictationWires = [];
const META_SLICE_MS = 55000;
const Dictation = (() => {
  const Recognition = typeof window !== "undefined" &&
    (window.SpeechRecognition || window.webkitSpeechRecognition);
  let activeStop = null;
  function supported() { return typeof Recognition === "function"; }
  function create() { return new Recognition(); }
  function stopActive() {
    if (activeStop) {
      const stop = activeStop;
      activeStop = null;
      try { stop(); } catch { /* Stopping a stale session is best-effort. */ }
    }
  }
  function setActive(stop) { activeStop = stop; }
  function clearActive(stop) { if (activeStop === stop) activeStop = null; }
  return { supported, create, stopActive, setActive, clearActive };
})();
const MetaStt = (() => {
  function supported() {
    return typeof MediaRecorder !== "undefined" &&
      !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }
  function active() { return metaSttPreferred && supported(); }
  return { supported, active };
})();
function refreshDictationBackends() {
  for (const ctx of dictationWires) {
    if (ctx.busy) continue; // A live session keeps its backend label.
    if (MetaStt.active() || (!Dictation.supported() && MetaStt.supported())) {
      ctx.button.textContent = "🎤 Dictate (Meta)";
      ctx.button.title = "Dictate into the " + ctx.noun + " using Meta speech-to-text " +
        "(uses your allowance). Audio is transcribed by Meta, not your browser vendor. " +
        "Dictation only fills the field - you review and send.";
    } else if (Dictation.supported()) {
      ctx.button.textContent = "🎤 Dictate (browser speech recognition)";
      ctx.button.title = ctx.browserTitle;
    }
  }
}
function dictateLangName(code) { return code === "pl-PL" ? "polski" : "English"; }
function dictateLangTag(code) { return code === "pl-PL" ? "pl" : "en"; }
function dictateErrorMessage(code) {
  switch (code) {
    case "not-allowed":
    case "service-not-allowed":
      return "Microphone blocked. Allow mic access for this page (HTTPS required), then try again.";
    case "no-speech":
      return "No speech heard. Try again.";
    case "audio-capture":
      return "No microphone found on this device.";
    case "network":
      return "Browser speech service unreachable. Check the connection and try again.";
    case "language-not-supported":
      return "This browser does not support that dictation language. Try the other language.";
    case "aborted":
      return "";
    default:
      return "Dictation error (" + code + "). Try again or type instead.";
  }
}
function joinDictation(head, tail) {
  if (!head) return tail;
  return /\s$/.test(head) ? head + tail : head + " " + tail;
}
function startBrowserDictation(ctx) {
  const field = ctx.field, button = ctx.button, lang = ctx.lang, status = ctx.status;
  function setRecording(on) {
    button.classList.toggle("recording", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Stop dictation" : "🎤 Dictate (browser speech recognition)";
  }
  Dictation.stopActive(); // Only one dictation session at a time.
  ctx.base = field.value;
  ctx.finalText = "";
  ctx.lastRendered = null;
  status.textContent = "";
  let session;
  try {
    session = Dictation.create();
  } catch {
    status.textContent = "Dictation unavailable in this browser. Type instead.";
    return;
  }
  ctx.rec = session;
  ctx.stopBrowser = () => {
    if (ctx.rec) { try { ctx.rec.stop(); } catch { /* stop() after end is harmless. */ } }
  };
  Dictation.setActive(ctx.stopBrowser);
  session.lang = lang.value;
  session.interimResults = true;
  session.continuous = false; // Single utterance; the platform end-points on silence.
  session.maxAlternatives = 1;
  session.onresult = (event) => {
    let interim = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const chunk = event.results[i][0].transcript;
      if (event.results[i].isFinal) ctx.finalText += chunk;
      else interim += chunk;
    }
    ctx.lastRendered = joinDictation(ctx.base, ctx.finalText + interim); // Interim live, provisional.
    field.value = ctx.lastRendered;
  };
  session.onerror = (event) => {
    const note = dictateErrorMessage(event.error || "unknown");
    if (note) {
      status.textContent = note;
      message(note);
    }
  };
  session.onend = () => {
    Dictation.clearActive(ctx.stopBrowser);
    const captured = ctx.finalText.trim() !== "";
    if (captured) {
      // If the user typed during dictation, append to their edit instead of clobbering it.
      field.value = (field.value === ctx.lastRendered || field.value === ctx.base) ?
        joinDictation(ctx.base, ctx.finalText) : joinDictation(field.value, ctx.finalText);
      const buddy = document.querySelector('[data-send-lang-for="' + field.id + '"]');
      if (buddy) buddy.value = dictateLangTag(lang.value); // Dictated text is tagged on send.
      status.textContent = "Dictation added (" + dictateLangName(lang.value) +
        "). Review and edit before sending. Nothing was sent.";
    } else if (status.textContent === "") {
      status.textContent = "No speech captured; field unchanged.";
      field.value = ctx.base;
    } else if (ctx.lastRendered !== null) {
      field.value = ctx.base; // Partial interim was shown; restore the pre-dictation text.
    } // Else an error note is already shown; keep it and leave the field alone.
    ctx.rec = null;
    setRecording(false);
    field.focus();
  };
  setRecording(true);
  status.textContent = "Listening (" + dictateLangName(lang.value) + ")... Speak now; press Stop or pause to finish.";
  try {
    session.start();
  } catch (error) {
    Dictation.clearActive(ctx.stopBrowser);
    ctx.rec = null;
    setRecording(false);
    field.value = ctx.base;
    status.textContent = "Could not start dictation (" +
      (error && error.message ? error.message : "mic unavailable") +
      "). Check mic permission and HTTPS, then try again.";
  }
}
/* ---- Identity: stepper, banner, toggletips, guided tour (client-only) ---- */
const reducedMotion = typeof window !== "undefined" && typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function stepperIndex() {
  if (selectedMode === null) return 0; // Connect
  if (supervisorPlanNonce) return 2; // A written plan awaits approval: Review.
  if (!task) return 1; // Connected, nothing produced yet: Plan.
  if (task.status === "completed") return triedApproved ? 4 : 3; // Approved: Run, then Done.
  if (["awaiting_approval", "review_required", "waiting_on_owner"].includes(task.status)) return 2;
  return 1; // queued/running/paused/applying/reconciliation/cancelled/rejected: back to Plan.
}
function updateStepper() {
  const current = stepperIndex();
  ["stepConnect", "stepPlan", "stepReview", "stepRun", "stepDone"].forEach((id, index) => {
    const item = element(id);
    if (!item) return;
    item.classList.toggle("done", index < current || (current === 4 && index === 4));
    item.classList.toggle("current", index === current);
    if (index === current) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
}
updateStepper();
const tryApprovedSibling = element("tryApproved");
if (tryApprovedSibling) tryApprovedSibling.addEventListener("click", () => { triedApproved = true; updateStepper(); });
const quickBanner = element("quickBanner");
if (quickBanner) {
  const dismiss = element("quickBannerDismiss");
  if (dismiss) dismiss.onclick = () => { quickBanner.hidden = true; };
}
function wireToggletips() {
  const tips = Array.from(document.querySelectorAll(".toggletip"));
  function closeAll(except) {
    for (const btn of tips) {
      if (btn === except) continue;
      btn.setAttribute("aria-expanded", "false");
      const bubble = document.getElementById(btn.getAttribute("aria-controls"));
      if (bubble) bubble.hidden = true;
    }
  }
  for (const btn of tips) {
    btn.addEventListener("click", (event) => {
      event.preventDefault(); // In-label tips must not re-focus the labeled control.
      const bubble = document.getElementById(btn.getAttribute("aria-controls"));
      if (!bubble) return;
      const open = bubble.hidden;
      closeAll(btn);
      bubble.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAll(null); });
  document.addEventListener("click", (event) => {
    if (!event.target.closest || !event.target.closest(".toggletip-wrap")) closeAll(null);
  });
}
wireToggletips();
/* Guided tour: welcome, wizard, sample review, approve, try-it, bell.
   Each anchored step is guarded: a missing or hidden anchor skips cleanly. */
const tourSteps = [
  {anchor: null, view: null, title: "Welcome to Remote AI Operator",
   text: "Connect, run work, then review and approve every change in one place. This tour points out each stop — about a minute. You can skip any time."},
  {anchor: "onboard-wizard", view: null, title: "Setup wizard",
   text: "Four steps to a working session: demo access code, judge account, spending limits, then your Meta key — or continue with the free offline reference. The progress line shows where you are."},
  {anchor: "sampleReview", view: "demo", title: "Sample review",
   text: "Load a ready-made validated task to practice the review loop. Below, the sample inputs (all valid, tricky, or all broken rows) give one-click data for trying the feature."},
  {anchor: "approve", view: "review", title: "The one approval place",
   text: "Read the change and the validation report, then approve. Approval applies it only to a separate local practice file — in every mode, never a push or deploy."},
  {anchor: "tryApproved", view: "review", title: "Try the approved feature",
   text: "Runs the approved importer on sample data and opens Step 3 with accepted issues and row errors — proof the approved code works, whether AI or the offline reference wrote it."},
  {anchor: "inboxBell", view: null, title: "Inbox bell",
   text: "Owner messages and waiting questions needing you land here. The count badge jumps you to the newest unread item."}
];
let tourAvailable = [];
let tourPosition = 0;
let tourReturnFocus = null;
function tourAnchorVisible(step) {
  if (!step.anchor) return true;
  const target = element(step.anchor);
  if (!target) return false;
  if (step.view) return true; // Switching to its view reveals it; verified on show.
  return target.getClientRects().length > 0;
}
function tourClearHighlight() {
  for (const marked of document.querySelectorAll(".tour-highlight")) marked.classList.remove("tour-highlight");
}
function tourPlaceCard(anchorEl) {
  const card = element("tourCard");
  card.classList.remove("tour-center");
  card.style.top = ""; card.style.left = "";
  if (!anchorEl) { card.classList.add("tour-center"); return; }
  const margin = 12;
  const rect = anchorEl.getBoundingClientRect();
  const cardW = Math.min(340, window.innerWidth - 32);
  const cardH = card.offsetHeight;
  const left = Math.min(Math.max(margin, rect.left), Math.max(margin, window.innerWidth - cardW - margin));
  let top = rect.bottom + 10;
  if (top + cardH > window.innerHeight - margin) top = rect.top - cardH - 10;
  card.style.left = left + "px";
  card.style.top = Math.max(margin, top) + "px";
}
function tourTrack() {
  if (element("tourOverlay").hidden || !tourAvailable.length) return;
  const step = tourAvailable[tourPosition];
  tourPlaceCard(step.anchor ? element(step.anchor) : null);
}
function tourShow(position) {
  tourPosition = position;
  const step = tourAvailable[position];
  if (step.view) switchView(step.view);
  element("tourTitle").textContent = step.title;
  element("tourText").textContent = step.text;
  element("tourCount").textContent = (position + 1) + " of " + tourAvailable.length;
  element("tourBack").disabled = position === 0;
  element("tourNext").textContent = position === tourAvailable.length - 1 ? "Done" : "Next";
  element("tourOverlay").hidden = false;
  tourClearHighlight();
  const anchorEl = step.anchor ? element(step.anchor) : null;
  if (anchorEl && anchorEl.getClientRects().length > 0) {
    anchorEl.classList.add("tour-highlight");
    anchorEl.scrollIntoView({block: "center", behavior: reducedMotion ? "auto" : "smooth"});
  }
  tourPlaceCard(anchorEl && anchorEl.getClientRects().length > 0 ? anchorEl : null);
  element("tourNext").focus();
}
function tourEnd() {
  element("tourOverlay").hidden = true;
  tourClearHighlight();
  tourAvailable = [];
  window.removeEventListener("scroll", tourTrack, true);
  window.removeEventListener("resize", tourTrack);
  if (tourReturnFocus && tourReturnFocus.focus) tourReturnFocus.focus();
  tourReturnFocus = null;
}
function tourStart() {
  tourReturnFocus = document.activeElement;
  tourAvailable = tourSteps.filter(tourAnchorVisible);
  if (!tourAvailable.length) return;
  window.addEventListener("scroll", tourTrack, true);
  window.addEventListener("resize", tourTrack);
  tourShow(0);
}
const tourStarter = element("tourStart");
if (tourStarter) tourStarter.onclick = tourStart;
const tourStarterNav = element("tourStartNav");
if (tourStarterNav) tourStarterNav.onclick = tourStart;
const tourBack = element("tourBack");
if (tourBack) tourBack.onclick = () => { if (tourPosition > 0) tourShow(tourPosition - 1); };
const tourNext = element("tourNext");
if (tourNext) tourNext.onclick = () => {
  if (tourPosition < tourAvailable.length - 1) tourShow(tourPosition + 1);
  else tourEnd();
};
const tourSkip = element("tourSkip");
if (tourSkip) tourSkip.onclick = tourEnd;
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && tourAvailable.length && !element("tourOverlay").hidden) tourEnd();
});
function dictateShortLang(tag) {
  const short = (tag || "en-US").split("-")[0].toLowerCase();
  return short === "pl" ? "pl" : "en";
}
async function transcribeSlice(blob, index, shortLang, inputHint) {
  const form = new FormData();
  form.append("audio", blob, "slice" + index + ((blob.type || "").includes("mp4") ? ".mp4" : ".webm"));
  form.append("language", shortLang);
  if (inputHint) form.append("input", inputHint);
  const headers = {};
  if (hosted) headers["X-Operator-Request"] = "1";
  else headers["Authorization"] = "Bearer " + element("token").value;
  const options = {method: "POST", headers, credentials: "same-origin", body: form};
  if (typeof AbortSignal !== "undefined" && typeof AbortSignal.timeout === "function") {
    options.signal = AbortSignal.timeout(75000);
  }
  let response;
  try {
    response = await fetch("/api/stt/transcribe", options);
  } catch (error) {
    throw new Error("Meta transcription unreachable (" +
      (error && error.name ? error.name : "network") + ").");
  }
  if (!response.ok) {
    let detail = "";
    try { detail = (await response.json()).detail || ""; }
    catch {
      try { detail = (await response.text()).slice(0, 300); }
      catch { detail = ""; }
    }
    throw new Error("Meta transcription refused (HTTP " + response.status + ")" +
      (detail ? ": " + detail : ""));
  }
  return response.json();
}
async function dictateViaMeta(ctx) {
  const field = ctx.field, button = ctx.button, lang = ctx.lang, status = ctx.status;
  ctx.busy = true; // Set synchronously: a second click during mic prompt is ignored.
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({audio: true});
  } catch {
    ctx.busy = false;
    if (Dictation.supported()) {
      status.textContent = "Microphone unavailable for Meta recording; using browser speech instead.";
      startBrowserDictation(ctx);
    } else {
      status.textContent = "Microphone blocked. Allow mic access for this page (HTTPS required), then try again.";
    }
    return;
  }
  const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((type) => {
    try { return MediaRecorder.isTypeSupported(type); } catch { return false; }
  }) || "";
  Dictation.stopActive(); // Only one dictation session at a time.
  const base = field.value;
  const shortLang = dictateShortLang(lang.value);
  let accumulated = "";
  let slices = 0;
  let stopping = false;
  let recorder = null;
  let chain = Promise.resolve();
  let failed = null;
  function setRecording(on) {
    button.classList.toggle("recording", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Stop dictation" : "🎤 Dictate (Meta)";
  }
  function stop() {
    stopping = true;
    if (recorder && recorder.state !== "inactive") {
      try { recorder.stop(); } catch { /* Already stopped. */ }
    }
  }
  Dictation.setActive(stop);
  const dispatch = button.onclick;
  button.onclick = () => stop();
  setRecording(true);
  function recordSlice() {
    return new Promise((resolve) => {
      const chunks = [];
      try {
        recorder = mime ? new MediaRecorder(stream, {mimeType: mime}) : new MediaRecorder(stream);
      } catch {
        resolve(null);
        return;
      }
      const timer = setTimeout(() => {
        if (recorder.state !== "inactive") { try { recorder.stop(); } catch { /* Raced. */ } }
      }, META_SLICE_MS);
      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size) chunks.push(event.data);
      };
      recorder.onstop = () => {
        clearTimeout(timer);
        recorder = null;
        if (stopping) status.textContent = "Finishing Meta transcription...";
        resolve(new Blob(chunks, {type: (mime.split(";")[0] || "audio/webm")}));
      };
      recorder.onerror = () => {
        clearTimeout(timer);
        recorder = null;
        resolve(new Blob(chunks, {type: "audio/webm"}));
      };
      try { recorder.start(); }
      catch { clearTimeout(timer); recorder = null; resolve(null); }
    });
  }
  while (!stopping && !failed) {
    const slice = ++slices;
    status.textContent = "Recording with Meta (slice " + slice + ", up to 55s each)... Press Stop to finish.";
    const blob = await recordSlice();
    if (!blob) { failed = failed || new Error("Recording failed."); break; }
    if (blob.size < 1024) continue; // Instant stop; nothing worth billing.
    status.textContent = "Transcribing slice " + slice + " with Meta... Press Stop to finish.";
    chain = chain.then(async () => {
      if (failed) return;
      const result = await transcribeSlice(blob, slice, shortLang, ctx.inputHint);
      const text = ((result && result.text) || "").trim();
      if (text) {
        accumulated = accumulated ? accumulated + " " + text : text;
        field.value = joinDictation(base, accumulated);
      }
    }).catch((error) => { failed = failed || error; stop(); });
  }
  await chain;
  function finish() {
    Dictation.clearActive(stop);
    for (const track of stream.getTracks()) { try { track.stop(); } catch { /* Best-effort. */ } }
    ctx.busy = false;
    setRecording(false);
    button.onclick = dispatch;
    refreshDictationBackends();
    field.focus();
  }
  if (failed && !accumulated && Dictation.supported()) {
    finish();
    status.textContent = failed.message + " Using browser speech instead.";
    message(failed.message + " Using browser speech instead.");
    startBrowserDictation(ctx);
    return;
  }
  if (accumulated) {
    field.value = joinDictation(base, accumulated);
    const buddy = document.querySelector('[data-send-lang-for="' + field.id + '"]');
    if (buddy) buddy.value = shortLang; // Dictated text is tagged on send.
    status.textContent = "Dictation added (Meta, " + dictateLangName(lang.value) +
      (failed ? "; stopped early: " + failed.message : "") +
      "). Review and edit before sending. Nothing was sent.";
    if (failed) message(failed.message);
  } else if (failed) {
    status.textContent = failed.message + " Field unchanged.";
    field.value = base;
  } else {
    status.textContent = "No speech captured; field unchanged.";
    field.value = base;
  }
  finish();
}
function wireDictation(fieldId, buttonId, langId, statusId, noun, inputHint) {
  const row = document.querySelector('[data-dictate-for="' + fieldId + '"]');
  if (!row) return;
  if (!Dictation.supported() && !MetaStt.supported()) return; // Row stays hidden.
  const ctx = {
    row, field: element(fieldId), button: element(buttonId), lang: element(langId),
    status: element(statusId), noun, inputHint, browserTitle: element(buttonId).title,
    busy: false, rec: null, stopBrowser: null, base: "", finalText: "", lastRendered: null,
  };
  dictationWires.push(ctx);
  row.hidden = false;
  ctx.button.onclick = () => {
    if (ctx.busy) return; // A Meta session replaces this handler while recording.
    if (ctx.rec) { ctx.stopBrowser(); return; } // Toggle browser session off.
    if (MetaStt.active()) dictateViaMeta(ctx);
    else if (Dictation.supported()) startBrowserDictation(ctx);
    else {
      const hint = hosted ?
        "Transcription unavailable: attach your Meta key in Your model key for Meta dictation." :
        "Transcription unavailable: this browser cannot dictate and Meta dictation is not configured on this server.";
      ctx.status.textContent = hint;
      message(hint);
    }
  };
  refreshDictationBackends();
}
wireDictation("prompt", "dictate-prompt", "dictate-lang-prompt", "dictate-status-prompt", "request", null);
wireDictation("supervisorInput", "dictate-supervisor", "dictate-lang-supervisor", "dictate-status-supervisor", "chat message", "supervisor");
wireDictation("answer-text", "dictate-answer", "dictate-lang-answer", "dictate-status-answer", "answer", "answer");
