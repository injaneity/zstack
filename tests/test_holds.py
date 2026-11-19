import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

QUEUE = Path(__file__).resolve().parent.parent / "queue.py"
spec = importlib.util.spec_from_file_location("queue_holds", QUEUE)
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)

HOLDS = """# comment
hold           | 5675          | yes | open PR, never touch
hands-off-user | someone       | no  | hands-off login
skip-author    | app/App-agents | no | bot
review-only    | F-teammate      | no  | teammate: first review only
exception      | impl_.rs      | yes | #5686 follow-up | the MSAA path | Robin 26 Oct
"""
NONE = {"hold": set(), "skip": set(), "review_only": set()}


class Holds(unittest.TestCase):
    def test_rows_parse(self):
        self.assertEqual(q.parse_holds(HOLDS), {"hold": {5675}, "skip": {"app/app-agents"}, "review_only": {"f-teammate"}})
        self.assertEqual(q.parse_holds("hold | #12 | all | x\n")["hold"], {12})

    def test_a_row_of_another_kind_that_names_no_pr_is_not_read_and_cannot_hold_every_pr(self):
        for row in ("review-only | f-teammate | maybe", "exception | impl_.rs | yes | #5686 follow-up", "", "# hold | 1 | all | x"):
            self.assertEqual({**q.parse_holds(row), "review_only": set()}, NONE, row)

    def test_malformed_rows_raise(self):
        for bad in ("hold 5675", "hodl | 5675 | no | x", "hold | | no | x", "hold | abc | no | x", "hold | 5675",
                    "no-move | 5675 | no | a kind this program does not know, on a PR", "review-only | | no | x", "review-only",
                    "preapprove | #5650 | no | " + "a" * 40 + " | a kind that is gone"):
            with self.assertRaises(ValueError, msg=bad):
                q.parse_holds(bad)

    def test_a_missing_or_malformed_file_is_unread_with_the_reason(self):
        pol = {"host": {"holds": "/nowhere/hold-prs.txt"}}
        self.assertEqual(q.holds(pol), {**NONE, "read": False, "error": "cannot read /nowhere/hold-prs.txt"})


class CronEnv(unittest.TestCase):
    CODE = ("import importlib.util as u, sys; s = u.spec_from_file_location('m', sys.argv[1]); "
            "m = u.module_from_spec(s); s.loader.exec_module(m); print(m.ENV[sys.argv[2]])")

    def test_gh_is_found_under_crons_bare_path(self):
        r = subprocess.run([sys.executable, "-I", "-c", self.CODE, str(QUEUE), "PATH"],
                           env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}, capture_output=True, text=True, check=True)
        self.assertTrue({"/usr/local/bin", "/usr/bin", "/bin"} <= set(r.stdout.strip().split(os.pathsep)), r.stdout)

    def test_the_callers_gh_config_dir_wins_and_home_is_the_default(self):
        for extra, want in (({"GH_CONFIG_DIR": "/x/gh"}, "/x/gh"), ({}, "/h/.config/gh")):
            r = subprocess.run([sys.executable, "-I", "-c", self.CODE, str(QUEUE), "GH_CONFIG_DIR"],
                               env={"PATH": "/usr/bin:/bin", "HOME": "/h", **extra}, capture_output=True, text=True, check=True)
            self.assertEqual(r.stdout.strip(), want)


if __name__ == "__main__":
    unittest.main()
