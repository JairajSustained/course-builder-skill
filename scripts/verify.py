"""Deterministic course checks. No LLM judgement here: only facts.

Usage (run through uv so the course packages are present):

    uv run --no-project --python <py> --with <pkg>==<ver> ... \
        verify.py <course_dir> --stage inventory|syllabus
    ... verify.py <course_dir> --module m03
    ... verify.py <course_dir> --all

Prints a JSON report to stdout. Exit code 0 = no errors, 1 = errors found,
2 = bad input (missing file, invalid JSON).

Errors block the build. Warnings go to reviewers as signals.

Module checks (v2 format): section/exercise pairing, quiz, takeaways, videos.
Without --no-run, each video URL is checked with the YouTube oEmbed endpoint
(15 s timeout). Results are cached in <course>/reports/video-check.json.
"""

import argparse
import ast
import io
import json
import keyword
import os
import re
import subprocess
import sys
import time
import tokenize
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "harness.py"
sys.path.insert(0, str(HERE))       # python -I does not add the script folder
from coursehash import inputs_hash  # noqa: E402

SETUP_LIMIT_MS = 8000     # the page watchdog is 15 s for setup plus learner code

EXERCISE_KINDS = {"exercise", "recall", "project-step"}

# Method names of builtin types. An API with one of these names (find, get,
# index, ...) is ambiguous: `text.find(...)` is not Beautiful Soup. Uses of
# ambiguous APIs give warnings, not errors.
AMBIGUOUS = {n for t in (str, bytes, dict, list, set, tuple, int, float) for n in dir(t)}


def code_tokens(code):
    """Names the code really uses: attribute names (.select_one) from ast.Attribute
    nodes, keyword arguments (class_=), names that are CALLED (BeautifulSoup(...)),
    imported names, and short string literals ("lxml"). A plain name is never
    counted: not an assignment target, not a value, not a call argument. So a
    variable named `text` does not count as the API `.text`."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return set()
    toks = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            toks.add(n.attr)
        elif isinstance(n, ast.keyword) and n.arg:
            toks.add(n.arg + "=")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            toks.add(n.func.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                toks.add(a.name.split(".")[-1])
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) <= 40:
            toks.add('"' + n.value + '"')
    return toks


RUNNABLE_LANGS = {"", "python", "python3", "py"}


def runnable_blocks(md, only_runnable=True):
    """Code of fenced blocks that the page shows as Python: ```python, ```py,
    ```python3 and unlabelled fences. ```text / ```pycon / ```html are skipped.
    With only_runnable=False every fenced block is returned."""
    blocks, cur, lang, fence = [], None, None, None
    for line in (md or "").split("\n"):
        stripped = line.strip()
        if cur is None:
            m = re.match(r"^(`{3,}|~{3,})\s*([\w+-]*)", stripped)
            if m:
                fence, lang, cur = m.group(1), m.group(2).lower(), []
        elif stripped.startswith(fence) and stripped.strip("`~") == "":
            if lang in RUNNABLE_LANGS or not only_runnable:
                blocks.append("\n".join(cur))
            cur = None
        else:
            cur.append(line)
    return blocks


def is_ambiguous(api):
    return api.rstrip("=").strip('"') in AMBIGUOUS


def api_index(inv):
    idx = {}
    for cid, c in inv.items():
        for a in c.get("apis") or []:
            idx.setdefault(a, set()).add(cid)
    return idx


def check_api_use(code, allowed, inv, idx, where, what, rep):
    """Error when code uses an API whose concepts are not taught yet."""
    toks = code_tokens(code)
    for tok in sorted(toks & set(idx)):
        owners = idx[tok]
        if owners & allowed:
            continue
        names = ", ".join(sorted(owners))
        decisions = {inv[o]["decision"] for o in owners}
        state = "is not taught in this course" if decisions <= {"defer", "skip"} else "is not taught yet at this point"
        msg = f"{what} uses `{tok}` (concept {names}), which {state}"
        if is_ambiguous(tok):
            rep.warn(where, msg + " (ambiguous name; ignore if it is a builtin method)")
        else:
            rep.err(where, msg)
    return toks
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Report:
    def __init__(self, inputs_hash_value=""):
        self.errors = []
        self.warnings = []
        self.runs = []
        self.inputs_hash = inputs_hash_value

    def err(self, where, msg):
        self.errors.append({"where": where, "message": msg})

    def warn(self, where, msg):
        self.warnings.append({"where": where, "message": msg})

    def as_dict(self, scope):
        return {
            "scope": scope,
            "ok": not self.errors,
            "inputs_hash": self.inputs_hash,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": self.errors,
            "warnings": self.warnings,
            "runs": self.runs,
        }


class Fatal(Exception):
    """Bad input: a missing file or invalid JSON. main() prints it and exits 2."""


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise Fatal(f"missing file: {path}")
    except json.JSONDecodeError as exc:
        raise Fatal(f"invalid JSON in {path}: {exc}")


def norm(code):
    return re.sub(r"\s+", " ", (code or "")).strip()


def strip_comments(code):
    """Code without comments (full-line and trailing). Uses tokenize; on a
    tokenize error it drops only full-line comments."""
    code = code or ""
    lines = code.split("\n")
    try:
        cuts = [(t.start[0], t.start[1]) for t in tokenize.generate_tokens(io.StringIO(code).readline)
                if t.type == tokenize.COMMENT]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return "\n".join(ln for ln in lines if not ln.strip().startswith("#"))
    for row, col in cuts:
        if 0 < row <= len(lines):
            lines[row - 1] = lines[row - 1][:col]
    return "\n".join(lines)


def ncode(code):
    """Code normalised for comparing: no comments, whitespace collapsed."""
    return norm(strip_comments(code))


def squash(text):
    """Text without any whitespace, with ' turned into ". For 'does A contain B' tests."""
    return re.sub(r"\s+", "", (text or "").replace("'", '"'))


def leak_forms(line):
    """Squashed forms of a key line that count as a leak when a text contains them:
    the whole line and, for `name = expr` with a long expression, the expression."""
    forms = [squash(line)]
    m = re.match(r"^[A-Za-z_]\w*\s*=(?!=)\s*(.+)$", line)
    if m and len(m.group(1).strip()) >= 12:
        forms.append(squash(m.group(1)))
    return forms


def leaks_in(squashed_text, line):
    return any(f in squashed_text for f in leak_forms(line))


def wcount(text):
    return len(re.findall(r"\w+", text or ""))


def is_trivial_line(line):
    return (len(line) < 12 or line.startswith("#") or line.startswith("import ")
            or (line.startswith("from ") and " import " in line))


def code_lines(code):
    """Non-trivial lines of code (no blank, comment or import line; length >= 12
    after strip), whitespace collapsed. Same definition for key lines and for
    the setup_code leak test."""
    out = []
    for ln in strip_comments(code).split("\n"):
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln and not is_trivial_line(ln):
            out.append(ln)
    return out


def key_lines(solution, starter):
    """Solution lines that the learner has to write: non-trivial and not in the starter."""
    have = {re.sub(r"\s+", " ", ln).strip() for ln in strip_comments(starter).split("\n")}
    return [ln for ln in code_lines(solution) if ln not in have]


def ident_tokens(text):
    return set(re.findall(r"[A-Za-z_]\w*|\d+", text or ""))


def check_call_messages(test_code):
    """String literals of the message argument of check(...) calls in test_code."""
    try:
        tree = ast.parse(test_code or "")
    except SyntaxError:
        return []
    msgs = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "check":
            arg = n.args[1] if len(n.args) > 1 else next(
                (k.value for k in n.keywords if k.arg == "message"), None)
            if arg is None:
                continue
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    msgs.append(sub.value)
    return msgs


def exercise_setup(e):
    """What runs before the learner code: setup_code + carry_code."""
    carry = e.get("carry_code")
    base = e.get("setup_code") or ""
    return base + "\n" + carry if isinstance(carry, str) and carry else base


def require(obj, keys, where, rep):
    ok = True
    for k in keys:
        if k not in obj:
            rep.err(where, f"missing field '{k}'")
            ok = False
    return ok


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------

def check_inventory(course, rep):
    inv = load_json(course / "inventory.json")
    concepts = inv.get("concepts")
    if not isinstance(concepts, list) or not concepts:
        rep.err("inventory", "'concepts' must be a non-empty list")
        return {}
    by_id = {}
    for i, c in enumerate(concepts):
        where = f"inventory.concepts[{i}]"
        if not require(c, ["id", "name", "summary", "decision", "reason", "source"], where, rep):
            continue
        if not ID_RE.match(c["id"]):
            rep.err(where, f"bad id '{c['id']}' (use lowercase-kebab)")
        if c["id"] in by_id:
            rep.err(where, f"duplicate id '{c['id']}'")
        if c["decision"] not in ("include", "defer", "skip"):
            rep.err(where, f"decision must be include|defer|skip, got '{c['decision']}'")
        src = str(c["source"]).split("#")[0].strip()
        if c.get("decision") == "include":
            if not src:
                rep.err(where, "included concept has no source; the course must not teach from memory")
            elif not (course / src).exists():
                rep.err(where, f"source file not found: {src}")
            if not isinstance(c.get("apis"), list):
                rep.err(where, "included concept needs an 'apis' list (method/attribute names, 'kw=' "
                               "keyword arguments, class names, or \"literal\" strings; [] if it has none)")
        elif src and not (course / src).exists():
            rep.warn(where, f"source file not found: {src}")
        by_id[c["id"]] = c
    for c in by_id.values():
        for p in c.get("prereqs", []) or []:
            if p not in by_id:
                rep.err(f"inventory:{c['id']}", f"unknown prereq '{p}'")
    included = [c for c in by_id.values() if c["decision"] == "include"]
    if len(included) < 8:
        rep.warn("inventory", f"only {len(included)} concepts marked include; course may be too thin")
    return by_id


# --------------------------------------------------------------------------
# Syllabus
# --------------------------------------------------------------------------

def check_syllabus(course, inv, rep, path=None):
    syl = load_json(path or (course / "syllabus.json"))
    if not require(syl, ["title", "description", "outcomes", "modules"], "syllabus", rep):
        return syl
    mods = syl["modules"]
    if not (3 <= len(mods) <= 12):
        rep.warn("syllabus", f"{len(mods)} modules; expected 3-12")
    seen_ids, concept_module = set(), {}
    for idx, m in enumerate(mods):
        where = f"syllabus.modules[{idx}]"
        if not require(m, ["id", "title", "summary", "objectives", "concepts", "kind"], where, rep):
            continue
        if m["id"] in seen_ids:
            rep.err(where, f"duplicate module id '{m['id']}'")
        seen_ids.add(m["id"])
        if m["kind"] not in ("module", "project"):
            rep.err(where, "kind must be module|project")
        if m["kind"] == "project":
            if m["concepts"]:
                rep.err(where, "the project module must have an empty 'concepts' list; "
                               "a project teaches nothing new. Put every concept in a module")
                continue
        elif m["kind"] == "module":
            n_c = len(m["concepts"])
            if not (2 <= n_c <= 10):
                rep.err(where, f"a module has 2-10 concepts, has {n_c}; split or merge modules")
            elif n_c > 8:
                rep.warn(where, f"module has {n_c} concepts; 8 or fewer keeps the module short")
        for cid in m["concepts"]:
            if cid not in inv:
                rep.err(where, f"unknown concept '{cid}'")
                continue
            if inv[cid]["decision"] != "include":
                rep.err(where, f"concept '{cid}' is '{inv[cid]['decision']}' in inventory, not include")
            if cid in concept_module and mods[concept_module[cid]].get("kind") == "module" and m["kind"] == "module":
                rep.warn(where, f"concept '{cid}' is introduced in two modules")
            concept_module.setdefault(cid, idx)
    if mods and mods[-1].get("kind") != "project":
        rep.err("syllabus", "the last module must be the final project (kind: project)")
    for cid, c in inv.items():
        if c["decision"] == "include" and cid not in concept_module:
            rep.err("syllabus", f"included concept '{cid}' is in no module")
        for p in c.get("prereqs", []) or []:
            if cid in concept_module and p in concept_module and concept_module[p] > concept_module[cid]:
                rep.err("syllabus", f"'{cid}' comes before its prereq '{p}'")
    syl["_concept_module"] = concept_module
    return syl


# --------------------------------------------------------------------------
# Modules
# --------------------------------------------------------------------------

def harness_run(setup, code, tests, time_limit=5.0):
    job = json.dumps({"setup_code": setup, "user_code": code, "test_code": tests, "time_limit": time_limit})
    try:
        p = subprocess.run(
            [sys.executable, "-I", str(HARNESS)],
            input=job, capture_output=True, text=True, timeout=time_limit + 20,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "message": "harness subprocess timeout"}
    if p.returncode != 0:
        return {"status": "harness_crash", "message": (p.stderr or p.stdout)[-1500:]}
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"status": "harness_crash", "message": p.stdout[-1500:]}


def check_pairing(where, sections, exercises, rep):
    """Screen shape: every section is followed by EXACTLY ONE exercise of kind
    "exercise" whose after_section is that section, in the same order."""
    sec_ids = [s.get("id") for s in sections if isinstance(s, dict)]
    ex_after = [e.get("after_section") for e in exercises
                if isinstance(e, dict) and e.get("kind") == "exercise"]
    for sid in sec_ids:
        n = ex_after.count(sid)
        if n == 0:
            rep.err(f"{where}:{sid}", "section has no exercise; every section needs exactly one "
                                      "exercise (kind exercise, after_section set to this section)")
        elif n > 1:
            rep.err(f"{where}:{sid}", f"{n} exercises come after this section; exactly one is allowed. "
                                      "Split the section into two screens")
    first = []
    for a in ex_after:
        if a in sec_ids and a not in first:
            first.append(a)
    if first != [sid for sid in sec_ids if sid in first]:
        rep.err(where, "exercises are not in section order; list them in the same order as their "
                       f"sections (sections: {sec_ids}; exercises after: {ex_after})")


def check_quiz(quiz, kind, where, inv, known_concepts, ex_ids, rep):
    if kind == "project":
        if quiz:
            rep.err(where, "a project has no quiz; remove 'quiz' or leave it empty")
        return
    if not isinstance(quiz, list):
        rep.err(where, "missing 'quiz': a module needs a list of 1-2 quiz items")
        return
    if not (1 <= len(quiz) <= 2):
        rep.err(where, f"module needs 1-2 quiz items, has {len(quiz)}")
    seen = set()
    for i, q in enumerate(quiz):
        qid = q.get("id") if isinstance(q, dict) else None
        qw = f"{where}:{qid if isinstance(qid, str) else 'quiz[' + str(i) + ']'}"
        if not isinstance(q, dict):
            rep.err(qw, "a quiz item must be an object")
            continue
        if not require(q, ["id", "question_md", "options", "answer", "explanation_md", "concepts"], qw, rep):
            continue
        if not isinstance(q["id"], str) or not ID_RE.match(q["id"]):
            rep.err(qw, f"bad quiz id '{q['id']}' (use lowercase-kebab, for example m02-q1)")
        elif q["id"] in seen or q["id"] in ex_ids:
            rep.err(qw, "duplicate id")
        seen.add(q["id"])
        if not isinstance(q["question_md"], str) or not q["question_md"].strip():
            rep.err(qw, "question_md is empty")
        elif wcount(q["question_md"]) < 6:
            rep.err(qw, f"question_md has {wcount(q['question_md'])} words; write a full question (6 words or more)")
        opts = q["options"]
        if not isinstance(opts, list) or not (3 <= len(opts) <= 5):
            rep.err(qw, f"options must be a list of 3-5 items, has {len(opts) if isinstance(opts, list) else 'none'}")
        else:
            if not all(isinstance(o, str) and o.strip() for o in opts):
                rep.err(qw, "every option must be a non-empty string")
            else:
                if len({o.strip().lower() for o in opts}) != len(opts):
                    rep.err(qw, "options must be distinct")
                for o in opts:
                    if re.search(r"\b(all|none) of the above\b", o, re.I):
                        rep.warn(qw, f"option '{o}': do not use 'all/none of the above'")
            ans = q["answer"]
            if isinstance(ans, bool) or not isinstance(ans, int) or not (0 <= ans < len(opts)):
                rep.err(qw, f"answer must be an index into options (0-{len(opts) - 1}), got {ans!r}")
        if not isinstance(q["explanation_md"], str) or not q["explanation_md"].strip():
            rep.err(qw, "explanation_md is empty")
        elif wcount(q["explanation_md"]) < 8:
            rep.err(qw, f"explanation_md has {wcount(q['explanation_md'])} words; explain the answer (8 words or more)")
        cs = q["concepts"]
        if not isinstance(cs, list) or not cs:
            rep.err(qw, "concepts must be a non-empty list of concept ids")
        else:
            for cid in cs:
                if cid not in inv:
                    rep.err(qw, f"unknown concept '{cid}'")
                elif cid not in known_concepts:
                    rep.err(qw, f"concept '{cid}' is not taught in this module or an earlier one")


def check_takeaways(tk, where, rep):
    if not isinstance(tk, list):
        rep.err(where, "missing 'takeaways': a list of 3-8 cheat-sheet lines")
        return
    if not (3 <= len(tk) <= 8):
        rep.err(where, f"takeaways needs 3-8 lines, has {len(tk)}")
    seen = set()
    for i, t in enumerate(tk):
        if not isinstance(t, str) or not t.strip():
            rep.err(where, f"takeaways[{i}] is empty; use a non-empty markdown string")
            continue
        if wcount(t) < 4:
            rep.err(where, f"takeaways[{i}] has {wcount(t)} words; write a full line (4 words or more)")
        key = re.sub(r"\W+", " ", t.lower()).strip()
        if key in seen:
            rep.err(where, f"takeaways[{i}] repeats an earlier takeaway")
        seen.add(key)
    texts = [t for t in tk if isinstance(t, str)]
    if texts and not any(re.search(r"`[^`\n]+`", t) for t in texts):
        rep.err(where, "no takeaway contains inline code; put at least one API name in backticks")


# --------------------------------------------------------------------------
# Videos (YouTube links, checked with the oEmbed endpoint)
# --------------------------------------------------------------------------

YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
OEMBED = "https://www.youtube.com/oembed?url={}&format=json"
VIDEO_CACHE_TTL = 7 * 24 * 3600
_video_cache = {}       # course path -> dict loaded from reports/video-check.json


def video_id(url):
    """The 11-character video id of a watch?v= or youtu.be link, or None."""
    try:
        u = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host not in YT_HOSTS:
        return None
    if host.endswith("youtu.be"):
        vid = u.path.strip("/").split("/")[0]
    elif u.path == "/watch":
        vid = (urllib.parse.parse_qs(u.query).get("v") or [""])[0]
    else:
        return None
    return vid if YT_ID_RE.match(vid) else None


def _cache_path(course):
    return course / "reports" / "video-check.json"


def _load_video_cache(course):
    key = str(course)
    if key not in _video_cache:
        try:
            data = json.loads(_cache_path(course).read_text(encoding="utf-8"))
            _video_cache[key] = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _video_cache[key] = {}
    return _video_cache[key]


def _save_video_cache(course):
    """Merge with the file on disk (other verify runs may write at the same time),
    then replace it in one step."""
    path = _cache_path(course)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            disk = json.loads(path.read_text(encoding="utf-8"))
            disk = disk if isinstance(disk, dict) else {}
        except (OSError, ValueError):
            disk = {}
        disk.update(_video_cache[str(course)])
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(disk, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def oembed_lookup(url):
    """Ask YouTube about one video. Returns {"http": <code>, "title": str|None}
    or {"http": None, "error": str} when the network fails."""
    req = urllib.request.Request(OEMBED.format(urllib.parse.quote(url, safe="")),
                                 headers={"User-Agent": "course-builder-verify/2"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
            return {"http": r.status, "title": str(body.get("title", "")),
                    "author_name": str(body.get("author_name", ""))}
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, "title": None}
    except Exception as exc:        # URLError, timeout, bad JSON, SSL error
        return {"http": None, "error": f"{type(exc).__name__}: {exc}"}


STOP_WORDS = {"the", "python", "library", "and", "for", "with", "module", "package", "tutorial",
              "guide", "intro", "introduction", "basics", "course", "learn", "learning", "using",
              "use", "how", "from", "your", "into", "beginner", "beginners", "complete", "full"}


def topic_words(course):
    """Significant words of the course topic (course.meta.json): 3+ characters, no stop words."""
    try:
        meta = json.loads((course / "course.meta.json").read_text(encoding="utf-8"))
        topic = str(meta.get("topic", ""))
    except (OSError, ValueError, AttributeError):
        return []
    return [w for w in re.findall(r"\w+", topic.lower()) if len(w) >= 3 and w not in STOP_WORDS]


def _alnum(text):
    return re.sub(r"\W+", "", (text or "").lower())


def check_video_online(course, v, vw, rep, strict=False):
    """Error or warning for one video. Cache: <course>/reports/video-check.json.
    strict (the --all gate): a video that could not be checked is an error."""
    url, title = v["url"], v["title"]
    cache = _load_video_cache(course)
    hit = cache.get(url)
    if not (isinstance(hit, dict) and isinstance(hit.get("http"), int)
            and time.time() - hit.get("checked", 0) < VIDEO_CACHE_TTL
            and (hit.get("http") != 200 or "author_name" in hit)):
        hit = oembed_lookup(url)
        if hit.get("http") in (200, 400, 401, 403, 404):
            hit["checked"] = int(time.time())
            cache[url] = hit
            _save_video_cache(course)
    code = hit.get("http")
    if code == 200:
        yt_title, author = hit.get("title") or "", hit.get("author_name") or ""
        want = set(re.findall(r"\w+", title.lower()))
        have = set(re.findall(r"\w+", yt_title.lower()))
        if want and len(want & have) * 2 < len(want):
            rep.err(vw, f"video title does not match: you gave \"{title}\" but YouTube says "
                        f"\"{yt_title}\"; copy the exact title of a video you opened")
        nc, na = _alnum(v["channel"]), _alnum(author)
        short, long_ = sorted((nc, na), key=len)
        if len(nc) < 3:
            rep.err(vw, f"channel \"{v['channel']}\" is too short or empty; copy the channel name of a video "
                        f"you opened (YouTube says the author is \"{author}\")")
        elif na and not ((nc in na or na in nc) and len(short) >= 0.6 * len(long_)):
            rep.err(vw, f"channel does not match: you gave \"{v['channel']}\" but YouTube says "
                        f"the author is \"{author}\"; copy the channel name of a video you opened")
        tw = topic_words(course)
        hay = (yt_title + " " + author).lower()
        if tw and not any(w in hay for w in tw):
            rep.err(vw, f"video looks unrelated to the topic: none of {tw} appears in the title "
                        f"\"{yt_title}\" or the author \"{author}\"")
    elif code in (400, 404):
        rep.err(vw, f"video does not exist (YouTube answered HTTP {code}): {url}")
    elif code in (401, 403):
        rep.warn(vw, f"embedding disabled for this video (HTTP {code}); the link still works")
    else:
        why = hit.get("error") or (f"HTTP {code}" if code else "no answer")
        msg = f"could not check this video ({why}); check it by hand"
        if strict:
            rep.err(vw, msg + ". The final gate needs a successful check: fix the network and run again")
        else:
            rep.warn(vw, msg)


def check_videos(course, videos, where, rep, run_online, strict=False):
    if videos is None:
        return
    if not isinstance(videos, list):
        rep.err(where, "'videos' must be a list")
        return
    if len(videos) > 2:
        rep.err(where, f"at most 2 videos per module, has {len(videos)}")
    seen = set()
    for i, v in enumerate(videos):
        vw = f"{where}:video[{i}]"
        if not isinstance(v, dict):
            rep.err(vw, "a video must be an object with url, title, channel, why")
            continue
        ok = True
        for k in ("url", "title", "channel", "why"):
            if not isinstance(v.get(k), str) or not v[k].strip():
                rep.err(vw, f"video needs a non-empty '{k}'")
                ok = False
        if not ok:
            continue
        if wcount(v["title"]) < 2:
            rep.err(vw, f"video title \"{v['title']}\" has fewer than 2 words; copy the full title")
        url = v["url"].strip()
        v["url"] = url
        if urllib.parse.urlparse(url).scheme != "https":
            rep.err(vw, "video url must start with https://")
            continue
        if video_id(url) is None:
            rep.err(vw, "video url must be a YouTube link (youtube.com/watch?v=<11 characters> "
                        f"or youtu.be/<11 characters>): {url}")
            continue
        if url in seen:
            rep.err(vw, "the same video is listed twice")
            continue
        seen.add(url)
        if run_online:
            check_video_online(course, v, vw, rep, strict)


def check_module(course, mod_id, syl, inv, rep, run_code=True, strict_net=False):
    mods = syl["modules"]
    index = next((i for i, m in enumerate(mods) if m.get("id") == mod_id), None)
    if index is None:
        rep.err(mod_id, "module id not in syllabus")
        return None
    smod = mods[index]
    mpath = course / "modules" / mod_id / "module.json"
    mod = load_json(mpath)
    where = mod_id
    if not require(mod, ["id", "title", "kind", "summary", "objectives", "sections", "exercises"], where, rep):
        return mod
    if mod["id"] != mod_id:
        rep.err(where, f"module.json id '{mod['id']}' does not match folder '{mod_id}'")
    kind = smod.get("kind", "module")
    if mod["kind"] != kind:
        rep.err(where, f"kind '{mod['kind']}' differs from syllabus kind '{kind}'")

    # Concepts available: everything the syllabus assigns to earlier modules,
    # plus what this module's sections teach.
    earlier = set()
    for m in mods[:index]:
        earlier.update(m.get("concepts", []))

    idx = api_index(inv)
    sections = mod["sections"]
    section_ids, taught_here = set(), set()
    taught_upto = {}          # section id -> concepts taught in this module up to and including it
    lesson_jobs = []          # (section, code) for runnable ```python blocks
    if not sections:
        rep.err(where, "no lesson sections")
    if kind == "project" and len(sections) > 2:
        rep.err(where, f"a project has 1-2 sections (the brief), has {len(sections)}")
    for s in sections:
        sw = f"{where}:{s.get('id', '?')}"
        if not require(s, ["id", "heading", "body_md", "concepts"], sw, rep):
            continue
        section_ids.add(s["id"])
        if kind == "module" and not (1 <= len(s["concepts"]) <= 3):
            rep.err(sw, f"a section teaches 1-3 concepts, has {len(s['concepts'])}; "
                        f"split the screen or list what it really teaches")
        for cid in s["concepts"]:
            if cid not in inv:
                rep.err(sw, f"unknown concept '{cid}'")
            elif kind == "project":
                # A project section never teaches. It may only point back to earlier modules.
                if cid not in earlier:
                    rep.err(sw, f"project section lists concept '{cid}', which no earlier module teaches; "
                                "a project teaches nothing new")
                continue
            taught_here.add(cid)
        taught_upto[s["id"]] = set(taught_here)
        blocks = runnable_blocks(s["body_md"])
        if blocks:
            check_api_use("\n".join(blocks), earlier | taught_here, inv, idx, sw, "lesson code", rep)
            # All ```python blocks of one section run in order in one namespace.
            lesson_jobs.append((s, "\n".join(blocks)))
        if kind == "module":
            # Screen shape: 60-200 words of prose plus at most one short code example.
            prose = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", " ", s["body_md"])
            words = len(re.findall(r"\w+", prose))
            if words < 50:
                rep.warn(sw, f"section is short ({words} words); a screen needs 60-200 words")
            elif words > 220:
                rep.warn(sw, f"section is long ({words} words); a screen needs 60-200 words. "
                             f"Cut it or move an idea to its own screen")
            if len(blocks) > 1:
                rep.warn(sw, f"section has {len(blocks)} runnable code blocks; use at most one short example")
        else:
            words = len(re.findall(r"\w+", s["body_md"]))
            if words > 500:
                rep.warn(sw, f"section is long ({words} words); check for padding")

    for cid in smod.get("concepts", []):
        if cid not in taught_here:
            rep.err(where, f"syllabus assigns concept '{cid}' but no section of this module teaches it")

    exercises = mod["exercises"]
    counts = {"exercise": 0, "recall": 0, "project-step": 0}
    used_here = set()
    practised = set()         # concepts that some solution really uses (by API, or by tag as a fallback)
    ex_ids = set()
    jobs = []
    sol_toks_by_ex = {}       # exercise id -> API tokens of the solution
    sol_lines_by_ex = {}      # exercise id -> non-trivial solution lines
    for e in exercises:
        ew = f"{where}:{e.get('id', '?')}"
        if not require(e, ["id", "title", "kind", "concepts", "instructions_md", "setup_code",
                           "starter_code", "solution_code", "hints", "test_code", "wrong_solutions"], ew, rep):
            continue
        if e["id"] in ex_ids:
            rep.err(ew, "duplicate exercise id")
        ex_ids.add(e["id"])
        if e["kind"] not in EXERCISE_KINDS:
            rep.err(ew, f"bad kind '{e['kind']}'")
            continue
        counts[e["kind"]] += 1
        if kind == "project" and e["kind"] != "project-step":
            rep.err(ew, "a project module holds only project-step exercises")
        if kind == "module" and e["kind"] == "project-step":
            rep.err(ew, "project-step belongs in the project module")
        after = e.get("after_section")
        if e["kind"] == "exercise" and after not in section_ids:
            rep.err(ew, f"after_section '{after}' is not a section of this module")
        if e["kind"] != "exercise" and after is not None:
            rep.err(ew, f"{e['kind']} needs after_section null, has '{after}'")
        # An exercise may use earlier modules' concepts plus what this module
        # has taught up to and including its section. Recall and project steps
        # come after all sections.
        allowed_here = taught_upto.get(after, set()) if e["kind"] == "exercise" else taught_here
        for cid in e["concepts"]:
            if cid not in inv:
                rep.err(ew, f"unknown concept '{cid}'")
                continue
            if cid not in earlier and cid not in allowed_here:
                if cid in taught_here:
                    rep.err(ew, f"uses concept '{cid}' before its section is reached (placed after '{after}')")
                else:
                    rep.err(ew, f"uses concept '{cid}' before any lesson teaches it")
            used_here.add(cid)
        allowed_all = earlier | allowed_here
        sol_toks = check_api_use(e["solution_code"], allowed_all, inv, idx, ew, "solution", rep)
        check_api_use(e["starter_code"], allowed_all, inv, idx, ew, "starter code", rep)
        if "carry_code" in e:
            if not isinstance(e["carry_code"], str):
                rep.err(ew, "carry_code must be a string")
            else:
                check_api_use(e["carry_code"], allowed_all, inv, idx, ew, "carry code", rep)
        sol_toks_by_ex[e["id"]] = sol_toks
        sol_lines_by_ex[e["id"]] = set(code_lines(e["solution_code"]))
        for cid in e["concepts"]:
            clear = [a for a in (inv.get(cid, {}).get("apis") or []) if not is_ambiguous(a)]
            if clear and not (set(clear) & sol_toks):
                rep.err(ew, f"tagged with '{cid}' but the solution uses none of its APIs {clear}; "
                            f"change the tag or make the solution use the concept")
        # A concept is PRACTISED only when a solution uses one of its clear APIs.
        # A concept without any clear API falls back to the tag.
        for cid, c in inv.items():
            if c.get("decision") != "include":
                continue
            clear = [a for a in (c.get("apis") or []) if not is_ambiguous(a)]
            if clear:
                if set(clear) & sol_toks:
                    practised.add(cid)
            elif cid in e["concepts"]:
                practised.add(cid)
        if e["kind"] == "recall":
            if not any(c in earlier for c in e["concepts"]):
                rep.err(ew, "recall exercise must practise a concept from an earlier module")
        # hints
        hints = e["hints"]
        want = 1 if e["kind"] == "project-step" else 3
        if not isinstance(hints, list) or len(hints) != want:
            rep.err(ew, f"needs exactly {want} hint(s), has {len(hints) if isinstance(hints, list) else 'none'}")
            hints = [h for h in hints if isinstance(h, str)] if isinstance(hints, list) else []
        sol_n = norm(e["solution_code"])
        keys = key_lines(e["solution_code"], e["starter_code"])
        known = ident_tokens(e["starter_code"]) | ident_tokens(e["setup_code"]) | set(keyword.kwlist)
        key_toks = set()
        for ln in keys:
            key_toks |= ident_tokens(ln)
        key_toks -= known
        for h_i, h in enumerate(hints):
            if not isinstance(h, str):
                rep.err(ew, f"hint {h_i + 1} must be a string")
                continue
            if wcount(h) < 4:
                rep.err(ew, f"hint {h_i + 1} has {wcount(h)} words; write a full hint (4 words or more)")
            if sol_n and sol_n in norm(h):
                rep.err(ew, f"hint {h_i + 1} contains the full solution")
            sh = squash(h)
            last = h_i >= want - 1      # hint 3 (or the single project hint)
            if not last:
                leaked = [ln for ln in keys if leaks_in(sh, ln)]
                if leaked:
                    rep.err(ew, f"hint {h_i + 1} gives away a solution line: `{leaked[0]}`. "
                                "Hints 1 and 2 may point at the idea but must not contain code the learner writes")
            else:
                if keys and all(leaks_in(sh, ln) for ln in keys):
                    rep.err(ew, f"hint {h_i + 1} contains every key line of the solution")
                elif len(key_toks) >= 3:
                    share = len(key_toks & ident_tokens(h)) / len(key_toks)
                    if share > 0.6:
                        rep.err(ew, f"hint {h_i + 1} gives away {share:.0%} of the new names in the solution "
                                    "(limit 60%); point at the method, do not write the answer")
        # leaks in the instructions and in the check messages
        instr = e["instructions_md"]
        if not isinstance(instr, str) or wcount(instr) < 12:
            rep.err(ew, f"instructions_md has {wcount(instr) if isinstance(instr, str) else 0} words; "
                        "say what to do and what the result is (12 words or more)")
        else:
            si = squash(instr)
            leaked = [ln for ln in keys if leaks_in(si, ln)]
            if leaked:
                rep.err(ew, f"instructions_md gives away a solution line: `{leaked[0]}`")
        for msg in check_call_messages(e["test_code"]):
            sm = squash(msg)
            leaked = [ln for ln in keys if leaks_in(sm, ln)]
            if leaked:
                rep.err(ew, f"a check message gives away a solution line: `{leaked[0]}`; "
                            "the learner sees this message when the check fails")
        # wrong solutions
        ws_raw = e["wrong_solutions"] if isinstance(e["wrong_solutions"], list) else []
        wrongs = [w for w in ws_raw if isinstance(w, dict)]
        if not isinstance(e["wrong_solutions"], list) or len(wrongs) != len(ws_raw):
            rep.err(ew, "wrong_solutions must be a list of objects {why, code}")
        if not wrongs:
            rep.err(ew, "needs at least one wrong solution")
        elif len(wrongs) < 2 and e["kind"] != "project-step":
            rep.warn(ew, "only one wrong solution; add a second plausible mistake")
        starter_n = ncode(e["starter_code"])
        for k, w in enumerate(wrongs):
            if not isinstance(w.get("code"), str):
                rep.err(ew, f"wrong solution {k} needs a string 'code', has {type(w.get('code')).__name__}")
                continue
            wn = ncode(w.get("code"))
            if norm(w.get("code")) == sol_n:
                rep.err(ew, "a wrong solution is identical to the solution")
            elif not wn:
                rep.err(ew, f"wrong solution {k} is empty; it must be a plausible mistake that reaches the checks")
            elif wn == starter_n:
                rep.err(ew, f"wrong solution {k} equals the starter code; it must be a plausible mistake")
        if "ns" not in e["test_code"] and "printed" in e["test_code"]:
            rep.warn(ew, "checks read only printed output; prefer checking variables or return values")
        if norm(e["starter_code"]) == sol_n:
            rep.err(ew, "starter code is the solution")
        if run_code:
            jobs.append((e, "solution", e["solution_code"], "pass"))
            jobs.append((e, "starter", e["starter_code"], "not-pass"))
            for k, w in enumerate(wrongs):
                if not isinstance(w.get("code"), str):
                    continue
                jobs.append((e, f"wrong[{k}] ({str(w.get('why', ''))[:60]})", w.get("code", ""), "not-pass"))

    if kind == "module":
        n = counts["exercise"]
        if not (5 <= n <= 6):
            rep.err(where, f"module needs 5-6 exercises, has {n}")
        check_pairing(where, sections, exercises, rep)
        if counts["recall"] > 1:
            rep.err(where, "at most one recall exercise per module")
        if index >= 1 and counts["recall"] == 0:
            rep.warn(where, "no recall exercise; modules after the first should revisit earlier material")
    else:
        if not (3 <= counts["project-step"] <= 6):
            rep.err(where, f"project needs 3-6 steps, has {counts['project-step']}")

    check_quiz(mod.get("quiz"), kind, where, inv, earlier | taught_here, ex_ids, rep)
    check_takeaways(mod.get("takeaways"), where, rep)
    check_videos(course, mod.get("videos"), where, rep, run_online=run_code, strict=strict_net)

    # Screen pairing by concept: the exercise after a section must practise what the section teaches.
    if kind == "module":
        by_after = {}
        for e in exercises:
            if isinstance(e, dict) and e.get("kind") == "exercise" and "id" in e:
                by_after.setdefault(e.get("after_section"), e)
        for sec in sections:
            e = by_after.get(sec.get("id")) if isinstance(sec, dict) else None
            if e is None or not isinstance(e.get("concepts"), list) or not isinstance(sec.get("concepts"), list):
                continue
            ew = f"{where}:{e['id']}"
            shared = set(e["concepts"]) & set(sec["concepts"])
            if not shared:
                rep.err(ew, f"the exercise does not tag any concept of its section '{sec['id']}' "
                            f"({sec['concepts']}); tag what the screen teaches")
                continue
            with_clear = {cid: [a for a in (inv.get(cid, {}).get("apis") or []) if not is_ambiguous(a)]
                          for cid in shared if cid in inv}
            with_clear = {c: a for c, a in with_clear.items() if a}
            toks = sol_toks_by_ex.get(e["id"], set())
            if with_clear and not any(set(a) & toks for a in with_clear.values()):
                rep.err(ew, f"the solution uses none of the APIs of the section's concepts {with_clear}; "
                            "the exercise must practise what its screen teaches")

    # setup_code is visible in the Data tab. It must not show another step's answer.
    for e in exercises:
        if not (isinstance(e, dict) and "id" in e and isinstance(e.get("setup_code"), str)):
            continue
        mine = set(code_lines(e["setup_code"]))
        if isinstance(e.get("solution_code"), str) and isinstance(e.get("starter_code"), str):
            own = mine & set(key_lines(e["solution_code"], e["starter_code"]))
            if own:
                rep.err(f"{where}:{e['id']}", f"setup_code shows this exercise's own answer (`{sorted(own)[0]}`); "
                                              "the Data tab must not show it. Remove that line from setup_code")
                continue
        for other_id, other_lines in sol_lines_by_ex.items():
            if other_id != e["id"] and mine & other_lines:
                shown = sorted(mine & other_lines)[0]
                rep.err(f"{where}:{e['id']}", f"setup_code shows another step's answer (the solution of "
                                              f"'{other_id}': `{shown}`); move it to carry_code")
                break

    # A solution must not be a copy of the lesson example of its module.
    lesson_blocks = [ncode(b) for sec in sections if isinstance(sec, dict)
                     for b in runnable_blocks(sec.get("body_md", ""), only_runnable=False)]
    for e in exercises:
        if not (isinstance(e, dict) and isinstance(e.get("solution_code"), str)):
            continue
        sn = ncode(e["solution_code"])
        if len(sn) >= 12 and any(sn in b for b in lesson_blocks):
            rep.err(f"{where}:{e.get('id', '?')}", "solution copies the lesson example; "
                                                    "the exercise must ask for something new")

    for s in sections:
        if kind != "module" or "concepts" not in s:
            continue
        if s["concepts"] and not (set(s["concepts"]) & used_here):
            rep.warn(f"{where}:{s['id']}", "no exercise uses what this section teaches (padding candidate)")

    if run_code and lesson_jobs:
        def go_lesson(job):
            s, code = job
            return s, harness_run("", code, "")

        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
            for s, res in pool.map(go_lesson, lesson_jobs):
                st = res.get("status")
                rep.runs.append({"exercise": s["id"], "run": "lesson examples", "expect": "pass",
                                 "status": st, "message": res.get("message", "")[:300]})
                if st != "pass":
                    rep.err(f"{where}:{s['id']}",
                            f"lesson ```python examples do not run on the pinned versions: {st}: "
                            f"{res.get('message', '')[:400]} (blocks in a section run in order in one "
                            f"namespace; mark non-runnable fragments as ```text or ```pycon)")

    if run_code and jobs:
        def go(job):
            e, label, code, expect = job
            return job, harness_run(exercise_setup(e), code, e["test_code"])

        wrong_status = {}
        slow_setup = {}
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as pool:
            for (e, label, code, expect), res in pool.map(go, jobs):
                ew = f"{where}:{e['id']}"
                st = res.get("status")
                if label.startswith("wrong["):
                    wrong_status.setdefault(e["id"], []).append(st)
                setup_ms = res.get("setup_ms")
                if isinstance(setup_ms, int) and setup_ms > SETUP_LIMIT_MS:
                    slow_setup[e["id"]] = max(slow_setup.get(e["id"], 0), setup_ms)
                rep.runs.append({"exercise": e["id"], "run": label, "expect": expect,
                                 "status": st, "message": res.get("message", "")[:300]})
                if st in ("setup_error", "harness_crash"):
                    rep.err(ew, f"{label}: {st}: {res.get('message', '')[:400]}")
                elif expect == "pass" and st != "pass":
                    rep.err(ew, f"reference solution does not pass: {st}: {res.get('message', '')[:400]}")
                elif expect == "not-pass" and st == "pass":
                    rep.err(ew, f"{label} passes the checks; the checks are too weak")
                elif expect == "not-pass" and st == "test_error":
                    rep.warn(ew, f"{label}: a check crashed instead of failing with a clear message "
                                 f"({res.get('message', '')[:160]}); guard types/names before comparing")
        # The page stops code that runs longer than ~15 s in total, and setup counts there.
        for eid, ms in sorted(slow_setup.items()):
            rep.err(f"{where}:{eid}", f"setup_code and carry_code take {ms / 1000:.1f} s to run; the page "
                                      f"allows about {SETUP_LIMIT_MS // 1000} s. Make the fixture smaller or "
                                      "remove slow imports")
        # At least one wrong solution must reach the checks and fail them.
        for e in exercises:
            if isinstance(e, dict) and e.get("id") in wrong_status and "fail" not in wrong_status[e["id"]]:
                rep.err(f"{where}:{e['id']}", "no wrong solution reaches the checks: none of them ends with "
                                              "status fail (the statuses are "
                                              f"{wrong_status[e['id']]}). Write a wrong solution that runs "
                                              "without error and fails a check")
    mod["_used"] = used_here
    mod["_practised"] = practised
    mod["_taught"] = taught_here
    return mod


def load_course_modules(course, syl):
    """[(module id, module dict)] for every syllabus module whose module.json loads."""
    out = []
    for m in syl.get("modules", []) if isinstance(syl, dict) else []:
        mid = m.get("id") if isinstance(m, dict) else None
        path = course / "modules" / str(mid) / "module.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((mid, data))
    return out


def check_course(course, syl, rep, only_module=None):
    """Checks that compare modules with each other: unique ids, duplicate exercises
    and, over the whole course, the quiz answer pattern. With only_module set, only
    findings that involve that module are reported; a clash with another module is a
    warning there, because that module may be in progress."""
    mods = load_course_modules(course, syl)

    def report(involved, where, msg):
        # --module: another module may be in progress (parallel authors), so a clash
        # with it is a warning. A clash inside the module stays an error. --all: error.
        if only_module is None:
            rep.err(where, msg)
        elif only_module in involved:
            if len(involved) == 1:
                rep.err(where, msg)
            else:
                rep.warn(where, msg + " (--module run: the other module may still change; "
                                      "the final --all run decides)")

    ids = {}                  # id -> first (module, kind of item)
    sols, instrs = {}, {}
    for mid, m in mods:
        for e in m.get("exercises") or []:
            if not isinstance(e, dict) or not isinstance(e.get("id"), str):
                continue
            eid = e["id"]
            if eid in ids and ids[eid][0] != mid:
                report({mid, ids[eid][0]}, f"{mid}:{eid}", f"id '{eid}' is already used by {ids[eid][1]} "
                       f"in module {ids[eid][0]}; ids must be unique in the whole course")
            ids.setdefault(eid, (mid, "an exercise"))
            sn = ncode(e.get("solution_code"))
            if sn:
                if sn in sols and sols[sn] != (mid, eid):
                    report({mid, sols[sn][0]}, f"{mid}:{eid}", f"solution_code equals the solution of "
                           f"'{sols[sn][1]}' (module {sols[sn][0]}); every exercise needs its own task")
                sols.setdefault(sn, (mid, eid))
            im = norm(e.get("instructions_md")).lower()
            if im:
                if im in instrs and instrs[im] != (mid, eid):
                    report({mid, instrs[im][0]}, f"{mid}:{eid}", f"instructions_md equals the instructions of "
                           f"'{instrs[im][1]}' (module {instrs[im][0]}); write a different task")
                instrs.setdefault(im, (mid, eid))
        for q in m.get("quiz") or []:
            if not isinstance(q, dict) or not isinstance(q.get("id"), str):
                continue
            qid = q["id"]
            if qid in ids and ids[qid][0] != mid:
                report({mid, ids[qid][0]}, f"{mid}:{qid}", f"id '{qid}' is already used by {ids[qid][1]} "
                       f"in module {ids[qid][0]}; ids must be unique in the whole course")
            ids.setdefault(qid, (mid, "a quiz item"))

    if only_module is not None:
        return
    items = []
    for mid, m in mods:
        for q in m.get("quiz") or []:
            opts, ans = (q.get("options"), q.get("answer")) if isinstance(q, dict) else (None, None)
            if isinstance(opts, list) and opts and isinstance(ans, int) and not isinstance(ans, bool) \
                    and 0 <= ans < len(opts) and all(isinstance(o, str) for o in opts):
                items.append((opts, ans))
    if len(items) >= 3 and len({a for _, a in items}) == 1:
        rep.warn("course", f"all {len(items)} quiz items have the same answer index ({items[0][1]}); "
                           "vary the position of the correct option")
    longest = sum(1 for opts, a in items
                  if len(opts) > 1 and len(opts[a]) > 1.5 * max(len(o) for i, o in enumerate(opts) if i != a))
    if items and longest * 2 > len(items):
        rep.warn("course", f"the correct option is much longer (over 50%) than every other option in "
                           f"{longest} of {len(items)} quiz items; learners can guess by length")


def write_report(path, text):
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"warning: could not write report {path}: {exc}", file=sys.stderr)


def safe_hash(course):
    try:
        return inputs_hash(course)
    except Exception:       # a report must always be written
        return ""


def fatal_exit(course, args, msg, h):
    out = {"ok": False, "fatal": msg, "scope": "all" if args.all else "fatal", "inputs_hash": h,
           "error_count": 1, "warning_count": 0,
           "errors": [{"where": "fatal", "message": msg}], "warnings": [], "runs": []}
    text = json.dumps(out, indent=2)
    if args.all:
        write_report(course / "reports" / "verify-report.json", text)
    if args.out:
        write_report(args.out, text)
    print(text)
    sys.exit(2)


def run_checks(course, args, h):
    rep = Report(h)
    inv = check_inventory(course, rep)
    scope = "inventory"
    if args.stage == "syllabus" or args.module or args.all:
        syl = check_syllabus(course, inv, rep, Path(args.syllabus_file) if args.syllabus_file else None)
        scope = "syllabus"
        if args.module:
            scope = f"module:{args.module}"
            check_module(course, args.module, syl, inv, rep, run_code=not args.no_run)
            check_course(course, syl, rep, only_module=args.module)
        elif args.all:
            scope = "all"
            practised, taught = set(), set()
            for m in syl.get("modules", []):
                if not (course / "modules" / m["id"] / "module.json").exists():
                    rep.err(m["id"], "module.json missing")
                    continue
                res = check_module(course, m["id"], syl, inv, rep, run_code=not args.no_run, strict_net=True)
                if res:
                    practised |= res.get("_practised", set())
                    taught |= res.get("_taught", set())
            check_course(course, syl, rep)
            cm = syl.get("_concept_module", {})
            mods_list = syl.get("modules", [])
            for cid, c in inv.items():
                if c["decision"] != "include":
                    continue
                # Route coverage errors to the module that the syllabus makes responsible.
                owner = mods_list[cm[cid]]["id"] if cid in cm else "syllabus"
                if cid not in taught:
                    rep.err(owner, f"coverage: included concept '{cid}' is never taught in a section")
                if cid not in practised:
                    rep.err(owner, f"coverage: included concept '{cid}' is never practised: no exercise "
                                   "solution uses one of its APIs (a concept without a clear API needs a tagged exercise)")
    return rep, scope


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("course_dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", choices=["inventory", "syllabus"])
    g.add_argument("--module")
    g.add_argument("--all", action="store_true")
    g.add_argument("--try", dest="try_ex", help="run --code-file against one exercise")
    ap.add_argument("--code-file")
    ap.add_argument("--syllabus-file")
    ap.add_argument("--no-run", action="store_true", help="skip code execution")
    ap.add_argument("--out", help="also write the JSON report to this path")
    args = ap.parse_args()

    course = Path(args.course_dir).resolve()

    if args.try_ex:
        # Learner simulation: run an attempt, report only what the learner sees.
        try:
            code = Path(args.code_file).read_text(encoding="utf-8")
            for mp in sorted((course / "modules").glob("*/module.json")):
                for e in load_json(mp).get("exercises", []):
                    if e.get("id") == args.try_ex:
                        res = harness_run(exercise_setup(e), code, e["test_code"])
                        print(json.dumps({"exercise": args.try_ex, "status": res.get("status"),
                                          "message": res.get("message"), "printed": res.get("printed", "")[:1500]}, indent=2))
                        sys.exit(0 if res.get("status") == "pass" else 1)
        except Fatal as exc:
            print(json.dumps({"ok": False, "fatal": str(exc)}))
            sys.exit(2)
        print(json.dumps({"fatal": f"exercise {args.try_ex} not found"}))
        sys.exit(2)

    h = safe_hash(course)
    try:
        rep, scope = run_checks(course, args, h)
    except Fatal as exc:
        fatal_exit(course, args, str(exc), h)
    except Exception as exc:
        if not args.all:
            raise
        fatal_exit(course, args, f"internal error in verify.py: {type(exc).__name__}: {exc}", h)

    out = rep.as_dict(scope)
    text = json.dumps(out, indent=2)
    if args.all:
        # The default report is the final gate. A --no-run pass is not a pass: code and
        # videos were not checked, so the stored report says ok false.
        stored = dict(out, ok=False, no_run=True) if args.no_run else out
        text = json.dumps(stored, indent=2)
        write_report(course / "reports" / "verify-report.json", text)
    if args.out:
        # cb.py passes --out with the same path; it must not undo the no-run mark.
        write_report(args.out, text)
    # Keep stdout short: drop passing runs.
    out["runs"] = [r for r in out["runs"] if not (
        (r["expect"] == "pass" and r["status"] == "pass") or
        (r["expect"] == "not-pass" and r["status"] != "pass"))]
    print(json.dumps(out, indent=2))
    sys.exit(0 if not rep.errors else 1)


if __name__ == "__main__":
    main()
