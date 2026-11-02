#!/usr/bin/env python3
"""Write <courses_root>/index.html: a catalog of every built course.

Usage: catalog.py <courses_root>

A course is listed when <root>/<slug>/dist/index.html exists. Title, tagline
and module count come from <slug>/syllabus.json. The page reads the learner's
progress in the browser from localStorage key "course:<slug>:v1", the same key
the course page writes. Each course gets the ids of its exercises and quiz
questions, so the catalog counts the same things as the course page.

Stdlib only.
"""

import html
import json
import sys
from pathlib import Path
from urllib.parse import quote


def read_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def course_info(root, d):
    syl = read_json(d / "syllabus.json") or {}
    meta = read_json(d / "course.meta.json") or {}
    ids = []
    # Count only modules that the syllabus lists. A stray module folder adds nothing,
    # and an id counts once, like the course page counts it.
    for sm in syl.get("modules", []):
        if not isinstance(sm, dict) or not sm.get("id"):
            continue
        m = read_json(d / "modules" / str(sm["id"]) / "module.json") or {}
        for item in list(m.get("exercises") or []) + list(m.get("quiz") or []):
            i = item.get("id") if isinstance(item, dict) else None
            if i and i not in ids:
                ids.append(i)
    return {
        "slug": d.name,
        "href": quote(d.name) + "/dist/index.html",
        "title": syl.get("title") or meta.get("title") or d.name,
        "tagline": syl.get("tagline", ""),
        "modules": len(syl.get("modules", [])),
        "ids": ids,
    }


def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")


PAGE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Courses</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root {
  --bg: #f4f6f8; --surface: #ffffff; --sunken: #eceff3; --ink: #18202c; --muted: #586274;
  --line: #d9dee6; --accent: #3546b8; --accent-soft: #e4e8fb; --accent-ink: #ffffff;
  --ok: #1d7a4f; --ok-soft: #e1f3ea;
  --font-display: "Bricolage Grotesque", "Avenir Next", "Segoe UI", system-ui, sans-serif;
  --font-body: "Source Serif 4", Georgia, "Times New Roman", serif;
  --font-mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  --radius: 8px;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #11151c; --surface: #191e27; --sunken: #0e1117; --ink: #e5e8ee; --muted: #9aa3b4;
    --line: #2b3240; --accent: #93a2ff; --accent-soft: #232a4a; --accent-ink: #0f1220;
    --ok: #52c48e; --ok-soft: #173326; color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #11151c; --surface: #191e27; --sunken: #0e1117; --ink: #e5e8ee; --muted: #9aa3b4;
  --line: #2b3240; --accent: #93a2ff; --accent-soft: #232a4a; --accent-ink: #0f1220;
  --ok: #52c48e; --ok-soft: #173326; color-scheme: dark;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--font-display); font-size: 15px; line-height: 1.5;
  padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 0 48px;
}
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
h1, h2, h3 { text-wrap: balance; line-height: 1.15; margin: 0; }
.wrap { max-width: 1100px; margin: 0 auto; }
.topbar { background: var(--surface); border-bottom: 1px solid var(--line); margin-inline: calc(-1 * max(16px, env(safe-area-inset-left, 0px)));
  padding-inline: max(16px, env(safe-area-inset-left, 0px)); }
.topbar-inner { max-width: 1100px; margin: 0 auto; min-height: 52px; display: flex; align-items: center; font-weight: 700; letter-spacing: -0.01em; }
.hero { display: grid; gap: 10px; padding-block: 40px 24px; max-width: 760px; }
.eyebrow { font-family: var(--font-mono); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); }
.hero h1 { font-size: clamp(32px, 5vw, 48px); letter-spacing: -0.025em; }
.hero p { margin: 0; font-size: 18px; color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 320px), 1fr)); gap: 16px; }
.card { display: grid; gap: 10px; align-content: start; border: 1px solid var(--line); border-radius: var(--radius); background: var(--surface); padding: 18px; min-width: 0; }
.card h2 { font-size: 22px; letter-spacing: -0.01em; overflow-wrap: anywhere; }
.card p { margin: 0; color: var(--muted); overflow-wrap: anywhere; }
.meta { font-family: var(--font-mono); font-size: 12px; color: var(--muted); display: flex; gap: 14px; flex-wrap: wrap; }
.bar { height: 6px; border-radius: 3px; background: var(--sunken); overflow: hidden; }
.bar > i { display: block; height: 100%; background: var(--ok); }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.btn { display: inline-flex; align-items: center; border: 1px solid var(--line); background: var(--surface); color: var(--ink); padding: 7px 14px; border-radius: var(--radius); font-weight: 500; text-decoration: none; }
.btn:hover { border-color: var(--muted); }
.btn-primary { background: var(--accent); border-color: var(--accent); color: var(--accent-ink); }
.chip { font-family: var(--font-mono); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; padding: 2px 8px; border-radius: 999px; background: var(--sunken); color: var(--muted); }
.chip-ok { background: var(--ok-soft); color: var(--ok); }
.empty { border: 1px dashed var(--line); border-radius: var(--radius); padding: 24px; color: var(--muted); background: var(--surface); }
</style>
</head>
<body>
<header class="topbar"><div class="topbar-inner">Courses</div></header>
<main class="wrap">
  <section class="hero">
    <div class="eyebrow">Your courses</div>
    <h1>Pick a course</h1>
    <p>Each course runs in your browser. Your progress stays in this browser.</p>
  </section>
  <section class="grid" id="grid" aria-label="Courses"></section>
</main>
<script id="catalog-data" type="application/json">__CATALOG_JSON__</script>
<script>
(function () {
  "use strict";
  var COURSES = JSON.parse(document.getElementById("catalog-data").textContent);
  var grid = document.getElementById("grid");
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    for (var k in (attrs || {})) {
      if (k === "text") n.textContent = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined && attrs[k] !== false) n.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function progress(c) {
    var st = {};
    try { st = JSON.parse(localStorage.getItem("course:" + c.slug + ":v1") || "{}") || {}; } catch (e) { st = {}; }
    var done = 0;
    c.ids.forEach(function (id) { if (st[id] && st[id].passed) done++; });
    var last = typeof st._last === "string" && /^[A-Za-z0-9_-]+--\d+$/.test(st._last) ? st._last : null;
    return { done: done, total: c.ids.length, last: last };
  }
  if (!COURSES.length) {
    grid.appendChild(el("div", { class: "empty", text: "No built courses yet. Build one, then run the catalog command again." }));
  }
  COURSES.forEach(function (c) {
    var p = progress(c);
    var pct = p.total ? Math.round(p.done / p.total * 100) : 0;
    var status = p.total && p.done === p.total ? "Complete" : (p.done > 0 ? p.done + " of " + p.total + " done" : "Not started");
    var label = p.done === 0 ? "Start" : (p.done === p.total ? "Review" : "Continue");
    var actions = [el("a", { class: "btn btn-primary", href: c.href + (p.last && p.done > 0 && p.done < p.total ? "#" + p.last : ""), text: label })];
    grid.appendChild(el("article", { class: "card" }, [
      el("h2", { text: c.title }),
      c.tagline ? el("p", { text: c.tagline }) : null,
      el("div", { class: "meta" }, [
        el("span", { text: c.modules + (c.modules === 1 ? " module" : " modules") }),
        el("span", { text: c.ids.length + " exercises and quiz questions" })
      ]),
      el("div", { class: "bar", role: "img", "aria-label": status }, [el("i", { style: "width:" + pct + "%" })]),
      el("div", { class: "row" }, actions.concat([el("span", { class: "chip" + (status === "Complete" ? " chip-ok" : ""), text: status })]))
    ]));
  });
})();
</script>
</body>
</html>
'''


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: catalog.py <courses_root>")
    root = Path(sys.argv[1]).resolve()
    if not root.is_dir():
        sys.exit(f"error: {root} is not a directory")
    courses = []
    for d in sorted(root.iterdir()):
        if d.name.startswith(".") or not d.is_dir():
            continue
        if (d / "dist" / "index.html").is_file():
            courses.append(course_info(root, d))
    courses.sort(key=lambda c: c["title"].lower())
    out = root / "index.html"
    out.write_text(PAGE.replace("__CATALOG_JSON__", safe_json(courses)), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out), "courses": [c["slug"] for c in courses]}, indent=2))


if __name__ == "__main__":
    main()
