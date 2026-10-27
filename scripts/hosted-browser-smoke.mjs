// Optional hosted-session browser check using Node 24 fetch and Edge/Chromium CDP.
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
const json = {headers:{"Content-Type":"application/json"}};
async function visitorSession() {
  const response = await fetch(config.origin + "/api/session", {method:"POST",
    headers:{...json.headers, "X-Operator-Request":"1"}, body:JSON.stringify({code:config.accessCode})});
  if (response.status !== 200) throw new Error("Second visitor session refused: " + response.status);
  const cookie = response.headers.getSetCookie()[0].split(";")[0];
  if (!cookie.startsWith("operator_session=")) throw new Error("Session cookie missing");
  if (!/;\s*HttpOnly/i.test(response.headers.getSetCookie()[0])) throw new Error("Session cookie is not HttpOnly");
  return cookie;
}
try {
  await send("Page.enable"); await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", {width:1100, height:950, deviceScaleFactor:1, mobile:false});
  await send("Page.navigate", {url:config.origin + "/"});
  await waitFor("document.readyState === 'complete' && document.getElementById('visitor-access') && !document.getElementById('visitor-access').hidden");
  if (!(await evaluate("document.getElementById('owner-access').hidden"))) throw new Error("Hosted page exposes the owner token field");
  if (!(await evaluate("document.getElementById('key-controls').hidden"))) throw new Error("Hosted page offers a key without a configured model route");
  if (!(await evaluate("document.getElementById('key-note').textContent.includes('No Meta model route')"))) throw new Error("Missing no-route explanation for the key section");
  if (!(await evaluate("document.getElementById('submit').disabled"))) throw new Error("Hosted work is enabled before admission");
  if (!(await evaluate("document.querySelector('.notice').textContent.includes('access code')"))) throw new Error("Hosted page does not announce the access-code step");

  await evaluate("document.getElementById('accessCode').value = 'wrong-code-value-123456'");
  await evaluate("document.getElementById('enterSession').click()");
  await waitFor("document.getElementById('message').textContent.includes('Access code refused')");
  if (await evaluate("document.getElementById('message').textContent.includes(" + JSON.stringify(config.accessCode) + ")")) throw new Error("Refusal echoed the access code");
  if (!(await evaluate("document.getElementById('submit').disabled && !document.getElementById('visitor-access').hidden"))) throw new Error("Refused access left work enabled");
  if (await evaluate("document.cookie.includes('operator_session')")) throw new Error("Session cookie is readable from page script");
  console.log("PASS: hosted page defaults to labelled scripted mode and refuses a wrong access code");

  await evaluate("document.getElementById('accessCode').value = " + JSON.stringify(config.accessCode));
  await evaluate("document.getElementById('enterSession').click()");
  await waitFor("!document.getElementById('submit').disabled");
  if (!(await evaluate("document.querySelector('.notice').textContent.includes('SCRIPTED OFFLINE')"))) throw new Error("Session did not start on the offline reference");
  if (await evaluate("document.getElementById('accessCode').value !== ''")) throw new Error("Access code retained in the page field");
  await evaluate("document.getElementById('submit').click()");
  await waitFor("document.getElementById('status').textContent === 'awaiting_approval' && !document.getElementById('approve').disabled");
  const taskId = await evaluate("document.getElementById('taskId').value.trim()");
  if (!/^[a-f0-9]{32}$/.test(taskId)) throw new Error("Hosted task is not bookmarkable");
  await evaluate("document.getElementById('approve').click()");
  await waitFor("document.getElementById('status').textContent === 'completed' && !document.getElementById('preview').disabled");
  await evaluate("document.getElementById('preview').click()");
  await waitFor("document.getElementById('issues').children.length === 2 && document.getElementById('import-errors').children.length === 2");
  console.log("PASS: hosted visitor session ran the scripted task, approved and exercised the sample");

  const other = await visitorSession();
  const foreign = await fetch(config.origin + "/api/tasks/" + taskId, {headers:{Cookie: other}});
  if (foreign.status !== 404) throw new Error("A second visitor read the first visitor's task: " + foreign.status);
  const foreignList = await (await fetch(config.origin + "/api/tasks", {headers:{Cookie: other}})).json();
  if (foreignList.length !== 0) throw new Error("A second visitor saw another visitor's task list");
  const noHeader = await fetch(config.origin + "/api/tasks", {method:"POST", headers:{Cookie: other, "Content-Type":"application/json"}, body:JSON.stringify({prompt:"unprotected"})});
  if (noHeader.status !== 403) throw new Error("Session POST without the explicit request header was accepted: " + noHeader.status);
  const noRoute = await fetch(config.origin + "/api/session/key", {method:"POST", headers:{Cookie: other, "X-Operator-Request":"1", "Content-Type":"application/json"}, body:JSON.stringify({api_key:"synthetic-key-not-real"})});
  if (noRoute.status !== 409) throw new Error("Key attach was accepted without a configured model route: " + noRoute.status);
  console.log("PASS: sessions are isolated, the request header is required and key attach is refused without a model route");

  await send("Page.reload");
  await waitFor("document.readyState === 'complete' && !document.getElementById('submit').disabled");
  await waitFor("document.getElementById('status').textContent === 'completed'");
  if (!(await evaluate("document.getElementById('taskId').value.trim() === " + JSON.stringify(taskId) + " && location.hash === '#" + taskId + "'"))) throw new Error("Session reload lost the visitor's task");
  for (const [label, width, height, mobile] of [["desktop",1100,950,false], ["mobile",390,844,true]]) {
    await send("Emulation.setDeviceMetricsOverride", {width,height,deviceScaleFactor:1,mobile});
    await new Promise((resolve) => setTimeout(resolve, 150));
    if (await evaluate("document.documentElement.scrollWidth > window.innerWidth")) throw new Error(label + " page has horizontal overflow");
    const pageHeight = await evaluate("document.documentElement.scrollHeight");
    const shot = await send("Page.captureScreenshot", {format:"png",captureBeyondViewport:true,
      clip:{x:0,y:0,width,height:pageHeight,scale:1}});
    await fs.writeFile(`${config.output}/${label}.png`, Buffer.from(shot.data,"base64"));
  }
  if (!(await evaluate("localStorage.length === 0 && sessionStorage.length === 0"))) throw new Error("Session state was written to browser storage");

  await evaluate("document.getElementById('leaveSession').click()");
  await waitFor("document.readyState === 'complete' && !document.getElementById('visitor-access').hidden && document.getElementById('submit').disabled");
  if (await evaluate("api('/api/config').then((configuration) => configuration.authenticated)")) throw new Error("Leave demo did not close the server session");
  if (await evaluate("document.getElementById('status').textContent !== 'No task selected'")) throw new Error("Leave demo retained the previous task view");
  console.log("PASS: reload keeps the session, browser storage stays empty and leaving the demo closes it");
  if (exceptions.length) throw new Error("Browser exceptions: " + exceptions.join(", "));
  console.log("PASS: hosted access, session isolation, scripted task and session exit; desktop/mobile layouts have no horizontal overflow");
} finally {
  if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({id:++serial,method:"Browser.close"}));
  await new Promise((resolve) => {socket.addEventListener("close",resolve,{once:true});setTimeout(resolve,2000);});
  socket.close();
}
