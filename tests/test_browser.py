"""The shared jobfill browser: its own port, reconnects, and failures that say what to do."""

import importlib.util
import os
import sys
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

from jobfill.apply import browser

from .helpers import temp_dir

NETSH = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      9318        9417
     50000       50059     *
"""


def no_fixed_port():
    env = dict(os.environ)
    env.pop("JOBFILL_CDP_PORT", None)
    env.pop("JOBFILL_BROWSER_PROFILE", None)
    return mock.patch.dict(os.environ, env, clear=True)


class TestReservedPorts(unittest.TestCase):
    def test_reads_windows_exclusion_ranges(self):
        result = mock.Mock(stdout=NETSH)
        with mock.patch.object(browser.os, "name", "nt"), mock.patch.object(browser.subprocess, "run", return_value=result):
            self.assertTrue(browser.reserved_by_windows(9333))
            self.assertTrue(browser.reserved_by_windows(50000))
            self.assertFalse(browser.reserved_by_windows(9444))

    def test_fixed_reserved_port_is_reported_before_launching(self):
        with no_fixed_port(), mock.patch.dict(os.environ, {"JOBFILL_CDP_PORT": "9333"}), \
                mock.patch.object(browser, "reserved_by_windows", return_value=True), \
                mock.patch.object(browser, "chrome_path", side_effect=AssertionError("must not launch")):
            with self.assertRaises(RuntimeError) as err:
                browser.ensure(temp_dir(self))
        self.assertIn("port 9333 is reserved by Windows", str(err.exception))
        self.assertIn("unset JOBFILL_CDP_PORT", str(err.exception))


class TestLaunchFailures(unittest.TestCase):
    def test_chromium_exiting_right_away_points_to_apply_close(self):
        # Any executable that rejects Chromium's flags exits at once, like a launch handed to another window.
        with no_fixed_port(), mock.patch.dict(os.environ, {"JOBFILL_CHROME": sys.executable}):
            with self.assertRaises(RuntimeError) as err:
                browser.ensure(temp_dir(self), headless=True, timeout=10)
        self.assertIn("exited right away", str(err.exception))
        self.assertIn("python -m jobfill apply close", str(err.exception))

    def test_timeout_kills_the_process_it_started(self):
        proc = mock.Mock(pid=4242)
        proc.poll.return_value = None   # running, but never writes DevToolsActivePort
        with no_fixed_port(), mock.patch.object(browser, "chrome_path", return_value="chrome"), \
                mock.patch.object(browser.subprocess, "Popen", return_value=proc), \
                mock.patch.object(browser, "_kill") as kill:
            with self.assertRaises(RuntimeError) as err:
                browser.ensure(temp_dir(self), timeout=0.6)
        kill.assert_called_once_with(4242)
        self.assertIn("closed it", str(err.exception))

    def test_stale_port_file_is_removed_before_launch(self):
        data = temp_dir(self)
        (data / "browser-profile").mkdir()
        (data / "browser-profile" / "DevToolsActivePort").write_text("9333\n/devtools/browser/old")
        proc = mock.Mock(pid=1)
        proc.poll.return_value = 0
        with no_fixed_port(), mock.patch.object(browser, "chrome_path", return_value="chrome"), \
                mock.patch.object(browser.subprocess, "Popen", return_value=proc):
            with self.assertRaises(RuntimeError):
                browser.ensure(data)
        self.assertFalse((data / "browser-profile" / "DevToolsActivePort").exists())


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestRealLaunch(unittest.TestCase):
    def test_own_port_reconnect_and_close(self):
        data = temp_dir(self)
        with no_fixed_port(), warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)   # the window outliving ensure() is the point
            first = browser.ensure(data, headless=True)
            self.addCleanup(browser.close, data)
            state = browser._read_state(data)
            self.assertEqual(first, browser.endpoint(state["port"]))
            self.assertTrue(browser.alive(state["port"]))
            # A second command (e.g. the candidate's terminal) reconnects instead of launching again.
            self.assertEqual(browser.ensure(data, headless=True), first)
            self.assertEqual(browser._read_state(data)["pid"], state["pid"])
            self.assertTrue(browser.close(data))
        for _ in range(40):
            if not browser.alive(state["port"]):
                break
            time.sleep(0.25)
        self.assertFalse(browser.alive(state["port"]))


@unittest.skipUnless(importlib.util.find_spec("playwright"), "Playwright not installed")
class TestFindPage(unittest.TestCase):
    """Each application keeps its own tab, also when every job is on one host (jobs.ashbyhq.com)."""

    def setUp(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.addCleanup(self.pw.stop)
        b = self.pw.chromium.launch()
        self.addCleanup(b.close)
        self.ctx = b.new_context()
        self.form = (Path(__file__).parent / "fixtures" / "ashby_like.html").as_uri()

    def tab(self, name=None):
        page = self.ctx.new_page()
        page.goto(self.form)
        if name:
            page.evaluate("(n) => { window.name = n; }", name)
        return page

    def test_another_applications_tab_is_never_taken(self):
        other = self.tab(browser.TAG_PREFIX + "6")
        page, fresh = browser.find_page(self.ctx, browser.TAG_PREFIX + "7", self.form)
        self.assertIsNot(page, other)
        self.assertTrue(fresh)
        self.assertEqual(other.evaluate("() => window.name"), browser.TAG_PREFIX + "6")

    def test_own_tab_then_an_untagged_tab_on_the_same_site(self):
        self.tab(browser.TAG_PREFIX + "6")
        mine = self.tab()   # opened by the candidate
        page, fresh = browser.find_page(self.ctx, browser.TAG_PREFIX + "7", self.form)
        self.assertIs(page, mine)
        self.assertFalse(fresh)
        self.assertIs(browser.find_page(self.ctx, browser.TAG_PREFIX + "7", self.form)[0], mine)


if __name__ == "__main__":
    unittest.main()
