import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "onigiri-mode" / "scripts" / "release-compare.py"


class ReleaseCompare(unittest.TestCase):
    def rows(self, d, run, passed, n=10):
        p = Path(d) / "runs" / run
        p.mkdir(parents=True)
        task = run.rsplit("-", 2)[-2] + "-" + run.rsplit("-", 1)[-1]
        (p / "results.jsonl").write_text("".join(json.dumps(
            {"trial_id": f"{task}-r{i + 1}", "task": task, "run_index": i, "passed": passed, "final": True,
             "tokens": {"input": 100}, "turns": 10, "wall_s": 50}) + "\n" for i in range(n)))

    def verdict(self, d):
        p = subprocess.run([sys.executable, str(SCRIPT), "0.49.4", "0.49.11", d], capture_output=True, text=True, check=True)
        return json.loads(p.stdout)

    def test_no_rows_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.verdict(d)["verdict"], "inconclusive")

    def test_a_regression_with_a_platform_missing_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as d:
            self.rows(d, "rb-l-0.49.4-MB-10", True)
            self.rows(d, "rb-l-0.49.11-MB-10", False)
            v = self.verdict(d)
            self.assertEqual((v["verdict"], v["missing_platforms"]), ("inconclusive", ["macos"]))
            self.assertTrue(v["regression"])

    def test_a_regression_with_an_unpaired_row_is_inconclusive(self):
        with tempfile.TemporaryDirectory() as d:
            for plat in ("rb-l-", "rb-m-fg-"):
                self.rows(d, f"{plat}0.49.4-MB-10", True)
                self.rows(d, f"{plat}0.49.11-MB-10", False, n=9)
            v = self.verdict(d)
            self.assertEqual(v["verdict"], "inconclusive")
            self.assertEqual(len(v["unpaired"]), 2)

    def test_full_pairs_on_both_platforms_give_regression_or_none(self):
        for new_passes, want in ((False, "regression"), (True, "no regression")):
            with self.subTest(want=want), tempfile.TemporaryDirectory() as d:
                for plat in ("rb-l-", "rb-m-fg-"):
                    self.rows(d, f"{plat}0.49.4-MB-10", True)
                    self.rows(d, f"{plat}0.49.11-MB-10", new_passes)
                self.assertEqual(self.verdict(d)["verdict"], want)


if __name__ == "__main__":
    unittest.main()
