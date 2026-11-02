# course-builder

A Claude Code skill that turns a topic into an interactive beginner course, in the style of
DataQuest or freeCodeCamp. You give a topic, for example "Beautiful Soup". You get one HTML
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

- [Claude Code](https://claude.com/claude-code). The workflow needs Claude Code's Workflow tool.
- `python3`, [`uv`](https://docs.astral.sh/uv/), and Node.js 22 or later.
- Google Chrome (for the browser test).
- Internet access: the docs fetch, PyPI, and the CDNs below.

Topics: Python, or Python libraries that run in [Pyodide](https://pyodide.org)
(the Pyodide distribution, or pure-Python packages from PyPI).

## Install

Clone the repo into a skills folder:

```bash
# for one project
git clone https://github.com/JairajSustained/course-builder-skill .claude/skills/course-builder
# or for all your projects
git clone https://github.com/JairajSustained/course-builder-skill ~/.claude/skills/course-builder
```

## Use

In Claude Code, ask:

```
make a course on Beautiful Soup
```

The course goes to `courses/<slug>/`. Open it with:

```bash
python3 .claude/skills/course-builder/scripts/cb.py open courses/<slug>
```

The build uses many agents. Every agent runs on Sonnet. To change the model, edit `MODEL`
at the top of `workflow/build-course.js`.

## Files

| Path | Role |
|---|---|
| `SKILL.md` | The entry point that Claude Code reads |
| `workflow/build-course.js` | The team workflow |
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
