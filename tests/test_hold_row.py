"""Tests for bin/hold-row: the sanctioned append of one holds row relaying Zane's call. Each test runs the real script
on a throwaway $ONIGIRI_ROOT. Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_a_row_the_parser_rejects_is_restored_from_the_backup(self):
        for row in ("hodl | 1 | no | Zane 11 Nov 14:56 SGT",                                       # unknown kind naming a PR
                    "preapprove | #7338 | no | a5d26a5 | deep; Zane 11 Nov 14:56 SGT",             # short sha
                    f"preapprove | #5178 | no | {SHA} | Zane 11 Nov 14:56 SGT"):                    # a second pin for #5178
            with self.subTest(row=row):
                self.holds.write_text(BASE + f"preapprove | #5178 | no | {'d' * 40} | Zane 5 Nov 10:00\n")
                before = self.holds.read_text()
                r = self.run_tool("add", row)
                self.assertEqual(r.returncode, 1)
                self.assertIn("restored", r.stderr)
                self.assertEqual(self.holds.read_text(), before)

    def test_a_row_must_name_zanes_call_and_its_time(self):
        for row in (f"preapprove | #7338 | no | {SHA} | deep",
                    f"preapprove | #7338 | no | {SHA} | deep; Zane said so",              # no time
                    f"preapprove | #7338 | no | {SHA} | deep; ops 11 Nov 14:56 SGT",      # not Zane
                    f"preapprove | #7338 | no | {SHA} | Zane 11 Nov 14:56 SGT | deep",    # the source is the last column
                    "hold | 1 | no | Zane 11 Nov 14:56\nhold | 2 | no | Zane 11 Nov 14:56",  # two rows
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

    def test_usage(self):
        for args in ((), ("add",), ("rm", ROW), ("add", ROW, "extra")):
            with self.subTest(args=args):
                self.assertEqual(self.run_tool(*args).returncode, 2)
        self.assertEqual(self.holds.read_text(), BASE)


if __name__ == "__main__":
    unittest.main()
