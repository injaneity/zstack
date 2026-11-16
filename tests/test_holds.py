import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

VM = Path(__file__).resolve().parent.parent / "services"
H, H2 = "a" * 40, "b" * 40


def load(name):
    spec = importlib.util.spec_from_file_location(name, VM / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mj = load("holds")

HOLDS = """# comment
hold           | 5675          | yes | open PR, never touch
hands-off-user | someone       | no  | hands-off login
skip-author    | app/app-agents | no | bot
review-only    | f-teammate      | no  | teammate: first review only
hands-off-path | impl_.rs      | yes | mode=split-undone | only if it undoes #2886
hands-off-path | never.rs      | no  | Robin reviews every change to it
exception      | impl_.rs      | yes | #5686 follow-up | the MSAA path | Robin 26 Oct
"""
FIX = mj.parse_holds(HOLDS)


class Holds(unittest.TestCase):
    def test_rows_parse(self):
        self.assertEqual(FIX, {"hold": {5675}, "paths": ["never.rs"], "preapproved": {}})
        self.assertEqual(mj.parse_holds("hands-off-path | x.rs | no | mode=never | b\n")["paths"], ["x.rs"])
        h = mj.parse_holds(f"preapprove | #5650 | no | {H} | Robin\npreapprove | 5690 | no | {H2} | x\n")
        self.assertEqual(h["preapproved"], {5650: H, 5690: H2})

    def test_a_row_that_names_no_pr_is_not_read_and_cannot_hold_every_pr(self):
        for row in ("review-only | f-teammate | no | teammate", "skip-author | app/app-agents | no | bot", "review-only | f-teammate | maybe",
                    "exception | impl_.rs | yes | #5686 follow-up"):
            self.assertEqual(mj.parse_holds(row), {"hold": set(), "paths": [], "preapproved": {}}, row)

    def test_malformed_rows_raise(self):
        for bad in ("hold 5675", "hodl | 5675 | no | x", "hold | | no | x", "hold | abc | no | x", "hold | 5675",
                    "no-move | 5675 | no | a kind this job does not know, on a PR", "review-only | | no | x", "review-only",
                    "hands-off-path | x.rs | no | mode=sometimes | x", "hands-off-path | x.rs", "hands-off-path | | no | x",
                    "preapprove | #5650 | no | abc123 | x", f"preapprove | #5650 | no | {H}", f"preapprove | x | no | {H} | x",
                    f"preapprove | #1 | no | {H} | x\npreapprove | #1 | no | {H2} | y"):
            with self.assertRaises(ValueError, msg=bad):
                mj.parse_holds(bad)

    def test_live_holds_file_parses(self):
        if not os.path.exists(mj.HOLDS_FILE):
            self.skipTest("no live holds file on this machine")
        h = mj.parse_holds(mj.read(mj.HOLDS_FILE))
        self.assertIn(5675, h["hold"])


class CronEnv(unittest.TestCase):
    def test_gh_is_found_under_crons_bare_path(self):
        r = subprocess.run([sys.executable, "-I", "-c", "import importlib.util as u, sys; s = u.spec_from_file_location('m', sys.argv[1]); "
                            "m = u.module_from_spec(s); s.loader.exec_module(m); print(m.ENV['PATH'])", str(VM / "holds.py")],
                           env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}, capture_output=True, text=True, check=True)
        self.assertTrue({"/usr/local/bin", "/usr/bin", "/bin"} <= set(r.stdout.strip().split(os.pathsep)), r.stdout)

    def test_the_callers_gh_config_dir_wins_and_home_is_the_default(self):
        code = ("import importlib.util as u, sys; s = u.spec_from_file_location('m', sys.argv[1]); "
                "m = u.module_from_spec(s); s.loader.exec_module(m); print(m.ENV['GH_CONFIG_DIR'])")
        for extra, want in (({"GH_CONFIG_DIR": "/x/gh"}, "/x/gh"), ({}, "/h/.config/gh")):
            r = subprocess.run([sys.executable, "-I", "-c", code, str(VM / "holds.py")],
                               env={"PATH": "/usr/bin:/bin", "HOME": "/h", **extra}, capture_output=True, text=True, check=True)
            self.assertEqual(r.stdout.strip(), want)


if __name__ == "__main__":
    unittest.main()
