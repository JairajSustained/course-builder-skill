"""Hash of the course input files. Stdlib only.

verify.py stores this hash in reports/verify-report.json. render.py and the
build report compare it with the current files. A different hash means the
report is stale: the course changed after the last full verify.
"""

import hashlib
from pathlib import Path


def input_files(course):
    course = Path(course)
    files = [course / "inventory.json", course / "syllabus.json"]
    files += sorted((course / "modules").glob("*/module.json"))
    return [p for p in files if p.exists()]


def inputs_hash(course):
    """sha256 hex over each input file: its relative path, a NUL byte, its bytes."""
    course = Path(course)
    h = hashlib.sha256()
    for p in input_files(course):
        h.update(str(p.relative_to(course)).encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


if __name__ == "__main__":
    import sys
    print(inputs_hash(sys.argv[1]))
