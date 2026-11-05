"""Test folders go away after the test, and a folder that cannot go yet never fails it."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from .helpers import remove_dir, temp_dir


class TestTempDir(unittest.TestCase):
    def scratch_case(self):
        class Scratch(unittest.TestCase):
            def runTest(self):
                pass
        return Scratch

    def test_deleted_after_the_test(self):
        case = self.scratch_case()()
        folder = temp_dir(case)
        (folder / "sub").mkdir()
        (folder / "sub" / "file.txt").write_text("x", encoding="utf-8")
        self.assertTrue(folder.is_dir())
        case.doCleanups()
        self.assertFalse(folder.exists())

    def test_deleted_after_the_class_when_the_class_owns_it(self):
        scratch = self.scratch_case()
        folder = temp_dir(scratch)
        self.assertTrue(folder.is_dir())
        scratch.doClassCleanups()
        self.assertFalse(folder.exists())

    def test_runs_after_the_cleanup_that_closes_what_uses_it(self):
        case = self.scratch_case()()
        folder = temp_dir(case)
        seen = []
        case.addCleanup(lambda: seen.append(folder.exists()))   # a browser or database closing
        case.doCleanups()
        self.assertEqual(seen, [True])
        self.assertFalse(folder.exists())


class TestRemoveDir(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def test_a_folder_held_for_a_moment_is_tried_again(self):
        real, calls = shutil.rmtree, []

        def held_twice(path, ignore_errors=False):
            calls.append(path)
            if len(calls) == 3:
                real(path, ignore_errors=ignore_errors)

        with mock.patch("tests.helpers.shutil.rmtree", held_twice), mock.patch("tests.helpers.time.sleep") as sleep:
            remove_dir(self.folder)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertFalse(self.folder.exists())

    def test_a_folder_still_held_is_left_without_failing(self):
        with mock.patch("tests.helpers.shutil.rmtree") as rmtree, mock.patch("tests.helpers.time.sleep"):
            remove_dir(self.folder, tries=3)
        self.assertEqual(rmtree.call_count, 3)
        self.assertTrue(self.folder.is_dir())

    def test_an_open_file_in_the_folder_does_not_fail(self):
        # What a browser that has not let go looks like on Windows; elsewhere the folder simply goes.
        with open(self.folder / "held.txt", "w", encoding="utf-8"):
            remove_dir(self.folder, tries=2, wait=0)
        remove_dir(self.folder)
        self.assertFalse(self.folder.exists())

    def test_a_folder_already_gone_is_fine(self):
        shutil.rmtree(self.folder)
        with mock.patch("tests.helpers.time.sleep") as sleep:
            remove_dir(self.folder)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
