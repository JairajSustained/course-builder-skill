# Course data schema

Agents write JSON only. They never write HTML. `scripts/render.py` turns the
JSON into the page, and `scripts/verify.py` checks every rule marked **(V)**.

All ids are lowercase-kebab (`^[a-z0-9][a-z0-9-]*$`).

## Course folder

```
courses/<slug>/
  course.meta.json        cb.py init + cb.py runtime write this. Do not hand-edit runtime.
  sources/                cached docs (markdown). INDEX.md lists each file and its URL.
  inventory.json          the concept inventory
  drafts/                 syllabus drafts, review notes (working files)
  syllabus.json           the approved syllabus
  modules/<id>/module.json
  reviews/                review findings per stage (markdown or JSON)
  reports/                verify-report.json, smoke-report.json, video-check.json (oEmbed cache), BUILD-REPORT.md
  dist/index.html         the course page (local, CDN runtime)
  dist/artifact.html      the same page as an artifact fragment (vendored runtime)
```

## Terms used by the rules

- **Normalised code:** the code with comments and blank lines removed, trailing spaces removed, and white space inside a line collapsed. Two codes are "equal" when their normalised forms are equal.
- **Non-trivial line:** a line of code that is not blank, not a comment, not an `import`, and has 12 or more characters after `strip()`.
- **Key line:** a non-trivial line of `solution_code` that is not in `starter_code` (compared normalised). Key lines are the answer. Hints, instructions and check messages must not give them away.
- **Unambiguous API:** an `apis` name that is not also a method of a builtin type. A concept has unambiguous APIs when at least one of its `apis` is unambiguous.

## inventory.json

```json
{
  "concepts": [
    {
      "id": "find-all",
      "name": "find_all()",
      "summary": "Returns a list of every tag that matches a name, attributes, or a function.",
      "decision": "include",
      "reason": "Core of nearly every scraping task; beginners need it in week one.",
      "source": "sources/02-searching-the-tree.md#find_all",
      "apis": ["find_all"],
      "prereqs": ["make-soup", "tag-object"],
      "pitfalls": ["Returns a ResultSet (list), not a Tag: calling .get_text() on it fails."]
    }
  ]
}
```

- `decision` **(V)**: `include` (taught and practised), `defer` (named in the course as "for later" but not taught), `skip` (left out). Every decision has a `reason`.
- `source` **(V)**: a file in `sources/`, with an optional anchor. For an `include` concept, a missing or empty source is an error: a concept with no source is from memory.
- `apis` **(V)**: required (a list, maybe empty) for every `include` concept. The exact names that a learner's code uses for this concept:
  - method or attribute names: `"select_one"`, `"get_text"`, `"attrs"`
  - keyword arguments, with a trailing `=`: `"class_="`, `"string="`
  - class or function names: `"BeautifulSoup"`
  - string literals, in double quotes: `"\"lxml\""`

  A concept counts as **practised** only when the solution code of some exercise uses one of its
  unambiguous APIs (see "Coverage" below). A concept whose `apis` are all ambiguous or empty falls
  back to the exercise tag. So give every concept the exact names that its solutions use.

  Give each API to one include concept only: the one that teaches it first. `verify.py`
  parses all lesson code, starter code and solutions with `ast`. Code that uses an API
  before its concept is taught is an error. Names that are also methods of builtin types
  (`find`, `get`, `index`, `replace`, ...) give a warning only, because `text.find()` is
  not `Tag.find()`.
- `prereqs` **(V)**: ids from this file. The syllabus must not teach a concept before its prereqs.
- `pitfalls`: real beginner mistakes from the docs, FAQ, or issue trackers. Authors turn these into wrong solutions and hints.

## syllabus.json

```json
{
  "title": "Beautiful Soup from Zero",
  "tagline": "Pull clean data out of messy HTML.",
  "description": "Markdown. 2-4 short paragraphs: who this is for, what you build, how the course works.",
  "prerequisites": ["Python basics: variables, lists, loops, functions"],
  "outcomes": ["Extract every link and its text from a page", "..."],
  "modules": [
    {
      "id": "m01",
      "title": "Your first soup",
      "summary": "One or two sentences.",
      "objectives": ["Parse an HTML string with html.parser", "..."],
      "concepts": ["make-soup", "tag-object"],
      "kind": "module",
      "rationale": "Why this module is here and why at this position."
    },
    { "id": "m07", "title": "Final project: ...", "kind": "project", "concepts": [], "...": "..." }
  ]
}
```

- **(V)** Every `include` concept is in exactly one module's `concepts` (the module that first teaches it).
- **(V)** Prereqs come in an earlier module or the same module.
- **(V)** The last module has `kind: "project"`. Its `concepts` list must be empty (error otherwise), because the project teaches nothing new.
- **(V)** Each module of `kind: "module"` has 2-10 `concepts` (error outside that range). More than 8 gives a warning.
- 3-12 modules in total (warning outside that range).

## modules/<id>/module.json

```json
{
  "id": "m02",
  "title": "Finding things",
  "kind": "module",
  "summary": "...",
  "objectives": ["..."],
  "sections": [
    {
      "id": "m02-s1",
      "heading": "find() gets the first match",
      "body_md": "Markdown lesson text with ```python fenced examples```.",
      "concepts": ["find"]
    }
  ],
  "exercises": [
    {
      "id": "m02-e1",
      "title": "Grab the page title",
      "kind": "exercise",
      "after_section": "m02-s1",
      "concepts": ["find", "get-text"],
      "instructions_md": "Markdown. Name the variable to create. Describe the data in `html_doc`.",
      "setup_code": "from bs4 import BeautifulSoup\nhtml_doc = \"\"\"<html>...</html>\"\"\"\n",
      "starter_code": "soup = BeautifulSoup(html_doc, \"html.parser\")\n\n# Find the <h1> and store its text in title_text\ntitle_text = None\n",
      "solution_code": "soup = BeautifulSoup(html_doc, \"html.parser\")\ntitle_text = soup.find(\"h1\").get_text()\n",
      "hints": ["nudge", "pointer", "near-solution"],
      "test_code": "check('title_text' in ns, 'Create a variable named title_text.')\n...",
      "wrong_solutions": [
        { "why": "stores the Tag, not its text", "code": "..." },
        { "why": "uses find_all, gets a list", "code": "..." }
      ]
    }
  ],
  "quiz": [
    {
      "id": "m02-q1",
      "question_md": "What does `soup.find(\"p\")` return when the page has no `<p>` tag?",
      "options": ["An empty list", "None", "An empty string", "It raises an error"],
      "answer": 1,
      "explanation_md": "`find` returns `None` when nothing matches. `find_all` returns an empty list.",
      "concepts": ["find"]
    }
  ],
  "takeaways": [
    "`soup.find(name)` returns the first match, or `None`.",
    "`soup.find_all(name)` returns a list of every match.",
    "`tag.get_text(strip=True)` gives the text without extra white space."
  ],
  "videos": [
    {
      "url": "https://www.youtube.com/watch?v=XXXXXXXXXXX",
      "title": "The exact title of the video",
      "channel": "Channel name",
      "why": "One sentence: what the video adds to this module."
    }
  ]
}
```

A module is a list of **screens**. A screen is one section (the learn half) and the one
exercise that follows it (the do half). After the screens come the recall exercise, the
quiz and the takeaways. `render.py` builds the screens from this file.

### Project module

`kind: "project"`: 1-2 sections (the brief) **(V)**, 3-6 `project-step` exercises, `takeaways` (required **(V)**), no `quiz` **(V)**, `videos` optional. On the page, the brief sections are "learn" screens, the steps are "do" screens, and the takeaways screen comes last.

### Sections

- **(V)** Each concept that the syllabus gives this module is in some section's `concepts`.
- **(V)** In a `module`, every section has 1-3 `concepts` (error for 0 or more than 3).
- **(V)** In a `project`, a section's `concepts` may list only concepts that earlier modules teach (error otherwise). A project section never counts as teaching a concept.
- **(V, warning)** Each section's concepts are used by at least one exercise in the module. If not, the section is a padding candidate.
- **(V, warning)** Word count of a `module` section, not counting fenced code: under 50 or over 220 words gets a warning. Aim for 60-200 words of prose plus at most one short code example. More than one runnable code block also gets a warning.
- **(V, warning)** A `project` section over 500 words gets a warning.
- **(V)** A `project` has 1-2 sections: the brief.
- **(V)** Lesson code runs. All ```` ```python ````, ```` ```py ````, ```` ```python3 ```` and unlabelled blocks of one section are joined in order
  and run in one fresh namespace on the pinned versions. They must run without error.
  So each section's examples must be self-contained: define `html_doc` and make the soup
  inside the section. Mark fragments that cannot run (output, `requests.get(...)` context,
  a REPL session) as ```` ```text ```` or ```` ```pycon ````.
- **(V)** Concept order inside a module: an `exercise` may use only earlier modules'
  concepts plus the concepts of the sections up to and including its `after_section`.
  This applies to the `concepts` tags **and** to the APIs that the solution and starter
  code really use (see `apis` above). Lesson code may use only concepts taught up to and
  including its own section.
- **(V) Coverage from code, not tags.** A concept is **practised** only when the solution code of
  some exercise uses one of its unambiguous APIs. A concept whose `apis` are all ambiguous or empty
  falls back to the exercise tag. A concept that is not practised is an error.
- **(V)** An exercise tagged with a concept that has unambiguous APIs, but whose solution uses none
  of them, is an error (tag and API mismatch). Only these count as use: an attribute (`.text`), a
  keyword argument (`class_=`), a name that is called (`BeautifulSoup(...)`), an imported name, and a short string. A variable name
  never counts: `text = "x"` does not use the API `.text`.
- **(V) Screen exercise.** The exercise of a screen must tag at least one concept of its section.
  When such a concept has unambiguous APIs, the solution must use one of them. Error otherwise.
- **(V) Duplicates in the whole course.** Two exercises with the same normalised `solution_code`,
  or the same normalised `instructions_md`, are an error.
- **(V) Lesson copy.** A normalised `solution_code` that is a substring of a normalised lesson code
  block of the same module is an error ("solution copies the lesson example"). The rule applies only to solutions of 12 or more normalised characters.
- Raw HTML in any Markdown field shows as plain text. Write "the `<h1>` tag" or
  "the <h1> tag"; both display as written. Links other than `http(s)://`, `#` or
  `mailto:` are removed.

### Exercises

- `kind` **(V)**: `exercise` (normal), `recall` (practises an earlier module, no lesson link), `project-step` (only in the project module).
- **(V)** A `module` has 5-6 `exercise` items and at most 1 `recall`. Modules after the first should have 1 `recall` (warning if missing).
- **(V)** A `project` has 3-6 `project-step` items, and no other kind.
- **(V)** `after_section` is a section id of this module for `exercise`. It must be `null` for `recall` and `project-step`.
- **(V) Pairing rule (screen shape), for a `module`.** Every section is followed by exactly one
  `exercise` whose `after_section` is that section. There are three faults:
  a section with no exercise, two exercises after one section, and exercises listed in a different
  order than their sections. A module has 5-6 sections and so 5-6 pairs. The `recall` exercise is
  not part of a pair.
- **(V)** Every concept in `concepts` is taught in this module's sections or assigned to an earlier module.
- **(V)** `hints`: exactly 3 for `exercise` and `recall`, exactly 1 for `project-step`. Each hint has at least 4 words.
- **(V)** `instructions_md` has at least 12 words.
- **(V) Hint leaks.** Hint 1 and hint 2 must not contain any key line (normalised; inline code spans and fenced blocks are checked too). Hint 3 (and the one project hint) must not contain all key lines, and must not contain more than 60% of the key-line tokens. The token rule counts only new names: it skips Python keywords and names that the starter or setup already has, and it applies only when 3 or more new names remain.
- **(V)** `instructions_md` and the messages of `check(...)` calls in `test_code` (string literals) must not contain a key line.
- **(V)** At least 1 wrong solution (2+ recommended). A wrong solution is never identical to the solution.
- **(V) Wrong solutions fail the checks.** At least one wrong solution per exercise must end with status `fail` (it ran, reached the checks and failed them). Error otherwise: "no wrong solution reaches the checks". A wrong solution that ends in `error` or `timeout` is allowed, but it does not count for this rule.
- **(V)** A wrong solution that is empty, or equal to the starter after normalising white space and comments, is an error.
- **(V) Unique ids.** Exercise ids and quiz ids are unique in the whole course, not only in one module. Start each id with the module id (`m02-e1`, `m02-q1`).

### Hidden code: `setup_code` and `carry_code`

- `setup_code`: the visible fixture. The page shows it on the **Data** tab.
- `carry_code` (optional, any exercise): hidden code that runs after `setup_code` and before the learner code. A project step uses it to recreate the results of earlier steps (the reference code of those steps). The learner never sees it.
- Execution everywhere is `setup_code + "\n" + carry_code`, then the learner code, then `test_code`.
- `render.py` writes two fields for each exercise in the page data: `setup_code` = `setup_code + "\n" + carry_code` (this is what runs), and `data_code` = `setup_code` only. The Data tab shows `data_code` (it falls back to `setup_code` if `data_code` is missing). `cb.py learner-view` also shows `setup_code` only.
- **(V)** `setup_code` must not contain a non-trivial line of the `solution_code` of ANOTHER exercise in the same module. Error: "setup_code shows another step's answer; move it to carry_code". It must also not contain a key line of its own exercise (a solution line that is not in the starter code): the Data tab would show the answer.

### Quiz

`quiz` is a list. A `module` needs 1-2 items **(V)**. A `project` has none: leave it out or use `[]` **(V)**.
Each item has these fields, and all are required **(V)**:

| field | rule |
|---|---|
| `id` | lowercase-kebab, unique in the module, for example `m02-q1` |
| `question_md` | markdown, at least 6 words |
| `options` | 3-5 strings, all non-empty and distinct (case-insensitive). Fewer than 3 is an error |
| `answer` | integer index into `options` (0-based) |
| `explanation_md` | markdown, at least 8 words. It says why the right option is right and why the best distractor is wrong |
| `concepts` | non-empty list of concept ids that this module or an earlier module teaches |

- **(V, warning)** An option that says "all of the above" or "none of the above".
- **(V, warning)** Course-wide: every quiz item has the same `answer` index (checked when there are 3 or more items).
- **(V, warning)** The correct option is the longest by more than 50% in more than half of the items.
- **Quiz key check (not in verify.py).** The workflow's learner agent answers every question from the lessons alone. `cb.py quiz-check <course dir> <module id> --choices '{"m02-q1": 1}'` compares the choices with `answer`. With `--partial`, a quiz id without a choice is listed in `"missing"` and only the answered ids are checked. It prints `{"ok": bool, "checked": n, "missing": [...], "mismatches": [{"id", "chosen", "answer"}]}` and exits 0 (ok), 1 (mismatch) or 2 (bad input). A mismatch is a high finding: "quiz key or wording may be wrong".
- Quiz results count toward module progress on the page.

### Takeaways

- `takeaways` is a list of 3-8 non-empty markdown strings **(V)**. It is required for `module` and for `project`.
- Each string is one cheat-sheet line that a learner keeps. It has at least 4 words **(V)**.
- **(V)** At least one takeaway of a module contains inline code.
- **(V)** Two takeaways of one module that are equal after normalising (case, white space) are an error.

### Videos

- `videos` is optional: a list of 0-2 objects **(V)**. Each object has `url`, `title`, `channel` and `why`. All four are non-empty strings **(V)**.
- `url` **(V)**: `https://www.youtube.com/watch?v=<11 characters>` or `https://youtu.be/<11 characters>`. Other hosts are an error. The same URL twice in one module is an error.
- `title` is the exact title of the video, with at least 2 words **(V)**. `why` is one sentence.
- **(V)** `channel` must match the oEmbed `author_name` (case-insensitive, normalised; one may contain the other). The normalised channel needs 3 or more characters, and the shorter name must be at least 60% of the longer. Error otherwise. `verify.py` stores `author_name` in the cache.
- **(V)** The video must relate to the course topic. The significant words of the `topic` in `course.meta.json` are taken (words under 3 characters and stop words such as "the", "python", "library" are dropped). At least one must appear in the oEmbed title or author (case-insensitive). Error: "video looks unrelated to the topic".
- Online check **(V)**: unless `--no-run` is given, `verify.py` asks `https://www.youtube.com/oembed?url=<url>&format=json` (15 s timeout) for each URL.

  | answer | result |
  |---|---|
  | HTTP 200 | The oEmbed title is compared with `title` (case-insensitive, by words). An error if less than half of the words of `title` are in the oEmbed title |
  | HTTP 400 or 404 | error: "video does not exist" |
  | HTTP 401 or 403 | warning: "embedding disabled". The link still works |
  | network failure or other HTTP code | warning: "could not check" with `--module`; **error** with `--all` |

  With `--all` (the final gate), a network failure is not a pass, so "could not check" is an error.
  Answers 200, 400, 401, 403 and 404 are cached in `reports/video-check.json`, keyed by URL, for 7 days. The cache holds the oEmbed title and `author_name`. Network failures are not cached.
- The page shows the videos in a collapsed "Optional: watch" box on the module's first screen and as links with thumbnails on the takeaways screen. They open in a new tab.

### Old modules

A module that was written before v2 has no `quiz` and no `takeaways`. It fails verify. This is intended. `render.py` still renders such a module without a crash.

### Execution rules (verify.py runs these in real CPython; the page runs the same harness in Pyodide)

- Run order: `setup_code`, `carry_code`, then the learner code, then `test_code`.
- **(V)** `solution_code` must pass.
- **(V)** `starter_code` must not pass.
- **(V)** Every wrong solution must not pass.
- **(V, warning)** A check that raises an exception instead of calling `check(...)` with a message gives the learner a confusing message. Guard it, for example `check(isinstance(ns.get('x'), list), 'x should be a list')` before you index `ns['x']`.
- Time limit: 5 seconds for the learner code and 5 seconds for the checks. The learner's clock starts
  AFTER `setup_code` (and `carry_code`) finish: the setup is not traced by the learner limit. **(V)** The page watchdog (15 s) covers setup and learner code together, so verify gives an error when setup and carry code take more than 8 s. The checks keep their own budget. A learner's
  `except Exception:` cannot catch the time limit. Code that the trace-based limit cannot
  stop (a C-level loop such as `sum(range(10**10))`, or a bare `except:` around a loop)
  is stopped by the verify subprocess timeout (25 s), and on the page by a 15 s watchdog
  that ends the Python worker and starts it again. The browser tab never freezes.

### test_code contract

`test_code` runs in its own namespace with:

| name | meaning |
|---|---|
| `ns` | dict of the learner's variables after their code ran |
| `printed` | everything the learner's code printed (str) |
| `check(cond, msg)` | records a check; the first failure stops and shows `msg` |

Good checks:
- Check values and types, not printed text. Use `printed` only when printing is the skill.
- Write messages that help the learner and do not give the answer away. "title_text should be a string, but it is a Tag. Which method gives you the text inside a tag?" is good. "Use .get_text()" gives the answer away.
- Check in order: does the variable exist, then the type, then the shape (length), then the exact value.
- Compute expected values from the fixture in `setup_code` where you can. Do not hand-copy long strings.

## Reports and render output

- `reports/verify-report.json`: written by every `verify.py --all` run, also after a fatal error
  (`{"ok": false, "fatal": "...", "scope": "all", "errors": [{"where": "fatal", "message": "..."}]}`).
  Every report has `inputs_hash` (from `scripts/coursehash.py`: `inventory.json`, `syllabus.json` and
  every `module.json`). `verify.py --all --no-run` stores `"ok": false` and `"no_run": true`, because
  no code ran. Such a report never gives `verified: true`.
- `cb.py verify` deletes `reports/verify-report.json` before a `--module` or `--all` run. A module change
  makes the old report stale, and only `--all` writes a new one.
- `reports/smoke-report.json`: `cb.py smoke` deletes the old file before it runs. When Chrome is missing it
  writes `{"ok": false, "skipped": true, "reason": "..."}`.
- `cb.py render` prints JSON with `verified`. If `verify-report.json` is missing, or its `inputs_hash`
  differs from the current hash, it prints `"verified": false` and `"stale_report": true`. It then ignores
  the report's unverified list and marks no exercise as verified. Otherwise `verified` is the report's `ok`.
- The workflow reports `status: "ok"` only when the final verify is ok, render says `verified: true`,
  the smoke test ran and passed, and no module has an open review finding. Otherwise it reports
  `status: "partial"` with a `reasons` list.

## Markdown images

`render.py` removes every `<img>` tag whose `src` does not start with `https://i.ytimg.com/` (YouTube
thumbnails). The learner sees the `alt` text instead. A course never needs an image from another host.
