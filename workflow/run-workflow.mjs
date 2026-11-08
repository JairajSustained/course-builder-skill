#!/usr/bin/env node
/**
 * run-workflow.mjs - run a course-builder workflow script under pi.
 *
 * Claude Code runs workflow/build-course.js inside its Workflow tool, which
 * provides agent(), parallel(), pipeline(), phase(), log() and args to a
 * sandboxed script. pi has no Workflow tool, so this runner provides the same
 * five primitives and executes the workflow script unchanged. Every agent is a
 * child `pi --print` process.
 *
 * Usage:
 *   node workflow/run-workflow.mjs --script workflow/build-course.js \
 *     --args '{"topic":"...","slug":"...","courseDir":"...","skillDir":"...","today":"..."}'
 *
 * Options:
 *   --script <path>       workflow script (default: workflow/build-course.js)
 *   --args <json>         workflow args object
 *   --args-file <path>    read the args object from a file
 *   --model <pattern>     pi model for every agent. Default: the model of the pi session that
 *                         starts the runner (PI_PROVIDER/PI_MODEL), else COURSE_BUILDER_MODEL,
 *                         else the pi default for a new session
 *   --concurrency <n>     agents running at once (default: CB_CONCURRENCY or 3)
 *   --timeout <seconds>   per-agent limit (default: CB_AGENT_TIMEOUT_MS/1000 or 2700)
 *   --dry-run             print what would run, spawn nothing
 *
 * Output:
 *   stdout                       one line per event (phase, agent, result)
 *   <courseDir>/reports/workflow.log        same lines, timestamped
 *   <courseDir>/reports/workflow-result.json the workflow's return value
 *   <courseDir>/reports/workflow-runs.json   per-agent run records
 *   <courseDir>/reports/workflow/agent-NN.*  prompts, outputs, JSON answers
 */

import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SELF = fileURLToPath(import.meta.url)
const HERE = path.dirname(SELF)
const PI_BIN = process.env.PI_BIN || 'pi'
const SYSTEM_SUBAGENT =
  'You are one sub-agent inside a larger automated build. You do not talk to a human. ' +
  'Follow the task exactly, use the command-line checks the task names, and end with the ' +
  'required JSON answer. Other sub-agents edit other files at the same time: only write the ' +
  'files your task names.'

// ---------------------------------------------------------------- cli
// The model every child pi runs on. The default is the model the calling pi
// session is on right now: pi exports the session's model to the bash tool as
// PI_PROVIDER / PI_MODEL, so the build follows whatever the user picked with
// /model instead of silently using whatever a fresh session would default to.
function sessionModel() {
  const model = process.env.PI_MODEL
  if (!model) return null
  const provider = process.env.PI_PROVIDER
  return model.includes('/') || !provider ? model : `${provider}/${model}`
}

function parseCli(argv) {
  const out = { script: path.join(HERE, 'build-course.js'), args: null, model: null, modelSource: null, concurrency: Number(process.env.CB_CONCURRENCY || 3), timeoutSec: Math.round(Number(process.env.CB_AGENT_TIMEOUT_MS || 2700000) / 1000), dryRun: false }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    const next = () => { const v = argv[++i]; if (v === undefined) throw new Error(`${a} needs a value`); return v }
    if (a === '--script') out.script = path.resolve(next())
    else if (a === '--args') out.args = JSON.parse(next())
    else if (a === '--args-file') out.args = JSON.parse(fs.readFileSync(next(), 'utf8'))
    else if (a === '--model') { out.model = next(); out.modelSource = '--model' }
    else if (a === '--concurrency') out.concurrency = Number(next())
    else if (a === '--timeout') out.timeoutSec = Number(next())
    else if (a === '--dry-run') out.dryRun = true
    else if (a === '-h' || a === '--help') { console.log(fs.readFileSync(SELF, 'utf8').split('*/')[0].replace(/^#!.*\n/, '')); process.exit(0) }
    else throw new Error(`unknown option ${a}`)
  }
  if (!out.args) throw new Error('missing --args or --args-file')
  if (!out.model && process.env.COURSE_BUILDER_MODEL) { out.model = process.env.COURSE_BUILDER_MODEL; out.modelSource = 'COURSE_BUILDER_MODEL' }
  if (!out.model && sessionModel()) { out.model = sessionModel(); out.modelSource = 'current pi session' }
  if (!out.model) out.modelSource = 'pi default'
  if (!Number.isFinite(out.concurrency) || out.concurrency < 1) out.concurrency = 1
  return out
}

let CLI
try {
  CLI = parseCli(process.argv.slice(2))
} catch (e) {
  console.error(`run-workflow: ${e.message}`)
  process.exit(2)
}
const ARGS = CLI.args
const COURSE = ARGS.courseDir ? path.resolve(ARGS.courseDir) : null
const WORK = COURSE ? path.join(COURSE, 'reports', 'workflow') : path.join(process.cwd(), '.workflow')
const LOG_FILE = COURSE ? path.join(COURSE, 'reports', 'workflow.log') : path.join(WORK, 'workflow.log')
fs.mkdirSync(WORK, { recursive: true })
for (const d of [path.dirname(LOG_FILE)]) fs.mkdirSync(d, { recursive: true })
if (COURSE) { try { fs.writeFileSync(path.join(COURSE, 'reports', 'workflow.pid'), String(process.pid) + '\n') } catch {} }

const startedAt = Date.now()
const stamp = () => new Date().toISOString().slice(11, 19)
const logLine = (s) => {
  const line = `[${stamp()}] ${s}`
  console.log(line)
  try { fs.appendFileSync(LOG_FILE, line + '\n') } catch {}
}

// ---------------------------------------------------------------- agent runs
const runs = []
let counter = 0
let maxAgents = Number(process.env.CB_MAX_AGENTS || 400)
const children = new Set()

// A slot limits how many child pi processes run at once. Slots are taken by
// agent() itself, not by parallel(), so nested parallel/pipeline calls cannot
// deadlock.
let active = 0
const waiters = []
function acquire() {
  if (active < CLI.concurrency) { active++; return Promise.resolve() }
  return new Promise((resolve) => waiters.push(resolve))
}
function release() {
  const w = waiters.shift()
  if (w) w()
  else active--
}

function translatePrompt(p) {
  // pi tool names instead of Claude Code's.
  return p
    .replace(/\bWebSearch\b/g, 'web_search_exa')
    .replace(/\bWebFetch\b/g, 'web_fetch_exa')
}

function toolsFor(prompt, opts) {
  if (opts.tools) return opts.tools
  if (prompt.includes('LEARNER VIEW (start)')) return 'bash'
  if (/^Run exactly|^Run this command exactly|^Run these two commands/m.test(prompt)) return 'bash'
  return 'read,write,edit,bash,web_search_exa,web_fetch_exa'
}

function schemaInstruction(schema, resultPath) {
  const required = Array.isArray(schema.required) && schema.required.length ? schema.required.join(', ') : 'all keys shown'
  return `

FINAL ANSWER (mandatory): the caller is a script, not a human.
Answer with ONE JSON object of this shape:
${JSON.stringify(schema, null, 2)}
Required keys: ${required}.
${resultPath
    ? `Write that object to the file ${resultPath} with the write tool: raw JSON only, no markdown fences, no other text in the file. Do this as your last action.`
    : 'Your final message must contain only that JSON object: no prose, no markdown fences.'}`
}

function tryParse(s) {
  if (typeof s !== 'string' || !s.trim()) return undefined
  let t = s.trim()
  if (t.startsWith('```')) t = t.replace(/^```[a-z]*\s*/i, '').replace(/```\s*$/, '').trim()
  try { return JSON.parse(t) } catch { }
  const first = t.search(/[{[]/)
  if (first < 0) return undefined
  let depth = 0, inStr = false, esc = false
  for (let i = first; i < t.length; i++) {
    const c = t[i]
    if (inStr) { if (esc) esc = false; else if (c === '\\') esc = true; else if (c === '"') inStr = false; continue }
    if (c === '"') { inStr = true; continue }
    if (c === '{' || c === '[') depth++
    else if (c === '}' || c === ']') {
      depth--
      if (depth === 0) { try { return JSON.parse(t.slice(first, i + 1)) } catch { return undefined } }
    }
  }
  return undefined
}

function extractJson(text) {
  if (!text) return undefined
  for (const m of text.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi)) {
    const v = tryParse(m[1])
    if (v !== undefined) return v
  }
  const direct = tryParse(text)
  if (direct !== undefined) return direct
  // last-resort: scan every top-level object and take the last one that parses
  let found
  const re = /\{/g
  let m
  while ((m = re.exec(text))) {
    const v = tryParse(text.slice(m.index))
    if (v !== undefined) { found = v; break }
  }
  return found
}

function validate(value, schema) {
  if (value === undefined) return 'no JSON was returned'
  if (schema.type === 'object' || schema.properties) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return 'the answer is not a JSON object'
    const missing = (schema.required || []).filter((k) => !(k in value))
    if (missing.length) return `missing required keys: ${missing.join(', ')}`
  }
  return null
}

function runPi({ prompt, tools, model, timeoutSec }) {
  return new Promise((resolve) => {
    const argv = ['--print', '--no-session', '--no-skills', '--no-prompt-templates']
    if (model) argv.push('--model', model)
    if (tools) argv.push('--tools', tools)
    argv.push('--append-system-prompt', SYSTEM_SUBAGENT)
    let child
    try {
      child = spawn(PI_BIN, argv, { cwd: process.cwd(), env: process.env, stdio: ['pipe', 'pipe', 'pipe'] })
    } catch (e) {
      resolve({ code: -1, stdout: '', stderr: String(e), error: true })
      return
    }
    children.add(child)
    let stdout = '', stderr = '', timedOut = false, killed = false
    const cap = 20 * 1024 * 1024
    child.stdout.on('data', (d) => { if (stdout.length < cap) stdout += d.toString() })
    child.stderr.on('data', (d) => { if (stderr.length < cap) stderr += d.toString() })
    const timer = setTimeout(() => { timedOut = true; killed = true; child.kill('SIGTERM'); setTimeout(() => child.kill('SIGKILL'), 15000) }, timeoutSec * 1000)
    child.on('error', (e) => { clearTimeout(timer); children.delete(child); resolve({ code: -1, stdout, stderr: stderr + '\n' + String(e), error: true }) })
    child.on('close', (code) => { clearTimeout(timer); children.delete(child); resolve({ code, stdout, stderr, timedOut, killed }) })
    child.stdin.on('error', () => {})
    child.stdin.end(prompt)
  })
}

async function agent(prompt, opts = {}) {
  if (typeof prompt !== 'string' || !prompt.trim()) {
    logLine(`agent: empty prompt for "${opts.label || '?'}" - skipped`)
    return null
  }
  if (++counter > maxAgents) throw new Error(`agent limit ${maxAgents} reached (CB_MAX_AGENTS)`)
  const n = counter
  const label = String(opts.label || `agent ${n}`)
  const phaseName = opts.phase ? ` [${opts.phase}]` : ''
  const schema = opts.schema || null
  const id = String(n).padStart(3, '0')
  const base = path.join(WORK, `agent-${id}`)
  const resultPath = schema && !CLI.dryRun ? `${base}.json` : null
  const model = CLI.model || null
  const tools = toolsFor(prompt, opts)
  let full = translatePrompt(prompt)
  if (tools === 'bash' && prompt.includes('LEARNER VIEW (start)')) {
    full += `

NOTE ON TOOLS: you have bash only (no read, no write tool). Write your attempt files with a heredoc, for example:
\`\`\`
cat > ${COURSE || '<course dir>'}/drafts/try/<exercise id>.py <<'PY'
print("your attempt")
PY
\`\`\``
  }
  if (schema) full += schemaInstruction(schema, resultPath)

  if (CLI.dryRun) {
    logLine(`DRY ${label}${phaseName} tools=${tools} model=${model || '(pi default)'} chars=${full.length}`)
    return null
  }

  try { fs.writeFileSync(`${base}.prompt.md`, full) } catch {}

  await acquire()
  const t0 = Date.now()
  logLine(`-> ${label}${phaseName} (${id}) tools=${tools}`)
  let value = undefined
  let problem = null
  try {
    for (let attempt = 1; attempt <= 2; attempt++) {
      const r = await runPi({ prompt: full, tools, model, timeoutSec: CLI.timeoutSec })
      try { fs.writeFileSync(`${base}.out.txt`, r.stdout) } catch {}
      if (r.stderr) { try { fs.appendFileSync(path.join(WORK, 'stderr.log'), `--- ${id} ${label} exit=${r.code}\n${r.stderr}\n`) } catch {} }
      if (resultPath) {
        try { value = tryParse(fs.readFileSync(resultPath, 'utf8')) } catch { value = undefined }
        try { fs.unlinkSync(resultPath) } catch {}
      }
      if (value === undefined) value = extractJson(r.stdout)
      if (schema) problem = validate(value, schema)
      else if (value === undefined && r.code !== 0) problem = `exit code ${r.code}`

      if (!problem) break
      const note = `Your previous attempt did not give a usable answer (${problem})${r.timedOut ? '; it timed out' : ''}.`
      logLine(`   ${label}: ${note} retrying once`)
      full = `${translatePrompt(prompt)}

${note}
Do the task again, completely, and then answer only in the required JSON format.`
      if (tools === 'bash' && prompt.includes('LEARNER VIEW (start)')) full += '\n(Your tools are bash only: write attempt files with a heredoc.)'
      if (schema) full += schemaInstruction(schema, resultPath)
      value = undefined
      if (attempt === 2) {
        const tail = (r.stdout || r.stderr || '').slice(-1500)
        logLine(`   ${label}: giving up (${problem}). Last output: ${tail.replace(/\s+/g, ' ').slice(0, 600)}`)
        logLine(`   ${label}: if the model is the problem (auth, 404, endpoint), re-run with --model <pattern> or set COURSE_BUILDER_MODEL`)
      }
    }
  } catch (e) {
    problem = String(e)
    logLine(`   ${label}: error ${problem}`)
  } finally {
    release()
  }
  const seconds = Math.round((Date.now() - t0) / 100) / 10
  runs.push({ n, label, phase: opts.phase || null, model, tools, schema: !!schema, seconds, ok: !problem && value !== undefined, problem })
  logLine(`<- ${label}${phaseName} ${value !== undefined ? 'ok' : 'FAILED'} ${seconds}s`)
  return problem ? null : (value === undefined ? null : value)
}

function parallel(thunks) {
  const list = (thunks || []).filter((f) => typeof f === 'function')
  return Promise.all(list.map((f) => Promise.resolve().then(f).catch((e) => { logLine(`parallel task failed: ${e && e.message ? e.message : e}`); return null })))
}

function pipeline(items, fn) {
  return Promise.all((items || []).map((item, i) => Promise.resolve().then(() => fn(item, i)).catch((e) => { logLine(`pipeline item ${i} failed: ${e && e.message ? e.message : e}`); return null })))
}

function phase(title) {
  logLine(`================ ${title} ================`)
}

function log(message) {
  logLine(String(message).replace(/\s+/g, ' ').slice(0, 2000))
}

// ---------------------------------------------------------------- run it
const AsyncFunction = Object.getPrototypeOf(async function () { }).constructor

let body
try {
  body = fs.readFileSync(CLI.script, 'utf8').replace(/^export\s+/gm, '')
} catch (e) {
  console.error(`run-workflow: cannot read ${CLI.script}: ${e.message}`)
  process.exit(2)
}

logLine(`workflow ${path.basename(CLI.script)} for "${ARGS.topic || ''}"`)
logLine(`course ${COURSE || '(none)'} | concurrency ${CLI.concurrency} | model ${CLI.model || '(pi default)'} (from ${CLI.modelSource}) | timeout ${CLI.timeoutSec}s`)

let result
try {
  const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', body)
  result = await fn(agent, parallel, pipeline, phase, log, ARGS)
} catch (e) {
  result = { status: 'failed', stage: 'workflow', courseDir: COURSE, errors: [String(e && e.stack ? e.stack.split('\n').slice(0, 6).join(' | ') : e)] }
  logLine(`workflow threw: ${result.errors[0]}`)
}

const seconds = Math.round((Date.now() - startedAt) / 100) / 10
const status = result && result.status ? result.status : 'ok'
logLine(`done: status=${status} agents=${runs.length} ${seconds}s`)
if (result && result.reasons && result.reasons.length) result.reasons.forEach((r) => logLine(`  reason: ${r}`))

const write = (file, value) => { try { fs.writeFileSync(file, JSON.stringify(value, null, 2)) } catch (e) { logLine(`could not write ${file}: ${e.message}`) } }
if (COURSE) {
  write(path.join(COURSE, 'reports', 'workflow-result.json'), result)
  write(path.join(COURSE, 'reports', 'workflow-runs.json'), runs)
  try { fs.unlinkSync(path.join(COURSE, 'reports', 'workflow.pid')) } catch {}
}
console.log(JSON.stringify(result, null, 2))

for (const c of children) c.kill('SIGTERM')
process.exit(status === 'failed' ? 1 : 0)
