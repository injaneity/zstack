import re
import subprocess
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
REFS = (
    re.compile(r"(?<![\w./~-])config/([\w-]+\.[a-z]+)\b"),
    re.compile(r"""["']config["']\s*[,/]\s*["']([\w-]+\.[a-z]+)["']"""),
)


def refs(text):
    found = {}
    for n, line in enumerate(text.splitlines(), 1):
        for rx in REFS:
            for m in rx.finditer(line):
                found.setdefault(m.group(1), []).append(n)
    return found


class ConfigRefs(unittest.TestCase):
    def test_every_config_file_named_exists(self):
        files = subprocess.run(["git", "-C", str(PKG), "ls-files", "-z"], capture_output=True, text=True,
                               check=True).stdout.split("\0")
        missing = []
        for f in files:
            if not f or f.startswith("vendor/") or PKG / f == Path(__file__).resolve():
                continue
            try:
                text = (PKG / f).read_text()
            except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
                continue
            for name, lines in refs(text).items():
                if not (PKG / "config" / name).is_file():
                    missing += [f"{f}:{n} names config/{name}, which does not exist" for n in lines]
        self.assertEqual(missing, [])

    def test_the_forms_the_pre_72_subagent_tool_used_are_caught(self):
        old = ('const MODELS_CONFIG = path.join(PACKAGE_DIR, "config", "models.json");\n'
               "// a child's provider and model come from config/models.json by agent name\n"
               'cfg = PKG / "config" / "slots.json"\n')
        self.assertEqual(refs(old), {"models.json": [1, 2], "slots.json": [3]})

    def test_home_and_other_repos_config_dirs_are_not_package_config(self):
        self.assertEqual(refs("~/.config/onigiri/host and .cursor/config/x.json and $HOME/.config/gh/hosts.yml"), {})


if __name__ == "__main__":
    unittest.main()
