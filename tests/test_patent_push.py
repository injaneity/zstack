import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

PKG = Path(__file__).resolve().parent.parent
H = "a" * 40
spec = importlib.util.spec_from_file_location("queue_patent", PKG / "queue.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)
pp = q.Patent(q.load())


class PatentPush(unittest.TestCase):

    def setUp(self):
        p = patch.dict(os.environ, {"ONIGIRI_WAKE_DRY": "1"})
        p.start()
        self.addCleanup(p.stop)

    def test_held_hits(self):
        self.assertEqual(pp.held_hits(["libs/app-spacesd/src/sealing.rs"], ""), ["libs/app-spacesd/src/sealing.rs"])
        self.assertTrue(pp.held_hits(["a.rs"], "+++ b/a.rs\n+let v = VaultKey::open();\n"))
        self.assertTrue(pp.held_hits(["a.rs"], "-// skyhook the session\n"))
        self.assertEqual(pp.held_hits(["libs/app-relay/src/lib.rs"], "+relay.unregister()\n"), [])
        self.assertEqual(pp.held_hits(["src/fix.rs"], "diff --git a/src/fix.rs b/src/fix.rs\n+// resealing the cache\n"), [])
        lock = "libs/app-spacesd/tests/skyhook-e2e/Cargo.lock"
        self.assertEqual(pp.held_hits([lock], f"diff --git a/{lock} b/{lock}\n+name = \"skyhook-e2e\"\n"), [])

    def test_minified_and_bundled_js_is_skipped(self):
        long = "+" + "x" * 1000 + "Space in the app (Skyhook)." + "y" * 50
        self.assertEqual(pp.held_hits(["web/assets/viewer.js"], "diff --git a/web/assets/viewer.js b/web/assets/viewer.js\n" + long + "\n"), [])
        for path in ("web/vendor/skyhook.min.js", "web/dist/skyhook.js", "web/bundle/app.js", "web/app.bundle.mjs"):
            with self.subTest(path=path):
                self.assertEqual(pp.held_hits([path], f"diff --git a/{path} b/{path}\n+const skyhook = 1;\n"), [])
        self.assertTrue(pp.held_hits(["web/src/viewer.ts"], "diff --git a/web/src/viewer.ts b/web/src/viewer.ts\n+skyhook.open();\n"))
        self.assertTrue(pp.held_hits(["web/distill/skyhook.js"], ""))

    def test_a_whitespace_only_change_is_skipped(self):
        reindent = ("diff --git a/a.rs b/a.rs\n@@ -1,2 +1,2 @@\n-fn skyhook() {\n-  vaultkey::open();\n"
                    "+    fn skyhook()  {\n+        vaultkey::open();\n")
        self.assertEqual(pp.held_hits(["a.rs"], reindent), [])
        moved = "diff --git a/a.rs b/a.rs\n@@ -1 +0,0 @@\n-fn skyhook() {}\n@@ -9,0 +9 @@\n+fn skyhook() {}\n"
        self.assertEqual(len(pp.held_hits(["a.rs"], moved)), 2)
        self.assertEqual(pp.held_hits(["a.rs"], "diff --git a/a.rs b/a.rs\n@@ -1 +1,2 @@\n-skyhook();\n+ skyhook();\n+skyhook();\n"), ["+skyhook();"])
        self.assertTrue(pp.held_hits(["a.rs"], "diff --git a/a.rs b/a.rs\n@@ -1 +1 @@\n-skyhook(a);\n+skyhook(b);\n"))

    def test_held_words_in_doc_comments_and_doc_tables_are_skipped(self):
        for line in ("/// the size skyhook of every window", "//! a skyhook of sizes", "/** Skyhook rules. */",
                     " * every skyhook gives", "    /// hidden ones, for the skyhook"):
            with self.subTest(line=line):
                self.assertEqual(pp.held_hits(["src/a.rs"], f"diff --git a/src/a.rs b/src/a.rs\n+{line}\n"), [])
        row = "diff --git a/src/bridge/README.md b/src/bridge/README.md\n+| `skyhookCode` | returns the skyhook code |\n"
        self.assertEqual(pp.held_hits(["src/bridge/README.md"], row), [])
        for path, line in (("src/a.rs", "let mut skyhook: Vec<usize> = vec![];"), ("src/a.rs", "// skyhook the session"),
                           ("src/a.ts", "x.map(|s| skyhook(s))"), ("src/a.rs", "*ptr = skyhook();"), ("docs/a.md", "Skyhook pairs two hosts.")):
            with self.subTest(line=line):
                self.assertTrue(pp.held_hits([path], f"diff --git a/{path} b/{path}\n+{line}\n"))
        self.assertTrue(pp.held_hits(["docs/skyhook.md"], "diff --git a/docs/skyhook.md b/docs/skyhook.md\n+| a | b |\n"))

    def test_choose_remote(self):
        pub, stg = "https://github.com/acme/app", "https://github.com/acme/app-private.git"
        self.assertEqual(pp.choose_remote(pub, [], lambda: False), "push")
        self.assertEqual(pp.choose_remote(pub, ["src/sealing.rs"], lambda: False), "hold")
        self.assertEqual(pp.choose_remote(pub, ["src/sealing.rs"], lambda: True), "staging")
        self.assertEqual(pp.choose_remote(stg, ["src/sealing.rs"], lambda: False), "push")

    def repo(self, tmp, base=None):
        def g(*a, cwd=None):
            return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
        pub, work, root = f"{tmp}/public.git", f"{tmp}/work", f"{tmp}/root"
        os.makedirs(root)
        g("init", "-q", "--bare", pub)
        g("init", "-q", "-b", "main", work)
        for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
            g("config", k, v, cwd=work)
        for name, text in {"README": "x\n", **(base or {})}.items():
            Path(work, name).parent.mkdir(parents=True, exist_ok=True)
            Path(work, name).write_text(text)
        g("add", ".", cwd=work)
        g("commit", "-qm", "base", cwd=work)
        g("remote", "add", "origin", pub, cwd=work)
        g("push", "-q", "origin", "main", cwd=work)
        g("fetch", "-q", "origin", cwd=work)
        hook = Path(work, ".git/hooks/pre-push")
        hook.write_text(f"#!{sys.executable}\nimport importlib.util, os, sys\n"
                        f"os.environ['ONIGIRI_ROOT'] = {root!r}\n"
                        f"s = importlib.util.spec_from_file_location('q', {str(PKG / 'queue.py')!r})\n"
                        "q = importlib.util.module_from_spec(s); s.loader.exec_module(q)\n"
                        "sys.exit(q.Patent(q.load()).main(sys.argv[1:], sys.stdin, staging_ok=lambda: False))\n")
        hook.chmod(0o755)
        return g, pub, work, root

    def push(self, tmp, branch, name, text):
        g, pub, work, root = self.repo(tmp)
        g("checkout", "-qb", branch, cwd=work)
        Path(work, name).parent.mkdir(parents=True, exist_ok=True)
        Path(work, name).write_text(text)
        g("add", ".", cwd=work)
        g("commit", "-qm", "x", cwd=work)
        r = subprocess.run(["git", "push", "origin", branch], cwd=work, capture_output=True, text=True)
        return r, g("ls-remote", pub, f"refs/heads/{branch}"), root

    def test_a_sealing_diff_is_not_pushed_publicly_and_wakes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote, root = self.push(tmp, "onigiri/x", "src/sealing.rs", "fn pair() {}\n")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("patent hold", r.stderr)
            self.assertEqual(remote, "")
            self.assertEqual([x.split(" ", 3)[1:3] for x in Path(f"{root}/events.log").read_text().splitlines()],
                             [["patent-held", "app:onigiri/x"]])
            self.assertIn('"kind": "blocked"', r.stdout + r.stderr)
            self.assertFalse(os.path.exists(f"{root}/buffer.jsonl"))
            g = subprocess.run(["git", "push", "origin", "onigiri/x"], cwd=f"{tmp}/work", capture_output=True, text=True)
            self.assertNotEqual(g.returncode, 0)
            self.assertNotIn('"kind": "blocked"', g.stdout + g.stderr)
            self.assertEqual(len(Path(f"{root}/events.log").read_text().splitlines()), 1)

    def test_a_ref_with_unicode_space_or_line_separator_is_still_checked(self):
        for branch in ("onigiri/x\u00a0y", "onigiri/x\u2028y"):
            with tempfile.TemporaryDirectory() as tmp:
                r, remote, _ = self.push(tmp, branch, "src/sealing.rs", "fn pair() {}\n")
                self.assertNotEqual(r.returncode, 0, branch)
                self.assertIn("patent hold", r.stderr)
                self.assertEqual(remote, "")

    def test_a_source_with_spaces_is_still_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, pub, work, _ = self.repo(tmp)
            Path(work, "src").mkdir()
            Path(work, "src/sealing.rs").write_text("fn pair() {}\n")
            g("add", ".", cwd=work)
            g("commit", "-qm", "x", cwd=work)
            r = subprocess.run(["git", "push", "origin", "HEAD@{0 seconds ago}:refs/heads/onigiri/x"], cwd=work, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("patent hold", r.stderr)
            self.assertEqual(g("ls-remote", pub, "refs/heads/onigiri/x"), "")

    def test_a_patent_wake_that_failed_is_sent_again_on_the_next_push(self):
        with tempfile.TemporaryDirectory() as root:
            there = q.Patent({**pp.pol, "host": {**pp.pol["host"], "root": root}})
            with patch.object(q, "wake", side_effect=["webhook failed: OSError", "webhook sent (200)"]) as post:
                self.assertFalse(there.wake_hold("onigiri/z", H, ["src/sealing.rs"]))
                self.assertTrue(there.wake_hold("onigiri/z", H, ["src/sealing.rs"]))
                self.assertFalse(there.wake_hold("onigiri/z", H, ["src/sealing.rs"]))
            self.assertEqual(post.call_count, 2)
            self.assertEqual([x.split(" ", 7)[6] for x in Path(f"{root}/events.log").read_text().splitlines()], ["failed:", "sent"])

    def test_the_installed_hook_is_a_symlink_from_its_new_path_or_its_old_one_and_logs_under_the_configured_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, pub, work, _ = self.repo(tmp)
            hook = Path(work, ".git/hooks/pre-push")
            hook.unlink()
            hook.symlink_to(PKG / "hooks" / "pre-push")
            self.held_and_clean(tmp, g, pub, work, "w")
        with tempfile.TemporaryDirectory() as tmp:
            g, pub, work, _ = self.repo(tmp)
            hook = Path(work, ".git/hooks/pre-push")
            hook.unlink()
            hook.symlink_to(PKG / "services" / "pre-push")
            self.held_and_clean(tmp, g, pub, work, "old")

    def held_and_clean(self, tmp, g, pub, work, name):
        Path(tmp, "bin").mkdir()
        Path(tmp, "bin/gh").write_text("#!/bin/sh\nexit 1\n")
        Path(tmp, "bin/gh").chmod(0o755)
        Path(tmp, "bin/python3").symlink_to(sys.executable)
        env = {"PATH": f"{tmp}/bin:/usr/bin:/bin", "HOME": tmp, "GH_CONFIG_DIR": f"{tmp}/gh", "ONIGIRI_WAKE_DRY": "1"}
        g("worktree", "add", "-qb", f"onigiri/{name}", f"{tmp}/wt", cwd=work)
        Path(tmp, "wt/sealing.rs").write_text("fn pair() {}\n")
        g("add", ".", cwd=f"{tmp}/wt")
        g("commit", "-qm", "x", cwd=f"{tmp}/wt")
        r = subprocess.run(["git", "push", "origin", f"onigiri/{name}"], cwd=f"{tmp}/wt", capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("patent hold", r.stderr)
        self.assertEqual(g("ls-remote", pub, f"refs/heads/onigiri/{name}"), "")
        log = Path(f"{tmp}/zstack/acme/app/events.log").read_text().splitlines()
        self.assertEqual([x.split(" ", 3)[1:3] for x in log], [["patent-held", f"app:onigiri/{name}"]])
        g("checkout", "-qb", "onigiri/ok", cwd=work)
        Path(work, "relay.rs").write_text("fn relay() {}\n")
        g("add", ".", cwd=work)
        g("commit", "-qm", "y", cwd=work)
        r = subprocess.run(["git", "push", "origin", "onigiri/ok"], cwd=work, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        pushes = [json.loads(x) for x in Path(f"{tmp}/zstack/acme/app/pushes.jsonl").read_text().splitlines()]
        self.assertEqual([(x["worktree"], x["ref"]) for x in pushes], [(os.path.realpath(work), "refs/heads/onigiri/ok")])

    def commits(self, tmp, branch, steps, base=None):
        g, pub, work, root = self.repo(tmp, base)
        g("checkout", "-qb", branch, cwd=work)
        for i, step in enumerate(steps):
            if isinstance(step, tuple):
                g("mv", step[1], step[2], cwd=work)
            else:
                for name, text in step.items():
                    if text is None:
                        g("rm", "-q", name, cwd=work)
                        continue
                    Path(work, name).parent.mkdir(parents=True, exist_ok=True)
                    Path(work, name).write_text(text)
                    g("add", name, cwd=work)
            g("commit", "-qm", f"step {i}", cwd=work)
        r = subprocess.run(["git", "push", "origin", branch], cwd=work, capture_output=True, text=True)
        return r, g("ls-remote", pub, f"refs/heads/{branch}")

    def test_a_held_commit_reverted_later_in_the_push_is_still_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/rev", [{"a.rs": "// vaultkey probe\n"}, {"a.rs": None}])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("patent hold", r.stderr)
            self.assertEqual(remote, "")

    def test_a_held_line_already_on_the_public_main_is_pushed(self):
        line = "// skyhook still sends this explicitly on graceful\n"
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/moved", [{"src/serve.rs": "fn a() {}\n", "src/ipc.rs": f"fn b() {{\n        {line}}}\n"}],
                                     base={"src/serve.rs": f"fn a() {{}}\n    {line}"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(remote, "")
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/new", [{"src/ipc.rs": f"{line}// skyhook is available from the store\n"}],
                                     base={"src/serve.rs": line})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("skyhook is available", r.stderr)
            self.assertEqual(remote, "")
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/space", [{"src/serve.rs": "// skyhook  still sends this\n"}],
                                     base={"src/serve.rs": "// skyhook still sends this\n"})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(remote, "")
        self.assertEqual(pp.held_hits([], "+// Skyhook the session\n", public=lambda t: t == "// Skyhook the session"), [])
        self.assertTrue(pp.held_hits([], "+// Skyhook the session\r secret\n", public=lambda t: t == "// Skyhook the session"))
        self.assertTrue(pp.held_hits([], "+// Skyhook the session\n", public=lambda t: False))

    def test_a_carriage_return_does_not_split_a_public_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/cr", [{"src/ipc.rs": "// skyhook still sends this\rsecret\n"}],
                                     base={"src/serve.rs": "// skyhook still sends this\n"})
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(remote, "")
        cr = "diff --git a/x b/x\n@@ -0,0 +1 @@\n+a\r@@@ b\r++skyhook\n"
        self.assertTrue(pp.held_hits([], cr, public=lambda t: True))
        self.assertTrue(pp.held_hits([], "diff --git a/x b/x\n@@ -0,0 +1 @@\n+// skyhook\x0b\n", public=lambda t: True))
        self.assertTrue(pp.held_hits([], "diff --git a/x b/x\n@@ -0,0 +1 @@\n+// skyhook\r\n", public=lambda t: True))
        crlf = "// skyhook " + "x" * (q.LONG_LINE - 11)
        for name, text in (("crlf", f"{crlf}\r\n"), ("tail", "// skyhook x\r" + "y" * q.LONG_LINE + "\n")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                r, remote = self.commits(tmp, f"onigiri/{name}", [{"src/serve.rs": text}])
                self.assertNotEqual(r.returncode, 0)
                self.assertEqual(remote, "")
        with tempfile.TemporaryDirectory() as tmp:
            g, _, work, _ = self.repo(tmp, {"src/serve.rs": "// skyhook x\rsecret\n"})
            for key in ("grep.lineNumber", "grep.column"):
                g("config", key, "true", cwd=work)
            self.assertFalse(q.Patent.in_tree("// skyhook x", "origin/main", cwd=work))
            self.assertFalse(q.Patent.in_tree("1:1:// skyhook x secret", "origin/main", cwd=work))
            self.assertFalse(q.Patent.in_tree("// skyhook x secret", "origin/main", cwd=work))
        with tempfile.TemporaryDirectory() as tmp:
            _, _, work, _ = self.repo(tmp, {"src/a.rs": "// skyhook\r\r\n", "src/b.rs": "// skyhook y\r\n", "src/c.rs": "// skyhook z\r"})
            for text in ("// skyhook", "// skyhook y", "// skyhook z"):
                self.assertFalse(q.Patent.in_tree(text, "origin/main", cwd=work))
            Path(work, "d.rs").write_bytes(b"\xff x\n")
            with self.assertRaises(UnicodeDecodeError):
                q.git("diff", "--no-index", "-U0", os.devnull, "d.rs", cwd=work, raw=True)
            with self.assertRaises(UnicodeDecodeError):
                q.git("-c", "alias.e=!printf '\\377' >&2", "e", cwd=work, raw=True)

    def test_a_held_file_renamed_away_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/mv", [{"src/sealing.rs": "fn x() {}\n"}, ("mv", "src/sealing.rs", "src/link.rs")])
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(remote, "")

    def test_rename_source_and_target_paths_both_count(self):
        ns = "R100\tsrc/sealing.rs\tsrc/link.rs\nM\tREADME\n"
        self.assertEqual(q.status_paths(ns), ["src/sealing.rs", "src/link.rs", "README"])
        self.assertEqual(pp.held_hits(q.status_paths(ns), ""), ["src/sealing.rs"])
        self.assertEqual(pp.held_hits(q.status_paths("R090\tsrc/a.rs\tsrc/skyhook.rs\n"), ""), ["src/skyhook.rs"])

    def test_a_combined_diff_counts_only_the_merges_own_lines(self):
        cc = ("diff --cc a.rs\nindex 1,2..3\n--- a/a.rs\n+++ b/a.rs\n@@@ -1,1 -1,1 +1,2 @@@\n"
              " +// skyhook from main\n++// resolved\n")
        self.assertEqual(pp.held_hits([], cc), [])
        self.assertTrue(pp.held_hits([], cc.replace("++// resolved", "++// vaultkey in the resolution")))
        self.assertTrue(pp.held_hits([], "diff --git a/x b/x\n@@ -1 +0,0 @@\n--- skyhook removed\n"))

    def test_merging_main_that_already_has_held_work_is_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, pub, work, root = self.repo(tmp)
            g("checkout", "-qb", "onigiri/m", cwd=work)
            Path(work, "mine.rs").write_text("fn mine() {}\n")
            g("add", ".", cwd=work)
            g("commit", "-qm", "mine", cwd=work)
            up = f"{tmp}/upstream"
            g("clone", "-q", "-b", "main", pub, up)
            for k, v in (("user.name", "u"), ("user.email", "u@u"), ("commit.gpgsign", "false")):
                g("config", k, v, cwd=up)
            Path(up, "src").mkdir()
            Path(up, "src/sealing.rs").write_text("fn pair() {}\n")
            g("add", ".", cwd=up)
            g("commit", "-qm", "upstream", cwd=up)
            g("push", "-q", "origin", "HEAD:main", cwd=up)
            g("fetch", "-q", "origin", cwd=work)
            g("checkout", "-q", "onigiri/m", cwd=work)
            g("merge", "-q", "--no-edit", "origin/main", cwd=work)
            r = subprocess.run(["git", "push", "origin", "onigiri/m"], cwd=work, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_branch_whose_history_is_clean_is_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/ok", [{"a.rs": "fn a() {}\n"}, ("mv", "a.rs", "b.rs")])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(remote, "")

    def test_a_push_whose_commit_carries_a_tool_trailer_is_refused_with_the_reason(self):
        for name, text, msg, why in (("a.rs", "fn a() {}\n", "fix: a\n\nCo-authored-by: Claude <noreply@anthropic.com>", "the repo invariant text"),):
            with self.subTest(why=why), tempfile.TemporaryDirectory() as tmp:
                g, pub, work, root = self.repo(tmp)
                g("checkout", "-qb", "onigiri/z", cwd=work)
                Path(work, name).parent.mkdir(parents=True, exist_ok=True)
                Path(work, name).write_text(text)
                g("add", "-f", ".", cwd=work)
                g("commit", "-qm", msg, cwd=work)
                r = subprocess.run(["git", "push", "origin", "onigiri/z"], cwd=work, capture_output=True, text=True)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("push refused", r.stderr)
                self.assertIn(why, r.stderr)
                self.assertEqual(g("ls-remote", pub, "refs/heads/onigiri/z"), "")
                self.assertFalse(os.path.exists(f"{root}/events.log"))

    def test_someone_elses_trailer_and_what_the_repos_own_ci_checks_are_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            g, pub, work, root = self.repo(tmp)
            g("checkout", "-qb", "onigiri/s", cwd=work)
            Path(work, "target").write_text("a file, not a build directory\n")
            g("add", ".", cwd=work)
            g("-c", "user.email=other@x", "commit", "-qm", "fix: theirs\n\nCo-authored-by: B <b@b.c>", cwd=work)
            r = subprocess.run(["git", "push", "origin", "onigiri/s"], cwd=work, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_relay_diff_is_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote, root = self.push(tmp, "onigiri/y", "relay.rs", "fn relay_unregister() {}\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(remote, "")
            self.assertFalse(os.path.exists(f"{root}/events.log"))


if __name__ == "__main__":
    unittest.main()
