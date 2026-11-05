"""The parallel test runner and the --fast wait scaling."""

import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from tests import WAIT_SCALE_ENV, scale_waits
from tests.run import FAST_SCALE, ROOT, child_env, find_classes, main, read_result, report, run_classes, select

SCRATCH = """
import os, time, unittest

class Passes(unittest.TestCase):
    def test_a(self):
        pass
    @unittest.skip("not here")
    def test_b(self):
        pass

class Fails(unittest.TestCase):
    def test_a(self):
        self.fail("this is the reason")
    def test_b(self):
        raise RuntimeError("another reason")

class BrokenSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError("no fixture")
    def test_a(self):
        pass

class Dies(unittest.TestCase):
    def test_a(self):
        os._exit(3)

class QuitsQuietly(unittest.TestCase):
    def test_a(self):
        os._exit(0)

class Hangs(unittest.TestCase):
    def test_a(self):
        time.sleep(120)
"""

CLASSES = {"scratch_run_mod.Passes": 2, "scratch_run_mod.Fails": 2, "scratch_run_mod.BrokenSetup": 1,
           "scratch_run_mod.Dies": 1, "scratch_run_mod.QuitsQuietly": 1}


def text_of_report(results, errors=()):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        code = report(results, list(errors), 1.0)
    return code, out.getvalue()


class TestRunning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.dir = Path(tmp.name)
        (cls.dir / "scratch_run_mod.py").write_text(SCRATCH, encoding="utf-8")
        cls.results = {r.name.split(".")[-1]: r for r in run_classes(CLASSES, processes=3, cwd=cls.dir)}

    def test_a_passing_class_counts_tests_and_skips(self):
        r = self.results["Passes"]
        self.assertTrue(r.ok, r.output)
        self.assertEqual((r.ran, r.skipped, r.failures, r.errors), (2, 1, 0, 0))

    def test_failures_keep_their_tracebacks(self):
        r = self.results["Fails"]
        self.assertFalse(r.ok)
        self.assertEqual((r.failures, r.errors, r.problem), (1, 1, ""))
        code, text = text_of_report([r])
        self.assertEqual(code, 1)
        self.assertIn("this is the reason", text)
        self.assertIn("another reason", text)
        self.assertIn("FAILED (failures=1, errors=1, crashed=0", text)

    def test_a_failing_setup_is_a_failure_though_no_test_ran(self):
        r = self.results["BrokenSetup"]
        self.assertFalse(r.ok)
        self.assertEqual((r.ran, r.errors, r.problem), (0, 1, ""))
        self.assertIn("Ran 0 of 1 test in", text_of_report([r])[1])

    def test_a_crash_fails_the_run(self):
        r = self.results["Dies"]
        self.assertFalse(r.ok)
        self.assertIn("exit code 3", r.problem)
        code, text = text_of_report([r])
        self.assertEqual(code, 1)
        self.assertIn("crashed=1", text)

    def test_exit_code_zero_without_every_test_run_fails_the_run(self):
        r = self.results["QuitsQuietly"]
        self.assertFalse(r.ok)
        self.assertEqual(r.problem, "ran 0 of 1 tests")

    def test_a_hung_class_is_stopped_and_fails_the_run(self):
        r = run_classes({"scratch_run_mod.Hangs": 1}, cwd=self.dir, timeout=2)[0]
        self.assertFalse(r.ok)
        self.assertIn("stopped", r.problem)

    def test_every_class_reports_once(self):
        seen = []
        run_classes({"scratch_run_mod.Passes": 2}, cwd=self.dir, on_done=lambda r: seen.append(r.name))
        self.assertEqual(seen, ["scratch_run_mod.Passes"])


class TestResultLines(unittest.TestCase):
    def test_all_counts_from_the_closing_lines(self):
        output = ("...\nRan 5 tests in 0.001s\n\n"
                  "FAILED (failures=3, errors=1, expected failures=1, unexpected successes=1)\n")
        r = read_result("m.C", 5, 1, 0.1, output)
        self.assertEqual((r.ran, r.failures, r.errors, r.problem), (5, 4, 1, ""))

    def test_a_printed_ok_cannot_replace_a_failed_verdict(self):
        output = "Ran 1 test in 0.1s\n\nFAILED (failures=1)\nOK\n"
        r = read_result("m.C", 1, 1, 0.1, output)
        self.assertEqual(r.failures, 1)
        self.assertFalse(r.ok)

    def test_exit_code_decides_even_when_the_lines_say_ok(self):
        r = read_result("m.C", 1, 3221225477, 0.1, "Ran 1 test in 0.1s\n\nOK\n")
        self.assertFalse(r.ok)
        self.assertIn("3221225477", r.problem)

    def test_no_classes_is_not_a_pass(self):
        self.assertEqual(text_of_report([])[0], 1)


class TestFinding(unittest.TestCase):
    def test_finds_classes_with_their_test_counts(self):
        classes, errors = find_classes()
        self.assertEqual(errors, [])
        self.assertEqual(classes["tests.test_run.TestResultLines"],
                         len(unittest.defaultTestLoader.getTestCaseNames(TestResultLines)))
        self.assertFalse([c for c in classes if not c.startswith("tests.test_")], classes)

    def test_a_module_that_cannot_be_imported_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.dict(sys.modules):
            (Path(tmp) / "test_scratch_good.py").write_text(
                "import unittest\nclass Good(unittest.TestCase):\n    def test_a(self):\n        pass\n", encoding="utf-8")
            (Path(tmp) / "test_scratch_broken.py").write_text("import scratch_no_such_module\n", encoding="utf-8")
            classes, errors = find_classes(tmp, tmp)
        self.assertEqual(classes, {"test_scratch_good.Good": 1})
        self.assertEqual(len(errors), 1)
        self.assertIn("scratch_no_such_module", errors[0])
        code, text = text_of_report([read_result("test_scratch_good.Good", 1, 0, 0.1, "Ran 1 test in 0.1s\n\nOK\n")], errors)
        self.assertEqual(code, 1)
        self.assertIn("not imported=1", text)

    def test_names_select_modules_and_classes_by_dotted_part(self):
        classes = {"tests.test_a.TestOne": 1, "tests.test_a.TestTwo": 2, "tests.test_ab.TestOne": 3}
        self.assertEqual(set(select(classes, [])), set(classes))
        self.assertEqual(set(select(classes, ["test_a"])), {"tests.test_a.TestOne", "tests.test_a.TestTwo"})
        self.assertEqual(set(select(classes, ["TestOne"])), {"tests.test_a.TestOne", "tests.test_ab.TestOne"})
        self.assertEqual(set(select(classes, ["test_a.TestOne"])), {"tests.test_a.TestOne"})
        self.assertEqual(set(select(classes, ["tests.test_ab.TestOne", "TestTwo"])),
                         {"tests.test_ab.TestOne", "tests.test_a.TestTwo"})

    def test_an_unknown_name_is_an_error_not_an_empty_pass(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as stop:
            main(["test_no_such_module"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("test_no_such_module", err.getvalue())


class TestSpeed(unittest.TestCase):
    def test_fast_is_a_setting_of_the_test_process_only(self):
        with mock.patch.dict(os.environ, {WAIT_SCALE_ENV: "0.5"}):
            self.assertNotIn(WAIT_SCALE_ENV, child_env("tests.test_a.TestOne", fast=False))
            self.assertEqual(child_env("tests.test_a.TestOne", fast=True)[WAIT_SCALE_ENV], str(FAST_SCALE))

    def test_an_excluded_class_keeps_normal_speed(self):
        with mock.patch("tests.run.NOT_SCALED", {"tests.test_a.TestOne"}):
            self.assertNotIn(WAIT_SCALE_ENV, child_env("tests.test_a.TestOne", fast=True))
            self.assertIn(WAIT_SCALE_ENV, child_env("tests.test_a.TestTwo", fast=True))

    def test_waits_and_delays_are_scaled(self):
        calls = []

        class Waits:
            def wait_for_timeout(self, timeout):
                calls.append(timeout)

        class Page(Waits):
            pass

        class Frame(Waits):
            pass

        class Locator:
            def type(self, text, *, delay=None, timeout=None, no_wait_after=None):
                calls.append((text, delay, timeout))

        scale_waits(0.25, Page, Frame, Locator)
        Page().wait_for_timeout(1000)
        Frame().wait_for_timeout(timedelta(seconds=2))
        Locator().type("a", delay=40, timeout=5000)
        Locator().type("b")
        self.assertEqual(calls, [250, timedelta(milliseconds=500), ("a", 10, 5000), ("b", None, None)])

    def run_python(self, code, scale):
        env = dict(os.environ)
        env.pop(WAIT_SCALE_ENV, None)
        if scale:
            env[WAIT_SCALE_ENV] = scale
        return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)

    @unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
    def test_only_the_tests_package_applies_the_setting(self):
        check = ("from playwright.sync_api import Frame, Locator, Page\n"
                 "print(sum(hasattr(f, '__wrapped__') for f in (Page.wait_for_timeout, Frame.wait_for_timeout, Locator.type)))\n")
        tests_on = self.run_python("import tests\n" + check, "0.25")
        self.assertEqual(tests_on.stdout.strip(), "3", tests_on.stderr)
        tests_off = self.run_python("import tests\n" + check, None)
        self.assertEqual(tests_off.stdout.strip(), "0", tests_off.stderr)
        # The real filler in a shell where the variable happens to be set: its waits stay as they are.
        production = self.run_python("import jobfill.cli, jobfill.apply.runner, sys\n"
                                     "assert 'tests' not in sys.modules\n" + check, "0.25")
        self.assertEqual(production.stdout.strip(), "0", production.stderr)

    def test_a_scale_out_of_range_stops_the_tests(self):
        for bad in ("0", "2", "fast"):
            result = self.run_python("import tests", bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn("ValueError", result.stderr)


if __name__ == "__main__":
    unittest.main()
