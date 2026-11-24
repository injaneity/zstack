import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

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
        for f in ("queue.py", "hooks/pre-push", *FILES, "config/invariants.toml", "config/repos/acme/app/invariants.toml"):
            (self.pkg / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PKG / f, self.pkg / f)
        shutil.copytree(PKG / "vendor", self.pkg / "vendor")
        self.env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"}
        self.env.pop("ONIGIRI_ROOT", None)

    def edit(self, old, new):
        self.assertEqual(sum(t.count(old) for t in SHIPPED.values()), 1, old)
        for f, t in SHIPPED.items():
            (self.pkg / f).write_text(t.replace(old, new))

    def run_(self, *args, prog="queue.py"):
        return subprocess.run([str(self.pkg / prog), *args], capture_output=True, text=True, env=self.env, timeout=60)

    def test_the_shipped_file_is_valid_and_get_reads_it(self):
        self.assertEqual(self.run_("check").returncode, 0)
        self.assertEqual(self.run_("get", "run.threads").stdout, "3\n")
        self.assertEqual(self.run_("get", "merge.gate_check").stdout, "ci-gate\n")
        self.assertEqual(json.loads(self.run_("get", "models.verifier").stdout), {"provider": "codex", "model": "gpt-6.1-terra"})
        self.assertEqual(len(json.loads(self.run_("get", "models.designer").stdout)), 2)
        dump = json.loads(self.run_("dump").stdout)
        machine, project = (tomllib.loads(SHIPPED[f]) for f in FILES)
        self.assertEqual({k: dump[k] for k in ("models", "run", "wake")}, {k: machine[k] for k in ("models", "run", "wake")})
        self.assertEqual({k: dump[k] for k in ("people", "patent", "issues", "review")},
                         {k: project[k] for k in ("people", "patent", "issues", "review")})
        self.assertEqual(dump["merge"], {"repo": "acme/app", **project["merge"]})
        self.assertEqual(dump["repos"], ["acme/app"])
        self.assertEqual(dump["host"], {"clone": "~/work/app", "root": "~/zstack/acme/app", "repos": ["acme/app"], "labels": [], "unlabelled": True,
                                        "all_labels": []})
        self.env["ONIGIRI_ROOT"] = "/somewhere/else/"
        self.assertEqual(self.run_("get", "host.root").stdout, "/somewhere/else\n")

    def second_repo(self):
        other = self.pkg / "config" / "repos" / "acme" / "tool"
        other.mkdir(parents=True)
        (other / "policy.toml").write_text(SHIPPED[FILES[1]].replace('gate_check = "ci-gate"', 'gate_check = "tool-gate"'))
        place = '\n[repos."acme/tool"]\nclone = "~/work/tool"\nroot = "~/zstack/acme/tool"\nlabels = []\nunlabelled = false\n'
        (self.pkg / FILES[0]).write_text(SHIPPED[FILES[0]] + place)
        return other

    def test_a_machine_with_two_repos_picks_by_name_then_by_the_clone_it_runs_in(self):
        other = self.second_repo()
        r = self.run_("get", "merge.repo")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr, "queue: this machine works on acme/app, acme/tool; set ZSTACK_REPO or run from one of their clones\n")
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
        r = subprocess.run([str(self.pkg / "queue.py"), "get", "merge.repo"], capture_output=True, text=True, env=self.env, cwd=clone)
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

    def test_two_machines_whose_pickup_overlaps_on_a_repo_are_refused(self):
        m = self.pkg / "config" / "machines"
        base = SHIPPED[FILES[0]]
        self.env["ZSTACK_MACHINE"] = "mac"
        for mac, linux, why in (
                ('labels = ["macOS"]\nunlabelled = false', 'labels = ["Linux"]\nunlabelled = true', None),
                ('labels = ["macOS"]\nunlabelled = true', 'labels = ["Linux"]\nunlabelled = true', "both pick up unlabelled items"),
                ('labels = ["macOS", "arm"]\nunlabelled = false', 'labels = ["arm"]\nunlabelled = true', "both pick up the label arm"),
                ('labels = []\nunlabelled = false', 'labels = ["Linux"]\nunlabelled = true', "both pick up every item")):
            (m / "mac.toml").write_text(base.replace("labels = []\nunlabelled = true", mac))
            (m / "linux.toml").write_text(base.replace("labels = []\nunlabelled = true", linux))
            r = self.run_("check")
            with self.subTest(why=why):
                self.assertEqual(r.returncode, 1 if why else 0, r.stderr)
                self.assertIn(why or "", r.stderr)

    def test_a_repos_own_rules_are_added_to_the_stacks(self):
        mine = self.pkg / "config" / "repos" / "acme" / "app" / "invariants.toml"
        mine.write_text('schema = 1\n[[invariant]]\nid = "project-gate"\ntext = "Wait for {merge.gate_check}."\nenforced = "ci"\ntest = "x"\n'
                        '[[guidance]]\nid = "project-tone"\ntext = "Be brief."\n')
        out = self.run_("invariants").stdout
        self.assertIn("### In this repo\n\n- Wait for ci-gate.\n", out)
        self.assertNotIn("Be brief.", out)
        self.assertEqual(self.run_("invariants", "project-tone").stdout, "- Be brief.\n")
        self.assertIn("### Always", out)
        self.assertEqual(self.run_("invariants", "project-gate").stdout, "- Wait for ci-gate.\n")
        mine.unlink()
        self.assertNotIn("### In this repo", self.run_("invariants").stdout)

    def test_check_refuses_a_clone_whose_commits_would_carry_no_identity(self):
        clone = self.pkg.parent / "nameless"
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        self.edit('clone = "~/work/app"', f'clone = "{clone}"')
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.assertIn("has no git user.email", self.run_("check").stderr)
        subprocess.run(["git", "-C", str(clone), "config", "--local", "user.email", "dev@example.com"], check=True)
        self.assertEqual(self.run_("check").returncode, 0)

    def test_a_repo_with_no_patent_hold_leaves_it_out_and_pushes_freely(self):
        self.edit('[patent]\nheld_words = ["skyhook", "vaultkey", "sealing"]', '[nopatent]\nheld_words = ["skyhook", "vaultkey", "sealing"]')
        self.assertIn("unknown key nopatent", self.run_("check").stderr)
        text = SHIPPED[FILES[1]]
        start, end = text.index("[patent]"), text.index("# How a PR merges")
        (self.pkg / FILES[1]).write_text(text[:start] + text[end:])
        self.assertIn("no key patent.held_words", self.run_("check").stderr)
        (self.pkg / "config" / "repos" / "acme" / "app" / "invariants.toml").unlink()
        self.assertEqual(self.run_("check").returncode, 0)
        self.assertNotIn("patent", json.loads(self.run_("dump").stdout))

    def test_a_repo_with_no_macos_hosted_lane_leaves_it_out(self):
        self.assertEqual(self.run_("get", "verify.macos_paths").stdout, '["driver/macos/"]\n')
        self.assertEqual(self.run_("get", "verify.macos_hosted_lane").stdout,
                         "gh workflow run e2e-macos-hosted.yml --ref {branch} -f source_sha={head}\n")
        text = SHIPPED[FILES[1]]
        (self.pkg / FILES[1]).write_text(text[:text.index("\n# The macOS hosted lane")] + "\n")
        self.assertEqual(self.run_("check").returncode, 0)
        r = self.run_("get", "verify.macos_paths")
        self.assertEqual((r.returncode, r.stderr), (1, "queue: no key verify.macos_paths\n"))

    def test_a_missing_key_is_named(self):
        r = self.run_("get", "ci.nope")
        self.assertEqual((r.returncode, r.stderr), (1, "queue: no key ci.nope\n"))

    def test_every_bad_edit_is_refused_with_its_reason(self):
        cases = [
            ("threads = 3", "threads = 0", "run.threads must be a positive integer, not 0"),
            ("threads = 3", 'threads = "3"', "run.threads must be a positive integer, not '3'"),
            ("threads = 3", "threads = true", "run.threads must be a positive integer, not True"),
            ("threads = 3\n", "", "missing run.threads"),
            ("threads = 3", "threads = 3\ntotal = 7", "unknown key run.total"),
            ('outside = "salvage"', 'outside = "salvage"\noutsid = "x"', "unknown key review.outsid"),
            ("schema = 1\n\n# Who", "schema = 2\n\n# Who", "schema must be 1, not 2"),
            ("schema = 1\n\n# The provider", "schema = true\n\n# The provider", "schema must be 1, not True"),
            ("verifier = {", "verifer = {", "unknown key models.verifer"),
            ('coding = { provider = "claudeAgent", model = "claude-opus-5-5" }\n', "", "missing models.coding"),
            ('explorer = {', 'orchestrator = { provider = "codex", model = "gpt-6.1-terra" }\nexplorer = {', "unknown key models.orchestrator"),
            ('t3 = "t3"\n', "", "missing run.t3"),
            ('t3 = "t3"', 't3 = ""', "run.t3 must be the T3 Code command"),
            ("lost_min = 15\n", "", "missing run.lost_min"),
            ("lost_min = 15", "lost_min = 0", "run.lost_min must be whole minutes"),
            ("lost_min = 15", 'lost_min = "15"', "run.lost_min must be whole minutes"),
            ("lost_min = 15", "lost_min = 2", "run.lost_min must be whole minutes, more than the 120 seconds a start may take, not 2"),
            ("schema = 1\n\n# Who", "schema = 1\nslot = 3\n\n# Who", "unknown key slot"),
            ('[review]\noutside = "salvage"\n', "", "[review] is missing or empty"),
            ('verifier = { provider = "codex", model = "gpt-6.1-terra" }', 'verifier = { provider = "codex" }',
             "models.verifier must be a {provider, model} table or a non-empty list of them"),
            ('verifier = { provider = "codex", model = "gpt-6.1-terra" }', "verifier = []", "models.verifier must be"),
            ('verifier = { provider = "codex", model = "gpt-6.1-terra" }',
             'verifier = { provider = "codex", model = "gpt-6.1-terra", fallback = true }', "models.verifier must be"),
            ("[people]", "[people", "is not TOML"),
            ('me = "octo"', 'me = ""', "people.me must be a login"),
            ('held_words = ["skyhook", "vaultkey", "sealing"]', "held_words = []", "patent.held_words must be a non-empty list"),
            ('held_words = ["skyhook", "vaultkey", "sealing"]', 'held_words = ["tele.port"]', "patent.held_words must be"),
            ('"Cargo.lock", "package-lock.json"', '"a/Cargo.lock", "package-lock.json"', "patent.skip_lockfiles must be"),
            ('private_repo = "acme/app-private"', 'private_repo = "acme/app"', "patent.private_repo must differ"),
            ('private_repo = "acme/app-private"', 'private_repo = "Acme/APP"', "patent.private_repo must differ"),
            ('[repos."acme/app"]', '[repos."acme"]', "repos.acme must be an owner/name table"),
            ("labels = []", 'labels = ["a", "a"]', 'repos."acme/app".labels must be a list of distinct label names'),
            ('review_label = "needs-review"', 'review_label = ""', "merge.review_label must be a label name"),
            ('preapproved_labels = ["preapproved"]', 'preapproved_labels = ["a", "a"]', "merge.preapproved_labels must be a list of distinct"),
            ('preapproved_labels = ["preapproved"]', 'preapproved_labels = ["needs-review"]', "merge.preapproved_labels must all differ"),
            ('hold_label = "hold"', 'hold_label = "needs-review"', "merge.preapproved_labels must all differ"),
            ('hold_label = "hold"', "", "missing merge.hold_label"),
            ('review_only = ["f-teammate"]', 'review_only = "f-teammate"', "people.review_only must be a list of distinct logins"),
            ('skip = ["app/app-agents"]', "", "missing people.skip"),
            ('gate_check = "ci-gate"', "", "missing merge.gate_check"),
            ('fixes = "Fixes #{issue}"', 'fixes = "Fixes #{n}"', "issues.fixes must be one line with {issue} once"),
            ('fixes = "Fixes #{issue}"', "", "missing issues.fixes"),
            ('refs = "Refs #{issue}"', 'refs = "Refs #{n}"', "issues.refs must be one line with {issue} once"),
            ('salvaged_from = "Salvaged from #{pr}"', 'salvaged_from = "Salvaged from {pr}"', "issues.salvaged_from must be"),
            ('url = "https://example.invalid/wake"', 'url = "http://example.invalid/wake"', "wake.url must be an https URL"),
            ('owner = "Robin"', "", "missing people.owner"),
            ('clone = "~/work/app"', 'clone = "work/app"', 'repos."acme/app".clone must be a path from / or ~/'),
            ('macos_paths = ["driver/macos/"]', "macos_paths = []", "verify.macos_paths must be a non-empty list of distinct paths in the repo"),
            ('macos_paths = ["driver/macos/"]', 'macos_paths = ["/driver/macos/"]', "verify.macos_paths must be"),
            ("source_sha={head}", "source_sha=HEAD", "verify.macos_hosted_lane must be one line with {branch} and {head} once each"),
            ("--ref {branch}", "--ref {branch} {branch}", "verify.macos_hosted_lane must be"),
            ("source_sha={head}", "source_sha={head} -f quick={quick}", "verify.macos_hosted_lane must be"),
            ("macos_hosted_lane = ", "macos_hosted_lan = ", "unknown key verify.macos_hosted_lan"),
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

    def test_every_reader_refuses_a_config_the_check_rejects(self):
        self.edit("threads = 3", "threads = -1")
        for args in (["seat", "coding"], ["invariants"], ["dump"]):
            with self.subTest(args=args):
                r = self.run_(*args)
                self.assertNotEqual(r.returncode, 0, r.stdout)
                self.assertIn("run.threads must be a positive integer, not -1", r.stderr)
                self.assertNotIn("Traceback", r.stderr)

    def test_a_push_is_refused_without_the_patent_policy(self):
        self.edit('held_words = ["skyhook", "vaultkey", "sealing"]', 'held_words = ["Skyhook"]')
        r = subprocess.run([str(self.pkg / "hooks/pre-push"), "origin", "https://github.com/acme/app"],
                           input=f"refs/heads/x {'a' * 40} refs/heads/x {'0' * 40}\n", capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("patent hold: the config is unreadable (patent.held_words must be", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_a_push_under_a_python_without_tomllib_still_reads_the_patent_policy(self):
        (self.pkg / "notoml").mkdir()
        (self.pkg / "notoml" / "tomllib.py").write_text("raise ModuleNotFoundError(\"No module named 'tomllib'\")\n")
        code = ("import importlib.util, sys; s = importlib.util.spec_from_file_location('pp', sys.argv[1]); "
                "q = importlib.util.module_from_spec(s); s.loader.exec_module(q); print(q.Patent(q.load()).held_hits(['src/sealing.rs'], ''))")
        r = subprocess.run([sys.executable, "-c", code, str(self.pkg / "queue.py")], capture_output=True, text=True,
                           env={**self.env, "PYTHONPATH": str(self.pkg / "notoml")}, timeout=60)
        self.assertEqual((r.returncode, r.stdout), (0, "['src/sealing.rs']\n"), r.stderr)

    def test_the_old_json_configs_are_gone(self):
        for f in ("models.json", "slots.json", "ci.json"):
            self.assertFalse((PKG / "config" / f).exists(), f)


if __name__ == "__main__":
    unittest.main()
