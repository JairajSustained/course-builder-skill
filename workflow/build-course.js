export const meta = {
  name: 'build-course',
  description: 'Build a verified interactive course from official docs: sources, concept inventory, syllabus panel, per-module write/run/review loops, course review, render + browser test',
  whenToUse: 'Run by the course-builder skill. args: {topic, slug, courseDir, skillDir, today, resume?, only?: [module ids], redo?: true}',
  phases: [
    { title: 'Sources', detail: 'find, fetch and judge official docs; set the runtime', model: 'sonnet' },
    { title: 'Inventory', detail: 'extract concepts from sources, merge, critique missing vs unneeded', model: 'sonnet' },
    { title: 'Syllabus', detail: '3 drafts from different angles, judge panel, synthesis, review loop', model: 'sonnet' },
    { title: 'Modules', detail: 'per module: write 5-6 screens + quiz + takeaways + videos, verify by execution, 4-lens review (depth, pedagogy, extras, learner), revise', model: 'sonnet' },
    { title: 'Course review', detail: 'cross-module review, fixes, full verification', model: 'sonnet' },
    { title: 'Ship', detail: 'render page, run every solution in real Pyodide, write report', model: 'sonnet' },
  ],
}

// ---------------------------------------------------------------- setup
const A = args || {}
if (!A.topic || !A.courseDir || !A.skillDir || !A.today) {
  throw new Error('args must include topic, courseDir, skillDir, today')
}
const C = A.courseDir
const SK = A.skillDir
const CB = `python3 ${SK}/scripts/cb.py`
const REF = `${SK}/references`
const RESUME = A.resume || 'start'   // start | inventory | syllabus | modules | review | ship
const ORDER = ['start', 'inventory', 'syllabus', 'modules', 'review', 'ship']
const runs = (stage) => ORDER.indexOf(RESUME) <= ORDER.indexOf(stage)
const ONLY = Array.isArray(A.only) && A.only.length ? A.only.map(String) : null   // only these module ids go through the module stage
const REDO = A.redo === true                                                       // true: rebuild modules even if they already pass verify
const GP = 'general-purpose'
// Every agent runs on Sonnet. Change it here only.
const MODEL = 'sonnet'
const AG = (prompt, opts) => agent(prompt, Object.assign({}, opts || {}, { model: MODEL }))

const TEAM = `You are one member of a curriculum team that builds a beginner course on "${A.topic}".
Course folder: ${C}
Rules you must follow:
- Read ${REF}/pedagogy.md and ${REF}/course-schema.md before you write or judge anything.
- Every fact, API name and behaviour must come from files in ${C}/sources/. Your memory is not a source.
- The course packages are pinned in ${C}/course.meta.json (runtime.local_pins). The docs may describe a newer version; prefer behaviour that holds in the pinned version, and say so when they differ.
- Only write the files your task names. Other agents work on other files at the same time.
- Your final answer is data for an orchestrator script, not a message for a human.`

// ---------------------------------------------------------------- schemas
const S = (properties, required) => ({ type: 'object', properties, required: required || Object.keys(properties) })
const STR = { type: 'string' }, BOOL = { type: 'boolean' }, NUM = { type: 'number' }
const STRS = { type: 'array', items: STR }

const SCOUT = S({
  docs_found: BOOL, docs_root: STR, docs_version: STR,
  runtime_supported: BOOL, runtime_reason: STR, pyodide_packages: STRS, pypi_packages: STRS,
  pages: { type: 'array', items: S({ url: STR, file: STR, kind: STR, scope: STR }) },
  notes: STR,
})
const FETCH = S({ results: { type: 'array', items: S({ url: STR, ok: BOOL, files: STRS, note: STR }) } })
const ENOUGH = S({
  enough: BOOL, missing: STRS,
  refetch: { type: 'array', items: S({ url: STR, file: STR, kind: STR, scope: STR }) },
  suggestions_for_user: STRS, version_notes: STR,
})
const RUNTIME = S({ ok: BOOL, pinned: STRS, message: STR })
const GATE = S({ ok: BOOL, error_count: NUM, warning_count: NUM, errors: STRS, warnings: STRS })
const FINDINGS = S({
  pass: BOOL,
  findings: { type: 'array', items: S({
    id: STR, kind: { type: 'string', enum: ['missing', 'unneeded', 'order', 'pedagogy', 'correctness', 'clarity', 'other'] },
    severity: { type: 'string', enum: ['high', 'medium', 'low'] }, problem: STR, fix: STR }) },
  summary: STR,
})
const LEARNER = S({
  pass: BOOL,
  findings: FINDINGS.properties.findings,
  summary: STR,
  quiz_choices: { type: 'object', additionalProperties: NUM },
})
const VIEW = S({ ok: BOOL, text: STR })
const QUIZCHECK = S({ ok: BOOL, checked: NUM, missing: STRS, mismatches: { type: 'array', items: S({ id: STR, chosen: NUM, answer: NUM }) } })
const REVISED = S({ verify_ok: BOOL, applied: STRS, rejected: { type: 'array', items: S({ id: STR, reason: STR }) }, notes: STR })
const JUDGE = S({ ranking: STRS, rationale: STR, grafts: STRS, concerns: STRS })
const MODLIST = S({ ok: BOOL, modules: { type: 'array', items: S({ id: STR, title: STR, kind: STR }) }, errors: STRS })
const AUTHORED = S({ id: STR, verify_ok: BOOL, error_count: NUM, warning_count: NUM, notes: STR })
const XREVIEW = S({ findings: { type: 'array', items: S({ module: STR, id: STR, severity: { type: 'string', enum: ['high', 'medium', 'low'] }, problem: STR, fix: STR }) }, summary: STR })
const SHIP = S({ render_ok: BOOL, verified: BOOL, stale_report: BOOL, smoke_ok: BOOL, smoke_skipped: BOOL, failures: STRS, console_errors: STRS, page: STR, exercises: NUM })

const serious = (fs) => (fs || []).filter(f => f.severity === 'high' || f.severity === 'medium')

function gate(cmd, label, phase) {
  return AG(`Run this command exactly and report its JSON result. Do not fix anything.
\`\`\`
${cmd}
\`\`\`
Copy "ok", "error_count", "warning_count" from the output. List each error and each warning as "<where>: <message>". If the command prints {"fatal": ...} or crashes, set ok=false and put the message in errors.`,
    { label, phase, schema: GATE, effort: 'low', agentType: GP })
}

// ================================================================ SOURCES
function fetchPrompt(pages) {
  return `${TEAM}

TASK: fetch these pages into ${C}/sources/ with the fetch tool. For each page run:
\`\`\`
${CB} fetch ${C} "<url>" <file> --kind <kind> --date ${A.today}
\`\`\`
It prints JSON with the files written and word counts. If a page gives very little text (it may need JavaScript) or fails, try ONE alternative: the raw docs source in the project repository (for example raw.githubusercontent.com/.../docs/<page>.rst or .md). As a last resort use WebFetch and ask for the full page as markdown with every code block, then save it yourself with the same front matter (url, fetched: ${A.today}, kind, title) and run \`${CB} index ${C}\`.
Pages:
${JSON.stringify(pages, null, 2)}`
}

async function pinRuntime(packages, pypi) {
  const rt = await AG(`Run exactly:
\`\`\`
${CB} runtime ${C} ${(packages || []).map(p => `--package ${p}`).join(' ')} ${(pypi || []).map(p => `--pypi ${p}`).join(' ')}
\`\`\`
If it fails because a name is missing from Pyodide, the name may be the import name, not the distribution name (for example "bs4" -> "beautifulsoup4"). Try the distribution name once. If the package is pure Python on PyPI but not in Pyodide, try once with --pypi <name>==<exact version>. Report ok=true only if a run succeeded, and copy runtime.local_pins into pinned. Otherwise ok=false with the message.`,
    { label: 'pin runtime', phase: 'Sources', schema: RUNTIME, effort: 'low', agentType: GP })
  if (rt && rt.ok) log(`Runtime pinned: ${rt.pinned.join(', ')}`)
  return rt
}

// Judge, then fetch what is missing, then judge again. Always end on a judge.
async function judgeSources() {
  let enough = null
  for (let round = 1; round <= 3; round++) {
    enough = await AG(`${TEAM}

TASK: judge if ${C}/sources/ is enough to write a complete, accurate beginner course on "${A.topic}". Read ${REF}/sources.md ("When it is enough"). Read ${C}/sources/INDEX.md and open the files. Check: user guide present from install to the main tasks; each major API area has a source; the pinned version (${C}/course.meta.json) vs the docs version — write version_notes listing behaviour or APIs that differ (check the changelog files).
A missing changelog alone does NOT make the sources insufficient: note the gap in version_notes (verify runs all code on the pinned version anyway). Judge "enough" on the guide and API coverage.
If something is missing and you can name a URL that has it, put it in refetch (file names must continue the NN- numbering and not clash with existing files). If it is missing and you cannot find it, add to suggestions_for_user where the user could get it (docs URL, PDF, repo docs folder, book, video course).`,
      { label: `judge sources (round ${round})`, phase: 'Sources', schema: ENOUGH, agentType: GP })
    if (!enough || enough.enough || !enough.refetch.length || round === 3) break
    await AG(fetchPrompt(enough.refetch), { label: `fetch missing pages (round ${round})`, phase: 'Sources', schema: FETCH, agentType: GP })
  }
  return enough
}

function needsSources(enough, resumeWith) {
  return { status: 'needs_sources', courseDir: C,
    missing: enough ? enough.missing : ['the source judge failed'],
    suggestions: enough ? enough.suggestions_for_user : [], resume_with: resumeWith }
}

if (runs('start')) {
  phase('Sources')
  const scout = await AG(`${TEAM}

TASK: plan the sources and the runtime. Read ${REF}/sources.md first.
1. Find the official documentation for "${A.topic}" (web search, then open the docs). Read its table of contents.
2. Runtime: learners run Python in the browser with Pyodide 0.28.3 (Python 3.13). Decide if this topic can be taught that way: Python itself, a Python library that exists in the Pyodide distribution, or a pure-Python library from PyPI that is not in Pyodide (network clients, servers, GPU and most C extensions do not work). pyodide_packages = Pyodide DISTRIBUTION names (for example "beautifulsoup4", not the import name "bs4"). pypi_packages = exact "name==version" pins for pure-Python libraries that are NOT in Pyodide (check the PyPI page: the wheel must be pure Python, "py3-none-any"). Set runtime_supported=true when either list covers the topic. If neither can run in Pyodide, set runtime_supported=false and explain. Use [] for a list that does not apply.
3. Plan 8-25 pages to fetch: the full user guide, the API reference pages for the areas a beginner course needs, the PyPI page or README, and the changelog. Name each file "NN-short-name.md" in reading order. kind is one of official-guide, api-reference, package-metadata, changelog, secondary.
   Pages that are one huge single page are fine: the fetch tool splits them. URLs that differ only by #anchor are the SAME page: list such a page once.
Do not fetch the pages into files yet. If you find no official docs at all, set docs_found=false, explain in notes, and suggest where the user could get docs.`,
    { label: 'scout sources', phase: 'Sources', schema: SCOUT, agentType: GP })

  if (!scout) return { status: 'failed', stage: 'sources', courseDir: C, errors: ['scout agent failed'] }
  if (!scout.runtime_supported) return { status: 'unsupported_runtime', courseDir: C, reason: scout.runtime_reason }
  // Pin the runtime before any early return, so a resume always has it.
  const rt = await pinRuntime(scout.pyodide_packages || [], scout.pypi_packages || [])
  if (!rt || !rt.ok) return { status: 'unsupported_runtime', courseDir: C, reason: rt ? rt.message : 'runtime step failed' }
  if (!scout.docs_found) {
    return { status: 'needs_sources', courseDir: C, missing: ['official documentation'], reason: scout.notes,
      suggestions: ['Paste the docs URL', `Put markdown, text or PDF docs into ${C}/sources/`], resume_with: 'inventory' }
  }

  const chunks = []
  for (let i = 0; i < scout.pages.length; i += 4) chunks.push(scout.pages.slice(i, i + 4))
  await parallel(chunks.map((ch, i) => () => AG(fetchPrompt(ch), { label: `fetch ${i + 1}/${chunks.length}`, phase: 'Sources', schema: FETCH, agentType: GP })))

  const enough = await judgeSources()
  if (!enough || !enough.enough) return needsSources(enough, 'inventory')
  if (enough.version_notes) log(`Version notes: ${enough.version_notes.slice(0, 300)}`)
} else {
  // Resume: the earlier run may have stopped before the runtime pin, and the
  // user may have added files to sources/ by hand.
  phase('Sources')
  const prep = await AG(`${TEAM}

TASK: prepare a resumed build.
1. Run \`${CB} index ${C}\` (rebuilds sources/INDEX.md from the files in sources/). If it lists files with missing front matter, add front matter to them (url or "local file", fetched: ${A.today}, kind, title) and run it again.
2. Run \`${CB} status ${C}\`. If meta.runtime is null, decide the Pyodide distribution names for "${A.topic}" (for example "beautifulsoup4") and run \`${CB} runtime ${C} --package <name> ...\`. If the package is not in Pyodide, try --pypi <name>==<exact version> once.
ok = the runtime is set. pinned = runtime.local_pins. message = what you did.`,
    { label: 'prepare resume', phase: 'Sources', schema: RUNTIME, effort: 'low', agentType: GP })
  if (!prep || !prep.ok) return { status: 'unsupported_runtime', courseDir: C, reason: prep ? prep.message : 'resume preparation failed' }
  if (RESUME === 'inventory') {
    const enough = await judgeSources()
    if (!enough || !enough.enough) return needsSources(enough, 'inventory')
  }
}

// ================================================================ INVENTORY
if (runs('inventory')) {
  phase('Inventory')
  const list = await AG(`List every file in ${C}/sources/ except INDEX.md with its word count (wc -w). Return JSON lines in "files" as "name:words".`,
    { label: 'list sources', phase: 'Inventory', schema: S({ files: STRS }), effort: 'low', agentType: GP })
  const files = (list ? list.files : []).map(s => { const [n, w] = s.split(':'); return { n: n.trim(), w: parseInt(w, 10) || 0 } })
  // balance files into groups of ~9000 words
  const groups = []; let cur = [], words = 0
  for (const f of files) {
    if (cur.length && words + f.w > 9000) { groups.push(cur); cur = []; words = 0 }
    cur.push(f.n); words += f.w
  }
  if (cur.length) groups.push(cur)
  log(`${files.length} source files in ${groups.length} extraction groups`)

  await parallel(groups.map((g, k) => () => AG(`${TEAM}

TASK: extract a concept inventory from these source files only:
${g.map(n => `- ${C}/sources/${n}`).join('\n')}
Write ${C}/drafts/inventory-part-${k + 1}.json in the inventory.json format of course-schema.md.
- One concept per idea a learner must understand or an API they must use (for example make-soup, find-all, css-select, get-text, tag-attributes, navigable-string, parser-choice).
- Use short, obvious kebab ids so other extractors pick the same ids for the same idea.
- For each: name, summary (1-2 sentences, from the source), source ("sources/<file>#<heading>"), apis (the exact names a learner's code uses for this concept: methods/attributes like "select_one", keyword arguments with a trailing = like "class_=", class names like "BeautifulSoup", or string literals in double quotes like '"lxml"'; [] if none), prereqs (ids), pitfalls (real beginner mistakes the docs warn about or that follow from the API), and a suggested decision (include/defer/skip) with a reason for a beginner who has 6-10 sessions of 30 minutes.
- Cover everything in your files, including advanced items (mark them defer/skip). Do not invent concepts that are not in the files.`,
    { label: `extract concepts ${k + 1}/${groups.length}`, phase: 'Inventory', agentType: GP })))

  await AG(`${TEAM}

TASK: merge ${C}/drafts/inventory-part-*.json into ${C}/inventory.json.
- Merge duplicates (same idea, different ids). Keep the clearest id; fix prereqs to match.
- Make the final include/defer/skip decision for a beginner. include = what they need to do the core tasks of this library on their own. A typical library course has 20-40 include concepts; a small library can have fewer. Every decision needs a reason.
- Keep pitfalls; merge them. Every include concept must have a source file and an "apis" list (see course-schema.md). An API name belongs to the concept that first teaches it; do not give the same API to two include concepts.
Then run \`${CB} verify ${C} --stage inventory\` and fix every error. Re-run until ok is true.`,
    { label: 'merge inventory', phase: 'Inventory', agentType: GP })

  const lenses = [
    ['missing', 'Find what is MISSING or wrongly excluded: concepts in the sources that a beginner needs for core tasks but that are absent, or marked defer/skip without a good reason; missing pitfalls; missing prereq links. Use kind "missing".'],
    ['unneeded', 'Find what is UNNEEDED: include concepts a beginner does not need yet (rare options, internals, advanced performance, legacy APIs), and concepts so small they should merge into another. Use kind "unneeded".'],
  ]
  const crits = await parallel(lenses.map(([k, brief]) => () => AG(`${TEAM}

TASK: review ${C}/inventory.json against ${C}/sources/. ${brief}
Each finding: id = the concept id (or "new:<id>" for a new concept), severity, problem, fix. pass = true only if you have no high or medium findings.`,
    { label: `inventory critic: ${k}`, phase: 'Inventory', schema: FINDINGS, agentType: GP })))
  const invFindings = crits.filter(Boolean).flatMap(c => c.findings)
  if (serious(invFindings).length) {
    await AG(`${TEAM}

TASK: revise ${C}/inventory.json using these critic findings. Apply each high/medium finding unless it contradicts the sources or the beginner scope; record a reason for each rejection. Then run \`${CB} verify ${C} --stage inventory\` until ok is true.
Findings:
${JSON.stringify(invFindings, null, 2)}`,
      { label: 'revise inventory', phase: 'Inventory', schema: REVISED, agentType: GP })
  }
  const g = await gate(`${CB} verify ${C} --stage inventory`, 'gate: inventory', 'Inventory')
  if (!g || !g.ok) return { status: 'failed', stage: 'inventory', courseDir: C, errors: g ? g.errors : ['gate failed'] }
}

if (ORDER.indexOf(RESUME) > ORDER.indexOf('inventory')) {
  let ig = await gate(`${CB} verify ${C} --stage inventory`, 'gate: inventory (resume)', 'Inventory')
  if (ig && !ig.ok) {
    await AG(`${TEAM}

TASK: fix ${C}/inventory.json so \`${CB} verify ${C} --stage inventory\` passes. The rules changed since this inventory was written. Most likely each include concept now needs an "apis" list (see course-schema.md: exact method/attribute names, "kw=" keyword arguments, class names, '"literal"' strings; [] if none) and a source file that exists. Read the cited sources to fill apis. Do not change ids or decisions. Errors:
${ig.errors.join('\n')}`,
      { label: 'update inventory to new rules', phase: 'Inventory', agentType: GP })
    ig = await gate(`${CB} verify ${C} --stage inventory`, 'gate: inventory (after update)', 'Inventory')
  }
  if (!ig || !ig.ok) return { status: 'failed', stage: 'inventory', courseDir: C, errors: ig ? ig.errors : ['gate failed'] }
}

// ================================================================ SYLLABUS
const SYLLABUS_RULES = `Syllabus rules (verify.py enforces them):
- Every "module" has 2-10 concepts (a warning above 8). Each section of a module teaches 1-3 concepts, so a module with 5-6 screens has about 5-8 concepts.
- The project module (kind "project", the last one) has "concepts": [] (an error otherwise). The project teaches nothing new: its sections may list only concepts that earlier modules teach.
- Every include concept is in exactly one module, and prereqs come first.`
let modules = null
if (runs('syllabus')) {
  phase('Syllabus')
  const angles = [
    ['task', 'TASK-FIRST: organise modules around real jobs a beginner wants to do (get a title, collect all links, read a table, clean text, follow a structure), introducing concepts when a job needs them.'],
    ['docs', 'CONCEPT-PATH-FIRST: follow the natural conceptual order of the library (objects, then navigation, then searching, then output), so each module has one clear mental model.'],
    ['pitfall', 'PITFALL-FIRST: order and group concepts to get ahead of the mistakes beginners really make (use the inventory pitfalls), so each module ends with the learner safe from its common errors.'],
  ]
  await parallel(angles.map(([k, brief]) => () => AG(`${TEAM}

TASK: draft a syllabus. Angle: ${brief}
Inputs: ${C}/inventory.json (only "include" concepts are taught; name "defer" items as next steps in the description), ${C}/sources/INDEX.md.
Write ${C}/drafts/syllabus-${k}.json in the syllabus.json format of course-schema.md. Add a "rationale" to each module. Each module is 5-6 screens (a screen = one short lesson section plus one exercise, one idea each), so give a module 5-6 ideas, not more. The last module is the final project (kind "project") built on embedded data (no live network).
${SYLLABUS_RULES}
Check it: \`${CB} verify ${C} --stage syllabus --syllabus-file ${C}/drafts/syllabus-${k}.json\` and fix errors until ok is true.`,
    { label: `syllabus draft: ${k}`, phase: 'Syllabus', agentType: GP })))

  const judges = await parallel([
    ['learning', 'Judge as a learning designer: progression, cognitive load per module, the first 10 minutes, the strength of the final project.'],
    ['coverage', 'Judge as a working developer who knows this library: coverage of what people really use, correct order of prerequisites, nothing important buried or missing.'],
  ].map(([k, brief]) => () => AG(`${TEAM}

TASK: ${brief}
Compare ${C}/drafts/syllabus-task.json, ${C}/drafts/syllabus-docs.json and ${C}/drafts/syllabus-pitfall.json with the syllabus rubric (S1-S5) in pedagogy.md. ranking = draft names best first ("task", "docs", "pitfall"). grafts = specific ideas from the other drafts worth moving into the winner. concerns = weaknesses that the final syllabus must avoid.`,
    { label: `syllabus judge: ${k}`, phase: 'Syllabus', schema: JUDGE, agentType: GP })))

  await AG(`${TEAM}

TASK: write the final ${C}/syllabus.json. Start from the drafts in ${C}/drafts/syllabus-*.json, follow the judges' ranking, apply the grafts and avoid the concerns:
${JSON.stringify(judges.filter(Boolean), null, 2)}
${SYLLABUS_RULES}
Then run \`${CB} verify ${C} --stage syllabus\` until ok is true.`,
    { label: 'synthesise syllabus', phase: 'Syllabus', agentType: GP })

  const sylLenses = [
    ['coverage', 'COVERAGE vs inventory: every include concept in the right module, defer items named as next steps, no concept taught twice, nothing in the syllabus that the sources do not support. Mark too-thin issues kind "missing", padding kind "unneeded".'],
    ['sequence', 'SEQUENCE and SIZE: prereqs first, each module one coherent idea, each module fits 5-6 screens (one idea per screen: one short section plus one exercise) without cramming, no grab-bag modules, 2-10 concepts per module, the project module lists no concepts. Use kind "order" or "missing"/"unneeded".'],
    ['learner', 'BEGINNER EXPERIENCE and PROJECT: a working result in the first 10 minutes, motivation from real tasks, a final project that uses most modules on realistic embedded data. Use kind "pedagogy".'],
  ]
  for (let round = 1; round <= 3; round++) {
    const reviews = await parallel(sylLenses.map(([k, brief]) => () => AG(`${TEAM}

TASK: review ${C}/syllabus.json (round ${round}). Lens: ${brief}
Use the syllabus rubric in pedagogy.md. Each finding: id = module id or "syllabus", severity, problem, concrete fix. pass = true only if no high or medium findings. Write your review to ${C}/reviews/syllabus-r${round}-${k}.md as well.`,
      { label: `syllabus review r${round}: ${k}`, phase: 'Syllabus', schema: FINDINGS, agentType: GP })))
    const fs = reviews.filter(Boolean).flatMap(r => r.findings)
    if (!serious(fs).length) { log(`Syllabus passed review in round ${round}`); break }
    await AG(`${TEAM}

TASK: revise ${C}/syllabus.json with these review findings (round ${round}). Apply every high/medium finding unless it conflicts with the sources, the inventory, or another finding; give a reason for each rejection. If a finding needs an inventory change (for example a concept should be include), edit ${C}/inventory.json too and re-run its check. ${SYLLABUS_RULES}
Then run \`${CB} verify ${C} --stage syllabus\` until ok is true.
Findings:
${JSON.stringify(fs, null, 2)}`,
      { label: `revise syllabus r${round}`, phase: 'Syllabus', schema: REVISED, agentType: GP })
  }
}

const ml = await AG(`Run \`${CB} verify ${C} --stage syllabus\` and read ${C}/syllabus.json. ok = the verify result ok. modules = every module in order as {id, title, kind}. errors = verify errors as "<where>: <message>".`,
  { label: 'gate: syllabus', phase: 'Syllabus', schema: MODLIST, effort: 'low', agentType: GP })
if (!ml || !ml.ok) return { status: 'failed', stage: 'syllabus', courseDir: C, errors: ml ? ml.errors : ['gate failed'] }
modules = ml.modules
log(`Syllabus: ${modules.length} modules: ${modules.map(m => m.title).join(' | ')}`)

// ================================================================ MODULES
const MODULE_RULES = `Module rules (screen shape, DataQuest style):
- 5-6 screens. A screen = ONE section (the learn half) followed by EXACTLY ONE exercise of kind "exercise" whose after_section is that section. List the exercises in the same order as the sections. So the module has 5-6 sections and 5-6 exercises. verify.py rejects a section without its exercise, two exercises after one section, and a wrong order.
- Each section teaches ONE idea: 1-3 concepts (never 0, never more than 3), 60-200 words of prose plus at most one short code example (under 12 lines). The exercise task applies the idea to DIFFERENT data than the example.
- The exercise of a screen must tag at least one concept of its section, and its solution must really use an API of that concept (verify.py reads the code with ast). A concept counts as practised only when some solution uses one of its APIs; a tag alone is not enough. A tag whose concept has APIs but whose solution uses none is an error.
- The solution must not copy the lesson example (an error if the normalised solution is part of a lesson code block of the same module). No two exercises in the course may have the same solution or the same instructions. Exercise ids and quiz ids are unique in the whole course: prefix them with the module id (m02-e1, m02-q1).
- Plus exactly 1 "recall" exercise (after_section null) if this is not the first module.
- Exercise floors: instructions_md at least 12 words and no line of the solution inside it; each hint at least 4 words. Hint 1 and hint 2 contain no key line (a key line = a non-trivial solution line that is not in the starter; not even inside backticks). Hint 3 does not give all key lines and not more than 60% of their tokens. Check messages (the strings in check(...)) never contain a key line.
- Wrong solutions: at least 2 real beginner mistakes. At least ONE must run to the checks and FAIL them (status "fail", not an error or timeout). A wrong solution that is empty or equal to the starter (ignoring white space and comments) is an error. Wrong solutions that crash or time out are allowed but do not count.
- "quiz": 1-2 items {id "<module id>-q1", for example m02-q1, question_md (at least 6 words), options (3-5, distinct), answer (index), explanation_md (at least 8 words), concepts (taught in this module or earlier)}. They test understanding of one idea; the wrong options are real misconceptions from the inventory pitfalls; no trivia, no "all of the above". Vary the position of the right answer; the right option is not the longest. The explanation says why the right option is right and why the tempting one is wrong.
- "takeaways": 3-8 cheat-sheet lines (markdown), each at least 4 words, at least one with inline code, no two the same. Only what this module taught.
- "videos": optional, 0-2 items {url, title, channel, why}. See the video rules below.
Difficulty rises: direct use -> combine with earlier concepts -> a small realistic task.`
const PROJECT_RULES = `Project rules: kind "project". 1-2 sections: the brief (a realistic scenario, the data, what the learner builds, which modules it draws on). The project teaches nothing new: a section's "concepts" may list only concepts that earlier modules teach, and the syllabus lists none for the project. 3-6 exercises of kind "project-step", after_section null, exactly 1 hint each (at least 4 words, not the answer). Use ONE larger realistic fixture (60-150 lines of HTML or data) shared by all steps.
- setup_code = the shared fixture ONLY. The learner sees it on the Data tab, so it must not hold any line of any step's answer (verify.py rejects a setup_code that shows a line of another exercise's solution).
- carry_code = hidden reference code of the earlier steps. It runs after setup_code and before the learner code, so each step can be checked alone. Put the code that earlier steps produce here, never in setup_code. The first step has no carry_code.
Instructions (at least 12 words) state the goal and the expected result shape, not the method. At least one wrong solution per step must run to the checks and fail them.
"takeaways" is required: 3-8 cheat-sheet lines about the whole task (which tool for which job, the pitfalls that the project met), each at least 4 words, at least one with inline code. The project has NO quiz (leave "quiz" out or []). "videos" is optional (see the video rules below).`
const VIDEO_RULES = `Video rules (videos are optional; 0-2 per module):
- Find them with WebSearch. Look for a short (under 15 minutes) beginner video from an official or reputable channel that explains something from THIS module better than text. A module with no good video has "videos": [].
- Use ONLY URLs that you opened in this session (a WebSearch result or a WebFetch of the page). Never write a URL from memory. Copy the exact video title (at least 2 words) and the exact channel name.
- url must be https://www.youtube.com/watch?v=<id> or https://youtu.be/<id>. why is one sentence.
- No exercise or quiz question may depend on a video. The learner can skip all videos.
- verify.py asks YouTube (oEmbed) about every URL: it must exist, the title must match, the "channel" must match the oEmbed author name, and the title or author must contain a significant word of the course topic ("video looks unrelated to the topic" is an error). A warning "embedding disabled" is fine (the link still works). A warning "could not check" means a network problem: keep the video only if you opened it. In the final full verify, "could not check" is an error.`

const LENSES = ['depth', 'pedagogy', 'extras', 'learner']

function lensPrompt(m, lens, round) {
  const L = {
    depth: `DEPTH, both directions (rubric A, B and D1). TOO THIN / ACCURACY: every concept assigned to this module is taught with an example AND practised on its own screen; each concept's pitfall appears in the lesson or as a wrong solution; exercises go beyond the lesson example; facts match the sources (open the cited source files and compare APIs, arguments, outputs). Use kind "missing" or "correctness". PADDING / NOISE: sections over about 200 words or with more than one idea (a screen has one idea), paragraphs that no exercise needs, filler openers, history, lists of optional arguments, unused code in examples, repeated explanations. Quote the exact sentence or block to cut. Use kind "unneeded". Keep the two kinds apart: never merge a "missing" finding and an "unneeded" finding.`,
    pedagogy: `PEDAGOGY / FRICTION / CHECK QUALITY (rubric C, D2, D3): difficulty ladder; hints go nudge -> pointer -> near-solution and never give the answer (hint 1 and 2 hold no line of the solution); instructions name one deliverable and show the data; the recall exercise; each screen's task uses different data than its example (rubric C5); check messages help but do not reveal the answer; wrong solutions are real beginner mistakes and at least one of them reaches the checks and fails them. Use kind "pedagogy" or "clarity".`,
    extras: `QUIZ / TAKEAWAYS / VIDEOS (rubric E). Quiz: each question tests understanding of one idea; wrong options are real misconceptions from the inventory pitfalls (compare with the pitfalls of the module's concepts); options are balanced (the right one is not the longest, no "all/none of the above"); the explanation says why the right option is right and why the tempting one is wrong; no trivia; exactly one option is right. Takeaways: cheat-sheet lines with code, only what this module taught, they cover every concept of the module, no filler. Videos (if any): read the verify result and ${C}/reports/video-check.json (it holds the oEmbed title and author of each URL); judge fit from title, channel and "why": short, official or reputable channel, beginner level, matches this module; no exercise or question depends on it; if unsure, open the page with WebFetch. Project module: takeaways required, no quiz. Use kind "pedagogy", "correctness" or "unneeded".`,
  }[lens]
  return `${TEAM}

TASK: review ${C}/modules/${m.id}/module.json ("${m.title}", ${m.kind}), round ${round}. Also read its entry in ${C}/syllabus.json and the inventory entries of its concepts.
Lens: ${L}
Each finding: id = section or exercise id, severity (high = learner is blocked or taught something wrong; medium = clear quality loss; low = polish), problem, concrete fix. pass = true only if no high or medium findings.`
}

// The learner agent gets the learner view IN ITS PROMPT. It gets no TEAM preamble,
// it opens no file, and it can only run `cb.py try`. So it cannot see any answer.
function learnerPrompt(m, round, viewText) {
  return `You are a beginner learner on "${A.topic}". You know basic Python. You are trying module ${m.id} ("${m.title}"), round ${round}. Your final answer is data for an orchestrator script, not a message for a human.
RULES (strict):
- Do NOT open, read, list or search any file or folder. This includes module.json, inventory.json, syllabus.json and sources/. They hold the answers.
- Your only view of the module is the LEARNER VIEW below. It is exactly what the course page shows: lessons, instructions, starter code, the Data tab (the code that runs before your code), and the quiz questions with their options (no answers).
- The only command you may run is: ${CB} try ${C} <exercise id> --code-file <file>
- You may write only your own attempt files, in ${C}/drafts/try/<exercise id>.py (create the folder if it is missing).
WHAT TO DO:
1. For each exercise, write your honest first attempt to ${C}/drafts/try/<exercise id>.py and run the try command. If it fails, read the message and try again like a learner would (max 3 attempts per exercise).
2. Answer every quiz question from the lessons alone. Put your answers in quiz_choices as an object: quiz id -> option index (0-based). Choose one option for each question, even when unsure.
3. Report findings: exercises you could not solve from the lesson and instructions, ambiguous wording, check messages that confused you, exercises that were trivial copies of the lesson example, and a quiz question where you could not decide, where two options look right, or where the lessons did not prepare you. Use kind "clarity" or "pedagogy". Put attempt counts in summary.
Each finding: id = section, exercise or quiz id, severity (high = you are blocked or taught something wrong; medium = clear quality loss; low = polish), problem, concrete fix. pass = true only if no high or medium findings.

LEARNER VIEW (start)
${viewText}
LEARNER VIEW (end)`
}

async function runLens(m, lens, round) {
  const label = `${m.id} review r${round}: ${lens}`
  if (lens !== 'learner') {
    const r = await AG(lensPrompt(m, lens, round), { label, phase: 'Modules', schema: FINDINGS, agentType: GP })
    return r ? r.findings : null
  }
  // 1. a low-effort agent prints the learner view and returns it as text
  const view = await AG(`Run exactly this command and return its complete output, unchanged, in "text". Do not shorten it. Do not open any other file. Set ok=false if the command fails.
\`\`\`
${CB} learner-view ${C} ${m.id}
\`\`\``,
    { label: `${m.id} learner view r${round}`, phase: 'Modules', schema: VIEW, effort: 'low', agentType: GP })
  if (!view || !view.ok || !view.text) { log(`${m.id}: learner view failed in round ${round}; the learner lens did not run`); return null }
  // 2. the learner works from the text in its prompt
  const r = await AG(learnerPrompt(m, round, view.text), { label, phase: 'Modules', schema: LEARNER, agentType: GP })
  if (!r) return null
  const fs = (r.findings || []).slice()
  // 3. quiz-check gate: the learner's choices against the answer key.
  // A project has no quiz. A module with a quiz and missing or partial choices
  // is a review gap: the key was not checked, so the status cannot be "ok".
  if (m.kind === 'project') return fs
  const choices = {}
  for (const [k, v] of Object.entries(r.quiz_choices || {})) {
    if (/^[a-z0-9][a-z0-9-]*$/.test(k) && Number.isInteger(v) && v >= 0) choices[k] = v
  }
  if (!Object.keys(choices).length) {
    log(`${m.id}: the learner gave no quiz choices; the quiz key was not checked in round ${round}`)
    fs.quizGap = true
    return fs
  }
  const qc = await AG(`Run exactly this command and report its JSON result. Exit code 1 means there are mismatches: that is a result, not a failure. Do not fix anything.
\`\`\`
${CB} quiz-check ${C} ${m.id} --partial --choices '${JSON.stringify(choices)}'
\`\`\`
Copy "ok", "checked", "missing" (quiz ids without a choice) and "mismatches" (each as id, chosen, answer). If the command exits with code 2 or crashes, set ok=false, checked=0, missing=[] and mismatches=[].`,
    { label: `${m.id} quiz check r${round}`, phase: 'Modules', schema: QUIZCHECK, effort: 'low', agentType: GP })
  if (!qc || (!qc.ok && !(qc.mismatches || []).length) || (qc.missing || []).length || !qc.checked) {
    log(`${m.id}: the quiz key was not fully checked in round ${round} (${qc ? 'missing: ' + ((qc.missing || []).join(', ') || 'none') + ', checked: ' + qc.checked : 'the check agent failed'})`)
    fs.quizGap = true
  }
  for (const x of (qc && qc.mismatches) || []) {
    fs.push({ id: x.id, kind: 'correctness', severity: 'high',
      problem: `Quiz key or wording may be wrong: a learner who used only the lessons chose option ${x.chosen}, but the key says option ${x.answer}.`,
      fix: 'Compare the question, the options and the key with the lesson. Fix the key if it is wrong. Fix the wording if it misleads. Fix the lesson if it does not teach the answer.' })
  }
  return fs
}

// Resume rule: with resume "modules" (or later) and no "redo", a module that
// already has a module.json that passes verify is skipped (cheap gate first).
const SKIP_DONE = ORDER.indexOf(RESUME) >= ORDER.indexOf('modules') && !REDO
if (ONLY) {
  const unknown = ONLY.filter(id => !modules.some(m => m.id === id))
  if (unknown.length) log(`args.only names ids that are not in the syllabus: ${unknown.join(', ')}`)
}
const todoModules = ONLY ? modules.filter(m => ONLY.includes(m.id)) : modules

const built = !runs('modules') ? [] : await pipeline(
  todoModules,
  async (m) => {
    if (SKIP_DONE) {
      const g0 = await gate(`${CB} verify ${C} --module ${m.id}`, `skip check: ${m.id}`, 'Modules')
      if (g0 && g0.ok) { log(`${m.id}: module.json exists and passes verify, skipped (use "redo": true to rebuild)`); return { id: m.id, ok: true, skipped: true, errors: [], open_findings: 0, review_gaps: [] } }
    }
    // 1. author
    const authored = await AG(`${TEAM}

TASK: write module ${m.id} "${m.title}" (kind ${m.kind}).
Read first: your module entry in ${C}/syllabus.json (objectives, concepts, rationale), the entries before and after it (do not teach later modules' concepts; you may use earlier ones), the inventory entries for your concepts (summary, pitfalls, source), and the cited source sections in ${C}/sources/.
Write ${C}/modules/${m.id}/module.json exactly in the module.json format of course-schema.md.
${m.kind === 'project' ? PROJECT_RULES : MODULE_RULES}

${VIDEO_RULES}
Turn inventory pitfalls into wrong solutions, hints and quiz options. Fixtures are embedded strings; no network.
Then run \`${CB} verify ${C} --module ${m.id}\`. Fix every error and re-run until ok is true (at most 8 runs). Then fix the warnings that are real.`,
      { label: `write ${m.id}`, phase: 'Modules', schema: AUTHORED, agentType: GP })
    if (!authored) return { id: m.id, ok: false, errors: ['author agent failed'], open_findings: 0, review_gaps: [] }

    // 2-3. review + revise, up to 2 rounds. Round 2 runs only the lenses that had
    // high or medium findings in round 1.
    let lastFindings = []
    let active = LENSES
    const gaps = new Set()
    for (let round = 1; round <= 2; round++) {
      const reviews = await parallel(active.map(lens => () => runLens(m, lens, round)))
      const seriousLenses = []
      lastFindings = []
      active.forEach((lens, i) => {
        const fl = reviews[i]
        if (!fl) { gaps.add(lens); return }
        if (fl.quizGap) gaps.add(lens)      // the learner ran, but the quiz key was not checked
        else gaps.delete(lens)
        lastFindings.push(...fl)
        if (serious(fl).length) seriousLenses.push(lens)
      })
      // next round: the lenses with serious findings, plus any lens whose agent failed
      active = LENSES.filter(l => seriousLenses.includes(l) || gaps.has(l))
      if (!serious(lastFindings).length) {
        if (round === 1 && active.length) continue
        break
      }
      await AG(`${TEAM}

TASK: revise ${C}/modules/${m.id}/module.json with these review findings (round ${round}). Apply every high/medium finding unless it conflicts with the sources or the rules; give a reason for each rejection. Cut what the depth reviewer marked as unneeded. Keep the module within the rules: the section/exercise pairing (5-6 screens), 1-3 concepts per section, the word range, hints, wrong solutions (one must fail the checks), the quiz, takeaways and videos. A finding that says "quiz key or wording may be wrong" means a learner who used only the lessons chose another option: check the key, the wording and the lesson. If you change a video, follow the video rules (WebSearch, only URLs you opened).
Then run \`${CB} verify ${C} --module ${m.id}\` until ok is true.
Findings:
${JSON.stringify(lastFindings, null, 2)}`,
        { label: `revise ${m.id} r${round}`, phase: 'Modules', schema: REVISED, agentType: GP })
    }
    const g = await gate(`${CB} verify ${C} --module ${m.id}`, `gate: ${m.id}`, 'Modules')
    return { id: m.id, ok: !!(g && g.ok), errors: g ? g.errors : ['gate failed'], open_findings: serious(lastFindings).length, review_gaps: [...gaps] }
  }
)
const moduleResults = built.filter(Boolean)
log(`Modules verified: ${moduleResults.filter(r => r.ok).length}/${todoModules.length}${ONLY ? ' (selected with "only")' : ''}`)

// ================================================================ COURSE REVIEW
let xr = null, fullGate = null
const droppedXr = []
if (runs('review')) {
phase('Course review')
xr = await AG(`${TEAM}

TASK: review the WHOLE course as one product: ${C}/syllabus.json and every ${C}/modules/*/module.json.
Look for problems no single-module review can see: concepts explained twice or in conflicting ways; quiz questions that repeat each other or an exercise across modules; takeaways that repeat each other; the same video in two modules, or videos that do not fit their module; difficulty jumps between modules; terms used inconsistently; recall exercises that do not revisit earlier modules; fixtures that are all the same; exercises that repeat each other across modules; a final project that ignores important modules; anything a later module assumes but no earlier module taught.
Each finding: module = a module id from ${C}/syllabus.json (for example m03; never a course name or "syllabus"), id = section/exercise id, severity, problem, fix.`,
  { label: 'cross-course review', phase: 'Course review', schema: XREVIEW, agentType: GP })

const fullGate1 = await gate(`${CB} verify ${C} --all`, 'gate: full verify', 'Course review')
const fixByModule = {}
for (const f of serious(xr ? xr.findings : [])) {
  if (!modules.some(m => m.id === f.module)) { droppedXr.push({ module: f.module, id: f.id, problem: f.problem }); log(`Dropped a cross-course finding: "${f.module}" is not a syllabus module id (${String(f.problem).slice(0, 100)})`); continue }
  (fixByModule[f.module] = fixByModule[f.module] || []).push(f)
}
for (const e of (fullGate1 ? fullGate1.errors : [])) {
  const mid = String(e).split(':')[0].trim()
  if (modules.some(m => m.id === mid)) (fixByModule[mid] = fixByModule[mid] || []).push({ id: mid, severity: 'high', problem: e, fix: 'make verify pass' })
}
const toFix = Object.keys(fixByModule)
if (toFix.length) {
  log(`Cross-course fixes in: ${toFix.join(', ')}`)
  await pipeline(toFix, async (mid) => AG(`${TEAM}

TASK: fix ${C}/modules/${mid}/module.json for these course-level findings. Apply each unless it conflicts with the sources or rules (give reasons). Change only this module's file. Then run \`${CB} verify ${C} --module ${mid}\` until ok is true.
Findings:
${JSON.stringify(fixByModule[mid], null, 2)}`,
    { label: `course fix ${mid}`, phase: 'Course review', schema: REVISED, agentType: GP }))
}
fullGate = await gate(`${CB} verify ${C} --all`, 'gate: final verify', 'Course review')
} else {
  fullGate = await gate(`${CB} verify ${C} --all`, 'gate: final verify', 'Ship')
}

// ================================================================ SHIP
phase('Ship')
// render checks that verify-report.json is current (inputs_hash). The last step
// before this point is always `verify --all`, so the report is current here.
const SHIP_PROMPT = (first) => `Run these two commands in order and report${first ? '' : ' as before'}:
\`\`\`
${CB} render ${C}
${CB} smoke ${C}
\`\`\`
render_ok = render exit code 0. verified = the "verified" value in the render JSON output. stale_report = the "stale_report" value in the render JSON output (false if it is absent). smoke_ok = smoke exit code 0. smoke_skipped = smoke printed "skipped": true (smoke-report.json then has ok=false and a "reason"). failures = each failing self-test result as "<id>: solution=<status> starter=<status> <solution_msg>", plus one "phone overflow: <text>" entry for each item of phone_overflow, plus one "ui: <text>" entry for each item of ui.problems, plus one "fallback: <text>" entry for each item of fallback.problems, plus any "fatal". (smoke-report.json holds these keys; the report is also in ${C}/reports/smoke-report.json.) console_errors = copy them. page = the "output" path from render. exercises = the "exercises" count from render.`
let ship = await AG(SHIP_PROMPT(true),
  { label: 'render + browser test', phase: 'Ship', schema: SHIP, effort: 'low', agentType: GP })

if (ship && !ship.smoke_ok && !ship.smoke_skipped && ship.failures.length) {
  // CPython passed but Pyodide did not: a version or platform difference.
  await AG(`${TEAM}

TASK: these exercises pass in local CPython but fail in the browser (Pyodide, pinned versions in course.meta.json), or the page has a problem:
${ship.failures.join('\n')}
Console errors: ${ship.console_errors.join(' | ') || 'none'}
Find the cause (a version difference, platform behaviour, timing) and fix the exercise data in ${C}/modules/<id>/module.json so it behaves the same in both. Do not edit the template or scripts. Re-run \`${CB} verify ${C} --module <id>\` for each module you change.`,
    { label: 'fix browser failures', phase: 'Ship', agentType: GP })
  // verify --all must run before render, so that the report is current.
  fullGate = await gate(`${CB} verify ${C} --all`, 'gate: verify after browser fix', 'Ship')
  ship = await AG(SHIP_PROMPT(false),
    { label: 're-render + browser test', phase: 'Ship', schema: SHIP, effort: 'low', agentType: GP })
}

// ---- status. "ok" needs every condition below. Otherwise "partial" with the reasons.
const reasons = []
if (!fullGate || !fullGate.ok) reasons.push('final verify did not pass (verify --all)')
if (!ship) reasons.push('render and browser test agent failed')
else {
  if (!ship.render_ok) reasons.push('render failed')
  if (!ship.verified) reasons.push(ship.stale_report ? 'render says the verify report is stale (the course changed after the last verify --all)' : 'render says the course is not verified')
  if (ship.smoke_skipped) reasons.push('browser test was skipped (no Chrome found)')
  else if (!ship.smoke_ok) reasons.push('browser test failed')
}
for (const r of moduleResults) {
  if (!r.ok) reasons.push(`module ${r.id} does not pass verify`)
  if (r.open_findings > 0) reasons.push(`module ${r.id} has ${r.open_findings} open review finding(s) (not re-reviewed after the last revision)`)
  if (r.review_gaps && r.review_gaps.length) reasons.push(`module ${r.id}: review lens did not finish its job (${r.review_gaps.join(', ')}); for the learner lens this includes a quiz key that was not fully checked`)
}
const allOk = reasons.length === 0

const report = await AG(`Write ${C}/reports/BUILD-REPORT.md for the person who asked for this course. Use short, plain sentences.
Include: the course title and module list with screen (exercise), quiz and takeaway counts, and the videos of each module with their verify warnings ("embedding disabled" or "could not check"), read from ${C}/reports/verify-report.json and ${C}/reports/video-check.json; sources used (from ${C}/sources/INDEX.md) and the version notes; final verify result (${C}/reports/verify-report.json: errors and warnings, grouped); the render "verified" value: ${ship ? JSON.stringify({ verified: ship.verified, stale_report: ship.stale_report }) : 'render did not run'} (say "verified" only when it is true); browser test result (${C}/reports/smoke-report.json; if it says skipped, say the browser test did not run); the overall status: ${allOk ? 'ok' : 'partial'}${reasons.length ? ', with these reasons: ' + JSON.stringify(reasons) : ''}; possible open issues: ${JSON.stringify({ modules: moduleResults.filter(r => !r.ok || r.open_findings || r.skipped), cross_review: xr ? xr.summary : 'n/a', dropped_cross_review_findings: droppedXr })} (note: "open_findings" counts high/medium findings from the LAST review round, before the final revision; they were not re-reviewed after it, so say "may be resolved, not re-reviewed"; "skipped" means the module was reused from an earlier run and not reviewed again); and how to open the page: ${ship ? ship.page : C + '/dist/index.html'}.
Do not claim anything the reports do not show. Return the report text.`,
  { label: 'build report', phase: 'Ship', effort: 'low', agentType: GP })

return {
  status: allOk ? 'ok' : 'partial',
  reasons,
  courseDir: C,
  page: ship ? ship.page : null,
  exercises: ship ? ship.exercises : null,
  verify: fullGate,
  browser: ship,
  modules: moduleResults,
  cross_review_summary: xr ? xr.summary : null,
  dropped_cross_review_findings: droppedXr,
  report: report,
}
