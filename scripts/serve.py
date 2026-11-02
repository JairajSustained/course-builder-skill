#!/usr/bin/env python3
"""Serve the courses folder on localhost and open a course (or the catalog).

Usage: serve.py <target> [--no-open]

<target> is a course folder (it has course.meta.json or dist/index.html) or the
courses root. The script:
  1. rebuilds <root>/index.html with catalog.py,
  2. reuses the server in <root>/.server.json when its pid is alive and its port
     answers, otherwise starts a detached `python3 -m http.server` on 127.0.0.1,
  3. opens the URL (macOS `open`, else webbrowser) unless --no-open is given,
  4. prints the URL as JSON.

The server binds to 127.0.0.1 only. It reuses the last port when that port is
free, so the browser origin (and the learner's saved progress) stays the same.
Stdlib only.
"""

import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
            return True
    except (OSError, ValueError, TypeError):
        return False


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # same as http.server: ignore TIME_WAIT
        try:
            s.bind(("127.0.0.1", int(port)))
        except (OSError, ValueError, TypeError):
            return False
    return True


def pick_port(preferred):
    if preferred and port_free(preferred):
        return int(preferred)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def read_record(path):
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def main():
    args = [a for a in sys.argv[1:] if a != "--no-open"]
    no_open = "--no-open" in sys.argv[1:] or os.environ.get("CB_NO_OPEN") == "1"
    if len(args) != 1:
        sys.exit("usage: serve.py <course folder or courses root> [--no-open]")
    target = Path(args[0]).resolve()
    if not target.is_dir():
        sys.exit(f"error: {target} is not a directory")

    is_course = (target / "course.meta.json").is_file() or (target / "dist" / "index.html").is_file()
    root = target.parent if is_course else target
    slug = target.name if is_course else None
    if is_course and not (target / "dist" / "index.html").is_file():
        sys.exit(f"error: {target / 'dist' / 'index.html'} is missing; run `cb.py render` first")

    # 1) Catalog
    rc = subprocess.call([sys.executable, "-I", str(HERE / "catalog.py"), str(root)], stdout=subprocess.DEVNULL)
    if rc != 0:
        sys.exit("error: the catalog could not be built")

    # 2) Server: reuse or start
    rec_path = root / ".server.json"
    rec = read_record(rec_path)
    reused = bool(rec.get("pid") and rec.get("port") and pid_alive(rec["pid"]) and port_open(rec["port"]))
    if reused:
        port, pid = int(rec["port"]), int(rec["pid"])
    else:
        port = pick_port(rec.get("port"))
        proc = subprocess.Popen(
            [sys.executable, "-I", "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(root)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, close_fds=True)
        pid = proc.pid
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not port_open(port):
            if proc.poll() is not None:
                sys.exit("error: the web server stopped at start; port " + str(port))
            time.sleep(0.1)
        if not port_open(port):
            sys.exit("error: the web server did not start in 8 seconds")
        tmp = rec_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"pid": pid, "port": port}), encoding="utf-8")
        os.replace(tmp, rec_path)

    base = f"http://127.0.0.1:{port}/"
    url = base + (quote(slug) + "/dist/index.html" if slug else "index.html")

    # 3) Open
    opened = False
    if not no_open:
        try:
            if sys.platform == "darwin":
                opened = subprocess.call(["open", url]) == 0
            else:
                opened = bool(webbrowser.open(url))
        except OSError:
            opened = False

    # 4) Report
    print(json.dumps({"ok": True, "url": url, "catalog": base + "index.html", "port": port, "pid": pid,
                      "reused_server": reused, "opened": opened}, indent=2))


if __name__ == "__main__":
    main()
