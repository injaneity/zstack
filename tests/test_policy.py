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
except ModuleNotFoundError:
    sys.path.insert(0, str(PKG / "vendor"))
    import tomli as tomllib
FILES = ("config/machines/default.toml", "config/repos/acme/app/policy.toml")
SHIPPED = {f: (PKG / f).read_text() for f in FILES}


class Policy(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.pkg = Path(td.name) / "pkg"
        for f in ("bin/policy", *FILES, "config/rules.toml", "config/state.toml"):
            (self.pkg / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PKG / f, self.pkg / f)
        shutil.copytree(PKG / "vendor", self.pkg / "vendor")
        self.env = {**os.environ, "ONIGIRI_HOST": "", "ONIGIRI_ROOT": str(Path(td.name) / "root"),
                    "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"}

    def edit(self, old, new):
        self.assertEqual(sum(t.count(old) for t in SHIPPED.values()), 1, old)
        for f, t in SHIPPED.items():
            (self.pkg / f).write_text(t.replace(old, new))

    def run_(self, *args, prog="bin/policy"):
        return subprocess.run([str(self.pkg / prog), *args], capture_output=True, text=True, env=self.env, timeout=60)

    def test_the_shipped_file_is_valid_and_get_reads_it(self):
        self.assertEqual(self.run_("check").returncode, 0)
        self.assertEqual(self.run_("get", "ci.fix_attempts").stdout, "3\n")
        self.assertEqual(self.run_("get", "ci.fix_attempts").stdout, "3\n")
        self.assertEqual(json.loads(self.run_("get", "models.verifier").stdout), {"provider": "openai-codex", "model": "gpt-6.1-terra"})
        self.assertEqual(len(json.loads(self.run_("get", "models.designer").stdout)), 2)
        dump = json.loads(self.run_("dump").stdout)
        machine, project = (tomllib.loads(SHIPPED[f]) for f in FILES)
        self.assertEqual({k: dump[k] for k in ("models", "run", "wake")}, {k: machine[k] for k in ("models", "run", "wake")})
        self.assertEqual({k: dump[k] for k in ("ci", "people", "patent", "issues", "review")},
                         {k: project[k] for k in ("ci", "people", "patent", "issues", "review")})
        self.assertEqual(dump["merge"], {"repo": "acme/app", **project["merge"]})
        self.assertEqual(dump["repos"], ["acme/app"])
        self.assertEqual(dump["host"], {"clone": "~/work/app", "root": "~/zstack/acme/app", "holds": "~/zstack/acme/app/hold-prs.txt"})

    def second_repo(self):
        other = self.pkg / "config" / "repos" / "acme" / "tool"
        other.mkdir(parents=True)
        (other / "policy.toml").write_text(SHIPPED[FILES[1]].replace('gate_check = "ci-gate"', 'gate_check = "tool-gate"'))
        (self.pkg / FILES[0]).write_text(SHIPPED[FILES[0]] + '\n[repos."acme/tool"]\nclone = "~/work/tool"\nroot = "~/zstack/acme/tool"\n')
        return other

    def test_a_machine_with_two_repos_picks_by_name_then_by_the_clone_it_runs_in(self):
        other = self.second_repo()
        r = self.run_("get", "merge.repo")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr, "policy: this machine works on acme/app, acme/tool; set ZSTACK_REPO or run from one of their clones\n")
        self.env["ZSTACK_REPO"] = "Acme/Tool"
        self.assertEqual(self.run_("get", "merge.gate_check").stdout, "tool-gate\n")
        self.assertEqual(self.run_("get", "host.clone").stdout, "~/work/tool\n")
        self.assertEqual(json.loads(self.run_("get", "repos").stdout), ["acme/app", "acme/tool"])
        self.env["ZSTACK_REPO"] = "acme/nope"
        self.assertIn("not acme/nope", self.run_("get", "merge.repo").stderr)
        del self.env["ZSTACK_REPO"]
        clone = self.pkg.parent / "clone"
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", "git@github.com:acme/tool.git"], check=True)
        r = subprocess.run([str(self.pkg / "bin/policy"), "get", "merge.repo"], capture_output=True, text=True, env=self.env, cwd=clone)
        self.assertEqual(r.stdout, "acme/tool\n")
        self.assertEqual(self.run_("check").returncode, 0)
        (other / "policy.toml").write_text("schema = 1\n")
        self.assertEqual(self.run_("check").returncode, 1)

    def test_the_machine_file_is_this_hosts_or_the_one_named_else_the_default(self):
        (self.pkg / "config" / "machines" / "box.toml").write_text(SHIPPED[FILES[0]].replace("idle_min = 150", "idle_min = 7"))
        self.assertEqual(self.run_("get", "run.idle_min").stdout, "150\n")
        self.env["ZSTACK_MACHINE"] = "box"
        self.assertEqual(self.run_("get", "run.idle_min").stdout, "7\n")
        self.assertIn("on box", self.run_("check").stdout)

    def test_a_repos_own_rules_are_added_to_the_stacks(self):
        (self.pkg / "config" / "repos" / "acme" / "app" / "rules.toml").write_text(
            'schema = 1\n[[section]]\ntitle = "Project"\n[[section.rule]]\nid = "project-gate"\ntext = "Wait for {merge.gate_check}."\n')
        out = self.run_("rules").stdout
        self.assertTrue(out.rstrip().endswith("### Project\n\n- Wait for ci-gate."), out[-200:])
        self.assertIn("### Work", out)
        self.assertEqual(self.run_("rules", "project-gate").stdout, "- Wait for ci-gate.\n")

    def test_a_missing_key_is_named(self):
        r = self.run_("get", "ci.nope")
        self.assertEqual((r.returncode, r.stderr), (1, "policy: no key ci.nope\n"))

    def test_every_bad_edit_is_refused_with_its_reason(self):
        cases = [
            ("floor_gb = 20", "floor_gb = 0", "merge.floor_gb must be a positive integer, not 0"),
            ("floor_gb = 20", 'floor_gb = "20"', "merge.floor_gb must be a positive integer, not '20'"),
            ("floor_gb = 20", "floor_gb = true", "merge.floor_gb must be a positive integer, not True"),
            ("floor_gb = 20\n", "", "missing merge.floor_gb"),
            ("floor_gb = 20", "floor_gb = 20\ntotal = 7", "unknown key merge.total"),
            ("fix_attempts = 3", "fix_attempts = 3\nfix_attemps = 4", "unknown key ci.fix_attemps"),
            ("schema = 1\n\n# How many", "schema = 2\n\n# How many", "schema must be 1, not 2"),
            ("schema = 1\n\n# The provider", "schema = true\n\n# The provider", "schema must be 1, not True"),
            ("verifier = {", "verifer = {", "unknown key models.verifer"),
            ('orchestrator = { provider = "claude-code", model = "claude-opus-5-5" }\n', "", "missing models.orchestrator"),
            ("schema = 1\n\n# How many", "schema = 1\nslot = 3\n\n# How many", "unknown key slot"),
            ("[ci]\nfix_attempts = 3\n", "", "[ci] is missing or empty"),
            ('verifier = { provider = "openai-codex", model = "gpt-6.1-terra" }', 'verifier = { provider = "openai-codex" }',
             "models.verifier must be a {provider, model} table or a non-empty list of them"),
            ('verifier = { provider = "openai-codex", model = "gpt-6.1-terra" }', "verifier = []", "models.verifier must be"),
            ('verifier = { provider = "openai-codex", model = "gpt-6.1-terra" }',
             'verifier = { provider = "openai-codex", model = "gpt-6.1-terra", fallback = true }', "models.verifier must be"),
            ("[ci]", "[ci", "is not TOML"),
            ('me = "octo"', 'me = ""', "people.me must be a login"),
            ('held_words = ["skyhook", "vaultkey", "sealing"]', "held_words = []", "patent.held_words must be a non-empty list"),
            ('held_words = ["skyhook", "vaultkey", "sealing"]', 'held_words = ["tele.port"]', "patent.held_words must be"),
            ('"Cargo.lock", "package-lock.json"', '"a/Cargo.lock", "package-lock.json"', "patent.skip_lockfiles must be"),
            ('private_repo = "acme/app-private"', 'private_repo = "acme/app"', "patent.private_repo must differ"),
            ('private_repo = "acme/app-private"', 'private_repo = "Acme/APP"', "patent.private_repo must differ"),
            ('[repos."acme/app"]', '[repos."acme"]', "repos.acme must be an owner/name table"),
            ('never_rerun = ["cla"]', 'never_rerun = ["cla", "cla"]', "merge.never_rerun must be a list of distinct check names"),
            ('tiers = ["light", "standard", "deep", "human"]', "tiers = []", "merge.tiers must be a non-empty list"),
            ('tiers = ["light", "standard", "deep", "human"]', 'tiers = ["light", "standard", "de.p", "deep", "human"]',
             "merge.tiers must be a non-empty list of distinct lowercase words"),
            ('preapprove_tiers = ["deep", "human"]', 'preapprove_tiers = ["deep", "hman"]', "merge.preapprove_tiers names hman"),
            ('gate_check = "ci-gate"', "", "missing merge.gate_check"),
            ('fixes = "Fixes #{issue}"', 'fixes = "Fixes #{n}"', "issues.fixes must be one line with {issue} once"),
            ('fixes = "Fixes #{issue}"', "", "missing issues.fixes"),
            ('salvaged_from = "Salvaged from #{pr}"', 'salvaged_from = "Salvaged from {pr}"', "issues.salvaged_from must be"),
            ('url = "https://example.invalid/wake"', 'url = "http://example.invalid/wake"', "wake.url must be an https URL"),
            ('owner = "Robin"', "", "missing people.owner"),
            ('clone = "~/work/app"', 'clone = "work/app"', 'repos."acme/app".clone must be a path from / or ~/'),
        ]
        for old, new, why in cases:
            with self.subTest(new=new or f"drop {old.strip()}"):
                self.edit(old, new)
                r = self.run_("check")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(why, r.stderr)

    def test_a_missing_file_is_refused(self):
        (self.pkg / FILES[1]).unlink()
        r = self.run_("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no repos/<owner>/<name>/policy.toml", r.stderr)
        (self.pkg / FILES[0]).unlink()
        self.assertIn("no machine file", self.run_("check").stderr)

    def test_readers_refuse_a_policy_bin_policy_rejects(self):
        self.edit("fix_attempts = 3", "fix_attempts = -1")
        for args in (["seat", "coding"], ["rules"], ["dump"]):
            with self.subTest(args=args):
                r = self.run_(*args)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("ci.fix_attempts must be a positive integer, not -1", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_the_merge_job_runs_no_gate_without_its_policy(self):
        (self.pkg / "services").mkdir(parents=True)
        for f in ("merge_job.py", "wake.py"):
            shutil.copy2(PKG / "services" / f, self.pkg / "services" / f)
        (self.pkg / "bin" / "gh").write_text("#!/bin/sh\necho called >> \"$0.log\"\n")
        (self.pkg / "bin" / "gh").chmod(0o755)
        self.edit('tiers = ["light", "standard", "deep", "human"]', 'tiers = ["light", "standard"]')
        env = {**self.env, "PATH": f"{self.pkg / 'bin'}:{self.env['PATH']}", "ZSTACK_ENSURED": "1"}
        r = subprocess.run([sys.executable, str(self.pkg / "services/merge_job.py"), "--dry-run"], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("merge_job: merge.preapprove_tiers names deep, human, which merge.tiers lacks", r.stderr)
        self.assertFalse((self.pkg / "bin" / "gh.log").exists())

    def test_hold_row_writes_nothing_without_the_merge_gates_policy(self):
        (self.pkg / "services").mkdir(parents=True, exist_ok=True)
        for f in ("merge_job.py", "wake.py"):
            shutil.copy2(PKG / "services" / f, self.pkg / "services" / f)
        shutil.copy2(PKG / "bin" / "hold-row", self.pkg / "bin" / "hold-row")
        root = Path(self.env["ONIGIRI_ROOT"])
        root.mkdir(parents=True)
        (root / "hold-prs.txt").write_text("hold | 1 | no | x\n")
        self.edit('gate_check = "ci-gate"', "")
        r = self.run_("add", "hold | 2 | no | Robin 12 Nov 15:00 SGT", prog="bin/hold-row")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("merge_job: missing merge.gate_check", r.stderr)
        self.assertEqual((root / "hold-prs.txt").read_text(), "hold | 1 | no | x\n")
        self.assertEqual(sorted(p.name for p in root.iterdir()), ["hold-prs.txt"])

    def test_a_push_is_refused_without_the_patent_policy(self):
        (self.pkg / "services").mkdir(parents=True, exist_ok=True)
        for f in ("patent_push.py", "wake.py", "pre-push"):
            shutil.copy2(PKG / "services" / f, self.pkg / "services" / f)
        self.edit('held_words = ["skyhook", "vaultkey", "sealing"]', 'held_words = ["Skyhook"]')
        r = subprocess.run([str(self.pkg / "services/pre-push"), "origin", "https://github.com/acme/app"],
                           input=f"refs/heads/x {'a' * 40} refs/heads/x {'0' * 40}\n", capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("patent hold: the config is unreadable (patent.held_words must be", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_push_under_a_python_without_tomllib_still_reads_the_patent_policy(self):
        (self.pkg / "services").mkdir(parents=True, exist_ok=True)
        for f in ("patent_push.py", "wake.py"):
            shutil.copy2(PKG / "services" / f, self.pkg / "services" / f)
        (self.pkg / "notoml").mkdir()
        (self.pkg / "notoml" / "tomllib.py").write_text("raise ModuleNotFoundError(\"No module named 'tomllib'\")\n")
        code = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('pp', sys.argv[1]); "
                "pp = importlib.util.module_from_spec(s); s.loader.exec_module(pp); print(pp.held_hits(['src/sealing.rs'], ''))")
        r = subprocess.run([sys.executable, "-c", code, str(self.pkg / "services/patent_push.py")], capture_output=True, text=True,
                           env={**self.env, "PYTHONPATH": str(self.pkg / "notoml")}, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, "['src/sealing.rs']\n"), r.stderr)

    def test_the_old_json_configs_are_gone(self):
        for f in ("models.json", "slots.json", "ci.json"):
            self.assertFalse((PKG / "config" / f).exists(), f)


if __name__ == "__main__":
    unittest.main()
