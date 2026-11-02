# Pedagogy rules and review rubric

Every author and reviewer agent reads this file. The rules exist to stop the two
known LLM failures:

1. **Too thin.** Few modules, one-line lessons, exercises that only repeat the example.
2. **Padded.** Long text that teaches nothing the learner practises: history,
   motivation speeches, every optional argument, "in this lesson we will…" filler.

Detail is measured against the **concept inventory**, never by word count.
Thin means an `include` concept is not taught or practised well. Padding means
text that serves no `include` concept and no exercise.

## Learner model (fixed for this skill)

- Beginner in the topic. Knows basic Python (variables, lists, dicts, loops, functions)
  unless the topic *is* Python basics.
- Has 20-40 minutes per module.
- Learns by doing. Reads a short explanation, then writes code on the same screen.

## Grounding

- Every fact, function name, argument, and behaviour comes from `sources/`. Cite the
  source file in your working notes. If the sources do not cover something, leave it
  out or flag it. Do not fill gaps from memory.
- Code examples must run on the pinned versions in `course.meta.json`.

## Friction (from "The easy way to learn nothing", Kevin Powell; Bastani et al. 2025)

Learning needs effort. A hint-only tutor kept exam scores level, but a tutor that gave
answers lowered them. So:

- The learner writes code first. The lesson shows a pattern on *different* data or a
  *different* element than the exercise. The exercise must not be a copy-paste of the example.
- Hints go from vague to specific:
  1. **Nudge**: points to the concept or the docs idea ("Which method returns *all* matches?").
  2. **Pointer**: names the tool and the shape ("find_all returns a list. Loop over it.").
  3. **Near-solution**: a partial skeleton with blanks. It is never the full answer.

verify.py enforces this: hint 1 and hint 2 contain no key line of the solution (a non-trivial solution line that is not in the starter), hint 3 does not contain all key lines and not more than 60% of their tokens, and each hint has at least 4 words. Instructions and check messages never contain a key line.

- The page unlocks hint k only after k counted attempts. A counted attempt is a Submit whose code, after normalising, differs from the starter and was never submitted before for this exercise. Normalising removes trailing spaces, blank lines and full-line comments. Run never counts. No hint shows before the first real try. A locked hint or solution button shows its lock reason as visible text.
- The page unlocks the solution only after several attempts and all hints. Authors do not
  hide answers in instructions or hints.
- Reading is short and the task is next to it (the DataQuest screen). The learner never scrolls
  through long text before the first line of code.
- **Recall** exercises (one per module from module 2) bring back an earlier concept with
  new data and no lesson link. Spaced retrieval beats re-reading.
- The project gives less help: 1 hint per step, more open instructions, more steps
  that combine concepts.

## Module shape (DataQuest screens)

A module is a list of **screens**. A screen has a learn half (a section) and a do half (the one
exercise after that section). The page shows both on one screen: text on the left, editor on the
right.

- 5-6 screens per module, so 5-6 sections and 5-6 `exercise` items. The pairing is exact: every
  section has exactly one exercise after it, in the same order. verify.py enforces this.
- After the screens: 1 recall exercise (from module 2 onwards), 1-2 quiz items, 3-8 takeaways, and
  0-2 optional videos.
- Each section teaches 1-3 concepts (never 0). It has one idea.
- The exercise of a screen tags at least one concept of its section. Its solution uses that concept's API. A concept is practised only when a solution really uses its API. A tag alone does not count.

### Screen rules

- **One idea per screen.** If you need "also" or "another way", it is a second screen.
- **Section body: 60-200 words of prose, plus at most one short code example** (under 12 lines).
  The verifier warns under 50 or over 220 words, and for more than one runnable code block.
- The text answers: What is it? When do I use it? What does the code look like? What is the
  common mistake? Show real output in a comment or a short ```` ```text ```` block.
- **The task applies the idea to different data than the example.** A new tag, a new list, a
  new page. The task is never a copy of the example with one word changed.
- The exercise instructions come after the text. They state one deliverable and name the variable.
  The learner must not need to scroll back to the text to find the task.
- Difficulty ladder inside a module:
  - The first screen applies one concept directly.
  - The middle screens combine the new concept with an earlier one.
  - The last screen is a small realistic task, for example from a product page,
    a table, or a news list.
- Project module: the brief (1-2 sections), then 3-6 project steps, then takeaways. No quiz.

## Quiz rules

The quiz (1-2 questions per module, none in the project) checks understanding, not memory.

- Test one idea per question: what code returns, which tool fits a job, or why code fails.
- Base each wrong option on a real misconception. Take them from the inventory `pitfalls`: a Tag
  vs its text, a list vs one item, `None` vs an empty list, `class` vs `class_`.
- Make all options the same length and style. The right option must not be the longest, and must not
  be in the same position every time.
- No trivia (dates, names, defaults that nobody needs to remember). No trick questions.
- No "all of the above" or "none of the above". verify.py warns on them.
- Use 3-5 options (verify.py rejects fewer than 3). Use code in the question when the idea is about code. The question has at least 6 words and the explanation at least 8 words.
- Vary the position of the right answer. verify.py warns when every item has the same answer index, or when the right option is the longest by more than 50% in most items.
- The workflow checks the key: a learner agent answers every question from the lessons alone, and `cb.py quiz-check` compares the answers with the key. A mismatch means the key or the wording may be wrong.
- `explanation_md` says why the right option is right **and** why the most tempting wrong option
  is wrong. It is short (1-3 sentences).
- Tag each question with the concepts that it tests. These must be taught in this module or earlier.
- Do not repeat an exercise. The question tests the same idea from another side.

## Takeaways rules

The takeaways screen is a cheat sheet that the learner keeps (3-8 lines).

- One line per idea. Each line is something the learner looks up later.
- Put the code in the line: `` `soup.find_all("a")` returns a list of every link tag. ``
- State the pitfall in the line when there is one: "returns `None`, not an empty list".
- Only repeat what this module taught. Add no new facts.
- Each line has at least 4 words. At least one line of a module has inline code. No two lines are the same.
- No filler ("Congratulations", "you learned").

## Video rules

Videos are optional (0-2 per module). Add one only when it explains something better than text,
for example how a page tree looks in the browser dev tools.

- Use official channels or reputable teachers. Prefer the library's own channel or a conference
  talk of a maintainer.
- Short: under 15 minutes. Beginner level. It matches the pinned version, or the module notes the
  difference in `why`.
- Watching supports doing and never replaces it. No exercise depends on a video. The module
  must work for a learner who skips it.
- Authors find videos with WebSearch and open each page. Use only URLs that you opened. Copy the exact
  title and channel from the page. Never write a URL from memory.
- verify.py checks each URL with YouTube oEmbed: the video must exist, the title (at least 2 words) must match, the `channel` must match the oEmbed author, and the title or author must contain a significant word of the course topic.
- `why` is one sentence: what the video adds.

## Exercise rules

- **One clear deliverable.** "Store the list of link texts in `link_texts`." Name the variable.
- **Realistic fixture.** `setup_code` holds a small, realistic HTML (or data) fixture,
  with 10-40 lines of HTML. In a project, the fixture is larger and shared by all steps. `setup_code`
  is the visible Data tab, so it never holds the answer of any step. Reference code of earlier
  steps goes in the hidden `carry_code`. Include the messy parts that the concept is for: whitespace,
  nested tags, missing attributes, and repeated classes.
- **Show the data.** The instructions describe what is in the fixture, or the starter
  code prints part of it. The learner must not need to guess the structure.
- **Starter code** sets up the boilerplate that is not the point of the exercise, and
  leaves the target line as `name = None` with a short comment. The starter must not pass.
- **Wrong solutions** are mistakes that real beginners make. Use the inventory `pitfalls`:
  find vs find_all, a Tag vs its text, a missing `.strip()`, `class` vs `class_`,
  off-by-one, `.string` returning None. Each wrong solution must fail with a message
  that helps. At least one wrong solution per exercise must run to the checks and fail them
  (status `fail`). A wrong solution that crashes or times out does not count. A wrong solution is
  never empty and never the starter.
- **Original.** A solution never copies the lesson example. No two exercises in the course share a
  solution or instructions. Instructions have at least 12 words.
- **No live network.** The page runs Python in the browser (Pyodide). There are no sockets
  and no `requests` calls. All data is in `setup_code`. The lesson may *show* a
  `requests.get(...)` line as context, but exercises never call it.
- Only the standard library plus the pinned course packages.

## Writing style

- ASD-STE100 style: short sentences, active voice, present tense, one idea per sentence.
- Address the learner as "you".
- No filler openers ("In this lesson we will explore…"). Start with the idea.
- No history, no "fun facts", no marketing.
- Use one term for one thing. If the docs say "tag", do not switch to "element" or "node".

## Review rubric

Reviewers score each item 0-2 (0 = fails, 1 = weak, 2 = good) and list **concrete**
fixes with ids (for example `m03-e4: wrong solution 2 passes for the wrong reason`).
Separate findings into **missing** (too thin) and **unneeded** (padding). Never merge them.
The workflow has one reviewer lens, "depth", for sections A and B. It still reports the two kinds apart.

### A. Coverage and depth (thin)
- A1. Every `include` concept assigned here is taught with an example **and** practised.
- A2. The common pitfall of each concept is named in the lesson or caught by a wrong solution.
- A3. Exercises go past the lesson example (new data, combination, or a realistic task).
- A4. The module could stand alone as a 20-40 minute session.

### B. Padding and noise
- B1. Every paragraph serves a concept that an exercise uses.
- B2. No filler openers, no history, no lists of every optional argument.
- B3. Code examples are minimal: no unused imports or variables.

### C. Pedagogy and friction
- C1. The difficulty ladder rises inside the module, with no big jumps.
- C2. Hints go nudge → pointer → near-solution, and none gives the full answer.
- C3. Instructions are unambiguous: one deliverable, a named variable, and the data is visible.
- C4. Recall exercise (module 2+) uses an earlier concept on new data.
- C5. Each screen has one idea, 60-200 words, and its task uses different data than the example.

### E. Quiz, takeaways and videos
- E1. Each quiz question tests understanding of one idea. Each wrong option is a real misconception from the pitfalls, not a joke or trivia.
- E2. Options are balanced: similar length and style, the right answer is not the longest, no "all/none of the above".
- E3. The explanation says why the right option is right and why the tempting option is wrong.
- E4. Takeaways are cheat-sheet lines with code, they only repeat what the module taught, and they cover every concept of the module.
- E5. Videos (if any) are short, from an official or reputable channel, match the module, and are optional: no exercise needs them. The `why` is one sentence. verify.py has confirmed that each URL exists and that the title matches.

### D. Correctness (also enforced by verify.py)
- D1. Facts match `sources/`.
- D2. Checks test values and types and give helpful messages that do not reveal the answer.
- D3. Wrong solutions are plausible beginner mistakes, not random junk.

### Syllabus rubric (stage: syllabus)
- S1. Coverage: every `include` concept appears once; `defer` items are named as "next steps".
- S2. Order: prereqs first; each module builds on the last; no module is a grab-bag.
- S3. Size: each module fits 5-6 screens (one section and one exercise each) without cramming. One idea per screen.
- S4. Project: the final project uses most modules and is a realistic task with embedded data.
- S5. Beginner path: the first module gives a working result in the first 10 minutes.
