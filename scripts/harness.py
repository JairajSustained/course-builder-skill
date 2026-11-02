"""Exercise harness. One file, two runtimes.

The same source runs in local CPython (verify.py) and in the browser (Pyodide,
inlined into the page by render.py). Keep it stdlib-only and Python 3.11+.

Contract:
    run_exercise(setup_code, user_code, test_code, time_limit=5.0) -> dict

    - setup_code runs first (hidden fixtures, e.g. an HTML string). Callers
      append carry_code to it (code that recreates results of earlier steps).
      Neither is traced and neither counts toward the learner time limit: the
      deadline starts when setup is done.
    - user_code runs next in the same namespace.
    - test_code runs last in its own namespace with:
        ns       -> the learner namespace (dict)
        printed  -> everything the learner code printed (str)
        check(condition, message) -> records a failed check; the first
                    failure stops the checks.
    Result dict: {"status": "pass"|"fail"|"error"|"timeout"|"setup_error"
                  |"test_error", "message": str, "printed": str,
                  "checks_passed": int, "setup_ms": int}
    setup_ms is the time that setup (and carry code) took, in milliseconds.
"""

import io
import sys
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr

MAX_PRINTED = 4000


class _CheckFailed(Exception):
    pass


class _TimeLimit(BaseException):
    # BaseException: a learner's `except Exception:` must not swallow it.
    pass


def _make_tracer(deadline):
    def tracer(frame, event, arg):
        if time.monotonic() > deadline:
            raise _TimeLimit()
        return tracer
    return tracer


def _learner_traceback(exc):
    """Keep only frames from the learner's code so the message stays short."""
    tb = traceback.extract_tb(exc.__traceback__)
    frames = [f for f in tb if f.filename == "<your code>"]
    lines = []
    for f in frames:
        lines.append(f"  line {f.lineno}: {f.line or ''}".rstrip())
    head = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, SyntaxError) and exc.filename == "<your code>":
        head = f"SyntaxError on line {exc.lineno}: {exc.msg}"
        lines = []
    return "\n".join(([head] + lines) if lines else [head])


def run_exercise(setup_code, user_code, test_code, time_limit=5.0):
    ns = {"__name__": "__main__"}
    out = io.StringIO()
    result = {"status": "error", "message": "", "printed": "", "checks_passed": 0, "setup_ms": 0}

    old_trace = sys.gettrace()
    try:
        with redirect_stdout(out), redirect_stderr(out):
            t_setup = time.monotonic()
            try:
                exec(compile(setup_code or "", "<setup>", "exec"), ns)
                result["setup_ms"] = int((time.monotonic() - t_setup) * 1000)
            except Exception as exc:  # fixture bug, not learner bug
                result["setup_ms"] = int((time.monotonic() - t_setup) * 1000)
                result["status"] = "setup_error"
                result["message"] = f"Exercise setup failed: {type(exc).__name__}: {exc}"
                return result
            try:
                code = compile(user_code or "", "<your code>", "exec")
            except SyntaxError as exc:
                result["status"] = "error"
                result["message"] = _learner_traceback(exc)
                return result
            # The learner deadline starts here, after setup (which may import
            # slow libraries) has finished.
            deadline = time.monotonic() + time_limit
            sys.settrace(_make_tracer(deadline))
            try:
                exec(code, ns)
            except _TimeLimit:
                result["status"] = "timeout"
                result["message"] = (
                    f"Your code ran longer than {time_limit:g} seconds. "
                    "Look for a loop that never ends."
                )
                return result
            except Exception as exc:
                result["status"] = "error"
                result["message"] = _learner_traceback(exc)
                return result
            finally:
                sys.settrace(old_trace)
    finally:
        sys.settrace(old_trace)
        printed = out.getvalue()
        result["printed"] = printed[:MAX_PRINTED]

    passed = 0

    def check(condition, message):
        nonlocal passed
        if not condition:
            raise _CheckFailed(message)
        passed += 1

    test_ns = {"ns": ns, "printed": printed, "check": check, "__name__": "__checks__"}
    # Checks may call learner functions, so they get their own time budget.
    sys.settrace(_make_tracer(time.monotonic() + time_limit))
    try:
        exec(compile(test_code or "", "<checks>", "exec"), test_ns)
    except _TimeLimit:
        result["status"] = "timeout"
        result["message"] = (
            f"Your code ran longer than {time_limit:g} seconds while it was checked. "
            "Look for a loop that never ends in your functions."
        )
        result["checks_passed"] = passed
        return result
    except _CheckFailed as exc:
        result["status"] = "fail"
        result["message"] = str(exc)
        result["checks_passed"] = passed
        return result
    except Exception as exc:
        # A check raised (e.g. KeyError because a variable is missing, or a
        # learner object has the wrong type). Report it as a failed check,
        # but mark it so verify.py can flag fragile tests.
        result["status"] = "test_error"
        result["message"] = (
            "A check could not run on your result "
            f"({type(exc).__name__}: {exc}). Check variable names and types."
        )
        result["checks_passed"] = passed
        return result
    finally:
        sys.settrace(old_trace)

    result["status"] = "pass"
    result["message"] = "All checks passed."
    result["checks_passed"] = passed
    return result


if __name__ == "__main__" and sys.platform != "emscripten":
    # CLI used by verify.py: reads one JSON job on stdin, prints one JSON result.
    # The browser (Pyodide, platform "emscripten") skips this block.
    import json

    job = json.load(sys.stdin)
    setup = job.get("setup_code", "") or ""
    if job.get("carry_code"):
        setup = setup + "\n" + job["carry_code"]
    res = run_exercise(
        setup,
        job.get("user_code", ""),
        job.get("test_code", ""),
        float(job.get("time_limit", 5.0)),
    )
    sys.stdout.write(json.dumps(res))
