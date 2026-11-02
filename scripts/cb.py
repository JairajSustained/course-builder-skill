#!/usr/bin/env python3
"""course-builder entry point. Stdlib only; run with any python3.

Commands (all take the course directory as first argument):

  cb.py init     <course_dir> --topic "Beautiful Soup" --title "..."
  cb.py runtime  <course_dir> --pyodide 0.28.3 --package beautifulsoup4 [--package ...]
  cb.py verify   <course_dir> (--stage inventory|syllabus | --module mXX | --all) [--no-run]
  cb.py render   <course_dir>
  cb.py smoke    <course_dir>
  cb.py learner-view <course_dir> <module_id>      what a learner sees (no answers)
  cb.py quiz-check   <course_dir> <module_id> --choices '{"<quiz id>": <index>}' [--partial]
  cb.py status   <course_dir>
  cb.py catalog  <courses_root>            write <courses_root>/index.html
  cb.py open     <course_dir | courses_root>   serve on 127.0.0.1 and open in the browser

Agents call only this file. It builds the right `uv run` command from
course.meta.json, so every check runs with the same pinned packages and the
same Python minor version that Pyodide ships.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PYODIDE = "0.28.3"


def meta_path(course):
    return course / "course.meta.json"


def load_meta(course):
    p = meta_path(course)
    if not p.exists():
        sys.exit(f"error: {p} not found; run `cb.py init` first")
    return json.loads(p.read_text(encoding="utf-8"))


def save_meta(course, meta):
    meta_path(course).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def cmd_init(a):
    course = Path(a.course_dir).resolve()
    for sub in ("sources", "modules", "drafts", "drafts/try", "reviews", "reports", "dist"):
        (course / sub).mkdir(parents=True, exist_ok=True)
    p = meta_path(course)
    if p.exists():
        meta = load_meta(course)
    else:
        meta = {"slug": course.name, "topic": a.topic, "title": a.title or a.topic,
                "level": "beginner", "runtime": None}
    if a.topic:
        meta["topic"] = a.topic
    if a.title:
        meta["title"] = a.title
    save_meta(course, meta)
    print(json.dumps({"ok": True, "course_dir": str(course), "meta": meta}, indent=2))


def fetch_lock(version):
    url = f"https://cdn.jsdelivr.net/pyodide/v{version}/full/pyodide-lock.json"
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def cmd_runtime(a):
    """Resolve packages against the Pyodide lock file and pin local versions."""
    course = Path(a.course_dir).resolve()
    meta = load_meta(course)
    lock = fetch_lock(a.pyodide)
    pkgs = lock["packages"]
    py = lock["info"]["python"]
    py_minor = ".".join(py.split(".")[:2])
    missing, pins, browser = [], {}, []

    def key(n):
        return n.lower().replace("_", "-")

    index = {key(k): k for k in pkgs}

    def walk(name):
        k = index.get(key(name))
        if k is None:
            return False
        entry = pkgs[k]
        if k in pins:
            return True
        if entry.get("package_type", "package") == "package":
            pins[k] = entry["version"]
        for d in entry.get("depends", []):
            walk(d)
        return True

    for name in a.package:
        if walk(name):
            browser.append(index[key(name)])
        else:
            missing.append(name)

    if missing:
        print(json.dumps({"ok": False, "missing_in_pyodide": missing,
                          "hint": "These packages are not in the Pyodide distribution. "
                                  "Pure-Python packages can use --pypi name==version instead."}, indent=2))
        sys.exit(1)

    pypi = []
    for spec in a.pypi or []:
        if "==" not in spec:
            sys.exit(f"error: --pypi needs an exact pin, e.g. name==1.2.3 (got {spec})")
        pypi.append(spec)

    meta["runtime"] = {
        "profile": "python-pyodide",
        "pyodide": a.pyodide,
        "python": py,
        "python_minor": py_minor,
        "browser_packages": browser,          # loaded with pyodide.loadPackage
        "pypi_packages": pypi,                # loaded with micropip in the page
        "local_pins": [f"{k}=={v}" for k, v in sorted(pins.items())] + pypi,
    }
    save_meta(course, meta)
    print(json.dumps({"ok": True, "runtime": meta["runtime"]}, indent=2))


def uv_cmd(meta, extra_with=()):
    rt = meta.get("runtime") or {}
    if not rt:
        sys.exit("error: runtime not set; run `cb.py runtime` first")
    uv = shutil.which("uv")
    if not uv:
        sys.exit("error: uv not found on PATH")
    cmd = [uv, "run", "--quiet", "--no-project", "--python", rt["python_minor"]]
    for pin in list(rt.get("local_pins", [])) + list(extra_with):
        cmd += ["--with", pin]
    cmd += ["python", "-I"]
    return cmd


def cmd_verify(a):
    course = Path(a.course_dir).resolve()
    meta = load_meta(course)
    cmd = uv_cmd(meta) + [str(HERE / "verify.py"), str(course)]
    report = course / "reports" / "verify-report.json"
    if a.stage:
        cmd += ["--stage", a.stage]
    elif a.module:
        # A module change makes the old full report stale. `--all` writes a new one.
        report.unlink(missing_ok=True)
        cmd += ["--module", a.module]
    else:
        report.unlink(missing_ok=True)
        cmd += ["--all", "--out", str(report)]
    if a.no_run:
        cmd.append("--no-run")
    if a.syllabus_file:
        cmd += ["--syllabus-file", str(Path(a.syllabus_file).resolve())]
    sys.exit(subprocess.call(cmd))


def cmd_render(a):
    course = Path(a.course_dir).resolve()
    meta = load_meta(course)
    cmd = uv_cmd(meta, extra_with=["markdown==3.7"]) + [str(HERE / "render.py"), str(course)]
    sys.exit(subprocess.call(cmd))


def cmd_fetch(a):
    course = Path(a.course_dir).resolve()
    uv = shutil.which("uv")
    if not uv:
        sys.exit("error: uv not found on PATH")
    cmd = [uv, "run", "--quiet", "--no-project", "--with", "beautifulsoup4==4.13.3",
           "--with", "markdownify==0.13.1", "python", "-I", str(HERE / "fetch_source.py"),
           str(course), a.url, a.file, a.kind, a.date]
    sys.exit(subprocess.call(cmd))


def cmd_try(a):
    course = Path(a.course_dir).resolve()
    meta = load_meta(course)
    cmd = uv_cmd(meta) + [str(HERE / "verify.py"), str(course), "--try", a.exercise,
                          "--code-file", str(Path(a.code_file).resolve())]
    sys.exit(subprocess.call(cmd))


def find_chrome():
    env = os.environ.get("CHROME_PATH")
    if env and Path(env).exists():
        return env
    for c in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
        if Path(c).exists():
            return c
    for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        p = shutil.which(n)
        if p:
            return p
    return None


def cmd_smoke(a):
    course = Path(a.course_dir).resolve()
    page = course / "dist" / "index.html"
    out = course / "reports" / "smoke-report.json"
    out.unlink(missing_ok=True)   # an old report must never pass for this run
    if not page.exists():
        sys.exit("error: dist/index.html missing; run `cb.py render` first")
    chrome = find_chrome()
    node = shutil.which("node")
    if not chrome or not node:
        skipped = {"ok": False, "skipped": True,
                   "reason": f"chrome={'found' if chrome else 'missing'}, "
                             f"node={'found' if node else 'missing'}"}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(skipped), encoding="utf-8")
        print(json.dumps(skipped))
        sys.exit(3)
    try:
        rc = subprocess.call([node, str(HERE / "smoke.mjs"), str(page), chrome, str(out)], timeout=420)
    except subprocess.TimeoutExpired:
        out.write_text(json.dumps({"ok": False, "fatal": "smoke test timed out after 420 s"}), encoding="utf-8")
        print(json.dumps({"ok": False, "fatal": "smoke test timed out after 420 s"}))
        rc = 1
    sys.exit(rc)


def cmd_index(a):
    """Rebuild sources/INDEX.md from the files in sources/ (stdlib only).
    Use it after the user drops files into sources/ by hand."""
    import re
    course = Path(a.course_dir).resolve()
    src = course / "sources"
    rows, bad = [], []
    for p in sorted(src.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        head = text[:600]
        get = lambda k: (re.search(rf"^{k}:\s*(.+)$", head, re.M) or [None, ""])[1].strip()
        if not text.startswith("---"):
            bad.append(p.name)
        words = len(re.findall(r"\w+", text))
        rows.append(f"| {p.name} | {get('kind') or '?'} | {words} | {get('title')} | {get('url')} |")
    (src / "INDEX.md").write_text("# Sources\n\n| file | kind | words | title | url |\n|---|---|---|---|---|\n"
                                  + "\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "files": len(rows), "missing_front_matter": bad}, indent=2))


def cmd_learner_view(a):
    """Print only what a learner sees on the page: lessons, instructions, the Data tab
    (setup_code), starter code, quiz questions and options.
    No solutions, hints, checks, wrong solutions, carry_code or quiz answers."""
    course = Path(a.course_dir).resolve()
    mp = course / "modules" / a.module / "module.json"
    if not mp.exists():
        sys.exit(f"error: {mp} not found")
    m = json.loads(mp.read_text(encoding="utf-8"))
    out = [f"# {m.get('title', '')}\n"]
    for s in m.get("sections", []):
        out.append(f"## Lesson {s.get('id')}: {s.get('heading', '')}\n\n{s.get('body_md', '')}\n")
    for e in m.get("exercises", []):
        setup = (e.get("setup_code") or "").rstrip("\n") or "# (no setup code)"
        out.append(f"## Exercise {e.get('id')} ({e.get('kind')}, after {e.get('after_section')}): {e.get('title', '')}\n\n"
                   f"{e.get('instructions_md', '')}\n\n"
                   f"Data tab (read only, runs before your code):\n```python\n{setup}\n```\n\n"
                   f"Starter code in the editor:\n```python\n{(e.get('starter_code') or '').rstrip(chr(10))}\n```\n")
    for q in m.get("quiz", []) or []:
        opts = "\n".join(f"  {i}. {o}" for i, o in enumerate(q.get("options", [])))
        out.append(f"## Quiz {q.get('id')}\n\n{q.get('question_md', '')}\n\n{opts}\n")
    if m.get("takeaways"):
        out.append("## Takeaways\n\n" + "\n".join(f"- {t}" for t in m["takeaways"]) + "\n")
    print("\n".join(out))


def cmd_quiz_check(a):
    """Compare a learner's quiz choices with the answer key of one module.

    --choices is a JSON object {"<quiz id>": <option index>} with one entry per quiz item.
    Prints {"ok", "checked", "mismatches": [{"id", "chosen", "answer"}]}.
    With --partial, a quiz id without a choice is not an error: it is listed in "missing"
    and only the answered ids are checked.
    Exit 0: all match. Exit 1: some mismatch. Exit 2: bad input.
    """
    def bad(msg):
        print(json.dumps({"ok": False, "error": msg}))
        sys.exit(2)

    course = Path(a.course_dir).resolve()
    mp = course / "modules" / a.module / "module.json"
    if not mp.exists():
        bad(f"{mp} not found")
    try:
        quiz = json.loads(mp.read_text(encoding="utf-8")).get("quiz") or []
        choices = json.loads(a.choices)
    except ValueError as err:
        bad(f"invalid JSON: {err}")
    if not isinstance(choices, dict):
        bad("--choices must be a JSON object: {\"<quiz id>\": <option index>}")
    by_id = {q.get("id"): q for q in quiz}
    unknown = sorted(k for k in choices if k not in by_id)
    if unknown:
        bad(f"unknown quiz ids for {a.module}: {', '.join(unknown)}")
    missing = sorted(k for k in by_id if k not in choices)
    if missing and not a.partial:
        bad(f"no choice for quiz ids: {', '.join(missing)}")
    mismatches = []
    for qid, q in by_id.items():
        if qid not in choices:
            continue
        c = choices[qid]
        if isinstance(c, bool) or not isinstance(c, int) or not 0 <= c < len(q.get("options", [])):
            bad(f"choice for {qid} must be an option index 0..{len(q.get('options', [])) - 1}")
        if c != q.get("answer"):
            mismatches.append({"id": qid, "chosen": c, "answer": q.get("answer")})
    print(json.dumps({"ok": not mismatches, "checked": len(by_id) - len(missing), "missing": missing,
                      "mismatches": mismatches}))
    sys.exit(0 if not mismatches else 1)


def cmd_catalog(a):
    """Write <courses root>/index.html: a catalog of every built course."""
    sys.exit(subprocess.call([sys.executable, "-I", str(HERE / "catalog.py"), str(Path(a.courses_root).resolve())]))


def cmd_open(a):
    """Serve the courses folder on localhost and open a course (or the catalog) in the browser."""
    sys.exit(subprocess.call([sys.executable, "-I", str(HERE / "serve.py"), str(Path(a.target).resolve())]))


def cmd_status(a):
    course = Path(a.course_dir).resolve()
    meta = load_meta(course)
    info = {"meta": meta, "files": {}}
    for rel in ("inventory.json", "syllabus.json", "reports/verify-report.json",
                "reports/smoke-report.json", "dist/index.html"):
        info["files"][rel] = (course / rel).exists()
    mods = sorted(p.parent.name for p in (course / "modules").glob("*/module.json"))
    info["modules_written"] = mods
    srcs = sorted(str(p.relative_to(course)) for p in (course / "sources").glob("*") if p.is_file())
    info["sources"] = srcs
    print(json.dumps(info, indent=2))


def main():
    ap = argparse.ArgumentParser(prog="cb.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("course_dir")
    p.add_argument("--topic", required=True); p.add_argument("--title")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("runtime"); p.add_argument("course_dir")
    p.add_argument("--pyodide", default=DEFAULT_PYODIDE)
    p.add_argument("--package", action="append", default=[], help="Pyodide package name")
    p.add_argument("--pypi", action="append", help="pure-Python PyPI pin name==ver, loaded by micropip")
    p.set_defaults(fn=cmd_runtime)

    p = sub.add_parser("verify"); p.add_argument("course_dir")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", choices=["inventory", "syllabus"])
    g.add_argument("--module"); g.add_argument("--all", action="store_true")
    p.add_argument("--no-run", action="store_true")
    p.add_argument("--syllabus-file", help="check a syllabus draft instead of syllabus.json")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("fetch"); p.add_argument("course_dir"); p.add_argument("url"); p.add_argument("file")
    p.add_argument("--kind", default="official-guide",
                   choices=["official-guide", "api-reference", "package-metadata", "changelog", "secondary"])
    p.add_argument("--date", required=True)
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("catalog"); p.add_argument("courses_root"); p.set_defaults(fn=cmd_catalog)
    p = sub.add_parser("open"); p.add_argument("target", help="a course folder or the courses root")
    p.set_defaults(fn=cmd_open)

    p = sub.add_parser("index"); p.add_argument("course_dir"); p.set_defaults(fn=cmd_index)

    p = sub.add_parser("learner-view"); p.add_argument("course_dir"); p.add_argument("module")
    p.set_defaults(fn=cmd_learner_view)

    p = sub.add_parser("quiz-check"); p.add_argument("course_dir"); p.add_argument("module")
    p.add_argument("--choices", required=True, help='JSON object {"<quiz id>": <option index>}')
    p.add_argument("--partial", action="store_true", help="check only the quiz ids that have a choice")
    p.set_defaults(fn=cmd_quiz_check)

    p = sub.add_parser("try"); p.add_argument("course_dir"); p.add_argument("exercise")
    p.add_argument("--code-file", required=True)
    p.set_defaults(fn=cmd_try)

    p = sub.add_parser("render"); p.add_argument("course_dir"); p.set_defaults(fn=cmd_render)
    p = sub.add_parser("smoke"); p.add_argument("course_dir"); p.set_defaults(fn=cmd_smoke)
    p = sub.add_parser("status"); p.add_argument("course_dir"); p.set_defaults(fn=cmd_status)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
