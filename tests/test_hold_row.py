"""Tests for bin/hold-row: the sanctioned append of one holds row relaying Zane's call. Each test runs the real script
on a throwaway $ONIGIRI_ROOT. Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PKG = Path(__file__).resolve().parent.parent
TOOL = PKG / "bin" / "hold-row"
BASE = "# holds\nhold | 5675 | yes | never touch\nreview-only | f-trycua | no | teammate\n"
SHA = "a5d26a5acaf968e20baf99c5aaa9325965950dba"
ROW = f"preapprove | #7338 | no | {SHA} | deep; Zane 11 Nov 14:56 SGT (salvage of #7255, green at pin)"


class HoldRow(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.holds = self.root / "hold-prs.txt"
        self.holds.write_text(BASE)

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.root)])

    def run_tool(self, *args):
        env = {**os.environ, "ONIGIRI_ROOT": str(self.root)}
        return subprocess.run([sys.executable, str(TOOL), *args], env=env, capture_output=True, text=True)

    def backups(self):
        return sorted(p for p in self.root.iterdir() if p.name.startswith("hold-prs.txt.bak-"))

    def test_appends_exactly_one_row_after_a_backup(self):
        r = self.run_tool("add", ROW)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.holds.read_text(), BASE + ROW + "\n")
        [b] = self.backups()
        self.assertEqual(b.read_text(), BASE)

    def test_the_merge_job_reads_the_new_row(self):
        self.run_tool("add", ROW)
        spec = importlib.util.spec_from_file_location("mj", PKG / "hosts" / "vm" / "merge_job.py")
        mj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mj)
        self.assertEqual(mj.parse_holds(self.holds.read_text())["preapproved"], {7338: SHA})

    def test_a_rerun_adds_nothing(self):
        self.run_tool("add", ROW)
        r = self.run_tool("add", ROW)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already there", r.stdout)
        self.assertEqual(self.holds.read_text().count(ROW), 1)

    def test_a_file_without_a_final_newline_gets_one_before_the_row(self):
        self.holds.write_text(BASE.rstrip("\n"))
        self.run_tool("add", ROW)
        self.assertEqual(self.holds.read_text(), BASE + ROW + "\n")

    def test_a_row_the_parser_rejects_never_reaches_the_file(self):
        for row in ("hodl | 1 | no | Zane 11 Nov 14:56 SGT",                                       # unknown kind naming a PR
                    "preapprove | #7338 | no | a5d26a5 | deep; Zane 11 Nov 14:56 SGT",             # short sha
                    f"preapprove | #5178 | no | {SHA} | Zane 11 Nov 14:56 SGT"):                    # a second pin for #5178
            with self.subTest(row=row):
                self.holds.write_text(BASE + f"preapprove | #5178 | no | {'d' * 40} | Zane 5 Nov 10:00\n")
                before = self.holds.read_text()
                r = self.run_tool("add", row)
                self.assertEqual(r.returncode, 1)
                self.assertIn("parser rejects", r.stderr)
                self.assertEqual(self.holds.read_text(), before)
                self.assertEqual(self.backups(), [])

    def test_a_written_file_the_parser_rejects_is_restored_from_the_backup(self):
        # the parse after the rename: here the file on disk fails it although the text did not before
        loader = importlib.machinery.SourceFileLoader("hold_row", str(TOOL))
        hr = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(hr)
        real, calls = hr.mj.parse_holds, []

        def parse(text):
            calls.append(text)
            if len(calls) == 3:
                raise ValueError("holds file line 9: torn")
            return real(text)
        with patch.object(hr.mj, "parse_holds", parse), self.assertRaises(SystemExit) as e, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            hr.add(ROW, str(self.holds))
        self.assertEqual(e.exception.code, 1)
        self.assertIn("restored", err.getvalue())
        self.assertEqual(self.holds.read_text(), BASE)
        [b] = self.backups()
        self.assertEqual(b.read_text(), BASE)

    def test_a_row_must_name_zanes_call_and_its_time(self):
        for row in (f"preapprove | #7338 | no | {SHA} | deep",
                    f"preapprove | #7338 | no | {SHA} | deep; Zane said so",              # no time
                    f"preapprove | #7338 | no | {SHA} | deep; ops 11 Nov 14:56 SGT",      # not Zane
                    f"preapprove | #7338 | no | {SHA} | Zane 11 Nov 14:56 SGT | deep",    # the source is the last column
                    "hold | 1 | no | Zane 11 Nov 14:56\nhold | 2 | no | Zane 11 Nov 14:56",  # two rows
                    "hold | 1 | no | x\u2028preapprove | #1 | no | " + "e" * 40 + " | Zane 11 Nov 14:56",  # two rows to splitlines()
                    "hold | 1 | no | x\x1chold | 2 | no | Zane 11 Nov 14:56",
                    "",
                    "# Zane 11 Nov 14:56"):
            with self.subTest(row=row):
                r = self.run_tool("add", row)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(self.holds.read_text(), BASE)
                self.assertEqual(self.backups(), [])

    def test_iso_timestamps_count(self):
        r = self.run_tool("add", "hold | 7400 | no | wait for bench; Zane 2026-11-11 17:00 SGT")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_holds_file_that_is_already_unreadable_is_left_alone(self):
        self.holds.write_text(BASE + "hodl | 1 | no | typo\n")
        r = self.run_tool("add", "hold | 7400 | no | Zane 11 Nov 17:00 SGT")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.holds.read_text(), BASE + "hodl | 1 | no | typo\n")

    def test_a_missing_holds_file_is_not_created(self):
        self.holds.unlink()
        r = self.run_tool("add", ROW)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.holds.exists())

    def test_a_symlinked_holds_file_keeps_its_link(self):
        real = self.root / "real.txt"
        self.holds.rename(real)
        self.holds.symlink_to(real)
        self.assertEqual(self.run_tool("add", ROW).returncode, 0)
        self.assertTrue(self.holds.is_symlink())
        self.assertEqual(real.read_text(), BASE + ROW + "\n")

    OLD = f"preapprove | #5178 | no | {'d' * 40} | any risk; Zane 5 Nov 10:00 SGT"
    NEW = f"preapprove | #5178 | no | {SHA} | any risk; Zane 11 Nov 18:00 SGT (new head)"

    def test_replace_comments_out_the_stale_row_and_puts_the_new_one_in_its_place(self):
        self.holds.write_text(BASE + self.OLD + "\nhold | 7400 | no | x\n")
        r = self.run_tool("replace", self.OLD, self.NEW)
        self.assertEqual(r.returncode, 0, r.stderr)
        tomb = f"# retired any risk; Zane 11 Nov 18:00 SGT (new head): {self.OLD}"
        self.assertEqual(self.holds.read_text(), BASE + tomb + "\n" + self.NEW + "\nhold | 7400 | no | x\n")
        [b] = self.backups()
        self.assertEqual(b.read_text(), BASE + self.OLD + "\nhold | 7400 | no | x\n")
        spec = importlib.util.spec_from_file_location("mj", PKG / "hosts" / "vm" / "merge_job.py")
        mj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mj)
        self.assertEqual(mj.parse_holds(self.holds.read_text())["preapproved"], {5178: SHA})

    def test_retire_drops_the_row_from_what_the_merge_job_reads(self):
        self.holds.write_text(BASE + self.OLD + "\n")
        r = self.run_tool("retire", self.OLD, "Zane 11 Nov 18:00 SGT: stale pin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.holds.read_text(), BASE + f"# retired Zane 11 Nov 18:00 SGT: stale pin: {self.OLD}\n")
        spec = importlib.util.spec_from_file_location("mj", PKG / "hosts" / "vm" / "merge_job.py")
        mj = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mj)
        self.assertEqual(mj.parse_holds(self.holds.read_text())["preapproved"], {})
        self.assertEqual(len(self.backups()), 1)

    def test_a_second_retire_or_replace_changes_nothing(self):
        for args in (("retire", self.OLD, "Zane 11 Nov 18:00 SGT"), ("replace", self.OLD, self.NEW)):
            with self.subTest(args=args[0]):
                self.holds.write_text(BASE + self.OLD + "\n")
                for p in self.backups():
                    p.unlink()
                self.assertEqual(self.run_tool(*args).returncode, 0)
                after = self.holds.read_text()
                r = self.run_tool(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("already retired", r.stdout)
                self.assertEqual(self.holds.read_text(), after)
                self.assertEqual(len(self.backups()), 1)

    def test_retire_and_replace_refuse_without_touching_the_file(self):
        cases = (("retire", "preapprove | #5178 | no | nope | Zane 5 Nov 10:00", "Zane 11 Nov 18:00"),   # no such row
                 ("retire", "hold | 5675", "Zane 11 Nov 18:00"),                    # a prefix is not the row
                 ("retire", "hold | 5675 | yes | never touch", "ops 11 Nov 18:00"),       # not Zane
                 ("retire", "hold | 5675 | yes | never touch", "Zane said so"),          # no time
                 ("retire", "# holds", "Zane 11 Nov 18:00"),                            # a comment is not a row
                 ("retire", "hold | 5675 | yes | never touch", "Zane 11 Nov 18:00\nhold | 1 | no | x"),  # a second line
                 ("replace", self.OLD, f"preapprove | #5178 | no | {SHA} | deep"),             # new row has no source
                 ("replace", self.OLD, "preapprove | #5178 | no | a5d26a5 | Zane 11 Nov 18:00"),  # parser rejects it
                 ("replace", "hold | 5675 | yes | never touch", "hold | 7400 | no | x; Zane 11 Nov 18:00"),  # dup below
                 ("retire", "hold | 7400 | no | dup", "Zane 11 Nov 18:00"))             # two equal lines
        for args in cases:
            with self.subTest(args=args):
                self.holds.write_text(BASE + self.OLD + "\nhold | 7400 | no | dup\nhold | 7400 | no | dup\n"
                                      + "hold | 7400 | no | x; Zane 11 Nov 18:00\n")
                before = self.holds.read_text()
                r = self.run_tool(*args)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertEqual(self.holds.read_text(), before)
                self.assertEqual(self.backups(), [])

    def test_usage(self):
        for args in ((), ("add",), ("rm", ROW), ("add", ROW, "extra"), ("retire", ROW), ("replace", ROW),
                     ("replace", ROW, ROW, "extra")):
            with self.subTest(args=args):
                self.assertEqual(self.run_tool(*args).returncode, 2)
        self.assertEqual(self.holds.read_text(), BASE)


if __name__ == "__main__":
    unittest.main()
