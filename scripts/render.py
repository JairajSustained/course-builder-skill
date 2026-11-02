"""Render course data into one self-contained HTML page.

Run through cb.py (needs the `markdown` package):  cb.py render <course_dir>

Reads course.meta.json, syllabus.json, modules/*/module.json and, when it
exists, reports/verify-report.json. Writes dist/index.html.

Per module the page gets a "screens" list. Each screen is one of:
  learn-do   a section and the one exercise that follows it (one screen)
  learn      a section with no exercise
  do         an exercise alone (extra exercise, recall, project step)
  quiz       the module quiz
  takeaways  the cheat sheet and the optional videos
Old modules without quiz, takeaways or videos still render.

Exercises with verification errors still render, but the page marks them as
"unverified" so the learner knows the checks may be wrong.

The verify report counts only when it is fresh: its "inputs_hash" must equal the
hash of the course files now (coursehash.py). A missing or stale report gives
"verified": false and "stale_report": true in the output. No exercise is then
marked verified, and the report's error list is not used.

Each exercise has an optional hidden "carry_code". It runs after setup_code and
before the learner's code. The page gets "setup_code" (setup + carry, for
running) and "data_code" (setup only, for the Data tab).
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import markdown

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))   # `python -I` does not add the script folder
from coursehash import inputs_hash  # noqa: E402
TEMPLATE = HERE.parent / "templates" / "course.html"
HARNESS = HERE / "harness.py"

MD = markdown.Markdown(extensions=["fenced_code", "tables", "sane_lists"])
# Course text talks about HTML tags all the time ("find the <h1> tag").
# Raw HTML must show as text, never become live markup or script.
MD.preprocessors.deregister("html_block")
MD.inlinePatterns.deregister("html")

SAFE_HREF = re.compile(r'href="(?!https?://|#|mailto:)[^"]*"', re.I)
IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
IMG_SRC = re.compile(r'\bsrc="([^"]*)"', re.I)
IMG_ALT = re.compile(r'\balt="([^"]*)"', re.I)
IMG_OK_PREFIX = "https://i.ytimg.com/"


def _img_filter(m):
    """Keep an <img> only when it loads from YouTube thumbnails. Else show its alt text."""
    tag = m.group(0)
    src = IMG_SRC.search(tag)
    if src and src.group(1).startswith(IMG_OK_PREFIX):
        return tag
    alt = IMG_ALT.search(tag)
    return alt.group(1) if alt else ""


def md(text):
    MD.reset()
    html = MD.convert(text or "")
    html = SAFE_HREF.sub('href="#"', html)
    html = IMG_TAG.sub(_img_filter, html)
    return html.replace('<a href="http', '<a target="_blank" rel="noopener noreferrer" href="http')


def md_inline(text):
    """Markdown for one short line: drop the wrapping <p> when there is only one paragraph."""
    html = md(text).strip()
    if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
        return html[3:-4]
    return html


YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url):
    """Return the video id of a YouTube URL, or None. Only https and known hosts pass."""
    try:
        u = urlparse(url or "")
    except ValueError:
        return None
    if u.scheme != "https" or (u.hostname or "").lower() not in YT_HOSTS:
        return None
    if (u.hostname or "").lower() == "youtu.be":
        cand = u.path.strip("/").split("/")[0]
    else:
        cand = (parse_qs(u.query).get("v") or [""])[0]
        if not cand:
            m = re.match(r"^/(?:embed|shorts)/([^/?]+)", u.path)
            cand = m.group(1) if m else ""
    return cand if YT_ID.match(cand) else None


def build_screens(kind, sections, exercises, has_quiz, has_takeaways):
    """Order of screens in a module. Items are ids; the page looks the content up."""
    screens, placed = [], set()
    if kind == "project":
        for s in sections:
            screens.append({"type": "learn", "section": s["id"], "exercise": None})
        for e in exercises:
            screens.append({"type": "do", "section": None, "exercise": e["id"]})
    else:
        for s in sections:
            mine = [e for e in exercises if e["kind"] == "exercise" and e.get("after_section") == s["id"]]
            if mine:
                screens.append({"type": "learn-do", "section": s["id"], "exercise": mine[0]["id"]})
                for e in mine[1:]:
                    screens.append({"type": "do", "section": None, "exercise": e["id"]})
                placed.update(e["id"] for e in mine)
            else:
                screens.append({"type": "learn", "section": s["id"], "exercise": None})
        for e in exercises:  # exercises with an unknown after_section: keep them reachable
            if e["id"] not in placed and e["kind"] != "recall":
                screens.append({"type": "do", "section": None, "exercise": e["id"]})
                placed.add(e["id"])
        for e in exercises:
            if e["id"] not in placed:
                screens.append({"type": "do", "section": None, "exercise": e["id"]})
    if has_quiz:
        screens.append({"type": "quiz", "section": None, "exercise": None})
    if has_takeaways:
        screens.append({"type": "takeaways", "section": None, "exercise": None})
    return screens


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def safe_json(obj):
    # Stop the JSON from closing the <script> element or opening a comment.
    s = json.dumps(obj, ensure_ascii=False)
    return s.replace("</", "<\\/").replace("<!--", "<\\!--")


def main():
    course = Path(sys.argv[1]).resolve()
    meta = load(course / "course.meta.json")
    syl = load(course / "syllabus.json")
    rt = meta.get("runtime") or {}

    # The verify report counts only when its hash matches the course files now.
    unverified = {}
    verified, stale = False, True
    rpt = course / "reports" / "verify-report.json"
    if rpt.exists():
        try:
            report = load(rpt)
        except ValueError:
            report = None
        if isinstance(report, dict) and report.get("inputs_hash") == inputs_hash(course):
            stale = False
            verified = bool(report.get("ok"))
            ex_ids = set()
            for sm in syl["modules"]:
                try:
                    for ex in load(course / "modules" / sm["id"] / "module.json").get("exercises", []):
                        if isinstance(ex, dict) and isinstance(ex.get("id"), str):
                            ex_ids.add((sm["id"], ex["id"]))
                except (OSError, ValueError, KeyError):
                    pass
            for e in report.get("errors", []):
                parts = str(e.get("where", "")).split(":")
                # Add an id only when it is an exercise id of that module.
                if len(parts) >= 2 and (parts[0], parts[1]) in ex_ids:
                    unverified.setdefault(parts[1], []).append(e.get("message", ""))

    titles = {m["id"]: m["title"] for m in syl["modules"]}
    concept_module = {}
    for m in syl["modules"]:
        for c in m.get("concepts", []):
            concept_module.setdefault(c, m["id"])

    modules, missing = [], []
    for sm in syl["modules"]:
        mp = course / "modules" / sm["id"] / "module.json"
        if not mp.exists():
            missing.append(sm["id"])
            continue
        m = load(mp)
        exercises = []
        for e in m["exercises"]:
            recall_from = None
            if e["kind"] == "recall":
                srcs = [concept_module.get(c) for c in e["concepts"] if concept_module.get(c) != sm["id"]]
                srcs = [s for s in srcs if s]
                if srcs:
                    recall_from = titles.get(srcs[0])
            setup = e["setup_code"]
            carry = e.get("carry_code") or ""
            exercises.append({
                "id": e["id"], "title": e["title"], "kind": e["kind"],
                "after_section": e.get("after_section"),
                "instructions_html": md(e["instructions_md"]),
                # setup_code runs before the learner code (setup + carry). The Data tab
                # shows data_code (setup only), never the carry code.
                "setup_code": setup + "\n" + carry if carry.strip() else setup,
                "data_code": setup, "starter_code": e["starter_code"],
                "solution_code": e["solution_code"],
                "hints_html": [md(h) for h in e["hints"]],
                "test_code": e["test_code"],
                "recall_from": recall_from,
                "unverified": bool(unverified.get(e["id"])),
                "verified": not stale and verified and not unverified.get(e["id"]),
            })
        quiz = []
        for q in m.get("quiz") or []:
            quiz.append({
                "id": q["id"], "question_html": md(q.get("question_md", "")),
                "options": [str(o) for o in q.get("options", [])],
                "answer": q.get("answer"),
                "explanation_html": md(q.get("explanation_md", "")),
                "concepts": q.get("concepts", []),
            })
        takeaways = [md_inline(t) for t in (m.get("takeaways") or []) if str(t).strip()]
        videos = []
        for v in (m.get("videos") or [])[:2]:
            vid = youtube_id(v.get("url"))
            if not vid:
                continue  # never put an unknown URL in a link
            videos.append({"id": vid, "url": "https://www.youtube.com/watch?v=" + vid,
                           "title": v.get("title", ""), "channel": v.get("channel", ""),
                           "why": v.get("why", "")})
        sections = [{"id": s["id"], "heading": s["heading"], "html": md(s["body_md"])}
                    for s in m["sections"]]
        modules.append({
            "id": m["id"], "title": m["title"], "kind": m["kind"],
            "summary": m["summary"], "objectives": m["objectives"],
            "sections": sections,
            "exercises": exercises,
            "quiz": quiz,
            "takeaways_html": takeaways,
            "videos": videos,
            "screens": build_screens(m["kind"], sections, exercises, bool(quiz), bool(takeaways)),
        })

    data = {
        "slug": meta["slug"], "title": syl.get("title") or meta.get("title"),
        "tagline": syl.get("tagline", ""),
        "description_html": md(syl.get("description", "")),
        "outcomes": syl.get("outcomes", []),
        "prerequisites": syl.get("prerequisites", []),
        "level": meta.get("level", "beginner"),
        "runtime": {
            "pyodide": rt.get("pyodide"),
            "browser_packages": rt.get("browser_packages", []),
            "pypi_packages": rt.get("pypi_packages", []),
        },
        "verified": verified,
        "modules": modules,
        "harness": HARNESS.read_text(encoding="utf-8"),
    }

    version = rt.get("pyodide") or "0.28.3"
    template = TEMPLATE.read_text(encoding="utf-8")
    title = re.sub(r"[<>&]", "", data["title"] or "Course")

    def build(index_url, package_base_url):
        d = dict(data)
        d["runtime"] = dict(data["runtime"], indexURL=index_url, packageBaseUrl=package_base_url)
        h = template.replace("__COURSE_TITLE__", title)
        h = h.replace("__PYODIDE_VERSION__", version)
        return h.replace("__COURSE_JSON__", safe_json(d))

    dist = course / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    # 1) Local page: full document, Pyodide files and wheels from the CDN.
    local = build(f"https://cdn.jsdelivr.net/npm/pyodide@{version}/",
                  f"https://cdn.jsdelivr.net/pyodide/v{version}/full/")
    local_doc = ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
                 "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
                 + local + "\n</html>\n")
    (dist / "index.html").write_text(local_doc, encoding="utf-8")
    # 2) Artifact fragment: the artifact host adds the document skeleton and
    #    blocks runtime fetches to CDNs, so Pyodide files must be published
    #    next to the page under pyodide/ (not automated yet).
    artifact = build("pyodide/", "pyodide/")
    (dist / "artifact.html").write_text(artifact, encoding="utf-8")

    n_ex = sum(len(m["exercises"]) for m in modules)
    n_quiz = sum(len(m["quiz"]) for m in modules)
    print(json.dumps({
        "ok": not missing, "verified": verified, "stale_report": stale,
        "output": str(dist / "index.html"),
        "artifact_fragment": str(dist / "artifact.html"), "modules": len(modules),
        "exercises": n_ex, "quizzes": n_quiz, "missing_modules": missing,
        "unverified_exercises": sorted(unverified), "bytes": len(local_doc.encode()),
    }, indent=2))
    sys.exit(0 if not missing else 1)


if __name__ == "__main__":
    main()
