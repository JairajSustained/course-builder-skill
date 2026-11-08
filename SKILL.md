---
name: course-builder
description: Build an interactive DataQuest/freeCodeCamp-style course on a Python topic or library (syllabus, short lessons, 5-6 learn-and-do screens per module with automatic checks and a hint ladder, a short quiz, takeaways and optional videos, final project) grounded in the official docs, verified by running every solution, and rendered as one HTML page where learners write and run real Python. Use when the user asks to create, make, build or generate a course, curriculum, learning track or practice exercises for a topic, e.g. "make a course on Beautiful Soup".
---

# course-builder

You turn a topic into a verified, interactive beginner course. A team of agents does
the work in a workflow, the way a curriculum team works: draft, review each other, fix.
Scripts check facts that an agent cannot be trusted to check.

## Harnesses

This skill runs the same workflow in two harnesses; everything except step 3 is identical.

- **Claude Code:** the workflow runs inside the Workflow tool (`workflow/build-course.js`).
- **pi:** there is no Workflow tool, so `workflow/run-workflow.mjs` runs the same script and
  gives every agent its own `pi --print` child process. See step 3.

For pi, put this folder where pi discovers skills: `<project>/.pi/skills/course-builder/`
(a symlink is fine), `~/.pi/agent/skills/course-builder/`, or `.agents/skills/course-builder/`.

## Fixed design decisions

- **Learner level:** always beginner (knows basic Python). Do not ask.
- **Fully automatic:** no approval stops for the syllabus or modules.
- **The one exception:** if the official docs cannot be found or are not enough, the
  build stops and asks the user for sources. Never write a course from memory.
- **Output:** one HTML page (`dist/index.html`). Learners run Python in the browser
  (Pyodide). The skill chooses this format. Do not ask.
- **Location:** courses go in `<project root>/courses/<slug>/`.
- **Models:** in Claude Code every workflow agent runs on Sonnet (`MODEL` at the top of
  `workflow/build-course.js`). In pi every agent runs on the model the calling pi session is
  using right now (pi exports it to the shell as `PI_PROVIDER`/`PI_MODEL`), so the build follows
  whatever you selected with `/model`. Override with `--model` or `COURSE_BUILDER_MODEL`; with
  neither and no pi session, a fresh pi session's default is used. Do not change it without
  asking the user.

## Course format (v2)

A course is a list of modules. A module is a list of **screens**, in the style of DataQuest.

- **Screen:** one short section (60-200 words, at most one code example, 1-3 concepts) and exactly one
  exercise after it. The exercise practises a concept of its section, and its solution uses that concept's API. The page shows the text on the left and the editor on the right.
  A module has 5-6 screens.
- **Recall:** from module 2, one exercise that brings back an earlier concept.
- **Quiz:** 1-2 multiple-choice questions per module, with 3-5 options. Wrong options are real misconceptions.
  The project has no quiz. A learner agent answers every question from the lessons alone, and
  `cb.py quiz-check` compares those answers with the key (a mismatch is a high finding).
- **Takeaways:** 3-8 cheat-sheet lines at the end of every module and of the project.
- **Videos:** optional, 0-2 per module. They are YouTube links that the learner may watch.
  Authors find them with WebSearch and use only URLs that they opened.
  `verify.py` checks each URL with YouTube oEmbed: the video must exist, the title must match, the
  channel must match the author, and the video must relate to the course topic.
  A video never replaces an exercise.
- **Project:** the brief (1-2 sections), 3-6 project steps, takeaways. The project teaches no new
  concept. Its `setup_code` is the shared fixture only. The reference code of earlier steps goes
  in the hidden `carry_code`, so the Data tab never shows an answer.
- **Checks:** every exercise has wrong solutions. At least one of them must reach the checks and fail
  them. A concept counts as practised only when a solution really uses its API (read with `ast`),
  not when an exercise only carries its tag. A variable name does not count as use of an API.
- **Parallel authors:** `verify --module` reports a clash with another module (duplicate solution,
  duplicate instruction, id reuse) as a warning, because that module may still change. `verify --all`
  makes it an error. A module with a quiz and missing quiz choices from the learner lens counts as a
  review gap, so the status is `partial`.

The exact rules are in `references/course-schema.md`. The teaching rules are in
`references/pedagogy.md`. An old module without `quiz` and `takeaways` fails verify.
Rebuild it with `"resume": "modules"`.

## Steps

1. **Name the course.** From the user's topic, make a slug (lowercase-kebab, for example
   `beautiful-soup`). The course directory is `<cwd>/courses/<slug>`, as an absolute path.
   If that directory already has a `course.meta.json`, it is a resume (see below).

2. **Initialise:**
   ```
   python3 <skill dir>/scripts/cb.py init <course dir> --topic "<topic as the user said it>"
   ```

3. **Run the workflow.** The harness decides how, and only how, the agents start:
   - **If you have a Workflow tool (Claude Code): use it.** Do not run `run-workflow.mjs` and do
     not call `pi` from Claude Code. The workflow's agents are Claude Code subagents.
   - **If you are pi (no Workflow tool): run the runner in the background** as described below.

   Both branches run the same `workflow/build-course.js` and return the same value, so step 4
   applies to either. Both take the same args:
   `{"topic": "...", "slug": "...", "courseDir": "<abs course dir>", "skillDir": "<abs skill dir>", "today": "<YYYY-MM-DD>"}`,
   with the optional `"resume"`, `"only"` and `"redo"` (see Resume).

   **Claude Code (Workflow tool): call it** with `scriptPath` `<skill dir>/workflow/build-course.js`
   and the args above. Tell the user in one line that the build runs in the background, and that
   they can watch it with `/workflows`.

   **pi (no Workflow tool): run the runner in the background.** The runner executes the same
   `workflow/build-course.js` and gives each agent its own `pi --print` child process:
   ```bash
   cd <project root>
   mkdir -p <course dir>/reports
   nohup node <skill dir>/workflow/run-workflow.mjs \
     --script <skill dir>/workflow/build-course.js \
     --args '<args JSON on one line>' \
     > <course dir>/reports/workflow.out 2>&1 < /dev/null &
   disown
   ```
   Tell the user in one line that the build runs in the background, where the log is
   (`<course dir>/reports/workflow.log`), and that the run takes a long time (many agents).
   Then poll, do not block: run `tail -n 30 <course dir>/reports/workflow.log` every few minutes
   with bash, and when the log has a line starting `done:` read
   `<course dir>/reports/workflow-result.json`. That file holds exactly what the Workflow tool
   would have returned, so step 4 applies unchanged. While you wait, `kill -0 $(cat <course dir>/reports/workflow.pid)`
   tells you the run is still alive. If it died without a `done:` line, read the end of
   `reports/workflow.out` and `reports/workflow/stderr.log`.

   Runner facts: `--concurrency` (default 3, `CB_CONCURRENCY`) agents at once, `--timeout`
   (default 2700 s) per agent, `CB_MAX_AGENTS` (default 400) as a cost guard. Every agent's
   prompt, stdout and JSON answer is kept in `<course dir>/reports/workflow/`, and
   `reports/workflow-runs.json` lists every run with its duration. Agents run on the model of the
   pi session that starts the runner (`PI_PROVIDER`/`PI_MODEL`), so they follow your current
   `/model`; pass `--model` or set `COURSE_BUILDER_MODEL` to pin another one, and the start line
   of `workflow.log` names the model and where it came from. Source discovery and video search
   need a web search tool: with the `pi-exa` package the runner enables `web_search_exa` and
   `web_fetch_exa` and rewrites those names in the agent prompts. A `--dry-run` prints every
   agent call without spending anything.

4. **When the workflow returns,** act on `status`:
   - `ok`: all of these are true: the final `verify --all` passed, `cb.py render` printed
     `"verified": true`, the browser test passed (it was not skipped), and no module has an open review
     finding. Give the page path, the module list with screen, quiz and video counts, and the key lines of
     `reports/BUILD-REPORT.md`. Offer to open it in the browser with `cb.py open` (see below).
   - `partial`: at least one condition above is false. The result has `reasons` (a list). Show each reason
     to the user. Add the details: verify errors, browser failures, unresolved review findings. A skipped
     browser test (no Chrome) is a reason too. Do not call it done. Offer a resume run for the failed stage.
   - `needs_sources`: tell the user what is missing (`missing`) and where they can get it
     (`suggestions`). They can paste a URL, or drop files (markdown, text, or a PDF that you
     convert to markdown) into `<course dir>/sources/` with the front matter from
     `references/sources.md`. Then resume with `"resume": "inventory"`. The resume rebuilds
     `sources/INDEX.md`, pins the runtime if it is missing, and judges the sources again.
   - `unsupported_runtime`: explain that this version runs only Python in the browser.
     Quote the reason. Do not build anyway.
   - `failed`: report the stage and the errors. Offer to resume from that stage after a fix.

## Open and catalog

- `python3 <skill dir>/scripts/cb.py open <course dir or courses root>` starts (or reuses) a local
  web server for the courses folder and opens the course (or the catalog) in the browser.
  It prints the URL as JSON. Use this to show a finished course to the user.
- `python3 <skill dir>/scripts/cb.py catalog <courses root>` writes `<courses root>/index.html`,
  a list of every built course with its progress. `cb.py open` rebuilds it each time.

## Resume

Add `"resume": "<stage>"` to the args to skip earlier stages and reuse their files.
The stages are `inventory`, `syllabus`, `modules`, `review` and `ship`. In pi, re-run the
runner with the same args plus the new `"resume"`. In Claude Code, to re-run a crashed
workflow with cached agent results, use `resumeFromRunId` with the same `scriptPath` and args.
The runner keeps no agent cache: a resumed pi run starts its agents fresh, but it reuses every
file the earlier stages wrote, which is what saves the time.

With `"resume": "modules"`, a module that already has a `module.json` and passes
`cb.py verify --module` is skipped (a cheap check runs first). Two more args control this:

- `"only": ["m03", "m05"]`: only these modules go through the module stage.
- `"redo": true`: rebuild the modules even if they pass verify.

A skipped module is not reviewed again. The build still ends with the course review (when the
stage runs), `verify --all`, render and the browser test.

## Rules for you (the main loop)

- Do not write course content yourself, and do not hand-edit the generated HTML. All
  content comes from the workflow's agents. All HTML comes from `scripts/render.py`
  and `templates/course.html`.
- Do not add a video yourself. Videos come from the workflow's authors, and verify checks them.
- Do not report a course as verified unless the output of `cb.py render` says `"verified": true`
  and `reports/smoke-report.json` has `ok: true`. `render` sets `verified` from
  `reports/verify-report.json`: the report must exist, be `ok`, and match the current course files
  (`inputs_hash`). If the output says `"stale_report": true`, the course changed after the last
  `verify --all`: run it again. A skipped smoke test (no Chrome) has `ok: false` and `skipped: true`:
  say that the browser test did not run. That is not a pass.
- To fix one module after the build: edit it through an agent that runs
  `cb.py verify <dir> --module <id>` until it passes. Then run `cb.py verify <dir> --all` (it
  writes a new `reports/verify-report.json`), then `cb.py render` and `cb.py smoke`.
  `verify --module` deletes the old report, because a module change makes it stale.

## Files

| path | role |
|---|---|
| `workflow/build-course.js` | the team workflow (sources → inventory → syllabus → modules → course review → ship) |
| `workflow/run-workflow.mjs` | pi runner: provides `agent`, `parallel`, `pipeline`, `phase`, `log` and `args` to `workflow/build-course.js` and runs every agent as a `pi --print` child process. Not used in Claude Code |
| `scripts/cb.py` | the one CLI the agents use: `init`, `runtime`, `fetch`, `index`, `verify`, `try`, `learner-view`, `quiz-check`, `render`, `smoke`, `status`, `catalog`, `open`. `learner-view` prints what the page shows (lessons, instructions, starter code, the Data tab, quiz questions without answers). `quiz-check <dir> <module> --choices '{"m02-q1": 1}'` compares learner answers with the key (exit 0 ok, 1 mismatch, 2 bad input) |
| `scripts/coursehash.py` | `inputs_hash(course_dir)`: a hash of `inventory.json`, `syllabus.json` and every `module.json`. `verify.py` stores it in the report. `render.py` compares it, to find a stale report |
| `scripts/harness.py` | runs one exercise. The same code runs in CPython (verify) and in Pyodide (the page). The learner time limit starts after `setup_code` and `carry_code` finish |
| `scripts/verify.py` | deterministic checks: schema, concept order (tags and real API use, via `ast`), coverage from real API use, hint and instruction leaks, screen pairing, duplicates and lesson copies, quiz, takeaways and content floors, videos (YouTube oEmbed: title, channel, topic), unique ids, and execution (lesson code runs; the solution passes; the starter fails; wrong solutions fail, and at least one reaches the checks). `--all` always writes `reports/verify-report.json` |
| `scripts/fetch_source.py` | docs page → faithful markdown in `sources/`; splits long pages |
| `scripts/render.py` | JSON → `dist/index.html` (local) and `dist/artifact.html` (artifact fragment). Prints `verified` and `stale_report`. Removes external images from Markdown |
| `scripts/smoke.mjs` | headless Chrome: runs every solution and starter in real Pyodide, walks the page UI, checks phone layout. Writes `reports/smoke-report.json` (`skipped: true` and `ok: false` when there is no Chrome) |
| `templates/course.html` | the learner page: syllabus home, learn-and-do screens, editor, Run and Submit, hint ladder, gated solution, quiz, takeaways. Python runs in a Web Worker with a watchdog |
| `references/pedagogy.md` | depth rules (thin vs padded), screen rules, friction rules, quiz, takeaways and video rules, review rubric |
| `references/course-schema.md` | the JSON formats and every rule verify.py enforces |
| `references/sources.md` | what to fetch, how to store it, when to stop and ask |

## Known limits

- Python topics only (Python itself, libraries that are in the Pyodide distribution, or
  pure-Python libraries from PyPI, which the scout pins as exact `name==version`). Other languages
  return `unsupported_runtime`.
- No live network in exercises. Pyodide has no sockets, so all data is embedded.
- The video check needs internet. With `--module`, a network failure gives the warning "could not check".
  With `--all` (the final gate), "could not check" is an error, because a network failure is not a pass.
  `--no-run` skips the video check.
- The page loads Pyodide from the jsDelivr CDN, so learners need internet access the first time.
- The page also loads the CodeMirror editor from the cdnjs CDN and fonts from Google Fonts. If cdnjs is
  blocked or slow (over 8 s), the page uses a plain textarea editor. If jsDelivr is blocked, the page
  shows a red notice with a Retry button, because Python cannot run.
- The catalog shows progress only when it and the courses come from one local server (one origin),
  because the browser keeps progress per origin. Use `cb.py open` for this.
- Publishing as a claude.ai Artifact is not wired up yet. An artifact page cannot fetch
  from a CDN at runtime, so the Pyodide runtime files would have to be published next
  to the page. `dist/artifact.html` is prepared for that, but this path has no test yet.
