import os
import subprocess
import sys
import unittest
from pathlib import Path

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

QUEUE = Path(__file__).resolve().parent.parent / "queue.py"

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
