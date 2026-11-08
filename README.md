# course-builder

A skill for Claude Code and pi that turns a topic into an interactive beginner course, in the style
of DataQuest or freeCodeCamp. You give a topic, for example "Beautiful Soup". You get one HTML
page where learners read a short explanation, write real Python in the browser, and get
automatic checks.

## What a course has

- A syllabus, then modules. Each module has 5-6 screens.
- Each screen has a short explanation and one coding task.
- Each module ends with a 1-2 question quiz and a takeaways cheat sheet.
- Optional, verified YouTube links (0-2 per module).
- A final project on a larger realistic data set.
- A real code editor, separate **Run** and **Submit** buttons, and a Data tab.
- Productive friction: hint k unlocks only after k real attempts, and the solution unlocks
  only after several attempts and all hints.
- Progress in the browser, a completion page, and a catalog of all your courses.

## How it works

The skill runs a workflow of agents that acts like a curriculum team:

1. **Sources.** It fetches the official docs. If it cannot find them, it stops and asks you.
2. **Inventory.** It lists the concepts from the docs. It decides what to include, defer or skip.
3. **Syllabus.** Three drafts from different angles. Judges compare them. Reviewers check the result.
4. **Modules.** One agent writes each module. Reviewers check depth, pedagogy, quiz and
   takeaways. A simulated learner tries the exercises.
5. **Course review.** One agent checks the whole course for gaps and repeats.
6. **Ship.** It renders the page and tests every exercise in a real browser.

Scripts do the checks that an agent cannot be trusted to do (`scripts/verify.py`):

- every reference solution passes, and every starter fails;
- at least one wrong solution reaches the checks and fails;
- lesson code runs on the pinned package versions;
- no exercise uses an API before a lesson teaches it (an AST check);
- hints, instructions and check messages do not leak the answer;
- no duplicate exercises, no copies of the lesson example;
- quiz, takeaways and video links meet their rules (each video is checked with YouTube oEmbed).

## Requirements

- [Claude Code](https://claude.com/claude-code) (its Workflow tool runs the build), or
  [pi](https://github.com/badlogic/pi-mono) (the bundled runner does, see below).
- `python3`, [`uv`](https://docs.astral.sh/uv/), and Node.js 22 or later.
- Google Chrome (for the browser test).
- Internet access: the docs fetch, PyPI, and the CDNs below.
- For pi: a web search tool. The runner enables `web_search_exa` / `web_fetch_exa` from the
  [`pi-exa`](https://www.npmjs.com/package/pi-exa) package (`pi install npm:pi-exa`).

Topics: Python, or Python libraries that run in [Pyodide](https://pyodide.org)
(the Pyodide distribution, or pure-Python packages from PyPI).

## Install

Clone the repo into a skills folder:

```bash
# Claude Code, for one project
git clone https://github.com/JairajSustained/course-builder-skill .claude/skills/course-builder
# Claude Code, for all your projects
git clone https://github.com/JairajSustained/course-builder-skill ~/.claude/skills/course-builder

# pi, for one project (a symlink keeps one copy of the skill)
mkdir -p .pi/skills
ln -s ../../.claude/skills/course-builder .pi/skills/course-builder
# pi, for all your projects
ln -s ../.claude/skills/course-builder ~/.pi/agent/skills/course-builder
```

pi also discovers `.agents/skills/` and `~/.agents/skills/`. Run `/reload` in an open pi
session after adding the folder.

## Use

In Claude Code, ask:

```
make a course on Beautiful Soup
```

In pi, ask the same thing, or force the skill with
`/skill:course-builder make a course on Beautiful Soup`. Either way the build is long, so in pi
the skill starts it in the background and polls `<course dir>/reports/workflow.log`.

On pi the workflow runs through the bundled runner, which gives every agent its own
`pi --print` child process:

```bash
node .pi/skills/course-builder/workflow/run-workflow.mjs \
  --script .pi/skills/course-builder/workflow/build-course.js \
  --args '{"topic":"Beautiful Soup","slug":"beautiful-soup","courseDir":"/abs/path/courses/beautiful-soup","skillDir":"/abs/path/.pi/skills/course-builder","today":"2025-01-30"}'
```

Options: `--concurrency` (default 3), `--timeout` seconds per agent (default 2700),
`--model`, and `--dry-run`. Every agent runs on the model your pi session is on when you start
the runner (pi exports it as `PI_PROVIDER`/`PI_MODEL`), so switching with `/model` changes what
the build uses. Override with `--model` or `COURSE_BUILDER_MODEL`; the start line of
`reports/workflow.log` names the model and where it came from. The runner writes the workflow's
return value to `<course dir>/reports/workflow-result.json`, a per-agent run list to
`workflow-runs.json`, and every prompt and answer to `reports/workflow/`.

The course goes to `courses/<slug>/`. Open it with:

```bash
python3 .claude/skills/course-builder/scripts/cb.py open courses/<slug>
```

The build uses many agents. In Claude Code every agent runs on Sonnet; to change that, edit
`MODEL` at the top of `workflow/build-course.js`. In pi the agents run on the model of the
session that starts the build; pass `--model` to the runner to pin another one.

## Files

| Path | Role |
|---|---|
| `SKILL.md` | The entry point that the harness reads |
| `workflow/build-course.js` | The team workflow |
| `workflow/run-workflow.mjs` | The pi runner: provides the workflow primitives and runs each agent as a `pi --print` child process |
| `scripts/cb.py` | The command-line tool that the agents use |
| `scripts/verify.py` | All quality gates |
| `scripts/harness.py` | Runs one exercise (local Python and the browser) |
| `scripts/render.py`, `templates/course.html` | Course data to the learner page |
| `scripts/smoke.mjs` | Browser test in headless Chrome |
| `scripts/fetch_source.py` | Docs page to Markdown |
| `scripts/catalog.py`, `scripts/serve.py` | Course catalog and local server |
| `references/` | Course schema, pedagogy rules, source rules |

## Runtime dependencies (not part of this repo)

The course page loads these at runtime from CDNs. They are not bundled here.

- [Pyodide](https://github.com/pyodide/pyodide) 0.28.3, from cdn.jsdelivr.net. Mozilla Public License 2.0.
- [CodeMirror 5](https://github.com/codemirror/codemirror5) 5.65.16, from cdnjs.cloudflare.com. MIT License.
- Fonts from Google Fonts.

`scripts/fetch_source.py` downloads documentation pages that you choose. Those pages keep their
own licenses. You are responsible for their terms if you publish a course or its `sources/` folder.
See `references/sources.md`.

## License

MIT. See [LICENSE](LICENSE).
