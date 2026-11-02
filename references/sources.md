# Source gathering rules

The course is only as good as its sources. The model's memory is not a source.

## What to fetch (in this order)

1. **Official documentation**: the full user guide, not only the landing page. For a
   library, also fetch the API reference pages for the functions that the guide covers.
2. **Package metadata**: the PyPI page (the current version, Python support) and the
   project README. A pure-Python library that is not in Pyodide can still be taught: its wheel must be
   pure Python (`py3-none-any`), and the runtime pins the exact `name==version` from PyPI.
3. **Official changelog or "what's new"**, when the course pins a version. Use it to avoid
   APIs that are deprecated or renamed in that version.
4. **Pitfall sources** (optional, mark them as secondary): the official FAQ, the
   troubleshooting section, and highly voted Q&A. Use them only to find real beginner
   mistakes, never as the source of truth for behaviour.

## Videos (optional, never a source of truth)

Modules may link 0-2 YouTube videos. A video is an optional extra. It is not a source for facts:
every fact in the course still comes from `sources/`.

- Authors find videos with WebSearch and open each page. They use only URLs that they opened.
- Prefer the official channel of the project or a talk by a maintainer. Otherwise use a
  reputable teacher. Keep it short (under 15 minutes) and at beginner level.
- If a video shows behaviour that the docs do not confirm, do not use it for facts.
  The lesson text must come from the docs.
- `verify.py` checks each URL with YouTube oEmbed (the video exists, the title matches, the channel
  matches the author, and the video relates to the course topic).
  The results are cached in `reports/video-check.json`.

## How to store

- One markdown file per page or topic in `sources/`, with a number prefix that keeps
  reading order: `01-quickstart.md`, `02-searching-the-tree.md`, ...
- The first lines of each file:
  ```
  ---
  url: https://...
  fetched: <date given by the orchestrator>
  kind: official-guide | api-reference | package-metadata | changelog | secondary
  ---
  ```
- Keep the real content: headings, code examples, argument lists, notes and warnings.
  Remove navigation, ads, and footers. Do not summarise away the code examples. Authors
  need them.
- `sources/INDEX.md` lists every file with its URL, kind, and a one-line scope.

## When it is enough

The sources are enough when **all** are true:
- The official guide is present, and it covers the core workflow from install to the
  main tasks.
- Each major API area named in the guide's table of contents has a source file.
- The pinned version is known, and the sources match it (or the differences are noted).

A missing changelog does **not** block the build on its own. Some projects keep it
outside the docs site. Record the gap in `version_notes`. `verify.py` runs every lesson
example and every exercise on the pinned version, so a version difference shows up as
a failing check, not as a silent error.

URLs with `#anchors` point into the same page. Fetch each page once. The fetch tool
removes the anchor, and it skips a page that is already in `sources/`.

## When it is not enough: stop and ask the user

Stop the build. Do not write a course from memory. Return a message to the user that has:
- what you tried (the URLs and what failed: 404, blocked, too thin, wrong version),
- what is missing (for example "the API reference for X"),
- **where they can get it**: a likely docs URL, a PDF manual, the GitHub repo `docs/`
  folder, a book chapter, or a video course. They can paste a link, drop files into
  `courses/<slug>/sources/`, or name a local path,
- the command to resume after they add the sources.

This stop is required even though the build is otherwise fully automatic.

## Licenses of fetched pages

Fetched pages keep their own license. Before you publish a course folder (for example in a
public repo), check the license of each file in `sources/`. Keep the copyright and license
notice that the license requires, or leave `sources/` out. A course that is based on the
docs needs a credit line that names the docs and their license.
