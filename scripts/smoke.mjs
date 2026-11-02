// Browser smoke test, in four parts, all in headless Chrome:
//   1. Self-test: open dist/index.html?selftest=1 and read window.__selftest.
//      The page runs every reference solution (must pass) and every starter
//      (must not pass) inside real Pyodide.
//   2. UI walk: open every screen like a learner. Focus and live region after each
//      route, Run and Submit rules, attempt counting (comments and blank lines do not
//      count, an earlier Submit does not count again), hints, visible lock text, solution
//      gate, Data tab (data_code only, never the carry code), quiz rules, takeaways,
//      videos, the completion page, the home page and the merge of saved progress.
//   3. Phone width: no sideways scroll on home, exercise, quiz, takeaways, completion.
//   4. Failure paths: a copy of the page with both CDNs unreachable. The editor must
//      fall back to a textarea, and the page must show the Python error notice with Retry.
//
// Usage: node smoke.mjs <path/to/index.html> <chrome-binary> <report.json>
// No npm packages: uses Node's built-in fetch and WebSocket (Node >= 22).

import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { readFile, writeFile, mkdtemp, rm, mkdir } from "node:fs/promises";
import { dirname, join, extname } from "node:path";
import { tmpdir } from "node:os";

const [, , pagePath, chrome, reportPath] = process.argv;
const TIMEOUT_MS = 180_000;   // wait for the self-test result
const TOTAL_MS = 330_000;     // hard limit for the whole run; cb.py stops smoke at 420 s
const startedAt = Date.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
  ".wasm": "application/wasm", ".zip": "application/zip", ".whl": "application/zip" };
const root = dirname(pagePath);
const server = createServer(async (req, res) => {
  try {
    const p = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (p === "/broken.html") {
      // The same page with both CDNs pointed at a closed port: the failure-path test uses it.
      const html = (await readFile(join(root, "index.html"), "utf8"))
        .replaceAll("https://cdn.jsdelivr.net/", "http://127.0.0.1:9/").replaceAll("https://cdnjs.cloudflare.com/", "http://127.0.0.1:9/");
      res.writeHead(200, { "content-type": types[".html"] }).end(html);
      return;
    }
    const file = join(root, p === "/" ? "index.html" : p);
    if (!file.startsWith(root)) { res.writeHead(403).end(); return; }
    const body = await readFile(file);
    res.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" }).end(body);
  } catch { res.writeHead(404).end(); }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;

const profile = await mkdtemp(join(tmpdir(), "cb-chrome-"));
const proc = spawn(chrome, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });

let wsUrl = null;
proc.stderr.on("data", (d) => {
  const m = /DevTools listening on (ws:\/\/\S+)/.exec(String(d));
  if (m) wsUrl = m[1];
});


const UI_WALK = (deadline) => `(async () => {
  const DEADLINE = ${deadline};
  const D = JSON.parse(document.getElementById("course-data").textContent);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = fn(); } catch (e) {} if (v) return v; await sleep(40); } return null; };
  const problems = []; const bad = (m) => { if (problems.length < 40) problems.push(m); };
  const stats = { exercises: 0, quizzes: 0, takeaways: 0, learn: 0, editor: null, hint_gate_checked: false, solution_gate_checked: false };
  const $ = (s) => document.querySelector(s);
  const app = $("#app");
  async function go(tag) {
    location.hash = tag === "home" ? "" : "#" + tag;
    const ok = await until(() => app.dataset.screen === tag, 30000);
    if (!ok) bad("screen did not render: " + tag);
    else {
      const h = app.querySelector("h1");
      if (!h) bad(tag + ": screen has no h1");
      else {
        if (document.activeElement !== h) bad(tag + ": focus is not on the screen heading");
        if (h.getAttribute("tabindex") !== "-1") bad(tag + ": heading has no tabindex -1");
        const lv = $("#route-live");
        if (!lv || lv.getAttribute("aria-live") !== "polite") bad(tag + ": no polite live region");
        else if (!lv.textContent.trim() || lv.textContent !== h.textContent) bad(tag + ": live region does not say the screen title: " + lv.textContent);
      }
    }
    return !!ok;
  }
  function setCode(code) {
    const c = $("#editor-host .CodeMirror");
    if (c && c.CodeMirror) { c.CodeMirror.setValue(code); return "codemirror"; }
    const ta = $("#editor-host textarea"); ta.value = code; ta.dispatchEvent(new Event("input")); return "textarea";
  }
  const status = () => $("#console-status");
  const settled = () => status() && status().dataset.kind !== "idle" && !/(Running|Checking)/.test(status().textContent);
  async function press(id) {
    await until(() => { const b = $(id); return b && !b.disabled; }, 60000);
    status().dataset.kind = "idle"; status().textContent = "Running…";
    $(id).click();
    await until(settled, 30000);
  }
  const attemptsText = () => ($("#attempts") || {}).textContent;
  const deepKinds = {};
  let firstEx = true;
  async function doExercise(tag, e, M) {
    stats.exercises++;
    const deep = !deepKinds[e.kind]; deepKinds[e.kind] = true;
    await until(() => $("#run-btn") && !$("#run-btn").disabled, 90000);
    stats.editor = setCode(e.starter_code);
    if (deep) {
    await press("#run-btn");
    if (/Correct/i.test(status().textContent)) bad(tag + ": Run said Correct");
    if (attemptsText() !== "0 attempts") bad(tag + ": Run counted an attempt: " + attemptsText());
    await press("#submit-btn");
    if (status().dataset.kind === "pass") bad(tag + ": the starter code passed Submit");
    if (attemptsText() !== "0 attempts") bad(tag + ": Submit of the starter counted: " + attemptsText());
    if (!stats.print_checked) {
      stats.print_checked = true;
      setCode('print("cb-smoke")'); await press("#run-btn");
      if (!/cb-smoke/.test(($("#console-printed") || {}).textContent || "")) bad(tag + ": printed output not shown in the console");
      if (attemptsText() !== "0 attempts") bad(tag + ": Run of print code counted an attempt");
    }
    // Friction rule: hint k unlocks after k counted attempts. No hint before a real try.
    const hb = $("#hint-btn");
    if (!hb) bad(tag + ": no hint button");
    else if (!hb.disabled) bad(tag + ": hint 1 is open before any counted attempt");
    // Visible lock text (not only a tooltip) while hint 1 and the solution are locked.
    const hl = $("#hint-lock"), sl = $("#sol-lock");
    if (!hl || hl.hidden || !/Hint 1/.test(hl.textContent)) bad(tag + ": no visible lock text for hint 1");
    if (!sl || sl.hidden || !/solution/i.test(sl.textContent)) bad(tag + ": no visible lock text for the solution");
    else stats.lock_text_checked = true;
    // A comment-only or blank-line change is not a new attempt.
    setCode(e.starter_code + "\\n# only a comment\\n\\n   \\n");
    await press("#submit-btn");
    if (attemptsText() !== "0 attempts") bad(tag + ": comment-only Submit counted: " + attemptsText());
    setCode(e.starter_code + "\\n_cb_n = 1\\n");
    await press("#submit-btn");
    if (attemptsText() !== "1 attempt") bad(tag + ": changed Submit not counted: " + attemptsText());
    await press("#submit-btn");
    if (attemptsText() !== "1 attempt") bad(tag + ": same code counted twice: " + attemptsText());
    setCode(e.starter_code + "\\n_cb_n = 1   \\n# note\\n");
    await press("#submit-btn");
    if (attemptsText() !== "1 attempt") bad(tag + ": same code with a comment and trailing spaces counted twice: " + attemptsText());
    if (hl && !hl.hidden && e.hints_html.length > 1 && !/Hint 2/.test(hl.textContent)) bad(tag + ": lock text does not name hint 2: " + hl.textContent);
    if ($("#hint-btn").disabled) bad(tag + ": hint 1 closed after 1 counted attempt");
    else { $("#hint-btn").click(); await sleep(50); }
    if ($("#hint-lock") && e.hints_html.length > 1 && ($("#hint-lock").hidden || !/Hint 2/.test($("#hint-lock").textContent))) bad(tag + ": no visible lock text for hint 2 after hint 1");
    if (document.querySelectorAll(".hint").length !== 1) bad(tag + ": hint 1 did not show");
    if (e.hints_html.length > 1) { if (!$("#hint-btn").disabled) bad(tag + ": hint 2 open after only 1 counted attempt"); else stats.hint_gate_checked = true; }
    if (firstEx && e.hints_html.length === 3) {
      firstEx = false;
      setCode(e.starter_code + "\\n_cb_n = 2\\n"); await press("#submit-btn");
      if ($("#hint-btn").disabled) bad(tag + ": hint 2 closed after 2 attempts"); else { $("#hint-btn").click(); await sleep(50); }
      if ($("#sol-btn").disabled === false) bad(tag + ": solution open before 3 attempts");
      setCode(e.starter_code + "\\n_cb_n = 3\\n"); await press("#submit-btn");
      if (attemptsText() !== "3 attempts") bad(tag + ": expected 3 attempts: " + attemptsText());
      setCode(e.starter_code + "\\n_cb_n = 1\\n"); await press("#submit-btn");
      if (attemptsText() !== "3 attempts") bad(tag + ": an earlier Submit counted again: " + attemptsText());
      if ($("#sol-btn").disabled === false) bad(tag + ": solution open before all hints are shown");
      if ($("#hint-btn").disabled) bad(tag + ": hint 3 closed after 3 attempts"); else { $("#hint-btn").click(); await sleep(50); }
      if ($("#sol-btn").disabled) bad(tag + ": solution closed after 3 attempts and all hints");
      else {
        if ($("#sol-lock") && !$("#sol-lock").hidden) bad(tag + ": lock text still visible after the solution unlocked");
        $("#sol-btn").click(); await sleep(50);
        if (!$("#sol-confirm")) bad(tag + ": no try-once-more confirm");
        else { $("#sol-confirm").click(); await sleep(50); if (!$(".solution pre") || $(".solution pre").textContent !== e.solution_code) bad(tag + ": solution text wrong"); else stats.solution_gate_checked = true; }
      }
    }
    }
    setCode(e.solution_code);
    if (deep) {
      await press("#run-btn");
      if (status().dataset.kind !== "ran" || !/Ran without errors/.test(status().textContent)) bad(tag + ": Run of the solution: " + status().textContent);
    }
    await press("#submit-btn");
    if (status().dataset.kind !== "pass") bad(tag + ": solution failed Submit: " + status().textContent + " " + ($("#console-msg") || {}).textContent);
    if (!/^Next/.test($("#next-btn").textContent) && !/Finish|Next module/.test($("#next-btn").textContent)) bad(tag + ": pager did not change to Next: " + $("#next-btn").textContent);
    $("#tab-data").click(); await sleep(60);
    const panel = $("#panel-data");
    if (panel.hidden) bad(tag + ": Data tab did not open");
    else {
      const txt = (panel.querySelector(".CodeMirror") ? panel.querySelector(".CodeMirror").CodeMirror.getValue() : (panel.querySelector("textarea") || {}).value) || "";
      if (typeof e.data_code !== "string") bad(tag + ": exercise has no data_code");
      if ("carry_code" in e) bad(tag + ": carry_code is in the page data");
      const shown = typeof e.data_code === "string" ? e.data_code : e.setup_code;
      if (shown && txt !== shown) bad(tag + ": Data tab shows the wrong code");
      if (!shown && !/no setup code/.test(txt)) bad(tag + ": empty Data tab has no note");
      // setup_code = data_code + carry code. The carry part must not show.
      if (typeof e.data_code === "string" && e.setup_code.length > e.data_code.length && e.setup_code.startsWith(e.data_code)) {
        stats.carry_exercises = (stats.carry_exercises || 0) + 1;
        const carry = e.setup_code.slice(e.data_code.length).trim();
        if (carry && txt.includes(carry)) bad(tag + ": Data tab shows the carry code");
      }
      if (!/runs before yours/.test(panel.textContent)) bad(tag + ": Data note missing");
    }
    $("#tab-code").click(); await sleep(30);
  }
  async function doQuiz(tag, M, idx) {
    M.quiz.forEach((q, qi) => { stats.quizzes++; });
    for (let qi = 0; qi < M.quiz.length; qi++) {
      const q = M.quiz[qi];
      const radios = () => Array.from(document.querySelectorAll('input[name="q-' + q.id + '"]'));
      const fb = () => radios()[0].closest("fieldset").querySelector(".q-fb");
      const check = () => $("#check-" + q.id);
      if (radios().length !== q.options.length) { bad(tag + ": option count wrong for " + q.id); continue; }
      if (qi % 2 === 0) {
        const wrong = q.options.map((_, i) => i).filter((i) => i !== q.answer);
        radios()[wrong[0]].click(); check().click(); await sleep(40);
        if (!/Not yet/.test(fb().textContent)) bad(tag + ": wrong answer did not say Not yet");
        if (fb().querySelector(".prose")) bad(tag + ": explanation shown after one wrong try");
        radios()[wrong[wrong.length - 1]].click(); check().click(); await sleep(40);
        if (!fb().querySelector(".prose")) bad(tag + ": explanation missing after two wrong tries");
        if (check().disabled) bad(tag + ": retry blocked after a wrong answer");
      }
      radios()[q.answer].click(); check().click(); await sleep(40);
      if (!/Correct/.test(fb().textContent) || !fb().querySelector(".prose")) bad(tag + ": correct answer feedback missing");
    }
  }
  const total = { ex: 0 };
  for (const M of D.modules) {
    for (let i = 0; i < M.screens.length; i++) {
      const sc = M.screens[i]; const tag = M.id + "--" + i;
      if (Date.now() > DEADLINE) { bad("UI walk ran out of time before " + tag); return JSON.stringify({ ok: false, problems, stats, timed_out: true }); }
      if (!(await go(tag))) continue;
      if (i === 0 && M.videos.length) {
        const wb = $("#watch-box");
        if (!wb) bad(tag + ": no Optional: watch box on the first screen"); else if (wb.open) bad(tag + ": watch box should start collapsed");
      }
      if (sc.type === "learn") {
        stats.learn++;
        if (!$(".lesson h1")) bad(tag + ": learn screen has no heading");
        if (!$(".lesson .prose") || !$(".lesson .prose").textContent.trim()) bad(tag + ": learn screen has no text");
      } else if (sc.type === "learn-do" || sc.type === "do") {
        const sec = sc.section ? M.sections.find((s) => s.id === sc.section) : null;
        const ex = M.exercises.find((e) => e.id === sc.exercise);
        if (sec) {
          const scratch = document.createElement("div"); scratch.innerHTML = sec.html;
          const want = scratch.textContent.trim().slice(0, 14);
          if (!$(".brief .prose").textContent.includes(want)) bad(tag + ": section text missing on learn-and-do screen");
        }
        if (!/Instructions/.test(($(".instr-head") || {}).textContent || "")) bad(tag + ": no Instructions heading");
        await doExercise(tag, ex, M);
      } else if (sc.type === "quiz") {
        await doQuiz(tag, M, i);
      } else if (sc.type === "takeaways") {
        stats.takeaways++;
        const lis = document.querySelectorAll("#cheat li");
        if (lis.length !== M.takeaways_html.length) bad(tag + ": takeaway count " + lis.length);
        const vids = document.querySelectorAll("#watch-section a.vid");
        if (vids.length !== M.videos.length) bad(tag + ": video link count " + vids.length);
        vids.forEach((a) => {
          if (!/^https:\\/\\/www\\.youtube\\.com\\/watch\\?v=/.test(a.href)) bad(tag + ": video href " + a.href);
          if (a.target !== "_blank" || !/noopener/.test(a.rel)) bad(tag + ": video link must open in a new tab");
          const img = a.querySelector("img");
          if (!img || !/^https:\\/\\/i\\.ytimg\\.com\\/vi\\/[\\w-]{11}\\/hqdefault\\.jpg$/.test(img.src)) bad(tag + ": thumbnail src wrong");
        });
        const nb = $("#next-module-btn");
        if (!nb) bad(tag + ": no next module button");
      }
    }
  }
  // Home and completion
  await go("home");
  if (!$("#continue-btn")) bad("home: no Continue where you left off button");
  if (document.querySelectorAll(".syl-row .chip-ok").length !== D.modules.length) bad("home: not every module shows Complete");
  await go("complete");
  const ex = D.modules.reduce((n, m) => n + m.exercises.length, 0), qz = D.modules.reduce((n, m) => n + m.quiz.length, 0);
  if (($("#count-ex") || {}).textContent !== ex + " / " + ex) bad("complete: exercise count " + ($("#count-ex") || {}).textContent);
  if (($("#count-qz") || {}).textContent !== qz + " / " + qz) bad("complete: quiz count " + ($("#count-qz") || {}).textContent);
  if (!/finished/.test(($("#complete-title") || {}).textContent || "")) bad("complete: title " + ($("#complete-title") || {}).textContent);
  if ((D.outcomes || []).length !== document.querySelectorAll("#outcomes input").length) bad("complete: outcome checklist size");
  // Saved progress: a save merges into what another tab wrote, and keeps it.
  try {
    const KEY = "course:" + D.slug + ":v1";
    const first = D.modules.flatMap((M) => M.screens.map((sc, i) => ({ M, sc, i }))).find((x) => x.sc.type === "learn-do" || x.sc.type === "do");
    if (first) {
      const tag = first.M.id + "--" + first.i;
      await go(tag);
      await until(() => $("#run-btn") && !$("#run-btn").disabled, 60000);
      const blob = JSON.parse(localStorage.getItem(KEY) || "{}");
      blob["zz-other-tab"] = { attempts: 2, hints: 0, solution: false, passed: false, code: null, seen: true, subs: [] };
      localStorage.setItem(KEY, JSON.stringify(blob));
      setCode("# merge-check\\nzz = 1\\n");
      await sleep(500);
      const after = JSON.parse(localStorage.getItem(KEY) || "{}");
      if (!after["zz-other-tab"]) bad("storage: a save removed a record that another tab wrote");
      if (!after[first.sc.exercise] || !/merge-check/.test(after[first.sc.exercise].code || "")) bad("storage: this tab's code was not saved");
      if (!after[first.sc.exercise] || !after[first.sc.exercise].passed) bad("storage: a save lost the passed flag");
      // A storage event must not throw or break the page.
      window.dispatchEvent(new StorageEvent("storage", { key: KEY, storageArea: localStorage }));
      await sleep(100);
      if (app.dataset.screen !== tag) bad("storage: a storage event changed the screen");
    }
  } catch (err) { bad("storage test failed: " + err); }
  return JSON.stringify({ ok: problems.length === 0, problems, stats });
})()`;


const KBD_PREP = `(async () => {
  const D = JSON.parse(document.getElementById("course-data").textContent);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = fn(); } catch (e) {} if (v) return v; await sleep(40); } return null; };
  const app = document.getElementById("app");
  let tag = null;
  for (const M of D.modules) { const i = M.screens.findIndex((s) => s.type === "learn-do" || s.type === "do"); if (i >= 0) { tag = M.id + "--" + i; break; } }
  location.hash = "#" + tag;
  await until(() => app.dataset.screen === tag, 30000);
  await until(() => { const b = document.getElementById("run-btn"); return b && !b.disabled; }, 60000);
  const c = document.querySelector("#editor-host .CodeMirror");
  if (c && c.CodeMirror) { c.CodeMirror.setValue('print("cb-kbd")'); c.CodeMirror.focus(); }
  else { const ta = document.querySelector("#editor-host textarea"); ta.value = 'print("cb-kbd")'; ta.dispatchEvent(new Event("input")); ta.focus(); }
  return JSON.stringify({ attempts: document.getElementById("attempts").textContent });
})()`;
const KBD_ARM = `(() => { const s = document.getElementById("console-status"); s.dataset.kind = "idle"; s.textContent = "before-key"; return JSON.stringify("armed"); })()`;
const KBD_WAIT = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const t = Date.now(); const s = document.getElementById("console-status");
  while (Date.now() - t < 30000 && (s.dataset.kind === "idle")) await sleep(40);
  while (Date.now() - t < 30000 && /(Running|Checking)/.test(s.textContent)) await sleep(40);
  return JSON.stringify({ kind: s.dataset.kind, text: s.textContent, printed: (document.getElementById("console-printed") || {}).textContent, attempts: document.getElementById("attempts").textContent });
})()`;

const PHONE_WALK = `(async () => {
  const D = JSON.parse(document.getElementById("course-data").textContent);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = fn(); } catch (e) {} if (v) return v; await sleep(40); } return null; };
  const app = document.getElementById("app");
  const tags = ["home"];
  const pick = (type) => { for (const M of D.modules) { const i = M.screens.findIndex((s) => s.type === type); if (i >= 0) return M.id + "--" + i; } return null; };
  [pick("learn-do"), pick("do"), pick("learn"), pick("quiz"), pick("takeaways"), "complete"].forEach((t) => t && tags.push(t));
  const wide = [];
  for (const tag of tags) {
    location.hash = tag === "home" ? "" : "#" + tag;
    await until(() => app.dataset.screen === tag, 30000);
    await sleep(250);
    if (window.innerWidth !== 400) wide.push(tag + " (viewport is " + window.innerWidth + " px, not 400)");
    else if (document.documentElement.scrollWidth > 401) wide.push(tag + " (page is " + document.documentElement.scrollWidth + " px wide)");
  }
  return JSON.stringify({ checked: tags, wide });
})()`;

const FALLBACK_WALK = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const until = async (fn, ms = 20000) => { const t = Date.now(); while (Date.now() - t < ms) { let v = null; try { v = fn(); } catch (e) {} if (v) return v; await sleep(40); } return null; };
  await until(() => document.getElementById("course-data") && document.getElementById("app"), 20000);
  const D = JSON.parse(document.getElementById("course-data").textContent);
  const app = document.getElementById("app");
  const problems = [];
  let tag = null;
  for (const M of D.modules) { const i = M.screens.findIndex((s) => s.type === "learn-do" || s.type === "do"); if (i >= 0) { tag = M.id + "--" + i; break; } }
  if (!tag) return JSON.stringify({ ok: true, skipped: "no exercise screen" });
  location.hash = "#" + tag;
  if (!(await until(() => app.dataset.screen === tag, 30000))) problems.push("exercise screen did not render with the CDNs unreachable");
  if (!document.querySelector("#editor-host textarea")) problems.push("no textarea fallback editor");
  if (document.querySelector(".CodeMirror")) problems.push("CodeMirror present although cdnjs is unreachable");
  const ta = document.querySelector("#editor-host textarea");
  if (ta) {
    ta.value = "x = 1"; ta.dispatchEvent(new Event("input")); await sleep(400);
    const saved = JSON.parse(localStorage.getItem("course:" + D.slug + ":v1") || "{}");
    const exId = Object.keys(saved).find((k) => saved[k] && saved[k].code === "x = 1");
    if (!exId) problems.push("draft code was not saved to localStorage");
  }
  const box = await until(() => { const b = document.getElementById("pyerror"); return b && !b.hidden && b; }, 30000);
  if (!box) problems.push("no Python error notice when jsDelivr is unreachable");
  else if (!Array.from(box.querySelectorAll("button")).some((b) => b.textContent === "Retry")) problems.push("Python error notice has no Retry button");
  else if (!/Python could not load/.test(box.textContent)) problems.push("Python error notice text: " + box.textContent.slice(0, 80));
  return JSON.stringify({ ok: problems.length === 0, problems });
})()`;

let report = { ok: false };
// Hard stop: whatever happens, finish within TIMEOUT_MS + 60 s.
const hardStop = setTimeout(async () => {
  try { proc.kill("SIGKILL"); } catch {}
  await mkdir(dirname(reportPath), { recursive: true }).catch(() => {});
  await writeFile(reportPath, JSON.stringify({ ok: false, fatal: "smoke test hard timeout" }, null, 2)).catch(() => {});
  console.log(JSON.stringify({ ok: false, fatal: "smoke test hard timeout" }));
  process.exit(1);
}, TOTAL_MS);
const consoleErrors = [];
try {
  const t0 = Date.now();
  while (!wsUrl && Date.now() - t0 < 20_000) await sleep(100);
  if (!wsUrl) throw new Error("Chrome did not start (no DevTools URL)");
  const httpBase = wsUrl.replace(/^ws:/, "http:").replace(/\/devtools\/browser\/.*$/, "");
  const target = await (await fetch(`${httpBase}/json/new?about:blank`, { method: "PUT" })).json();

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const pending = new Map();
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error")
      consoleErrors.push(msg.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300));
    if (msg.method === "Runtime.exceptionThrown")
      consoleErrors.push((msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text || "").slice(0, 300));
  };
  // Every DevTools call has its own timeout, so a busy page cannot hang the test.
  const send = (method, params = {}, ms = 20_000) => new Promise((r, j) => {
    const i = ++id;
    const t = setTimeout(() => { pending.delete(i); j(new Error(`DevTools ${method} timed out`)); }, ms);
    pending.set(i, (msg) => { clearTimeout(t); r(msg); });
    ws.send(JSON.stringify({ id: i, method, params }));
  });

  await send("Runtime.enable");
  await send("Page.enable");
  await send("Page.navigate", { url: `http://127.0.0.1:${port}/index.html?selftest=1` });

  const start = Date.now();
  let result = null;
  while (Date.now() - start < TIMEOUT_MS) {
    await sleep(1000);
    const r = await send("Runtime.evaluate", { expression: "JSON.stringify(window.__selftest || null)", returnByValue: true });
    const v = r.result?.result?.value;
    if (v && v !== "null") { result = JSON.parse(v); break; }
  }
  // UI walk, as a learner. Every screen of every module.
  let ui = { ok: false, problems: ["UI walk did not run"] };
  let phone = { checked: [], wide: ["did not run"] };
  let fallback = { ok: false, problems: ["fallback walk did not run"] };
  let errorsBeforeFailureTest = null;
  if (result) {
    const evalJson = async (expression, ms) => {
      const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, ms);
      const v = r.result?.result?.value;
      if (!v) throw new Error("page script failed: " + JSON.stringify(r.result?.exceptionDetails?.exception?.description || r.result).slice(0, 300));
      return JSON.parse(v);
    };
    // Keep 60 s for the keyboard, phone and failure-path parts.
    const walkDeadline = startedAt + TOTAL_MS - 60_000;
    const walkStart = Date.now();
    try { ui = await evalJson(UI_WALK(walkDeadline), Math.max(10_000, walkDeadline - Date.now() + 5_000)); } catch (err) { ui = { ok: false, problems: [String(err?.message || err)] }; }
    ui.ms = Date.now() - walkStart;
    // Keyboard shortcuts: Ctrl/Cmd+Enter runs, Shift+Ctrl/Cmd+Enter submits.
    try {
      const problems = [];
      const pressEnter = async (modifiers) => {
        for (const type of ["rawKeyDown", "keyUp"])
          await send("Input.dispatchKeyEvent", { type, modifiers, key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
      };
      const prep = await evalJson(KBD_PREP, 120_000);
      for (const [name, mods] of [["Ctrl+Enter", 2], ["Meta+Enter", 4]]) {
        await evalJson(KBD_ARM, 10_000);
        await pressEnter(mods);
        const r = await evalJson(KBD_WAIT, 40_000);
        if (r.kind !== "ran" || !/Ran without errors/.test(r.text)) problems.push(name + " did not Run: " + r.text);
        if (!/cb-kbd/.test(r.printed || "")) problems.push(name + ": printed output missing");
        if (r.attempts !== prep.attempts) problems.push(name + " counted an attempt");
      }
      let first = true;
      for (const [name, mods] of [["Shift+Ctrl+Enter", 10], ["Shift+Meta+Enter", 12]]) {
        await evalJson(KBD_ARM, 10_000);
        await pressEnter(mods);
        const r = await evalJson(KBD_WAIT, 40_000);
        if (r.kind === "ran" || r.kind === "idle") problems.push(name + " did not Submit: " + r.kind + " " + r.text);
        if (first && r.attempts === prep.attempts) problems.push(name + ": the changed code was not counted");
        first = false;
      }
      ui.keyboard = { ok: problems.length === 0, problems };
      if (problems.length) { ui.ok = false; ui.problems = (ui.problems || []).concat(problems.map((p) => "keyboard: " + p)); }
    } catch (err) {
      ui.ok = false; ui.problems = (ui.problems || []).concat(["keyboard: " + String(err?.message || err)]);
    }
    // Phone width: the page must not scroll sideways on any kind of screen.
    try {
      await send("Emulation.setDeviceMetricsOverride", { width: 400, height: 800, deviceScaleFactor: 1, mobile: true });
      await sleep(300);
      phone = await evalJson(PHONE_WALK, 120_000);
    } catch (err) { phone = { checked: [], wide: [String(err?.message || err)] }; }
    errorsBeforeFailureTest = consoleErrors.length;
    // Failure paths: both CDNs unreachable (textarea fallback, Python error notice).
    try {
      await send("Emulation.clearDeviceMetricsOverride");
      await send("Page.navigate", { url: `http://127.0.0.1:${port}/broken.html` });
      await sleep(1500);
      fallback = await evalJson(FALLBACK_WALK, 90_000);
    } catch (err) { fallback = { ok: false, problems: [String(err?.message || err)] }; }
  }
  const overflow = phone.wide.length > 0;

  // The failure test makes console errors on purpose. Keep them out of the report.
  if (errorsBeforeFailureTest !== null) consoleErrors.length = errorsBeforeFailureTest;
  if (!result) report = { ok: false, fatal: "timed out waiting for self-test", console_errors: consoleErrors };
  else report = { ...result, ok: !!result.ok && !overflow && !!ui.ok && !!fallback.ok, phone_overflow_on_home: overflow, phone_overflow: phone.wide, phone_checked: phone.checked,
                  ui, fallback, console_errors: consoleErrors };
  ws.close();
} catch (err) {
  report = { ok: false, fatal: String(err?.message || err), console_errors: consoleErrors };
} finally {
  proc.kill("SIGKILL");
  server.close();
  await rm(profile, { recursive: true, force: true }).catch(() => {});
}

await mkdir(dirname(reportPath), { recursive: true });
await writeFile(reportPath, JSON.stringify(report, null, 2));
const short = { ...report };
if (Array.isArray(short.results)) short.results = short.results.filter((r) => !r.ok);
console.log(JSON.stringify(short, null, 2));
clearTimeout(hardStop);
process.exit(report.ok ? 0 : 1);
