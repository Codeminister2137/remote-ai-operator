// Optional dependency-free browser check using Node 24's WebSocket and Edge/Chromium CDP.
import fs from "node:fs/promises";
const config = JSON.parse(await fs.readFile(process.argv[2], "utf8"));
const targets = await (await fetch(`http://127.0.0.1:${config.debugPort}/json`)).json();
const target = targets.find((item) => item.type === "page");
if (!target) throw new Error("No owned browser page");
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {socket.addEventListener("open", resolve, {once:true}); socket.addEventListener("error", reject, {once:true});});
let serial = 0;
const pending = new Map();
const exceptions = [];
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.method === "Runtime.exceptionThrown") exceptions.push(message.params.exceptionDetails.text);
  if (!message.id) return;
  const operation = pending.get(message.id);
  if (!operation) return;
  pending.delete(message.id); clearTimeout(operation.timer);
  if (message.error) operation.reject(new Error(JSON.stringify(message.error))); else operation.resolve(message.result);
});
function send(method, params = {}) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {pending.delete(id); reject(new Error("CDP deadline: " + method));}, 20000);
    pending.set(id, {resolve, reject, timer}); socket.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const result = await send("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true});
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
}
async function waitFor(expression) {
  const deadline = Date.now() + 35000;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Page condition deadline: " + expression);
}
try {
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", {width:1100, height:950, deviceScaleFactor:1, mobile:false});
  await send("Page.navigate", {url:config.url});
  await waitFor("document.readyState === 'complete' && document.getElementById('submit')");
  await evaluate("document.getElementById('token').value = " + JSON.stringify(config.token));
  await evaluate("document.getElementById('connect').click()");
  await waitFor("!document.getElementById('submit').disabled");
  if (!(await evaluate("document.querySelector('.notice').textContent.includes('SCRIPTED OFFLINE')"))) throw new Error("Expected explicit offline mode");
  await evaluate("document.getElementById('submit').click()");
  await waitFor("document.getElementById('status').textContent === 'awaiting_approval' && !document.getElementById('approve').disabled");
  await evaluate("document.getElementById('approve').click()");
  await waitFor("document.getElementById('status').textContent === 'completed' && !document.getElementById('preview').disabled");
  await evaluate("document.getElementById('preview').click()");
  await waitFor("document.getElementById('issues').children.length === 2 && document.getElementById('import-errors').children.length === 2");
  for (const [label, width, height, mobile] of [["desktop",1100,950,false], ["mobile",390,844,true]]) {
    await send("Emulation.setDeviceMetricsOverride", {width,height,deviceScaleFactor:1,mobile});
    await new Promise((resolve) => setTimeout(resolve, 150));
    const overflow = await evaluate("document.documentElement.scrollWidth > window.innerWidth");
    if (overflow) throw new Error(label + " page has horizontal overflow");
    const pageHeight = await evaluate("document.documentElement.scrollHeight");
    const shot = await send("Page.captureScreenshot", {format:"png",captureBeyondViewport:true,
      clip:{x:0,y:0,width,height:pageHeight,scale:1}});
    await fs.writeFile(`${config.output}/${label}.png`, Buffer.from(shot.data,"base64"));
  }
  await evaluate("document.getElementById('csv').value = " + JSON.stringify("id,title,status\nx,<img src=x onerror=window.pwned=true>,open\n"));
  await evaluate("document.getElementById('preview').click()");
  await waitFor("document.getElementById('issues').children.length === 1");
  if (await evaluate("Boolean(document.querySelector('#issues img')) || Boolean(window.pwned)")) throw new Error("Sample data was interpreted as executable HTML");
  const savedTask = await evaluate("location.hash");
  if (!/^#[a-f0-9]{32}$/.test(savedTask)) throw new Error("Missing bookmarkable task");
  await evaluate(`(async () => {
    const originalFetch = window.fetch;
    window.fetch = async (...args) => {
      if (typeof args[0] === 'string' && args[0].endsWith('/events')) return new Response(JSON.stringify(
        Array.from({length:35}, (_,i) => ({sequence:100000+i,kind:'tool_completed',payload:{name:'synthetic-'+i}}))
      ), {status:200,headers:{'Content-Type':'application/json'}});
      return originalFetch(...args);
    };
    try { await refresh(); } finally { window.fetch = originalFetch; }
  })()`);
  if (!(await evaluate("document.getElementById('events').children.length === 20 && document.getElementById('events').firstChild.textContent.startsWith('16 · ') && !document.getElementById('events').textContent.includes('100000')"))) throw new Error('History must show bounded task-local activity numbers');
  await evaluate("document.getElementById('eventHistory').click()");
  if (!(await evaluate("document.getElementById('events').children.length === 35 && document.getElementById('events').firstChild.textContent.startsWith('1 · ')"))) throw new Error('Full task history unavailable');
  await evaluate("document.getElementById('newTask').click()");
  if (!(await evaluate("document.getElementById('taskId').value === '' && location.hash === '' && document.getElementById('events').children.length === 0 && document.getElementById('issues').children.length === 0 && document.getElementById('diff').textContent === 'No candidate yet.' && document.getElementById('approve').disabled && !document.getElementById('submit').disabled"))) throw new Error('New draft retains old progress or approval');
  await evaluate("document.getElementById('refreshTasks').click()");
  await waitFor("document.getElementById('recentTasks').options.length > 1");
  if (await evaluate("document.getElementById('taskId').value !== ''")) throw new Error('Refreshing saved tasks exits draft');
  await evaluate("chooseTask(" + JSON.stringify(savedTask.slice(1)) + ")");
  await evaluate("document.getElementById('recentTasks').value = ''; document.getElementById('recentTasks').dispatchEvent(new Event('change'))");
  if (!(await evaluate("document.getElementById('events').children.length === 0 && document.getElementById('status').textContent === 'No task selected'"))) throw new Error('Empty chooser retains old progress');
  await evaluate("chooseTask(" + JSON.stringify(savedTask.slice(1)) + ")");
  console.log('PASS: new task draft clears progress/evidence/results; saved-task refresh preserves draft; task-local bounded history expands');
  await send("Page.reload");
  await waitFor("document.readyState === 'complete' && document.getElementById('connect')");
  if (await evaluate("document.getElementById('token').value !== ''")) throw new Error("Credential persisted after reload");
  await evaluate("document.getElementById('token').value = " + JSON.stringify(config.token));
  await evaluate("document.getElementById('connect').click()");
  await waitFor("document.getElementById('status').textContent === 'completed' && !document.getElementById('preview').disabled");
  if (await evaluate("location.hash") !== savedTask) throw new Error("Reconnect changed task");
  if (await evaluate("document.getElementById('taskId').closest('details').open")) throw new Error("Manual recovery unexpectedly required");
  // Fresh-root reconnect must discover saved tasks without an ID/bookmark.
  const newest = await evaluate("api('/api/tasks', 'POST', {prompt: '<img src=x onerror=window.pwned=true> ' + 'long title '.repeat(20)}).then(task => task.id)");
  await send("Page.navigate", {url:config.url});
  await waitFor("document.readyState === 'complete' && document.getElementById('recentTasks')");
  if (!(await evaluate("document.getElementById('recentTasks').disabled"))) throw new Error("Chooser enabled before owner connection");
  await evaluate("document.getElementById('token').value = " + JSON.stringify(config.token));
  await evaluate("document.getElementById('connect').click()");
  await waitFor("document.getElementById('taskId').value === " + JSON.stringify(newest) + " && document.getElementById('status').textContent === 'queued'");
  if (await evaluate("Boolean(document.querySelector('#recentTasks img')) || Boolean(window.pwned)")) throw new Error("Task prompt interpreted as HTML");
  if (await evaluate("document.documentElement.scrollWidth > window.innerWidth")) throw new Error("Recent task chooser causes mobile overflow");

  // Synthetic running metadata on first read proves reconnect polling; host task remains completed.
  const originalID = savedTask.slice(1);
  await evaluate(`(() => {
    const original = window.fetch;
    window.__reconnectReads = 0;
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (args[0] === ${JSON.stringify('/api/tasks/' + originalID)}) {
        window.__reconnectReads++;
        if (window.__reconnectReads === 1) {
          const body = await response.json(); body.task.status = 'running';
          return new Response(JSON.stringify(body), {status:200, headers:{'Content-Type':'application/json'}});
        }
      }
      return response;
    };
  })()`);
  await evaluate("document.getElementById('recentTasks').value = " + JSON.stringify(originalID) + "; document.getElementById('recentTasks').dispatchEvent(new Event('change'))");
  await waitFor("window.__reconnectReads >= 2 && document.getElementById('status').textContent === 'completed'");

  // A late response from the old selection must not overwrite the selected task's events.
  await evaluate(`(() => {
    const original = window.fetch; let first = true;
    window.__oldEventsPending = false; window.__oldEventsReleased = false;
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (first && args[0] === ${JSON.stringify('/api/tasks/' + originalID + '/events')}) {
        first = false; window.__oldEventsPending = true;
        await new Promise(resolve => {window.__releaseOldEvents = resolve;});
        window.__oldEventsReleased = true;
        return new Response(JSON.stringify([{sequence:999,kind:'STALE_EVENT_SENTINEL',payload:{}}]), {status:200,headers:{'Content-Type':'application/json'}});
      }
      return response;
    };
  })()`);
  await evaluate("document.getElementById('recentTasks').value = " + JSON.stringify(originalID) + "; document.getElementById('recentTasks').dispatchEvent(new Event('change'))");
  await waitFor("window.__oldEventsPending === true");
  await evaluate("document.getElementById('recentTasks').value = " + JSON.stringify(newest) + "; document.getElementById('recentTasks').dispatchEvent(new Event('change'))");
  await waitFor("document.getElementById('taskId').value === " + JSON.stringify(newest) + " && document.getElementById('status').textContent === 'queued'");
  await evaluate("window.__releaseOldEvents()");
  await waitFor("window.__oldEventsReleased === true");
  await new Promise(resolve => setTimeout(resolve, 150));
  if (await evaluate("document.getElementById('events').textContent.includes('STALE_EVENT_SENTINEL')")) throw new Error("Old events overwrote new selection");
  if (!(await evaluate("document.getElementById('status').textContent === 'queued'"))) throw new Error("Old task overwrote new selection");
  // All following recovery states are synthetic fixtures on the scripted server.
  const selectFixture = async (id) => evaluate("document.getElementById('recentTasks').value = " + JSON.stringify(id) + "; document.getElementById('recentTasks').dispatchEvent(new Event('change'))");
  await selectFixture(config.recoveryTask);
  await waitFor("document.getElementById('status').textContent === 'reconciliation_required' && !document.getElementById('reconcile').disabled");
  await evaluate("document.getElementById('reconcile').click()");
  await waitFor("document.getElementById('status').textContent === 'paused' && !document.getElementById('resume').disabled");
  const recovered = await evaluate("api('/api/tasks/' + " + JSON.stringify(config.recoveryTask) + ")");
  const recoveredEvents = await evaluate("api('/api/tasks/' + " + JSON.stringify(config.recoveryTask) + " + '/events')");
  if (recovered.task.pending !== null || recovered.task.history.length !== 1 || recovered.task.applied_digest !== null) throw new Error('Reconciliation changed more than observed state');
  if (recoveredEvents.some(event => event.kind === 'started')) throw new Error('Inspection automatically started execution');

  await selectFixture(config.mismatchTask);
  await waitFor("document.getElementById('status').textContent === 'reconciliation_required' && !document.getElementById('reconcile').disabled");
  await evaluate("document.getElementById('reconcile').click()");
  await waitFor("document.getElementById('message').textContent.includes('manual inspection') && !document.getElementById('reconcile').disabled");
  if (!(await evaluate("document.getElementById('status').textContent === 'reconciliation_required' && document.getElementById('resume').disabled"))) throw new Error('Uncertain source was silently resumed');

  await selectFixture(config.applyingTask);
  await waitFor("document.getElementById('status').textContent === 'applying' && !document.getElementById('reconcile').disabled");
  await evaluate("document.getElementById('reconcile').click()");
  await waitFor("document.getElementById('status').textContent === 'completed' && !document.getElementById('preview').disabled");
  await evaluate("document.getElementById('preview').click()");
  await waitFor("document.getElementById('issues').children.length === 2 && document.getElementById('import-errors').children.length === 2");

  await selectFixture(config.recoveryTask);
  await waitFor("document.getElementById('status').textContent === 'paused' && !document.getElementById('resume').disabled");
  // Expired Meta metadata fixture only: the backend remains scripted/no paid calls.
  await evaluate(`(() => {
    const original = window.fetch;
    window.__budgetRunPosts = 0;
    window.fetch = async (...args) => {
      if (args[0] === '/api/config') return new Response(JSON.stringify({mode:'meta',live_inference:true,https_origin:null}), {status:200,headers:{'Content-Type':'application/json'}});
      if (args[0] === '/api/allowance') return new Response(JSON.stringify({enabled:true,currency:'USD',limit_usd:'1.00',reserved_usd_estimate:'0.75',remaining_usd_estimate:'0.25',requests_used:2,requests_limit:4,expires_at:1,expired:true}), {status:200,headers:{'Content-Type':'application/json'}});
      if (typeof args[0] === 'string' && args[0].endsWith('/run') && args[1]?.method === 'POST') window.__budgetRunPosts++;
      return original(...args);
    };
  })()`);
  await evaluate("document.getElementById('connect').click()");
  await waitFor("document.getElementById('allowance-note').textContent.includes('Allowance expired') && !document.getElementById('refreshAllowance').disabled");
  if (!(await evaluate("document.getElementById('submit').disabled && document.getElementById('resume').disabled && !document.getElementById('revalidate').disabled"))) throw new Error('Expired allowance did not isolate inference from local review');
  if (!(await evaluate("document.getElementById('allowance-summary').textContent.includes('$0.2500') && document.getElementById('allowance-note').textContent.includes('not provider credit balance')"))) throw new Error('Spending scope/estimate label missing');
  await evaluate("document.getElementById('submit').onclick(); document.getElementById('resume').onclick()");
  if (await evaluate("window.__budgetRunPosts !== 0")) throw new Error('Expired UI starts inference');
  console.log('PASS: interrupted edit/apply reconciliation, mismatched-effect refusal, read-only/expired allowance UI (explicit synthetic fixtures)');
  await evaluate("document.getElementById('token').value = ''; document.getElementById('token').dispatchEvent(new Event('input'))");
  if (!(await evaluate("document.getElementById('recentTasks').disabled && document.getElementById('approve').disabled && document.getElementById('submit').disabled"))) throw new Error("Credential change leaves task controls active");
  if (!(await evaluate("document.getElementById('allowance-summary').textContent.includes('Connect to view') && document.getElementById('refreshAllowance').disabled"))) throw new Error("Credential change retains budget controls");
  console.log("PASS: saved-task discovery without ID, inert task prompt, reconnect polling fixture, stale-response refusal, credential invalidation");
  if (exceptions.length) throw new Error("Browser exceptions: " + exceptions.join(", "));
  console.log("PASS: real browser task, recovery, approval and isolated sample; desktop/mobile layouts have no horizontal overflow");
} finally {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({id:++serial,method:"Browser.close"}));
  await new Promise((resolve) => {socket.addEventListener("close",resolve,{once:true});setTimeout(resolve,2000);});
  socket.close();
}
