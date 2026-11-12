"""Tests for bin/policy, the one reader of config/policy.toml, and for the readers that refuse a policy it rejects.
Each bad file is the shipped one with one edit, run from a throwaway copy of the package's bin/ and config/.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
try:
    import tomllib
except ModuleNotFoundError:   # python3 3.9 and 3.10, as bin/policy
    sys.path.insert(0, str(PKG / "vendor"))
    import tomli as tomllib
SHIPPED = (PKG / "config" / "policy.toml").read_text()


class Policy(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.pkg = Path(td.name) / "pkg"
        for f in ("bin/policy", "bin/model-for", "bin/pi-run", "bin/hourly", "bin/check-host", "config/policy.toml"):
            (self.pkg / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PKG / f, self.pkg / f)
        shutil.copytree(PKG / "vendor", self.pkg / "vendor")   # bin/policy's tomllib before python3 3.11
        self.env = {**os.environ, "ONIGIRI_HOST": "", "ONIGIRI_ROOT": str(Path(td.name) / "root"),
                    "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"}

    def write(self, text):
        (self.pkg / "config" / "policy.toml").write_text(text)

    def run_(self, *args, prog="bin/policy"):
        return subprocess.run([str(self.pkg / prog), *args], capture_output=True, text=True, env=self.env, timeout=60)

    def test_the_shipped_file_is_valid_and_get_reads_it(self):
        self.assertEqual(self.run_("check").returncode, 0)
        self.assertEqual(self.run_("get", "slots.total").stdout, "7\n")
        self.assertEqual(self.run_("get", "slots.auto").stdout, "5\n")
        self.assertEqual(self.run_("get", "ci.fix_attempts").stdout, "3\n")
        self.assertEqual(json.loads(self.run_("get", "models.verifier").stdout), {"provider": "openai-codex", "model": "gpt-6.1-terra"})
        self.assertEqual(len(json.loads(self.run_("get", "models.designer").stdout)), 2)
        self.assertEqual(json.loads(self.run_("dump").stdout), tomllib.loads(SHIPPED))

    def test_a_missing_key_is_named(self):
        r = self.run_("get", "slots.nope")
        self.assertEqual((r.returncode, r.stderr), (1, "policy: no key slots.nope\n"))

    def test_every_bad_edit_is_refused_with_its_reason(self):
        """One edit each: what it breaks, and the reason bin/policy must give. None is ever read as a default."""
        cases = [
            ("total = 7", "total = 0", "slots.total must be a positive integer, not 0"),
            ("total = 7", 'total = "7"', "slots.total must be a positive integer, not '7'"),
            ("total = 7", "total = true", "slots.total must be a positive integer, not True"),
            ("total = 7\n", "", "missing slots.total"),
            ("fix_attempts = 3", "fix_attempts = 3\nfix_attemps = 4", "unknown key ci.fix_attemps"),
            ("schema = 1", "schema = 2", "schema must be 1, not 2"),
            ("schema = 1", "schema = true", "schema must be 1, not True"),
            ("verifier = {", "verifer = {", "unknown key models.verifer"),
            ('research = { provider = "claude-code", model = "claude-opus-5-5" }\n', "", "missing models.research"),
            ("schema = 1", "schema = 1\nslot = 3", "unknown key slot"),
            ("[ci]\nfix_attempts = 3\n", "", "[ci] is missing or empty"),
            ('verifier = { provider = "openai-codex", model = "gpt-6.1-terra" }', 'verifier = { provider = "openai-codex" }',
             "models.verifier must be a {provider, model} table or a non-empty list of them"),
            ('verifier = { provider = "openai-codex", model = "gpt-6.1-terra" }', "verifier = []", "models.verifier must be"),
            ('verifier = { provider = "openai-codex", model = "gpt-6.1-terra" }',
             'verifier = { provider = "openai-codex", model = "gpt-6.1-terra", fallback = true }', "models.verifier must be"),
            ("[slots]", "[slots", "is not TOML"),
            ('me = "injaneity"', 'me = ""', "people.me must be a GitHub login"),
            ('held_words = ["teleport", "keyvault", "pairing"]', "held_words = []", "patent.held_words must be a non-empty list"),
            ('held_words = ["teleport", "keyvault", "pairing"]', 'held_words = ["tele.port"]', "patent.held_words must be"),
            ('"Cargo.lock", "package-lock.json"', '"a/Cargo.lock", "package-lock.json"', "patent.skip_lockfiles must be"),
            ('private_repo = "trycua/cua-staging"', 'private_repo = "trycua/cua"', "patent.private_repo must differ"),
            ('private_repo = "trycua/cua-staging"', 'private_repo = "TryCua/CUA"', "patent.private_repo must differ"),
            ('public_repo = "trycua/cua"\n', "", "missing patent.public_repo"),
            ('\nrepo = "trycua/cua"', '\nrepo = "trycua"', "merge.repo must be owner/name"),
            ('never_rerun = ["cla"]', 'never_rerun = ["cla", "cla"]', "merge.never_rerun must be a list of distinct check names"),
            ('tiers = ["light", "standard", "deep", "human"]', "tiers = []", "merge.tiers must be a non-empty list"),
            ('tiers = ["light", "standard", "deep", "human"]', 'tiers = ["light", "standard", "de.p", "deep", "human"]',
             "merge.tiers must be a non-empty list of distinct lowercase words"),
            ('preapprove_tiers = ["deep", "human"]', 'preapprove_tiers = ["deep", "hman"]', "merge.preapprove_tiers names hman"),
            ('gate_check = "ci-gate"', "", "missing merge.gate_check"),
            ("interval_s = 5400", "interval_s = 0", "pickup.interval_s must be a positive integer, not 0"),
            ("deadline_s = 1200", "deadline_s = 5400", "pickup.deadline_s must be under pickup.interval_s"),
            ('label_hosts = { Linux = "vm", macOS = "mini" }', 'label_hosts = { Linux = "" }', "pickup.label_hosts must be a table"),
        ]
        for old, new, why in cases:
            with self.subTest(new=new or f"drop {old.strip()}"):
                self.assertEqual(SHIPPED.count(old), 1, old)
                self.write(SHIPPED.replace(old, new))
                r = self.run_("check")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(why, r.stderr)

    def test_a_missing_file_is_refused(self):
        (self.pkg / "config" / "policy.toml").unlink()
        r = self.run_("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("cannot read", r.stderr)

    def test_readers_refuse_a_policy_bin_policy_rejects(self):
        """model-for (so every launch), pi-run ci and the hourly pass stop with bin/policy's reason instead of a default."""
        self.write(SHIPPED.replace("fix_attempts = 3", "fix_attempts = -1"))
        for prog, args in (("bin/model-for", ["coding"]), ("bin/hourly", ["log", "h", "a", "i", "r"])):
            with self.subTest(prog=prog):
                r = self.run_(*args, prog=prog)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("ci.fix_attempts must be a positive integer, not -1", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
        run = Path(self.env["ONIGIRI_ROOT"]) / "review" / "x"
        run.mkdir(parents=True)
        (run / "run.json").write_text(json.dumps({"state": "collected", "attempt": 1, "pid": None, "rc": 0}))
        r = self.run_("ci", "x", "1", "a" * 40, "red", prog="bin/pi-run")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("ci.fix_attempts must be a positive integer, not -1", r.stderr)
        self.assertEqual(json.loads((run / "run.json").read_text())["state"], "collected")

    def test_the_merge_job_runs_no_gate_without_its_policy(self):
        """A merge gate whose rules cannot be read merges nothing: the job exits before any gh call, naming why."""
        (self.pkg / "hosts" / "vm").mkdir(parents=True)
        for f in ("merge_job.py", "wake.py"):
            shutil.copy2(PKG / "hosts" / "vm" / f, self.pkg / "hosts" / "vm" / f)
        (self.pkg / "bin" / "gh").write_text("#!/bin/sh\necho called >> \"$0.log\"\n")
        (self.pkg / "bin" / "gh").chmod(0o755)
        self.write(SHIPPED.replace('tiers = ["light", "standard", "deep", "human"]', 'tiers = ["light", "standard"]'))
        env = {**self.env, "PATH": f"{self.pkg / 'bin'}:{self.env['PATH']}", "ZSTACK_ENSURED": "1"}
        r = subprocess.run([sys.executable, str(self.pkg / "hosts/vm/merge_job.py"), "--dry-run"], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("merge_job: merge.preapprove_tiers names deep, human, which merge.tiers lacks", r.stderr)
        self.assertFalse((self.pkg / "bin" / "gh.log").exists())

    def test_hold_row_writes_nothing_without_the_merge_gates_policy(self):
        """bin/hold-row checks every write with the merge job's parser, so a policy that stops the job stops the write."""
        (self.pkg / "hosts" / "vm").mkdir(parents=True, exist_ok=True)
        for f in ("merge_job.py", "wake.py"):
            shutil.copy2(PKG / "hosts" / "vm" / f, self.pkg / "hosts" / "vm" / f)
        shutil.copy2(PKG / "bin" / "hold-row", self.pkg / "bin" / "hold-row")
        root = Path(self.env["ONIGIRI_ROOT"])
        root.mkdir(parents=True)
        (root / "hold-prs.txt").write_text("hold | 1 | no | x\n")
        self.write(SHIPPED.replace('gate_check = "ci-gate"', ""))
        r = self.run_("add", "hold | 2 | no | Zane 12 Nov 15:00 SGT", prog="bin/hold-row")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("merge_job: missing merge.gate_check", r.stderr)
        self.assertEqual((root / "hold-prs.txt").read_text(), "hold | 1 | no | x\n")
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["hold-prs.txt"])   # no backup or temp file either

    def test_a_push_is_refused_without_the_patent_policy(self):
        """cua's pre-push hook with no readable [patent] refuses every push, so a held change can never go out unread."""
        (self.pkg / "hosts" / "vm").mkdir(parents=True, exist_ok=True)
        for f in ("patent_push.py", "wake.py", "pre-push"):
            shutil.copy2(PKG / "hosts" / "vm" / f, self.pkg / "hosts" / "vm" / f)
        self.write(SHIPPED.replace('held_words = ["teleport", "keyvault", "pairing"]', 'held_words = ["Teleport"]'))
        r = subprocess.run([str(self.pkg / "hosts/vm/pre-push"), "origin", "https://github.com/trycua/cua"],
                           input=f"refs/heads/x {'a' * 40} refs/heads/x {'0' * 40}\n", capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("patent hold: config/policy.toml is unreadable (patent.held_words must be", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_push_under_a_python_without_tomllib_still_reads_the_patent_policy(self):
        """bin/policy falls back to vendor/tomli, so the hook still holds a pairing push on the mini's /usr/bin/python3."""
        (self.pkg / "hosts" / "vm").mkdir(parents=True, exist_ok=True)
        for f in ("patent_push.py", "wake.py"):
            shutil.copy2(PKG / "hosts" / "vm" / f, self.pkg / "hosts" / "vm" / f)
        (self.pkg / "notoml").mkdir()
        (self.pkg / "notoml" / "tomllib.py").write_text("raise ModuleNotFoundError(\"No module named 'tomllib'\")\n")
        code = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('pp', sys.argv[1]); "
                "pp = importlib.util.module_from_spec(s); s.loader.exec_module(pp); print(pp.held_hits(['src/pairing.rs'], ''))")
        r = subprocess.run([sys.executable, "-c", code, str(self.pkg / "hosts/vm/patent_push.py")], capture_output=True, text=True,
                           env={**self.env, "PYTHONPATH": str(self.pkg / "notoml")}, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, "['src/pairing.rs']\n"), r.stderr)

    def test_check_host_reports_a_bad_policy_as_one_fail_line_and_goes_on(self):
        self.write(SHIPPED.replace("schema = 1", "schema = true"))
        r = self.run_("--offline", prog="bin/check-host")
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("FAIL  policy: schema must be 1, not True", r.stdout)
        self.assertIn("gh: ", r.stdout)   # the checks after it still ran

    def test_the_old_json_configs_are_gone(self):
        """One source of truth: the three JSON files the policy replaced are not shipped next to it."""
        for f in ("models.json", "slots.json", "ci.json"):
            self.assertFalse((PKG / "config" / f).exists(), f)


if __name__ == "__main__":
    unittest.main()
