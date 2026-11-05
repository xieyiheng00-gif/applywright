"""Run the tests in less time: one process per test class, several at a time.

    python -m tests.run                  everything at normal speed (before a PR)
    python -m tests.run --fast           fixed waits and typing delays a quarter as long (before a commit)
    python -m tests.run test_workday     only these modules or classes (TestSourceChoice works too)

`python -m unittest discover -s tests -t .` still runs the same tests one at a time. --fast only sets
JOBFILL_TEST_WAIT_SCALE for the test processes; tests/__init__.py reads it, nothing under jobfill/ does.
"""

import argparse
import locale
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import IO, NamedTuple

from jobfill.apply.browser import _kill
from tests import WAIT_SCALE_ENV

ROOT = Path(__file__).resolve().parent.parent
PROCESSES = min(12, os.cpu_count() or 1)
FAST_SCALE = 0.25
CLASS_TIMEOUT = 900        # seconds: a hung class must fail the run, not stall it (the slowest takes minutes)
SLOWEST_SHOWN = 5
SEPARATOR = "=" * 70       # unittest prints this line above each failure

# Classes that fail only with shortened waits run at normal speed under --fast. Never weaken the test instead.
NOT_SCALED = frozenset()

_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_VERDICT = re.compile(r"^(?:OK|FAILED|NO TESTS RAN)\b.*$", re.M)
_COUNT = re.compile(r"([a-z][a-z ]*)=(\d+)")


@dataclass
class ClassResult:
    name: str
    expected: int              # tests discovery found in the class
    returncode: "int | None"   # None: stopped after the timeout
    seconds: float
    output: str
    ran: int = 0
    skipped: int = 0
    failures: int = 0
    errors: int = 0
    problem: str = ""          # the process did not report a normal result

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.problem


def read_result(name, expected, returncode, seconds, output) -> ClassResult:
    """The result of one class from unittest's closing lines; the exit code decides whether it passed."""
    result = ClassResult(name, expected, returncode, seconds, output)
    ran = None
    for ran in _RAN.finditer(output):
        pass
    if ran:
        result.ran = int(ran.group(1))
        verdict = _VERDICT.search(output, ran.end())
        counts = {k.strip(): int(v) for k, v in _COUNT.findall(verdict.group(0))} if verdict else {}
        result.skipped = counts.get("skipped", 0)
        result.failures = counts.get("failures", 0) + counts.get("unexpected successes", 0)
        result.errors = counts.get("errors", 0)
    if returncode is None:
        result.problem = f"no result after {seconds:.0f} s, stopped"
    elif returncode and not (result.failures or result.errors):
        result.problem = f"crashed with exit code {returncode}"
    elif not returncode and result.ran != expected:
        result.problem = f"ran {result.ran} of {expected} tests"
    return result


def find_classes(start_dir=ROOT / "tests", top_level_dir=ROOT):
    """({tests.module.Class: number of tests}, [import errors]), from unittest discovery."""
    def flatten(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from flatten(item)
            else:
                yield item

    loader = unittest.TestLoader()
    suite = loader.discover(str(start_dir), top_level_dir=str(top_level_dir))
    # Modules that fail to import show up as stand-in tests from unittest.loader, and in loader.errors.
    ids = {test.id() for test in flatten(suite) if type(test).__module__ != "unittest.loader"}
    classes = Counter(test_id.rsplit(".", 1)[0] for test_id in ids)
    return dict(sorted(classes.items())), list(loader.errors)


def select(classes: dict, names) -> dict:
    """The classes a name refers to by any dotted part: test_x, TestY, test_x.TestY. Unknown names raise."""
    if not names:
        return classes
    chosen = {}
    for name in names:
        matched = {c: n for c, n in classes.items() if f".{name}." in f".{c}."}
        if not matched:
            raise LookupError(f"no test module or class named {name}")
        chosen.update(matched)
    return chosen


def child_env(name: str, fast: bool) -> dict:
    env = dict(os.environ)
    env.pop(WAIT_SCALE_ENV, None)   # normal speed means normal speed, whatever the shell has set
    if fast and name not in NOT_SCALED:
        env[WAIT_SCALE_ENV] = str(FAST_SCALE)
    return env


class Job(NamedTuple):
    name: str
    expected: int
    proc: subprocess.Popen
    output: IO[bytes]
    started: float


def run_classes(classes: dict, fast=False, processes=PROCESSES, cwd=ROOT, timeout=CLASS_TIMEOUT, on_done=None) -> list:
    """Run each class in its own process, at most `processes` at a time, and return their results."""
    waiting, running, results = list(classes.items()), [], []

    def start(name, expected) -> Job:
        # A file, not a pipe: the browser driver inherits stderr, and a pipe it holds open would never end.
        output = tempfile.TemporaryFile()
        proc = subprocess.Popen([sys.executable, "-m", "unittest", name], cwd=cwd, env=child_env(name, fast),
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=True)   # its own group, so _kill reaches its browsers
        return Job(name, expected, proc, output, time.monotonic())

    def finish(job: Job, returncode):
        job.output.seek(0)
        text = job.output.read().decode(locale.getpreferredencoding(False), errors="replace").replace("\r\n", "\n")
        job.output.close()
        result = read_result(job.name, job.expected, returncode, time.monotonic() - job.started, text)
        results.append(result)
        if on_done:
            on_done(result)

    try:
        while waiting or running:
            while waiting and len(running) < processes:
                running.append(start(*waiting.pop(0)))
            for job in list(running):
                returncode = job.proc.poll()
                if returncode is None and time.monotonic() - job.started < timeout:
                    continue
                if returncode is None:
                    _kill(job.proc.pid)
                    job.proc.wait()
                running.remove(job)
                finish(job, returncode)
            time.sleep(0.05)
    finally:
        for job in running:   # interrupted: leave no test browsers behind
            _kill(job.proc.pid)
    return results


def report(results: list, import_errors: list, seconds: float) -> int:
    """Print the combined result and return the exit code."""
    failed = [r for r in results if not r.ok]
    for text in import_errors:
        print(f"\n{SEPARATOR}\nCould not import a test module\n{text.strip()}")
    for r in failed:
        # What unittest printed from the first failure on; all of it when the process died before that.
        at = r.output.find(SEPARATOR)
        print(f"\n{SEPARATOR}\n{r.name}: {r.problem or f'exit code {r.returncode}'}, {r.seconds:.1f} s")
        print((r.output[at:] if at >= 0 else r.output).strip())

    print(f"\nSlowest of {len(results)} classes:")
    for r in sorted(results, key=lambda r: r.seconds, reverse=True)[:SLOWEST_SHOWN]:
        print(f"{r.seconds:8.1f} s  {r.name}")

    total, expected = sum(r.ran for r in results), sum(r.expected for r in results)
    ran = str(total) if total == expected else f"{total} of {expected}"
    print(f"\nRan {ran} test{'' if expected == 1 else 's'} in {seconds:.1f} s, skipped {sum(r.skipped for r in results)}")
    if results and not failed and not import_errors:
        print("OK")
        return 0
    print(f"FAILED (failures={sum(r.failures for r in results)}, errors={sum(r.errors for r in results)}, "
          f"crashed={sum(1 for r in failed if r.problem)}, not imported={len(import_errors)})")
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.run", description="Run each test class in its own process.")
    parser.add_argument("names", nargs="*", help="only these test modules or classes, e.g. test_workday")
    parser.add_argument("--fast", action="store_true", help=f"fixed waits and typing delays at {FAST_SCALE} of normal")
    args = parser.parse_args(argv)

    started = time.monotonic()
    classes, import_errors = find_classes()
    try:
        classes = select(classes, args.names)
    except LookupError as err:
        for text in import_errors:
            print(text.strip(), file=sys.stderr)
        parser.error(str(err))

    speed = f", waits at {FAST_SCALE}" if args.fast else ""
    print(f"{sum(classes.values())} tests in {len(classes)} classes, {PROCESSES} processes at a time{speed}", flush=True)

    def progress(result):
        print("." if result.ok else "C" if result.problem else "F", end="", flush=True)

    results = run_classes(classes, fast=args.fast, on_done=progress)
    print(flush=True)
    return report(results, import_errors, time.monotonic() - started)


if __name__ == "__main__":
    # A traceback that cannot be encoded for this console must not take the summary down with it.
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.exit(main())
