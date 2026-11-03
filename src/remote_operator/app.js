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
let hiddenPollTicks = 0;
let triedApproved = false;
let currentView = "demo";
let reviewContext = null; // {run_id, thread_id, thread_topic} when opened from a run.
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
element("demoReviewCta").onclick = () => switchView("review");
switchView("demo");
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
function startPolling() {
  if (progressTimer !== null || !selectedMode) return;
  progressTimer = setInterval(async () => {
    if (pollBusy) return;
    if (typeof document !== "undefined" && document.hidden) {
      hiddenPollTicks++;
      if (hiddenPollTicks % 15 !== 0) return; // Hidden tab: poll about every 15s.
    } else hiddenPollTicks = 0;
    pollBusy = true;
    try { await refresh(); await refreshExternalActions(); try { await loadSupervisorRuns(); } catch { /* Runs refresh is best-effort inside the task poll. */ } }
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
  for (const id of ["runMode", "supervisionMode", "powerDelay", "powerArm"]) element(id).disabled = selectedMode === null || connecting;
  for (const id of ["recentTasks", "refreshTasks", "load", "taskId"]) element(id).disabled = busy || connecting || selectedMode === null;
  element("approve").disabled = !active || busy || task.status !== "awaiting_approval";
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
  element("cancel").disabled = !active || ["completed", "applying", "rejected", "cancelled"].includes(task.status);
  const parked = active && task.status === "waiting_on_owner";
  element("question-block").hidden = !parked;
  if (parked) element("pending-question").textContent = task.pending_question || "(no question recorded)";
  element("send-answer").disabled = !parked || busy;
  if (typeof supervisorActions === "function") supervisorActions();
  if (typeof updateStepper === "function") updateStepper();
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
    option.value = item.id; option.textContent = item.status + " · " + item.prompt;
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
  element("approvalWindow").textContent = "Approval windows: tasks ~" + settings.task_approval_minutes + " min, plans ~" + settings.approval_window_minutes + " min. Expired approvals need a fresh validation or plan.";
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
  if (!status.running) {
    element("windowStatus").textContent = "Window: no agent activity reported.";
  } else {
    let line = "Window: " + (status.slice || "unscheduled slice");
    if (status.phase) line += " · " + status.phase;
    if (status.last_heartbeat_utc) line += " (last heartbeat " + status.last_heartbeat_utc + ")";
    if (status.note) line += " " + status.note;
    element("windowStatus").textContent = line;
  }
  const agents = Array.isArray(status.agents) ? status.agents : [];
  element("agentsStatus").textContent = agents.length ?
    "Agents: " + agents.map((a) => a.name + " (" + (a.task || "idle") + ", " + (a.status || "?") + ")").join("; ") + "." :
    "Agents: none active.";
  const health = status.drain;
  if (!health || !health.alive) {
    element("drainStatus").textContent = "Worker: NOT draining. Queued runs are stalled; restart serve on the PC.";
    return;
  }
  const ago = health.last_pass === null ? "never" : Math.max(0, Math.round(Date.now() / 1000 - health.last_pass)) + "s ago";
  element("drainStatus").textContent = "Worker: draining, last pass " + ago +
    " (passes " + health.passes + ", errors " + health.errors + ", restarts " + health.restarts + ", serve PID " + status.serve_pid + ").";
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
  const out = await api("/api/tasks/" + element("taskId").value.trim() + "/answer", "POST", {text});
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
  element("planSettingsNote").textContent = supervisorPlanNonce ?
    "Approving now: " + mode + " " + supervision :
    "Approval settings will appear here once a plan is written.";
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
    left.textContent = f.left.join("\n");
    const right = document.createElement("pre");
    right.className = "diff-col diff-new";
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
    " \u00b7 base " + short(task.base_digest) + (promptLine ? ' \u00b7 "' + promptLine + '"' : "");
  const rows = [];
  const push = (ok, text) => rows.push((ok ? "PASS " : "FAIL ") + text);
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
  checklist.replaceChildren(...rows.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
}
async function loadSupervisorRuns() {
  const session = sessionRevision;
  const payload = await api("/api/supervisor/runs");
  if (session !== sessionRevision || selectedMode === null) return;
  const all = Array.isArray(payload) ? payload : (payload.runs || []);
  const runs = supervisorThreadId ? all.filter((run) => run.thread_id === supervisorThreadId) : all;
  const list = element("supervisorRuns");
  if (!runs.length) {
    const empty = document.createElement("li");
    empty.textContent = supervisorThreadId ? "No runs for this thread yet." : "No runs yet.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...runs.map((run) => {
    const item = document.createElement("li");
    item.textContent = run.status + " · " + run.run_id.slice(0, 12) + " ";
    const review = document.createElement("button");
    review.textContent = "Review"; review.className = "secondary";
    review.onclick = () => guarded(() => supervisorStep(async () => {
      const detail = await api("/api/supervisor/runs/" + run.run_id);
      if (!detail.task_id) { supervisorNote("Run has no linked task yet; wait for the worker."); return; }
      await chooseTask(detail.task_id);
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
  if (element("supervisorThreads").value) await openSupervisorThread(element("supervisorThreads").value);
}));
element("supervisorOpen").onclick = () => guarded(() => supervisorStep(async () => {
  const topic = element("supervisorNewTopic").value.trim();
  if (!topic) { supervisorNote("Enter a thread topic first."); return; }
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
  task = null; taskEvents = []; showAllEvents = false; reviewContext = null; triedApproved = false;
  element("status").textContent = "No task selected";
  renderDiffViews("", "No candidate yet.");
  element("evidence").textContent = "Not validated.";
  element("validation-summary").textContent = "Not validated.";
  element("verdict").textContent = "No candidate yet.";
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
async function chooseTask(id) {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("Choose a saved task or provide a valid task ID.");
  reviewContext = null;
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
    "Status: " + task.status + ".";
  element("demoReviewStatus").textContent = "Task " + task.status + ": " + element("verdict").textContent;
  element("demoReviewCta").disabled = false;
  renderApprovalSubject();
  actions();
  const events = await api("/api/tasks/" + encodeURIComponent(id) + "/events");
  if (!stillSelected(id, selection, session)) return;
  taskEvents = events; renderEvents();
  element("events").scrollTop = element("events").scrollHeight;
  if (["running", "applying"].includes(task.status)) startPolling();
  else stopPolling();
  const status = task.status;
  if (previousStatus !== status && !["running", "applying"].includes(status)) await recentTasks();
  if (status === "awaiting_approval" && previousStatus !== status) {
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
  if (!task.approval) { message("This task has no valid approval yet. Run fresh validation first, then approve."); return; }
  const short = (hex) => typeof hex === "string" ? hex.slice(0, 12) : "?";
  const runner = task.evidence && task.evidence.runner ? task.evidence.runner : "not run yet";
  const summary = "Approve this change?\n\nCandidate: " + short(task.evidence && task.evidence.candidate_digest) +
    "\nBase: " + short(task.base_digest) + "\nValidated by: " + runner +
    "\nTarget: separate LOCAL demo target only — no push, no deploy." +
    "\n\nPress OK to approve, Cancel to keep reviewing.";
  if (!window.confirm(summary)) return;
  await taskAction("/approve", {nonce: task.approval.nonce}, "Verified change applied to the local demo target.");
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
  triedApproved = true;
  updateStepper();
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
  element("execMode").textContent = "Execution mode: " + (selectedMode.live_inference ? "live Meta inference" : "scripted offline reference") + ".";
  await loadSettings();
  if (session !== sessionRevision) return;
  await refreshPowerStatus();
  if (session !== sessionRevision) return;
  await refreshWindowStatus();
  if (session !== sessionRevision) return;
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
  } catch (error) {
    supervisorNote("Supervisor data unavailable: " + error.message);
  }
  const saved = element("taskId").value.trim();
  if (saved || summaries.length) {
    await chooseTask(saved || summaries[0].id);
    if (session === sessionRevision) {
      switchView("review");
      message("Connected. Your saved task is loaded; review its current status in the Review view.");
    }
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
/* ---- Dictation v1: Web Speech API textarea filler (client-only) ----
   Fills the paired textarea only; never sends. Rows stay hidden unless
   the browser exposes SpeechRecognition (standard or webkit prefix). */
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
function dictateLangName(code) { return code === "pl-PL" ? "polski" : "English"; }
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
function wireDictation(fieldId, buttonId, langId, statusId) {
  const row = document.querySelector('[data-dictate-for="' + fieldId + '"]');
  if (!Dictation.supported() || !row) return; // Row stays hidden: typed input unaffected.
  const field = element(fieldId);
  const button = element(buttonId);
  const lang = element(langId);
  const status = element(statusId);
  row.hidden = false;
  let rec = null;
  let base = "";
  let finalText = "";
  let lastRendered = null;
  function setRecording(on) {
    button.classList.toggle("recording", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
    button.textContent = on ? "Stop dictation" : "🎤 Dictate (browser speech recognition)";
  }
  function stop() {
    if (rec) { try { rec.stop(); } catch { /* stop() after end is harmless. */ } }
  }
  button.onclick = () => {
    if (rec) { stop(); return; } // Toggle off; onend commits the transcript.
    Dictation.stopActive(); // Only one dictation session at a time.
    base = field.value;
    finalText = "";
    lastRendered = null;
    status.textContent = "";
    let session;
    try {
      session = Dictation.create();
    } catch {
      status.textContent = "Dictation unavailable in this browser. Type instead.";
      return;
    }
    rec = session;
    Dictation.setActive(stop);
    session.lang = lang.value;
    session.interimResults = true;
    session.continuous = false; // Single utterance; the platform end-points on silence.
    session.maxAlternatives = 1;
    session.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const chunk = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += chunk;
        else interim += chunk;
      }
      lastRendered = joinDictation(base, finalText + interim); // Interim shown live, provisional.
      field.value = lastRendered;
    };
    session.onerror = (event) => {
      const note = dictateErrorMessage(event.error || "unknown");
      if (note) {
        status.textContent = note;
        message(note);
      }
    };
    session.onend = () => {
      Dictation.clearActive(stop);
      const captured = finalText.trim() !== "";
      if (captured) {
        // If the user typed during dictation, append to their edit instead of clobbering it.
        field.value = (field.value === lastRendered || field.value === base) ?
          joinDictation(base, finalText) : joinDictation(field.value, finalText);
        status.textContent = "Dictation added (" + dictateLangName(lang.value) +
          "). Review and edit before sending. Nothing was sent.";
      } else if (status.textContent === "") {
        status.textContent = "No speech captured; field unchanged.";
        field.value = base;
      } else if (lastRendered !== null) {
        field.value = base; // Partial interim was shown; restore the pre-dictation text.
      } // Else an error note is already shown; keep it and leave the field alone.
      rec = null;
      setRecording(false);
      field.focus();
    };
    setRecording(true);
    status.textContent = "Listening (" + dictateLangName(lang.value) + ")... Speak now; press Stop or pause to finish.";
    try {
      session.start();
    } catch (error) {
      Dictation.clearActive(stop);
      rec = null;
      setRecording(false);
      field.value = base;
      status.textContent = "Could not start dictation (" +
        (error && error.message ? error.message : "mic unavailable") +
        "). Check mic permission and HTTPS, then try again.";
    }
  };
}
wireDictation("prompt", "dictate-prompt", "dictate-lang-prompt", "dictate-status-prompt");
wireDictation("supervisorInput", "dictate-supervisor", "dictate-lang-supervisor", "dictate-status-supervisor");
wireDictation("answer-text", "dictate-answer", "dictate-lang-answer", "dictate-status-answer");
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
   text: "Four steps to a working session: demo access code, judge account, spending limits, then your Meta key — or continue with the free scripted reference. The progress line shows where you are."},
  {anchor: "sampleReview", view: "demo", title: "Sample review",
   text: "Load a ready-made validated task to practice the review loop. Below, the CSV gallery (happy path, edge cases, all invalid) gives one-click inputs for trying the feature."},
  {anchor: "approve", view: "review", title: "The one approval place",
   text: "Read the diff and the validation report, then approve. Approval applies this candidate only to the separate local demo target — never a push or deploy."},
  {anchor: "tryApproved", view: "review", title: "Try the approved feature",
   text: "Runs the new importer on sample data and opens Demo step 3 with accepted issues and row errors — proof the approved code works."},
  {anchor: "inboxBell", view: null, title: "Inbox bell",
   text: "Owner messages and parked questions needing you land here. The count badge jumps you to the newest unread item."}
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
