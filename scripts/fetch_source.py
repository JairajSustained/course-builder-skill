"""Fetch one documentation page and store it as faithful markdown.

Run through cb.py:  cb.py fetch <course_dir> <url> <file.md> --kind official-guide --date YYYY-MM-DD

Keeps headings, code blocks, argument lists and notes. Strips navigation,
sidebars, scripts and footers. Rebuilds sources/INDEX.md afterwards.
Plain-text and markdown URLs (e.g. raw GitHub docs) are stored as they are.
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify

MAIN_SELECTORS = [
    "[itemprop=articleBody]", "div[role=main]", "main article", "article",
    "div.body", "div.document", "main", "#content", "div.content", "div.markdown-body",
]
DROP = ["script", "style", "nav", "header", "footer", "aside", "form", "noscript",
        ".headerlink", ".sphinxsidebar", ".related", ".toctree-wrapper .caption",
        ".wy-nav-side", ".rst-footer-buttons", ".edit-this-page", "[role=navigation]"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (course-builder docs fetch)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        ctype = r.headers.get("content-type", "")
        charset = r.headers.get_content_charset() or "utf-8"
        return r.read().decode(charset, "replace"), ctype


def to_markdown(html):
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "")
    main = None
    for sel in MAIN_SELECTORS:
        main = soup.select_one(sel)
        if main and len(main.get_text(strip=True)) > 200:
            break
    main = main or soup.body or soup
    for sel in DROP:
        for n in main.select(sel):
            n.decompose()
    # Sphinx/pygments: keep code text, drop line-number tables.
    for n in main.select("td.linenos, .linenodiv"):
        n.decompose()
    md = markdownify(str(main), heading_style="ATX", bullets="-", code_language="python",
                     escape_underscores=False, escape_asterisks=False, escape_misc=False)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return title, md


SPLIT_WORDS = 6000
PART_WORDS = 4500


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:40].strip("-") or "part"


def split_markdown(md):
    """Split a long page on '## ' headings into parts of about PART_WORDS words.
    Code fences are respected: a '## ' line inside a fence is not a heading."""
    chunks, cur, in_fence = [], [], False
    for line in md.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## ") and cur:
            chunks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        chunks.append("\n".join(cur))
    parts, buf = [], []
    for ch in chunks:
        if buf and len(re.findall(r"\w+", "\n".join(buf + [ch]))) > PART_WORDS:
            parts.append("\n".join(buf))
            buf = []
        buf.append(ch)
    if buf:
        parts.append("\n".join(buf))
    return parts


def rebuild_index(sources):
    rows = []
    for p in sorted(sources.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        head = p.read_text(encoding="utf-8", errors="replace")[:600]
        url = re.search(r"^url:\s*(.+)$", head, re.M)
        kind = re.search(r"^kind:\s*(.+)$", head, re.M)
        title = re.search(r"^title:\s*(.+)$", head, re.M)
        words = len(re.findall(r"\w+", p.read_text(encoding="utf-8", errors="replace")))
        rows.append(f"| {p.name} | {kind.group(1).strip() if kind else '?'} | {words} | "
                    f"{title.group(1).strip() if title else ''} | {url.group(1).strip() if url else ''} |")
    text = ("# Sources\n\n| file | kind | words | title | url |\n|---|---|---|---|---|\n"
            + "\n".join(rows) + "\n")
    (sources / "INDEX.md").write_text(text, encoding="utf-8")


def main():
    course, url, fname, kind, date = sys.argv[1:6]
    sources = Path(course) / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    if not re.match(r"^[0-9]{2}-[a-z0-9-]+\.md$", fname):
        print(json.dumps({"ok": False, "error": "file name must look like 03-searching-the-tree.md"}))
        sys.exit(1)
    # Anchors point into the same page: fetch each page once.
    url = url.split("#", 1)[0]
    existing = []
    for p in sorted(sources.glob("*.md")):
        if p.name == "INDEX.md":
            continue
        m = re.search(r"^url:\s*(\S+)\s*$", p.read_text(encoding="utf-8", errors="replace")[:400], re.M)
        if m and m.group(1).split("#", 1)[0].rstrip("/") == url.rstrip("/"):
            existing.append(f"sources/{p.name}")
    if existing:
        print(json.dumps({"ok": True, "url": url, "already_fetched": True, "files": existing,
                          "note": "this page is already in sources/; not fetched again"}))
        return
    try:
        body, ctype = fetch(url)
    except Exception as exc:
        print(json.dumps({"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)
    if "html" in ctype or body.lstrip().lower().startswith(("<!doctype", "<html")):
        title, md = to_markdown(body)
    else:
        title, md = "", body.strip()
    words = len(re.findall(r"\w+", md))
    code_blocks = md.count("```") // 2
    files = []
    if words > SPLIT_WORDS:
        parts = split_markdown(md)
        base = fname[:-3]
        for k, part in enumerate(parts, 1):
            first = re.search(r"^#{1,3} (.+)$", part, re.M)
            heading = first.group(1) if first else f"part {k}"
            pname = f"{base}-p{k:02d}-{slugify(heading)}.md"
            front = (f"---\nurl: {url}\nfetched: {date}\nkind: {kind}\n"
                     f"title: {title} (part {k} of {len(parts)}: {heading})\n---\n\n")
            (sources / pname).write_text(front + part + "\n", encoding="utf-8")
            files.append({"file": f"sources/{pname}", "words": len(re.findall(r"\w+", part))})
    else:
        front = f"---\nurl: {url}\nfetched: {date}\nkind: {kind}\ntitle: {title}\n---\n\n"
        (sources / fname).write_text(front + md + "\n", encoding="utf-8")
        files.append({"file": f"sources/{fname}", "words": words})
    rebuild_index(sources)
    print(json.dumps({"ok": words > 150, "url": url, "title": title, "words": words,
                      "code_blocks": code_blocks, "files": files,
                      "warning": "" if words > 150 else "very little text extracted; the page may need JavaScript. Try the raw docs source (e.g. GitHub .rst/.md) instead."}))


if __name__ == "__main__":
    main()
