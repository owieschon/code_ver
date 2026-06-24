#!/usr/bin/env python3
"""Answer-key tests for the grading-seam fixture task t01.

Mirrors the real battery's key format: a standalone unittest suite that imports
the *target* repo by path and asserts the behaviour the task is graded on. The
seeded (defective) terminal tree fails these; the reference (clean) tree passes.

    python3 key_tests.py /path/to/terminal_repo
    KEYTEST_TARGET=/path/to/terminal_repo python3 key_tests.py
"""

import os
import sys
import unittest


def _target_path():
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        return sys.argv.pop(1)
    target = os.environ.get("KEYTEST_TARGET")
    if not target:
        sys.exit("usage: key_tests.py <terminal repo path> (or set KEYTEST_TARGET)")
    return target


sys.path.insert(0, os.path.abspath(_target_path()))

from invoicing.totals import line_total  # noqa: E402


class LineTotalKey(unittest.TestCase):
    def test_line_total_basic(self):
        self.assertEqual(line_total(3, 100), 300)

    def test_line_total_zero_quantity(self):
        self.assertEqual(line_total(0, 100), 0)

    def test_line_total_single_unit(self):
        self.assertEqual(line_total(1, 50), 50)


if __name__ == "__main__":
    unittest.main()
